"""Task dispatcher — bounded concurrent execution of the pending task queue.

The :class:`TaskDispatcher` is the one consumer the kernel queue was missing:
it polls :meth:`TaskManager.take_next_pending` (atomic, even across processes
sharing a SQLite task store) and runs each claimed task through an injected
execute callable — ``AgencyOrchestrator.execute_task`` — with a bounded
worker pool. Tasks from different submitters therefore execute in parallel
instead of awaiting inline.

One execution authority: the dispatcher claims a task (PENDING -> RUNNING)
before anyone else can; waiters never execute, they await
:meth:`wait_task_result`-style completion via :meth:`TaskDispatcher.wait`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Final

import structlog

from agency.kernel.tasks import Task, TaskManager, TaskStatus

log = structlog.get_logger(__name__)

DEFAULT_POLL_INTERVAL_S: Final = 0.25
DEFAULT_EVENT_TTL_S: Final = 300.0

_TERMINAL: Final[frozenset[TaskStatus]] = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.BLOCKED}
)

ExecuteFn = Callable[[str], Awaitable[object]]


class TaskDispatcher:
    """Runs pending tasks concurrently through an injected execute callable.

    Usage::

        dispatcher = TaskDispatcher(task_manager, orchestrator.execute_task,
                                    max_concurrency=2)
        dispatcher.start()
        ...
        await dispatcher.stop()  # drains in-flight executions
    """

    def __init__(
        self,
        task_manager: TaskManager,
        execute: ExecuteFn,
        *,
        max_concurrency: int = 2,
        poll_interval: float = DEFAULT_POLL_INTERVAL_S,
        event_ttl: float = DEFAULT_EVENT_TTL_S,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._task_manager = task_manager
        self._execute = execute
        self._max_concurrency = max_concurrency
        self._poll_interval = poll_interval
        self._event_ttl = event_ttl
        self._running = False
        self._loop_task: asyncio.Task[None] | None = None
        self._in_flight: dict[str, asyncio.Task[None]] = {}
        # Completion events: created on dispatch, also registered by waiters
        # for tasks that have not been claimed yet. Pruned event_ttl seconds
        # after completion so long-running servers do not accumulate them.
        self._done_events: dict[str, asyncio.Event] = {}

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    @property
    def running(self) -> bool:
        return self._running

    @property
    def in_flight_count(self) -> int:
        return len(self._in_flight)

    def start(self) -> None:
        """Start the poll loop (idempotent)."""

        if self._running:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self._run_loop())
        log.info("dispatcher.started", max_concurrency=self._max_concurrency)

    async def stop(self, *, drain_timeout: float = 30.0) -> None:
        """Stop polling and drain in-flight executions (best effort)."""

        self._running = False
        loop_task = self._loop_task
        self._loop_task = None
        if loop_task is not None:
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)
        in_flight = list(self._in_flight.values())
        if in_flight:
            log.info("dispatcher.draining", count=len(in_flight))
            done, pending = await asyncio.wait(in_flight, timeout=drain_timeout)
            for task in pending:
                task.cancel()
                log.warning("dispatcher.drain_timeout", task=task.get_name())
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        log.info("dispatcher.stopped")

    # ------------------------------------------------------------------ #
    # Queue consumption
    # ------------------------------------------------------------------ #

    async def _run_loop(self) -> None:
        """Poll the pending queue and dispatch claims up to max_concurrency."""

        while self._running:
            try:
                if len(self._in_flight) >= self._max_concurrency:
                    await asyncio.sleep(self._poll_interval)
                    continue
                task = await self._task_manager.take_next_pending()
                if task is None:
                    await asyncio.sleep(self._poll_interval)
                    continue
                self._spawn(task)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must survive bad tasks
                log.exception("dispatcher.poll_failed")
                await asyncio.sleep(self._poll_interval)

    def _spawn(self, task: Task) -> None:
        """Claim bookkeeping + background execution for one claimed task."""

        done = self._done_events.get(task.task_id)
        if done is None:
            done = asyncio.Event()
            self._done_events[task.task_id] = done
        done.clear()

        async def runner() -> None:
            try:
                await self._execute(task.task_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - execute_fn owns task status
                log.exception("dispatcher.execute_failed", task_id=task.task_id)
            finally:
                self._in_flight.pop(task.task_id, None)
                done.set()
                loop = asyncio.get_running_loop()
                loop.call_later(self._event_ttl, self._prune_event, task.task_id)

        self._in_flight[task.task_id] = asyncio.create_task(runner())
        log.info(
            "dispatcher.dispatched",
            task_id=task.task_id,
            priority=task.priority,
            in_flight=len(self._in_flight),
        )

    # ------------------------------------------------------------------ #
    # Waiting for results
    # ------------------------------------------------------------------ #

    async def wait(self, task_id: str, *, timeout: float | None = None) -> Task:
        """Wait until ``task_id`` completes and return its final state.

        Safe to call before the task is claimed: the completion event is
        registered immediately and resolved by whichever dispatcher run
        executes the task. Raises ``asyncio.TimeoutError`` on timeout and
        ``KeyError`` when the task never exists.
        """

        done = self._done_events.get(task_id)
        if done is None:
            # Already terminal (and its completion event long pruned)?
            task = await self._task_manager.get_task(task_id)
            if task is not None and task.status in _TERMINAL:
                return task
            done = asyncio.Event()
            self._done_events[task_id] = done
        await asyncio.wait_for(done.wait(), timeout)
        task = await self._task_manager.get_task(task_id)
        if task is None:
            raise KeyError(f"unknown task {task_id!r}")
        return task

    def _prune_event(self, task_id: str) -> None:
        """Drop a completion event once set and its task no longer in flight."""

        event = self._done_events.get(task_id)
        if event is not None and event.is_set() and task_id not in self._in_flight:
            self._done_events.pop(task_id, None)
