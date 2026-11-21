"""Status updates are compare-and-set across processes (gaps.md #19 tail).

Two stores (simulated processes) share one SQLite file. A status write only
applies when the stored status still matches the writer's expectation;
terminal rows (COMPLETED/FAILED/BLOCKED) are immutable — a late writer
observes the stored terminal record and yields — and a stale write fails
closed instead of overwriting blind. Leases/heartbeats are covered by
``tests/test_task_lease.py`` and must keep passing unchanged.
"""

from __future__ import annotations

from typing import Any

import pytest

from agency.kernel.task_store import TaskStore
from agency.kernel.tasks import Task, TaskManager, TaskStatus, TaskWriteResult


def _patch_first_read_stale(
    manager: TaskManager, stale: Task, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Make the manager's first status read return a stale snapshot.

    Simulates the read-before-write race window between two processes: the
    manager read its status, another process changed the record, only then
    does the manager's write arrive. Later reads delegate to the real store.
    """
    real_get = manager.get_task
    state = {"used": False}

    async def stale_then_real(task_id: str, **kwargs: Any) -> Task | None:
        if not state["used"]:
            state["used"] = True
            return stale
        return await real_get(task_id, **kwargs)

    monkeypatch.setattr(manager, "get_task", stale_then_real)


@pytest.mark.asyncio
async def test_cas_applies_when_status_matches(tmp_path) -> None:
    """Happy path: claim then complete under CAS across two stores."""
    db = str(tmp_path / "tasks.db")
    async with (
        TaskStore(db, owner_id="proc-a") as store_a,
        TaskStore(db, owner_id="proc-b") as store_b,
    ):
        manager_a = TaskManager(store_a)
        task = await manager_a.create_task(title="shared", created_by="api")
        claimed = await TaskManager(store_b).take_next_pending()
        assert claimed is not None and claimed.status is TaskStatus.RUNNING

        # Store-level CAS: the expected status matches, so the write takes.
        outcome = await store_b.update_task(
            task.task_id,
            status=TaskStatus.COMPLETED,
            output={"summary": "done"},
            expected_status=TaskStatus.RUNNING,
        )
        assert outcome is TaskWriteResult.APPLIED
        seen = await manager_a.get_task(task.task_id)
        assert seen is not None
        assert seen.status is TaskStatus.COMPLETED
        assert seen.output == {"summary": "done"}


@pytest.mark.asyncio
async def test_manager_update_status_end_to_end_across_processes(tmp_path) -> None:
    db = str(tmp_path / "tasks.db")
    async with (
        TaskStore(db, owner_id="proc-a") as store_a,
        TaskStore(db, owner_id="proc-b") as store_b,
    ):
        manager_a = TaskManager(store_a)
        task = await manager_a.create_task(title="shared", created_by="api")
        manager_b = TaskManager(store_b)
        await manager_b.take_next_pending()
        completed = await manager_b.update_status(
            task.task_id, TaskStatus.COMPLETED, output={"summary": "ok"}
        )
        assert completed.status is TaskStatus.COMPLETED
        # The other process observes the terminal record through the store.
        seen = await manager_a.get_task(task.task_id)
        assert seen is not None and seen.status is TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_terminal_record_immutable_under_second_writer(tmp_path) -> None:
    """A late writer cannot overwrite a terminal record; it must yield."""
    db = str(tmp_path / "tasks.db")
    async with (
        TaskStore(db, owner_id="proc-a") as store_a,
        TaskStore(db, owner_id="proc-b") as store_b,
    ):
        manager_a = TaskManager(store_a)
        task = await manager_a.create_task(title="shared", created_by="api")
        await TaskManager(store_b).take_next_pending()
        assert (
            await store_b.update_task(
                task.task_id,
                status=TaskStatus.COMPLETED,
                output={"summary": "winner"},
                expected_status=TaskStatus.RUNNING,
            )
            is TaskWriteResult.APPLIED
        )

        # Stale late writer (still believes RUNNING) tries FAILED: yield.
        assert (
            await store_a.update_task(
                task.task_id, status=TaskStatus.FAILED, expected_status=TaskStatus.RUNNING
            )
            is TaskWriteResult.YIELDED_TERMINAL
        )
        # Even a CAS naming the terminal status is a write to an immutable
        # record and is refused.
        assert (
            await store_a.update_task(
                task.task_id,
                status=TaskStatus.FAILED,
                error="late",
                expected_status=TaskStatus.COMPLETED,
            )
            is TaskWriteResult.YIELDED_TERMINAL
        )
        # An unguarded metadata write cannot touch a terminal row either.
        assert (
            await store_a.update_task(task.task_id, output={"summary": "loser"})
            is TaskWriteResult.YIELDED_TERMINAL
        )

        seen = await manager_a.get_task(task.task_id)
        assert seen is not None
        assert seen.status is TaskStatus.COMPLETED
        assert seen.output == {"summary": "winner"}
        assert seen.error is None


@pytest.mark.asyncio
async def test_stale_write_fails_closed_not_overwrites(tmp_path) -> None:
    """A zombie writer's terminal write is refused while the row moved on."""
    db = str(tmp_path / "tasks.db")
    async with (
        TaskStore(db, owner_id="proc-a") as store_a,
        TaskStore(db, owner_id="proc-b") as store_b,
    ):
        manager_a = TaskManager(store_a)
        task = await manager_a.create_task(title="shared", created_by="api")
        await manager_a.take_next_pending()  # proc-a claims, then "hangs"

        # proc-b requeues the claim (recovery): the row is PENDING again.
        assert await store_b.requeue_running(note="recovered", expired_only=False) == 1

        # proc-a's zombie write still believes RUNNING: refused, not applied.
        assert (
            await store_a.update_task(
                task.task_id,
                status=TaskStatus.COMPLETED,
                output={"summary": "zombie"},
                expected_status=TaskStatus.RUNNING,
            )
            is TaskWriteResult.STALE
        )
        seen = await manager_a.get_task(task.task_id)
        assert seen is not None
        assert seen.status is TaskStatus.PENDING
        assert seen.output == {}

        # proc-b re-claims and completes; the legitimate record stands.
        await TaskManager(store_b).take_next_pending()
        await TaskManager(store_b).update_status(
            task.task_id, TaskStatus.COMPLETED, output={"summary": "ok"}
        )
        final = await manager_a.get_task(task.task_id)
        assert final is not None and final.status is TaskStatus.COMPLETED
        assert final.output == {"summary": "ok"}


@pytest.mark.asyncio
async def test_manager_yields_to_terminal_record_without_raising(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manager-level late writer: logged yield, terminal record returned."""
    db = str(tmp_path / "tasks.db")
    async with (
        TaskStore(db, owner_id="proc-a") as store_a,
        TaskStore(db, owner_id="proc-b") as store_b,
    ):
        manager_a = TaskManager(store_a)
        task = await manager_a.create_task(title="shared", created_by="api")
        await manager_a.take_next_pending()
        stale_snapshot = await manager_a.get_task(task.task_id)
        assert stale_snapshot is not None

        # proc-b terminalizes the task after proc-a's read.
        await TaskManager(store_b).update_status(
            task.task_id, TaskStatus.COMPLETED, output={"summary": "winner"}
        )

        _patch_first_read_stale(manager_a, stale_snapshot, monkeypatch)
        # Must not raise: the late writer observes the terminal record.
        yielded = await manager_a.update_status(
            task.task_id, TaskStatus.FAILED, error="late writer"
        )
        assert yielded.status is TaskStatus.COMPLETED
        assert yielded.output == {"summary": "winner"}
        assert yielded.error is None
        # The record was not overwritten by the late writer.
        seen = await manager_a.get_task(task.task_id)
        assert seen is not None and seen.status is TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_stale_manager_write_fails_closed(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "tasks.db")
    async with (
        TaskStore(db, owner_id="proc-a") as store_a,
        TaskStore(db, owner_id="proc-b") as store_b,
    ):
        manager_a = TaskManager(store_a)
        task = await manager_a.create_task(title="shared", created_by="api")
        await manager_a.take_next_pending()
        stale_snapshot = await manager_a.get_task(task.task_id)
        assert stale_snapshot is not None

        # proc-b requeues the task (PENDING) after proc-a's read.
        await store_b.requeue_running(note="recovered", expired_only=False)

        _patch_first_read_stale(manager_a, stale_snapshot, monkeypatch)
        with pytest.raises(ValueError, match="stale task write"):
            await manager_a.update_status(
                task.task_id, TaskStatus.COMPLETED, output={"summary": "zombie"}
            )
        seen = await manager_a.get_task(task.task_id)
        assert seen is not None and seen.status is TaskStatus.PENDING


@pytest.mark.asyncio
async def test_yielded_terminal_write_records_no_duplicate_parent_result(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A late writer yields without duplicating the parent notification."""
    db = str(tmp_path / "tasks.db")
    async with (
        TaskStore(db, owner_id="proc-a") as store_a,
        TaskStore(db, owner_id="proc-b") as store_b,
    ):
        manager_a = TaskManager(store_a)
        parent = await manager_a.create_task(title="parent", created_by="coord")
        child = await manager_a.create_task(
            title="child", created_by="worker", parent_id=parent.task_id
        )
        await manager_a.take_next_pending()
        stale_snapshot = await manager_a.get_task(child.task_id)
        assert stale_snapshot is not None

        # proc-b executes and terminalizes the child (notifies the parent).
        manager_b = TaskManager(store_b)
        await manager_b.update_status(
            child.task_id, TaskStatus.COMPLETED, output={"summary": "winner"}
        )

        # proc-a's late FAILED write must yield, not duplicate the notify.
        _patch_first_read_stale(manager_a, stale_snapshot, monkeypatch)
        yielded = await manager_a.update_status(child.task_id, TaskStatus.FAILED)
        assert yielded.status is TaskStatus.COMPLETED

        seen = await manager_a.get_task(parent.task_id)
        assert seen is not None
        results = [m for m in seen.messages if m.type == "child_result"]
        assert len(results) == 1
        assert results[0].content["status"] == "completed"


@pytest.mark.asyncio
async def test_illegal_transition_fails_closed_store_backed(tmp_path) -> None:
    db = str(tmp_path / "tasks.db")
    async with TaskStore(db) as store:
        manager = TaskManager(store)
        task = await manager.create_task(title="t", created_by="u")
        await manager.update_status(task.task_id, TaskStatus.COMPLETED, output={"summary": "x"})
        with pytest.raises(ValueError, match="illegal task transition"):
            await manager.update_status(task.task_id, TaskStatus.RUNNING)
        with pytest.raises(ValueError, match="illegal task transition"):
            await manager.update_status(task.task_id, TaskStatus.FAILED)
        seen = await manager.get_task(task.task_id)
        assert seen is not None
        assert seen.status is TaskStatus.COMPLETED
        assert seen.output == {"summary": "x"}


@pytest.mark.asyncio
async def test_blocked_to_pending_requeue_survives_cas(tmp_path) -> None:
    """Legal non-obvious transitions (BLOCKED -> PENDING) still apply."""
    db = str(tmp_path / "tasks.db")
    async with TaskStore(db) as store:
        manager = TaskManager(store)
        task = await manager.create_task(title="t", created_by="u")
        await manager.update_status(task.task_id, TaskStatus.BLOCKED, error="waiting")
        reopened = await manager.update_status(task.task_id, TaskStatus.PENDING)
        assert reopened.status is TaskStatus.PENDING
        claimed = await manager.take_next_pending()
        assert claimed is not None and claimed.task_id == task.task_id


@pytest.mark.asyncio
async def test_missing_task_write_is_missing(tmp_path) -> None:
    async with TaskStore(str(tmp_path / "tasks.db")) as store:
        assert await store.update_task("nope", status=TaskStatus.FAILED) is TaskWriteResult.MISSING
        manager = TaskManager(store)
        with pytest.raises(KeyError):
            await manager.update_status("nope", TaskStatus.FAILED)


@pytest.mark.asyncio
async def test_lease_cleared_by_cas_terminal_write(tmp_path) -> None:
    """The CAS terminal write still clears the lease (wave F intact)."""
    db = str(tmp_path / "tasks.db")
    async with TaskStore(db, owner_id="proc-a", lease_seconds=60) as store:
        manager = TaskManager(store)
        task = await manager.create_task(title="t", created_by="u")
        await manager.take_next_pending()
        assert await manager.heartbeat_many([task.task_id]) == 1
        await manager.update_status(task.task_id, TaskStatus.COMPLETED)
        assert await manager.heartbeat_many([task.task_id]) == 0
        seen = await manager.get_task(task.task_id)
        assert seen is not None and seen.status is TaskStatus.COMPLETED
