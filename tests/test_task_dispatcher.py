"""TaskDispatcher: bounded concurrency, priority order, wait(), graceful drain."""

from __future__ import annotations

import asyncio

import pytest

from agency.dispatch import TaskDispatcher
from agency.kernel.tasks import TaskManager, TaskStatus


class ConcurrencyProbe:
    """Fake execute callable recording peak concurrency and completion order."""

    def __init__(self, delay: float = 0.05) -> None:
        self._delay = delay
        self.current = 0
        self.peak = 0
        self.completed: list[str] = []

    async def __call__(self, task_id: str) -> None:
        self.current += 1
        self.peak = max(self.peak, self.current)
        await asyncio.sleep(self._delay)
        self.current -= 1
        self.completed.append(task_id)


@pytest.mark.asyncio
async def test_bounded_concurrency_and_drain() -> None:
    manager = TaskManager()
    probe = ConcurrencyProbe()
    dispatcher = TaskDispatcher(manager, probe, max_concurrency=2, poll_interval=0.01)
    dispatcher.start()
    tasks = [await manager.create_task(title=f"t{i}", created_by="u") for i in range(5)]
    try:
        for task in tasks:
            await dispatcher.wait(task.task_id, timeout=10)
        assert sorted(probe.completed) == sorted(t.task_id for t in tasks)
        assert probe.peak <= 2
    finally:
        await dispatcher.stop()
    assert dispatcher.in_flight_count == 0


@pytest.mark.asyncio
async def test_priority_order_with_serial_pool() -> None:
    manager = TaskManager()
    probe = ConcurrencyProbe(delay=0.02)
    dispatcher = TaskDispatcher(manager, probe, max_concurrency=1, poll_interval=0.01)
    dispatcher.start()
    low = await manager.create_task(title="low", created_by="u", priority=1)
    high = await manager.create_task(title="high", created_by="u", priority=10)
    mid = await manager.create_task(title="mid", created_by="u", priority=5)
    for task in (low, mid, high):
        await dispatcher.wait(task.task_id, timeout=10)
    try:
        assert probe.completed == [high.task_id, mid.task_id, low.task_id]
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_wait_before_claim_and_unknown_task_timeout() -> None:
    manager = TaskManager()
    started = asyncio.Event()

    async def slow_execute(task_id: str) -> None:
        started.set()
        await asyncio.sleep(0.15)

    dispatcher = TaskDispatcher(manager, slow_execute, max_concurrency=2, poll_interval=0.01)
    dispatcher.start()
    try:
        task = await manager.create_task(title="slow", created_by="u")
        waiter = asyncio.create_task(dispatcher.wait(task.task_id, timeout=10))
        await started.wait()
        # wait() registered before/while the dispatcher owns the task still
        # resolves when the run finishes — the bare callable leaves it RUNNING.
        final = await waiter
        assert final.status is TaskStatus.RUNNING
        assert final.task_id == task.task_id
        with pytest.raises(TimeoutError):
            await dispatcher.wait("no-such-task", timeout=0.05)
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_wait_returns_terminal_state_after_prune_window_is_irrelevant() -> None:
    """wait() on an already-terminal task resolves without a completion event."""
    manager = TaskManager()
    probe = ConcurrencyProbe(delay=0.01)
    dispatcher = TaskDispatcher(manager, probe, max_concurrency=1, poll_interval=0.01)
    task = await manager.create_task(title="quick", created_by="u")
    dispatcher.start()
    try:
        await dispatcher.wait(task.task_id, timeout=5)
    finally:
        await dispatcher.stop()
    # The bare probe leaves the status RUNNING (execute_fn owns status);
    # mark terminal, prune the event, then wait must fall back to the store.
    await manager.update_status(task.task_id, TaskStatus.COMPLETED)
    short_ttl = TaskDispatcher(manager, probe, max_concurrency=1, event_ttl=0.05)
    final = await short_ttl.wait(task.task_id, timeout=5)
    assert final.status is TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_execute_failure_is_contained() -> None:
    manager = TaskManager()

    async def failing(task_id: str) -> None:
        raise RuntimeError("boom")

    dispatcher = TaskDispatcher(manager, failing, max_concurrency=1, poll_interval=0.01)
    dispatcher.start()
    try:
        task = await manager.create_task(title="bad", created_by="u")
        final = await dispatcher.wait(task.task_id, timeout=5)
        # execute_fn owns status; the dispatcher loop survives the failure.
        assert final.status is TaskStatus.RUNNING  # marked at claim, error not fatal to loop
        nxt = await manager.create_task(title="next", created_by="u")
        probe_task = await dispatcher.wait(nxt.task_id, timeout=5)
        assert probe_task.task_id == nxt.task_id
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_invalid_concurrency_rejected() -> None:
    manager = TaskManager()
    with pytest.raises(ValueError, match="max_concurrency"):

        async def noop(task_id: str) -> None:
            return None

        TaskDispatcher(manager, noop, max_concurrency=0)
