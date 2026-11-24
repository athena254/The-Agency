"""Routing tests: build tasks reach the Blacksmith; forge work never takes the chat path."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from agency.agents.planner import AgentPlanner
from agency.agents.registry import AgentRegistry
from agency.autonomy.engine import run_autonomous
from agency.autonomy.strategies import forge_build_strategy
from agency.factory.prompts import PromptRegistry
from agency.factory.prompts_builtin import ensure_builtin_prompts
from agency.factory.service import AgentFactory
from agency.forge.external_agent import ExternalAgentReport
from agency.forge.inspection import InspectionReport
from agency.forge.orchestration import BlacksmithOps
from agency.forge.security_scan import SecurityReport
from agency.kernel.registry import AgentRegistry as KernelRegistry
from agency.orchestrator import AgencyOrchestrator
from tests._fake_paths import FAKE_REPO


def _pass_report() -> ExternalAgentReport:
    now = datetime.now(UTC)
    root = "D:/fake/worktree"
    return ExternalAgentReport(
        report_id=str(uuid4()),
        created_at=now,
        worktree_path=root,
        branch="blacksmith-test",
        task="t",
        exit_code=0,
        changed_paths=("src/x.py",),
        inspection=InspectionReport(report_id=str(uuid4()), created_at=now, root=root, gate="PASS"),
        security=SecurityReport(
            report_id=str(uuid4()),
            created_at=now,
            root=root,
            gate="PASS",
            scanned_files=1,
            skipped_files=0,
        ),
        gate="PASS",
    )


def test_forge_strategy_tags_build_subtasks() -> None:
    specs = forge_build_strategy("Build a weather tool. Then summarize the results.")
    assert len(specs) == 2
    assert specs[0].required_capabilities == ["forge"]
    assert specs[1].required_capabilities == []


def test_forge_strategy_ignores_non_build_tasks() -> None:
    specs = forge_build_strategy("What is the weather today?")
    assert specs
    assert all(s.required_capabilities == [] for s in specs)


def test_default_strategy_unchanged() -> None:
    reg = AgentRegistry()
    graph = AgentPlanner(reg).plan("Build a weather tool")
    assert all("forge" not in s.required_capabilities for s in graph.subtasks)


def test_engine_spawns_blacksmith_for_build_task() -> None:
    reg = AgentRegistry()
    kernels = KernelRegistry()
    prompts = PromptRegistry(":memory:")
    ensure_builtin_prompts(prompts)
    factory = AgentFactory(
        ":memory:",
        kernel_registry=kernels,
        runtime_registry=reg,
        allowed_capabilities={"forge"},
        is_prompt_published=prompts.is_published,
        prompt_resolver=prompts,
    )
    planner = AgentPlanner(reg, strategy=forge_build_strategy)
    try:
        res = run_autonomous(
            "Build a weather tool", reg, planner, available_tools=set(), factory=factory
        )
        assert len(res.spawns) == 1
        spawn = res.spawns[0]
        assert spawn["capabilities"] == ["forge"]
        agent = kernels.get_required(str(spawn["agent_id"]))
        assert agent.name == "blacksmith"
        assert agent.metadata.get("factory") == "agent-factory-v1"
        assert "system_prompt" in agent.metadata
        graph = planner.get_plan(res.plan_id)
        assert graph is not None
        assert all(s.assigned_agent_id == agent.id for s in graph.subtasks)
    finally:
        factory.close()


@pytest.mark.asyncio
async def test_forge_domain_routes_to_ops(tmp_path) -> None:
    ops = BlacksmithOps(str(tmp_path / "jobs.db"), runner=lambda *a, **k: _pass_report())
    orch = AgencyOrchestrator(
        config={"forge_repo": FAKE_REPO},
        memory_db_path=str(tmp_path / "memory.db"),
        blacksmith_ops=ops,
    )
    await orch.start()
    try:
        agent = await orch.register_agent("blacksmith", "forge", ["forge"])
        task = await orch.submit_task(
            title="Build x", description="Build function x", agent_id=agent.id
        )
        result = await orch.execute_task(task.task_id)
        assert result.status.value == "completed"
        assert "forge job" in str(result.output)
        assert "awaiting human ship approval" in str(result.output)
        status = ops.status()
        assert status.total == 1
        assert status.by_state.get("AWAITING_HUMAN") == 1
    finally:
        await orch.stop()
        ops.close()


@pytest.mark.asyncio
async def test_forge_domain_without_ops_fails_honestly(tmp_path) -> None:
    orch = AgencyOrchestrator(memory_db_path=str(tmp_path / "memory.db"))
    await orch.start()
    try:
        agent = await orch.register_agent("blacksmith", "forge", ["forge"])
        task = await orch.submit_task(title="Build x", description="Build x", agent_id=agent.id)
        result = await orch.execute_task(task.task_id)
        assert result.status.value == "failed"
        assert "not configured" in str(result.output)
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_forge_domain_without_repo_fails_honestly(tmp_path) -> None:
    ops = BlacksmithOps(str(tmp_path / "jobs.db"), runner=lambda *a, **k: _pass_report())
    orch = AgencyOrchestrator(memory_db_path=str(tmp_path / "memory.db"), blacksmith_ops=ops)
    await orch.start()
    try:
        agent = await orch.register_agent("blacksmith", "forge", ["forge"])
        task = await orch.submit_task(title="Build x", description="Build x", agent_id=agent.id)
        result = await orch.execute_task(task.task_id)
        assert result.status.value == "failed"
        assert "forge_repo" in str(result.output)
        assert ops.status().total == 0  # nothing dispatched without a repo
    finally:
        await orch.stop()
        ops.close()
