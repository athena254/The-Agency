"""Bounded async worker pool for Agency factory tasks.

Composes the existing atomic-claim ``TaskDispatcher`` with explicit worker and
pending-task ceilings, so a burst of requests cannot spawn unbounded work.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any

from agency.dispatch import TaskDispatcher
from agency.kernel.tasks import Task, TaskManager, TaskStatus

MAX_WORKERS = 64
DEFAULT_WORKERS = 8
DEFAULT_MAX_PENDING = 1000


class FactoryQueueFull(Exception):
    """Raised when the pending-task ceiling is reached."""


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(low, min(high, value))


class FactoryWorkerPool:
    """Owns a dispatcher with bounded workers and bounded pending depth."""

    def __init__(
        self,
        task_manager: TaskManager,
        execute: Callable[[str], Awaitable[object]],
        *,
        max_workers: int | None = None,
        max_pending: int | None = None,
        poll_interval: float = 0.1,
    ) -> None:
        workers = (
            max_workers
            if max_workers is not None
            else _env_int("AGENCY_FORGE_MAX_WORKERS", DEFAULT_WORKERS, 1, MAX_WORKERS)
        )
        if not 1 <= workers <= MAX_WORKERS:
            raise ValueError(f"max_workers must be 1..{MAX_WORKERS}")
        pending = (
            max_pending
            if max_pending is not None
            else _env_int("AGENCY_FORGE_MAX_PENDING", DEFAULT_MAX_PENDING, 1, 100_000)
        )
        if pending < 1:
            raise ValueError("max_pending must be >= 1")
        self.max_workers = workers
        self.max_pending = pending
        self._manager = task_manager
        self._dispatcher = TaskDispatcher(
            task_manager, execute, max_concurrency=workers, poll_interval=poll_interval
        )

    @property
    def running(self) -> bool:
        return self._dispatcher.running

    @property
    def in_flight_count(self) -> int:
        return self._dispatcher.in_flight_count

    def start(self) -> None:
        self._dispatcher.start()

    async def stop(self, *, drain_timeout: float = 30.0) -> None:
        await self._dispatcher.stop(drain_timeout=drain_timeout)

    async def pending_count(self) -> int:
        tasks = await self._manager.list_tasks(status=TaskStatus.PENDING)
        return len(tasks)

    async def submit(
        self, *, created_by: str, title: str = "", input: dict[str, Any] | None = None
    ) -> Task:
        if await self.pending_count() >= self.max_pending:
            raise FactoryQueueFull(f"pending ceiling reached: {self.max_pending}")
        return await self._manager.create_task(created_by=created_by, title=title, input=input)

    async def wait(self, task_id: str, *, timeout: float | None = None) -> Task:
        return await self._dispatcher.wait(task_id, timeout=timeout)
