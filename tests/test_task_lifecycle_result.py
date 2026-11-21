"""Delegated child-task results return to the submitter (task-lifecycle gap d).

When a child task (``parent_id`` set) reaches a terminal state, its result is
recorded on the parent's task record as a ``child_result`` message — the
submitter reads task status through the parent (``GET /v1/tasks/{id}`` or
``TaskManager.get_task``). The child's own ``output``/``error`` fields remain
the authoritative result; the parent record is the notification surface.
"""

from __future__ import annotations

import pytest

from agency.kernel.task_store import TaskStore
from agency.kernel.tasks import TaskManager, TaskStatus


@pytest.mark.asyncio
async def test_child_result_recorded_on_parent_on_completion(tmp_path) -> None:
    async with TaskStore(str(tmp_path / "tasks.db")) as store:
        manager = TaskManager(store)
        parent = await manager.create_task(title="parent", created_by="coord")
        child = await manager.create_task(
            title="child", created_by="worker", parent_id=parent.task_id
        )
        await manager.update_status(
            child.task_id, TaskStatus.COMPLETED, output={"summary": "42 widgets built"}
        )
        seen = await manager.get_task(parent.task_id)
        assert seen is not None
        results = [m for m in seen.messages if m.type == "child_result"]
        assert len(results) == 1
        assert results[0].created_by == "task_manager"
        assert results[0].content == {
            "child_task_id": child.task_id,
            "status": "completed",
            "summary": "42 widgets built",
            "error": None,
        }


@pytest.mark.asyncio
async def test_child_result_records_error_on_failure(tmp_path) -> None:
    async with TaskStore(str(tmp_path / "tasks.db")) as store:
        manager = TaskManager(store)
        parent = await manager.create_task(title="parent", created_by="coord")
        child = await manager.create_task(
            title="child", created_by="worker", parent_id=parent.task_id
        )
        await manager.update_status(child.task_id, TaskStatus.FAILED, error="provider unreachable")
        seen = await manager.get_task(parent.task_id)
        assert seen is not None
        results = [m for m in seen.messages if m.type == "child_result"]
        assert len(results) == 1
        assert results[0].content["status"] == "failed"
        assert results[0].content["error"] == "provider unreachable"


@pytest.mark.asyncio
async def test_blocked_child_notifies_parent_of_timeout(tmp_path) -> None:
    async with TaskStore(str(tmp_path / "tasks.db")) as store:
        manager = TaskManager(store)
        parent = await manager.create_task(title="parent", created_by="coord")
        child = await manager.create_task(
            title="child", created_by="worker", parent_id=parent.task_id
        )
        await manager.update_status(child.task_id, TaskStatus.BLOCKED, error="timed out")
        seen = await manager.get_task(parent.task_id)
        assert seen is not None
        results = [m for m in seen.messages if m.type == "child_result"]
        assert len(results) == 1
        assert results[0].content["status"] == "blocked"


@pytest.mark.asyncio
async def test_child_result_survives_restart(tmp_path) -> None:
    db = str(tmp_path / "tasks.db")
    async with TaskStore(db) as store:
        manager = TaskManager(store)
        parent = await manager.create_task(title="parent", created_by="coord")
        child = await manager.create_task(
            title="child", created_by="worker", parent_id=parent.task_id
        )
        await manager.update_status(child.task_id, TaskStatus.COMPLETED, output={"summary": "done"})
    async with TaskStore(db) as reopened:
        seen = await TaskManager(reopened).get_task(parent.task_id)
        assert seen is not None
        results = [m for m in seen.messages if m.type == "child_result"]
        assert len(results) == 1
        assert results[0].content["child_task_id"] == child.task_id


@pytest.mark.asyncio
async def test_tasks_without_parent_record_no_message(tmp_path) -> None:
    async with TaskStore(str(tmp_path / "tasks.db")) as store:
        manager = TaskManager(store)
        task = await manager.create_task(title="solo", created_by="u")
        await manager.update_status(task.task_id, TaskStatus.COMPLETED, output={"summary": "x"})
        seen = await manager.get_task(task.task_id)
        assert seen is not None
        assert [m.type for m in seen.messages] == []


@pytest.mark.asyncio
async def test_missing_parent_does_not_break_the_transition(tmp_path) -> None:
    async with TaskStore(str(tmp_path / "tasks.db")) as store:
        manager = TaskManager(store)
        # parent_id points at a task that does not exist (deleted/foreign).
        child = await manager.create_task(title="child", created_by="worker", parent_id="ghost")
        await manager.update_status(
            child.task_id, TaskStatus.COMPLETED, output={"summary": "still completes"}
        )
        seen = await manager.get_task(child.task_id)
        assert seen is not None
        assert seen.status is TaskStatus.COMPLETED
