"""Agent-to-agent delegation: delegate_task + the delegate_task tool."""

from __future__ import annotations

import pytest

from agency.kernel.tasks import TaskStatus
from agency.orchestrator import AgencyOrchestrator


def _orchestrator() -> AgencyOrchestrator:
    return AgencyOrchestrator(
        config={"max_delegation_depth": 1},
        memory_db_path=":memory:",
        skill_db_path=":memory:",
    )


@pytest.mark.asyncio
async def test_delegate_task_executes_child_on_target_agent() -> None:
    orch = _orchestrator()
    await orch.start()
    await orch.start_dispatcher()
    try:
        parent_agent = await orch.register_agent("coordinator", "general", ["inspect"])
        worker = await orch.register_agent("worker", "research", ["inspect"])
        parent = await orch._task_manager.create_task(
            title="parent", created_by=parent_agent.id, input={"description": "parent work"}
        )
        child = await orch.delegate_task(
            parent.task_id,
            worker.id,
            title="child",
            description="Summarise the Agency architecture in one paragraph.",
        )
        assert child.status is TaskStatus.COMPLETED
        assert child.parent_id == parent.task_id
        assert child.created_by == worker.id
        assert child.output.get("summary")
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_delegate_task_rejects_unknown_target() -> None:
    orch = _orchestrator()
    await orch.start()
    try:
        agent = await orch.register_agent("coordinator", "general", ["inspect"])
        parent = await orch._task_manager.create_task(
            title="parent", created_by=agent.id, input={"description": "work"}
        )
        with pytest.raises(ValueError, match="unknown or revoked target agent"):
            await orch.delegate_task(parent.task_id, "ghost-agent", description="work")
        with pytest.raises(ValueError, match="unknown parent task"):
            await orch.delegate_task("no-such-parent", agent.id, description="work")
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_delegation_depth_guard() -> None:
    orch = _orchestrator()  # max_delegation_depth=1
    await orch.start()
    await orch.start_dispatcher()
    try:
        parent_agent = await orch.register_agent("coordinator", "general", ["inspect"])
        worker = await orch.register_agent("worker", "research", ["inspect"])
        parent = await orch._task_manager.create_task(
            title="parent", created_by=parent_agent.id, input={"description": "work"}
        )
        child = await orch.delegate_task(
            parent.task_id, worker.id, description="first hop"
        )
        with pytest.raises(ValueError, match="delegation depth limit"):
            await orch.delegate_task(child.task_id, parent_agent.id, description="second hop")
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_delegate_task_tool_registered_and_runs() -> None:
    orch = _orchestrator()
    await orch.start()
    try:
        specs = {spec.name: spec for spec in orch._tool_registry.list_specs()}
        assert "delegate_task" in specs
        ctx = orch._tool_context("system", "no-parent")
        result = await orch._tool_registry.call(
            "delegate_task",
            {"target_agent_id": "ghost", "task": "do something"},
            ctx,
        )
        assert result.ok is False
        assert "unknown or revoked target agent" in (result.error or "")
    finally:
        await orch.stop()
