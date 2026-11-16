"""Orchestrator skill execution uses explicit blueprint pins and host grants."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agency.kernel.identity import Agent
from agency.orchestrator import AgencyOrchestrator
from agency.skills.executor import OperationRegistry
from agency.skills.registry import SkillSpec, SkillStatus
from agency.tools.base import ToolContext


def _published_skill(orch: AgencyOrchestrator, *, permissions: tuple[str, ...] = ()) -> None:
    assert orch.skill_registry is not None
    orch.skill_registry.register(
        SkillSpec(
            skill_id="text-count",
            version="1.2.3",
            name="Text count",
            description="Count provided text.",
            permissions=permissions,
            implementation="text.count",
            provenance="test fixture",
            status=SkillStatus.DRAFT,
        )
    )
    registry = orch.skill_registry
    registry.transition("text-count", "1.2.3", SkillStatus.TESTING)
    registry.transition("text-count", "1.2.3", SkillStatus.APPROVED, "review evidence")
    registry.publish("text-count", "1.2.3", "publication evidence")


def _pinned_agent(*, skill_refs: tuple[tuple[str, str], ...] = (("text-count", "1.2.3"),)) -> Agent:
    return Agent(
        id="factory-agent-1",
        name="Factory agent",
        metadata={
            "factory": "agent-factory-v1",
            "skill_refs": [list(ref) for ref in skill_refs],
        },
    )


def test_orchestrator_executes_only_explicitly_pinned_published_skill(tmp_path) -> None:
    operations = OperationRegistry()
    operations.register("text.count", lambda payload: {"count": len(payload["text"])})
    orch = AgencyOrchestrator(
        memory_db_path=":memory:",
        skill_db_path=str(tmp_path / "skills.db"),
        skill_operations=operations,
    )
    try:
        _published_skill(orch)
        agent = _pinned_agent()
        orch._identity_registry.register(agent)

        result = orch.execute_skill(agent.id, "text-count", "1.2.3", {"text": "hello"})

        assert result.success is True
        assert result.skill_id == "text-count"
        assert result.version == "1.2.3"
        assert result.output == {"count": 5}
    finally:
        if orch._skill_registry is not None:
            orch._skill_registry.close()


def test_orchestrator_refuses_unpinned_skill_without_running_operation(tmp_path) -> None:
    calls: list[dict[str, object]] = []
    operations = OperationRegistry()
    operations.register("text.count", lambda payload: calls.append(payload))
    orch = AgencyOrchestrator(
        memory_db_path=":memory:",
        skill_db_path=str(tmp_path / "skills.db"),
        skill_operations=operations,
    )
    try:
        _published_skill(orch)
        agent = _pinned_agent(skill_refs=())
        orch._identity_registry.register(agent)

        result = orch.execute_skill(agent.id, "text-count", "1.2.3", {"text": "x"})

        assert result.success is False
        assert result.error == "skill is not pinned to this agent"
        assert calls == []
    finally:
        if orch._skill_registry is not None:
            orch._skill_registry.close()


def test_orchestrator_uses_trusted_authorizer_for_skill_permissions(tmp_path) -> None:
    calls: list[dict[str, object]] = []
    operations = OperationRegistry()
    operations.register("text.count", lambda payload: calls.append(payload))
    orch = AgencyOrchestrator(
        config={
            "skill_authorizer": lambda actor, permission: False,
            "skill_allowed_permissions": {"text.read"},
        },
        memory_db_path=":memory:",
        skill_db_path=str(tmp_path / "skills.db"),
        skill_operations=operations,
    )
    try:
        _published_skill(orch, permissions=("text.read",))
        agent = _pinned_agent()
        orch._identity_registry.register(agent)

        result = orch.execute_skill(agent.id, "text-count", "1.2.3", {"text": "x"})

        assert result.success is False
        assert result.error == "permission denied"
        assert calls == []
    finally:
        if orch._skill_registry is not None:
            orch._skill_registry.close()


def test_orchestrator_fails_closed_without_skill_authorizer(tmp_path) -> None:
    calls: list[dict[str, object]] = []
    operations = OperationRegistry()
    operations.register("text.count", lambda payload: calls.append(payload))
    orch = AgencyOrchestrator(
        config={"skill_allowed_permissions": {"text.read"}},
        memory_db_path=":memory:",
        skill_db_path=str(tmp_path / "skills.db"),
        skill_operations=operations,
    )
    try:
        _published_skill(orch, permissions=("text.read",))
        agent = _pinned_agent()
        orch._identity_registry.register(agent)

        result = orch.execute_skill(agent.id, "text-count", "1.2.3", {"text": "x"})

        assert result.success is False
        assert result.error == "permission denied"
        assert calls == []
    finally:
        if orch._skill_registry is not None:
            orch._skill_registry.close()


@pytest.mark.asyncio
async def test_ordinary_agent_tool_loop_can_invoke_only_pinned_skill(tmp_path) -> None:
    calls: list[dict[str, object]] = []
    operations = OperationRegistry()
    operations.register("text.count", lambda payload: calls.append(payload) or {"count": 5})
    orch = AgencyOrchestrator(
        memory_db_path=":memory:",
        skill_db_path=str(tmp_path / "skills.db"),
        skill_operations=operations,
    )
    await orch.start()
    try:
        _published_skill(orch)
        agent = _pinned_agent()
        orch._identity_registry.register(agent)
        assert orch._tool_registry is not None

        result = await orch._tool_registry.call(
            "agency_skill",
            {"skill_id": "text-count", "version": "1.2.3", "inputs": {"text": "hello"}},
            ToolContext(agent_id=agent.id, task_id="task-1"),
        )

        assert result.ok is True
        assert result.output == {"count": 5}
        assert calls == [{"text": "hello"}]
    finally:
        await orch.stop()
        if orch._skill_registry is not None:
            orch._skill_registry.close()


@pytest.mark.asyncio
async def test_successful_skill_use_proposes_successor_without_mutating_published_version(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operations = OperationRegistry()
    operations.register("text.count", lambda payload: {"count": len(payload["text"])})
    orch = AgencyOrchestrator(
        memory_db_path=":memory:",
        skill_db_path=str(tmp_path / "skills.db"),
        skill_operations=operations,
    )
    await orch.start()
    try:
        _published_skill(orch)
        agent = _pinned_agent()
        orch._identity_registry.register(agent)
        assert orch._tool_registry is not None
        assert orch._tool_driver is not None

        async def run_tool(*, ctx, **_kwargs):
            outcome = await orch._tool_registry.call(
                "agency_skill",
                {
                    "skill_id": "text-count",
                    "version": "1.2.3",
                    "inputs": {"text": "hello"},
                },
                ctx,
            )
            step = SimpleNamespace(
                tool="agency_skill",
                args={
                    "skill_id": "text-count",
                    "version": "1.2.3",
                    "inputs": {"text": "hello"},
                },
                ok=outcome.ok,
                evidence=outcome.evidence,
            )
            return SimpleNamespace(
                status="completed",
                steps=[step],
                final_answer="Counted five characters.",
                llm_calls=1,
            )

        monkeypatch.setattr(orch._tool_driver, "run", AsyncMock(side_effect=run_tool))
        task = await orch.submit_task("Count text", "Count hello", agent.id)

        await orch.execute_task(task.task_id)

        assert orch.skill_registry is not None
        versions = orch.skill_registry.list_versions("text-count")
        assert [(spec.version, spec.status) for spec in versions] == [
            ("1.2.3", SkillStatus.PUBLISHED),
            ("1.2.4", SkillStatus.DRAFT),
        ]
        assert "Count hello" in versions[-1].description
    finally:
        await orch.stop()
        if orch.skill_registry is not None:
            orch.skill_registry.close()
