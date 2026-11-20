"""TaskStore: SQLite persistence, atomic claims, and restart requeue."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from agency.kernel.task_store import TaskStore
from agency.kernel.tasks import TaskManager, TaskMessage, TaskStatus


@pytest.mark.asyncio
async def test_roundtrip_and_messages(tmp_path) -> None:
    async with TaskStore(str(tmp_path / "tasks.db")) as store:
        manager = TaskManager(store)
        task = await manager.create_task(
            title="Ship it", created_by="agent-1", input={"description": "do the thing"}, priority=3
        )
        await manager.add_message(task.task_id, TaskMessage(task_id="", type="note", content="hi"))

        loaded = await manager.get_task(task.task_id)
        assert loaded is not None
        assert loaded.title == "Ship it"
        assert loaded.status is TaskStatus.PENDING
        assert loaded.priority == 3
        assert [m.content for m in loaded.messages] == ["hi"]


@pytest.mark.asyncio
async def test_status_and_output_persist(tmp_path) -> None:
    db = str(tmp_path / "tasks.db")
    async with TaskStore(db) as store:
        manager = TaskManager(store)
        task = await manager.create_task(title="t", created_by="u")
        await manager.update_status(
            task.task_id,
            TaskStatus.COMPLETED,
            output={"summary": "all done"},
        )
    # A brand-new manager over the same file sees the terminal state + payload.
    async with TaskStore(db) as fresh_store:
        fresh = TaskManager(fresh_store)
        seen = await fresh.get_task(task.task_id)
        assert seen is not None
        assert seen.status is TaskStatus.COMPLETED
        assert seen.output == {"summary": "all done"}


@pytest.mark.asyncio
async def test_list_filters(tmp_path) -> None:
    async with TaskStore(str(tmp_path / "tasks.db")) as store:
        manager = TaskManager(store)
        mine = await manager.create_task(title="mine", created_by="agent-1")
        await manager.create_task(title="theirs", created_by="agent-2")
        listed = await manager.list_tasks(created_by="agent-1")
        assert [t.task_id for t in listed] == [mine.task_id]
        pending = await manager.list_tasks(status=TaskStatus.PENDING)
        assert len(pending) == 2


@pytest.mark.asyncio
async def test_duplicate_task_id_rejected(tmp_path) -> None:
    async with TaskStore(str(tmp_path / "tasks.db")) as store:
        manager = TaskManager(store)
        await manager.create_task(task_id="dup", title="first", created_by="u")
        with pytest.raises(ValueError, match="already exists"):
            await manager.create_task(task_id="dup", title="second", created_by="u")


@pytest.mark.asyncio
async def test_claim_is_priority_ordered_and_marks_running(tmp_path) -> None:
    async with TaskStore(str(tmp_path / "tasks.db")) as store:
        manager = TaskManager(store)
        low = await manager.create_task(title="low", created_by="u", priority=1)
        high = await manager.create_task(title="high", created_by="u", priority=10)
        mid = await manager.create_task(title="mid", created_by="u", priority=5)

        claimed = []
        while (task := await manager.take_next_pending()) is not None:
            claimed.append(task.task_id)
        assert claimed == [high.task_id, mid.task_id, low.task_id]
        for task_id in claimed:
            seen = await manager.get_task(task_id)
            assert seen is not None and seen.status is TaskStatus.RUNNING
        assert await manager.take_next_pending() is None


@pytest.mark.asyncio
async def test_claim_across_managers_sharing_one_file(tmp_path) -> None:
    """Two managers on one DB share the queue; claims never hand out twice."""
    db = str(tmp_path / "tasks.db")
    async with TaskStore(db) as store_a, TaskStore(db) as store_b:
        first = TaskManager(store_a)
        second = TaskManager(store_b)
        task = await first.create_task(title="shared", created_by="api")
        claimed = await second.take_next_pending()
        assert claimed is not None and claimed.task_id == task.task_id
        # Already RUNNING -> not claimable again.
        assert await first.take_next_pending() is None


@pytest.mark.asyncio
async def test_requeue_running_appends_note(tmp_path) -> None:
    async with TaskStore(str(tmp_path / "tasks.db")) as store:
        manager = TaskManager(store)
        task = await manager.create_task(title="crashed", created_by="u")
        await manager.update_status(task.task_id, TaskStatus.RUNNING)
        requeued = await store.requeue_running(note="requeued after restart")
        assert requeued == 1
        seen = await manager.get_task(task.task_id)
        assert seen is not None and seen.status is TaskStatus.PENDING
        assert any(m.content == "requeued after restart" for m in seen.messages)
        assert await store.requeue_running(note="again") == 0


@pytest.mark.asyncio
async def test_timestamps_roundtrip_timezone_aware(tmp_path) -> None:
    async with TaskStore(str(tmp_path / "tasks.db")) as store:
        manager = TaskManager(store)
        task = await manager.create_task(title="tz", created_by="u")
        seen = await manager.get_task(task.task_id)
        assert seen is not None
        assert seen.created_at.tzinfo is not None
        assert abs(seen.created_at - task.created_at) < timedelta(seconds=1)
        assert isinstance(seen.created_at, datetime)
