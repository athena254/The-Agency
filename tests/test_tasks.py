"""Tests for agency.kernel.tasks."""

import pytest

from agency.kernel.identity import Agent
from agency.kernel.tasks import TaskManager, TaskMessage, TaskStatus


async def test_task_lifecycle():
    manager = TaskManager()
    task = await manager.create_task(title="scan", created_by="planner", input={"target": "x"})
    assert task.status is TaskStatus.PENDING
    assert await manager.get_task(task.task_id) is not None

    fetched = await manager.update_status(task.task_id, TaskStatus.RUNNING)
    assert fetched.status is TaskStatus.RUNNING

    await manager.update_status(task.task_id, TaskStatus.COMPLETED)
    completed = await manager.get_task(task.task_id)
    assert completed and completed.status is TaskStatus.COMPLETED


async def test_illegal_transition():
    manager = TaskManager()
    task = await manager.create_task(created_by="me")
    await manager.update_status(task.task_id, TaskStatus.COMPLETED)
    with pytest.raises(ValueError):
        await manager.update_status(task.task_id, TaskStatus.RUNNING)


async def test_unknown_task():
    manager = TaskManager()
    assert await manager.get_task("nope") is None
    with pytest.raises(KeyError):
        await manager.update_status("nope", TaskStatus.FAILED)


async def test_list_tasks_filter():
    manager = TaskManager()
    await manager.create_task(created_by="a")
    await manager.create_task(created_by="b")
    tasks = await manager.list_tasks()
    assert len(tasks) == 2
    blocked = await manager.list_tasks(TaskStatus.BLOCKED)
    assert blocked == []


async def test_duplicate_task_id_rejected():
    manager = TaskManager()
    await manager.create_task(created_by="a", task_id="t1")
    with pytest.raises(ValueError):
        await manager.create_task(created_by="b", task_id="t1")


async def test_messages_are_exchanged():
    manager = TaskManager()
    task = await manager.create_task(created_by="planner")
    msg = TaskMessage(
        task_id="", type="hypothesis", content={"risk": "high"}, created_by="threat-modeler"
    )
    bound = await manager.add_message(task.task_id, msg)
    assert bound.task_id == task.task_id

    with pytest.raises(ValueError):
        await manager.add_message(
            task.task_id,
            TaskMessage(task_id="other-task", type="finding", created_by="x"),
        )


async def test_messages_are_immutable():
    manager = TaskManager()
    task = await manager.create_task(created_by="planner")
    msg = TaskMessage(task_id=task.task_id, type="command", created_by="planner")
    with pytest.raises(AttributeError):
        msg.content = "mutated"  # type: ignore[misc]


async def test_priority_scheduling():
    manager = TaskManager()
    low = await manager.create_task(created_by="a", priority=1, task_id="low")
    high = await manager.create_task(created_by="a", priority=10, task_id="high")
    mid = await manager.create_task(created_by="a", priority=5, task_id="mid")

    first = await manager.take_next_pending()
    assert first and first.task_id == "high"

    second = await manager.take_next_pending()
    assert second and second.task_id == "mid"

    third = await manager.take_next_pending()
    assert third and third.task_id == "low"

    assert await manager.take_next_pending() is None
    await manager.update_status(low.task_id, TaskStatus.COMPLETED)
    await manager.update_status(high.task_id, TaskStatus.COMPLETED)
    await manager.update_status(mid.task_id, TaskStatus.COMPLETED)


async def test_blocked_then_re_pending():
    manager = TaskManager()
    task = await manager.create_task(created_by="a")
    await manager.update_status(task.task_id, TaskStatus.BLOCKED)
    assert await manager.take_next_pending() is None
    await manager.update_status(task.task_id, TaskStatus.PENDING)
    assert await manager.take_next_pending() is not None


async def test_failed_carries_error():
    manager = TaskManager()
    task = await manager.create_task(created_by="a")
    failed = await manager.update_status(task.task_id, TaskStatus.FAILED, error="boom")
    assert failed.error == "boom"
    assert failed.status is TaskStatus.FAILED


async def test_agent_referenced_by_id_only():
    agent = Agent(name="planner", id="planner-1")
    manager = TaskManager()
    task = await manager.create_task(created_by=agent.id)
    assert task.created_by == "planner-1"
