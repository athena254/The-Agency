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


@pytest.mark.asyncio
async def test_dispatcher_renews_in_flight_leases(tmp_path) -> None:
    """The heartbeat loop pushes a running task's lease forward (gaps.md #19a)."""
    import sqlite3
    from datetime import datetime

    from agency.kernel.task_store import TaskStore

    db = str(tmp_path / "tasks.db")
    store = TaskStore(db, owner_id="proc-a", lease_seconds=60)
    await store.initialize()
    manager = TaskManager(store)
    samples: list[datetime] = []

    async def sampling_run(task_id: str) -> None:
        for _ in range(4):
            await asyncio.sleep(0.12)
            conn = sqlite3.connect(db)
            try:
                row = conn.execute(
                    "SELECT lease_expires_at FROM tasks WHERE task_id = ?", (task_id,)
                ).fetchone()
            finally:
                conn.close()
            assert row is not None and row[0] is not None
            samples.append(datetime.fromisoformat(row[0]))

    dispatcher = TaskDispatcher(manager, sampling_run, poll_interval=0.01, heartbeat_interval=0.05)
    task = await manager.create_task(title="t", created_by="u")
    dispatcher.start()
    try:
        await dispatcher.wait(task.task_id, timeout=15)
    finally:
        await dispatcher.stop()
    # Four samples over >=0.36 s with a 0.05 s heartbeat: renewals push the
    # expiry forward (an unrenewed lease would show the same claim-time
    # expiry in every sample).
    assert len(samples) == 4
    assert samples[-1] > samples[0]
    await store.close()


@pytest.mark.asyncio
async def test_wait_resolves_work_done_by_another_dispatcher(tmp_path) -> None:
    """A waiter sees tasks another process's dispatcher executed (shared store)."""
    from agency.kernel.task_store import TaskStore

    db = str(tmp_path / "tasks.db")
    store_a = TaskStore(db, owner_id="proc-a")
    await store_a.initialize()
    manager_a = TaskManager(store_a)
    store_b = TaskStore(db, owner_id="proc-b")
    await store_b.initialize()
    manager_b = TaskManager(store_b)

    async def complete(task_id: str) -> None:
        await manager_a.update_status(
            task_id, TaskStatus.COMPLETED, output={"summary": "done elsewhere"}
        )

    async def noop(task_id: str) -> None:
        return None

    task = await manager_a.create_task(title="cross", created_by="u")
    # Process B waits on its own (never-fired) event while process A's
    # dispatcher claims and completes the task in the shared store.
    waiter_dispatcher = TaskDispatcher(manager_b, noop, wait_poll_interval=0.05)
    waiter = asyncio.create_task(waiter_dispatcher.wait(task.task_id, timeout=15))
    await asyncio.sleep(0.05)
    runner = TaskDispatcher(manager_a, complete, poll_interval=0.01)
    runner.start()
    try:
        final = await waiter
        assert final.status is TaskStatus.COMPLETED
        assert final.output == {"summary": "done elsewhere"}
    finally:
        await runner.stop()
        await store_a.close()
        await store_b.close()


@pytest.mark.asyncio
async def test_cross_process_child_result_lands_on_parent(tmp_path) -> None:
    """Cross-process completion of a child records its result on the parent."""
    from agency.kernel.task_store import TaskStore

    db = str(tmp_path / "tasks.db")
    store_a = TaskStore(db, owner_id="proc-a")
    await store_a.initialize()
    manager_a = TaskManager(store_a)
    store_b = TaskStore(db, owner_id="proc-b")
    await store_b.initialize()
    manager_b = TaskManager(store_b)

    parent = await manager_a.create_task(title="parent", created_by="coord")
    child = await manager_a.create_task(
        title="child", created_by="worker", parent_id=parent.task_id
    )

    async def complete(task_id: str) -> None:
        await manager_b.update_status(task_id, TaskStatus.COMPLETED, output={"summary": "ok"})

    async def noop(task_id: str) -> None:
        return None

    runner = TaskDispatcher(manager_b, complete, poll_interval=0.01)
    runner.start()
    try:
        final = await TaskDispatcher(manager_a, noop, wait_poll_interval=0.05).wait(
            child.task_id, timeout=15
        )
        assert final.status is TaskStatus.COMPLETED
    finally:
        await runner.stop()
        await store_a.close()
        await store_b.close()
    # The result was recorded on the parent record even though process B
    # executed the child: the submitter reads it from the parent task.
    reopened = TaskStore(db, owner_id="proc-c")
    await reopened.initialize()
    try:
        seen = await TaskManager(reopened).get_task(parent.task_id)
        assert seen is not None
        results = [m for m in seen.messages if m.type == "child_result"]
        assert len(results) == 1
        assert results[0].content["child_task_id"] == child.task_id
        assert results[0].content["summary"] == "ok"
    finally:
        await reopened.close()
