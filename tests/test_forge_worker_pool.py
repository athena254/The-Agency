"""Tests for the bounded Agency factory worker pool."""

from __future__ import annotations

import asyncio

import pytest

from agency.forge.worker_pool import DEFAULT_WORKERS, FactoryQueueFull, FactoryWorkerPool
from agency.kernel.tasks import TaskManager, TaskStatus


def test_defaults_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FactoryWorkerPool(TaskManager(), lambda _task_id: asyncio.sleep(0))
    assert pool.max_workers == DEFAULT_WORKERS
    assert pool.max_pending >= 1
    with pytest.raises(ValueError):
        FactoryWorkerPool(TaskManager(), lambda _task_id: asyncio.sleep(0), max_workers=999)
    monkeypatch.setenv("AGENCY_FORGE_MAX_WORKERS", "999")
    assert FactoryWorkerPool(TaskManager(), lambda _task_id: asyncio.sleep(0)).max_workers == 64


def test_submit_claims_and_completes() -> None:
    async def run() -> None:
        manager = TaskManager()
        seen: list[str] = []

        async def execute(task_id: str) -> None:
            seen.append(task_id)
            await manager.update_status(task_id, TaskStatus.COMPLETED)

        pool = FactoryWorkerPool(manager, execute, max_workers=2, poll_interval=0.01)
        task = await pool.submit(created_by="test", title="one")
        pool.start()
        done = await pool.wait(task.task_id, timeout=5)
        await pool.stop()
        assert done.status is TaskStatus.COMPLETED
        assert seen == [task.task_id]

    asyncio.run(run())


def test_pending_ceiling_rejects_extra_requests() -> None:
    async def run() -> None:
        manager = TaskManager()
        pool = FactoryWorkerPool(
            manager, lambda _task_id: asyncio.sleep(0), max_workers=1, max_pending=1
        )
        await pool.submit(created_by="test", title="one")
        assert await pool.pending_count() == 1
        with pytest.raises(FactoryQueueFull):
            await pool.submit(created_by="test", title="two")

    asyncio.run(run())
