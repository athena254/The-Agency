"""End-to-end: request an agent, run a task, and verify self-improvement wiring."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agency.agents.executor import ExecutionResult, ExecutionStatus
from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.factory.prompts import PromptRegistry, PromptStatus
from agency.factory.prompts_builtin import ensure_builtin_prompts
from agency.factory.requested_agent import RequestedAgentRequest
from agency.factory.service import AgentFactory, BlueprintStatus
from agency.kernel.registry import AgentRegistry as KernelRegistry
from agency.orchestrator import AgencyOrchestrator
from agency.skills.registry import SkillSpec, SkillStatus


@pytest.mark.asyncio
async def test_orchestrator_request_agent_then_task_improves_it(tmp_path) -> None:
    prompts = PromptRegistry(str(tmp_path / "prompts.db"))
    ensure_builtin_prompts(prompts)
    kernels = KernelRegistry()
    factory = AgentFactory(
        str(tmp_path / "factory.db"),
        authorizer=lambda actor, perm: perm == "agent.approve",
        allowed_capabilities={"search"},
        kernel_registry=kernels,
        runtime_registry=RuntimeRegistry(),
        is_prompt_published=prompts.is_published,
        prompt_resolver=prompts,
        is_skill_published=lambda sid, ver: True,
    )
    orch = AgencyOrchestrator(
        config={
            "agent_factory": factory,
            "prompt_registry": prompts,
            # Human gate for self-modification (gaps.md #2): the hook may only
            # re-version when this actor is authorized for agent.self_improve.
            "factory_self_improve_authorizer": lambda actor, perm: (
                perm == "agent.self_improve" and actor == "human-lead"
            ),
            "factory_self_improve_approver": "human-lead",
        },
        memory_db_path=":memory:",
        skill_db_path=str(tmp_path / "skills.db"),
    )
    await orch.start()
    try:
        service = orch.requested_agent_service
        assert service is not None
        registry = orch.skill_registry
        assert registry is not None

        registry.register(
            SkillSpec(
                skill_id="search-docs",
                version="1.0.0",
                name="Search docs",
                description="Search documents by keyword.",
                implementation="search.docs",
                provenance="test",
                status=SkillStatus.DRAFT,
            )
        )
        registry.transition("search-docs", "1.0.0", SkillStatus.TESTING)
        registry.transition("search-docs", "1.0.0", SkillStatus.APPROVED, "review")
        registry.publish("search-docs", "1.0.0", "review")

        result = service.request_agent(
            RequestedAgentRequest(
                blueprint_id="scout",
                name="Scout",
                purpose="Search documents by keyword.",
                creator="owner",
                domain="research",
                capabilities=("search",),
            )
        )
        assert result.attached_skills == (("search-docs", "1.0.0"),)
        agent = kernels.get_required(result.agent_id)
        orch._identity_registry.register(agent)
        assert orch._tool_driver is not None

        async def run_tool(*, ctx, **_kwargs):
            step = SimpleNamespace(
                tool="agency_skill",
                args={},
                ok=False,
                evidence={"skill_id": "search-docs", "version": "1.0.0"},
            )
            return SimpleNamespace(
                status="completed",
                steps=[step],
                final_answer="No matching documents found.",
                llm_calls=1,
            )

        orch._tool_driver.run = AsyncMock(side_effect=run_tool)
        task = await orch.submit_task("Find docs", "Search the docs", agent.id)
        execution = await orch.execute_task(task.task_id)
        assert execution.status.value == "completed"

        improved = factory.get("scout", "1.0.1")
        assert improved.status is BlueprintStatus.ACTIVE
        assert improved.approver == "human-lead"
        assert factory.get("scout", "1.0.0").status is BlueprintStatus.REVOKED
        assert prompts.get("purpose-scout", "1.0.1").status is PromptStatus.PUBLISHED
        versions = registry.list_versions("search-docs")
        assert any(
            item.version == "1.0.1" and item.status is SkillStatus.DRAFT for item in versions
        )
    finally:
        await orch.stop()
        if orch.skill_registry is not None:
            orch.skill_registry.close()
        prompts.close()
        factory.close()


def _wired_orchestrator(tmp_path, *, human_gate: bool):
    """Factory + orchestrator harness shared by the classic-path tests."""
    prompts = PromptRegistry(str(tmp_path / "prompts.db"))
    ensure_builtin_prompts(prompts)
    kernels = KernelRegistry()
    factory = AgentFactory(
        str(tmp_path / "factory.db"),
        authorizer=lambda actor, perm: perm == "agent.approve",
        allowed_capabilities={"search"},
        kernel_registry=kernels,
        runtime_registry=RuntimeRegistry(),
        is_prompt_published=prompts.is_published,
        prompt_resolver=prompts,
        is_skill_published=lambda sid, ver: True,
    )
    config: dict[str, object] = {"agent_factory": factory, "prompt_registry": prompts}
    if human_gate:
        config["factory_self_improve_authorizer"] = lambda actor, perm: (
            perm == "agent.self_improve" and actor == "human-lead"
        )
        config["factory_self_improve_approver"] = "human-lead"
    orch = AgencyOrchestrator(
        config=config,
        memory_db_path=":memory:",
        skill_db_path=str(tmp_path / "skills.db"),
    )
    return orch, factory, prompts, kernels


@pytest.mark.asyncio
async def test_classic_task_refines_prompt_from_assessment_evidence(tmp_path) -> None:
    """Gap 7: classic (non-tool) tasks also reach prompt refinement."""
    orch, factory, prompts, kernels = _wired_orchestrator(tmp_path, human_gate=True)
    await orch.start()
    try:
        service = orch.requested_agent_service
        assert service is not None
        result = service.request_agent(
            RequestedAgentRequest(
                blueprint_id="scout",
                name="Scout",
                purpose="Search documents by keyword.",
                creator="owner",
                # Not in TOOL_CAPABLE_DOMAINS: exercises the classic path where
                # there are no skill steps to derive gaps from.
                domain="coding",
                capabilities=("search",),
            )
        )
        agent = kernels.get_required(result.agent_id)
        orch._identity_registry.register(agent)

        async def execute(**kwargs):
            return ExecutionResult(
                task_id=kwargs["task_id"], status=ExecutionStatus.COMPLETED, output="ok"
            )

        orch._executor.execute = AsyncMock(side_effect=execute)
        task = await orch.submit_task("Find docs", "Search the docs", agent.id)
        execution = await orch.execute_task(task.task_id)
        assert execution.status.value == "completed"

        improved = factory.get("scout", "1.0.1")
        assert improved.status is BlueprintStatus.ACTIVE
        assert improved.approver == "human-lead"
        assert factory.get("scout", "1.0.0").status is BlueprintStatus.REVOKED
        refined = prompts.get("purpose-scout", "1.0.1")
        assert refined.status is PromptStatus.PUBLISHED
        assert "check failed" in refined.body
        assert "short output" in refined.body
    finally:
        await orch.stop()
        if orch.skill_registry is not None:
            orch.skill_registry.close()
        prompts.close()
        factory.close()


@pytest.mark.asyncio
async def test_classic_task_without_self_improve_approval_still_completes(tmp_path) -> None:
    """Gap 7: no approver means refinement fails closed but the task completes."""
    orch, factory, prompts, kernels = _wired_orchestrator(tmp_path, human_gate=False)
    await orch.start()
    try:
        service = orch.requested_agent_service
        assert service is not None
        result = service.request_agent(
            RequestedAgentRequest(
                blueprint_id="scout",
                name="Scout",
                purpose="Search documents by keyword.",
                creator="owner",
                domain="coding",
                capabilities=("search",),
            )
        )
        agent = kernels.get_required(result.agent_id)
        orch._identity_registry.register(agent)

        async def execute(**kwargs):
            return ExecutionResult(
                task_id=kwargs["task_id"], status=ExecutionStatus.COMPLETED, output="ok"
            )

        orch._executor.execute = AsyncMock(side_effect=execute)
        task = await orch.submit_task("Find docs", "Search the docs", agent.id)
        execution = await orch.execute_task(task.task_id)
        assert execution.status.value == "completed"

        assert factory.get("scout", "1.0.0").status is BlueprintStatus.ACTIVE
        with pytest.raises(KeyError):
            factory.get("scout", "1.0.1")
        versions = prompts.list_versions("purpose-scout")
        assert versions[-1].version == "1.0.0"
    finally:
        await orch.stop()
        if orch.skill_registry is not None:
            orch.skill_registry.close()
        prompts.close()
        factory.close()
