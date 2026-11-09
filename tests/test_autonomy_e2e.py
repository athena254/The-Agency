"""E2E: run_autonomous + orchestrator.submit_autonomous_task.

Creates registry with 2 agents, runs multi-step task requiring missing
capability + missing tool, asserts spawns + tool_gaps + proof chain
verifies + lattice nodes created.
"""

from __future__ import annotations

import hashlib

from agency.agents.planner import AgentPlanner, SubtaskSpec
from agency.agents.registry import AgentRegistry
from agency.autonomy.engine import run_autonomous
from agency.autonomy.proof import verify_chain
from agency.autonomy.toolforge import can_self_publish, decide_tool_gap
from agency.kernel.identity import Agent, Capability
from agency.lattice.models import NodeType
from agency.orchestrator import AgencyOrchestrator


def _strategy(_task: str) -> list[SubtaskSpec]:
    return [
        SubtaskSpec(title="research step", required_capabilities=["research"]),
        SubtaskSpec(title="quantum step", required_capabilities=["quantum-compute"]),
    ]


def _registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.register(
        Agent(id="a1", name="a1", domain="general", capabilities=[Capability(name="research")])
    )
    reg.register(
        Agent(id="a2", name="a2", domain="general", capabilities=[Capability(name="code")])
    )
    return reg


def test_run_autonomous_e2e() -> None:
    reg = _registry()
    planner = AgentPlanner(reg, strategy=_strategy)
    result = run_autonomous(
        "Step 1: research. Step 2: quantum.",
        reg,
        planner,
        available_tools={"web_search"},
        required_tools=["web_search", "quantum_sim"],
    )
    assert result.plan_id
    # spawns: quantum-compute missing -> 1 spawn, registered + assigned
    assert len(result.spawns) == 1
    assert result.spawns[0]["capabilities"] == ["quantum-compute"]
    assert isinstance(result.spawns[0]["agent_id"], str)
    assert result.spawns[0]["agent_id"] in reg
    # planner state reflects assignment (router agent_id may be None when busy)
    graph = planner.get_plan(result.plan_id)
    assert graph is not None
    assert all(s.assigned_agent_id for s in graph.subtasks)
    assert "a1" in {s.assigned_agent_id for s in graph.subtasks}
    # tool gaps: present -> USE, missing -> BUILD
    gaps = {g["tool"]: g for g in result.tool_gaps}
    assert gaps["web_search"]["action"] == "USE"
    assert gaps["quantum_sim"]["action"] == "BUILD"
    assert gaps["quantum_sim"]["rule"] == "T-BUILD"
    # forge: simulate build + publish gate
    sha = hashlib.sha256(b"quantum_sim").hexdigest()
    assert decide_tool_gap("quantum_sim", {"web_search"}).action == "BUILD"
    assert can_self_publish("PASS", sha, sha) is True
    assert can_self_publish("FAIL", sha, sha) is False
    assert any(
        e.actor == "forge" and e.action == "self_publish" and e.payload["published"] is True
        for e in result.proof.events
    )
    # proof chain verifies + manifest
    assert verify_chain(result.proof.events) is True
    assert result.proof.count == len(result.proof.events) > 0


async def test_submit_autonomous_task_e2e(tmp_path) -> None:  # type: ignore[no-untyped-def]
    orch = AgencyOrchestrator(memory_db_path=str(tmp_path / "memory.db"))
    await orch.start()
    try:
        await orch.register_agent("researcher", "general", ["research"])
        await orch.register_agent("coder", "general", ["code"])
        orch._planner = AgentPlanner(orch._runtime_registry, strategy=_strategy)
        assert orch._lattice is not None
        before = await orch._lattice.find_nodes(NodeType.TASK, limit=10000)
        result = await orch.submit_autonomous_task(
            "Step 1: research. Step 2: quantum.",
            required_tools=["web_search", "quantum_sim"],
            available_tools={"web_search"},
        )
        assert len(result.spawns) == 1
        assert {g["tool"]: g["action"] for g in result.tool_gaps} == {
            "web_search": "USE",
            "quantum_sim": "BUILD",
        }
        assert verify_chain(result.proof.events) is True
        # spawned agent mirrored into identity registry
        assert await orch.list_agents()
        spawn_id = result.spawns[0]["agent_id"]
        assert any(a.id == spawn_id for a in await orch.list_agents())
        # lattice nodes created (task node from submit_task mirror)
        after = await orch._lattice.find_nodes(NodeType.TASK, limit=10000)
        assert len(after) > len(before)
        assert any(
            "Step 1: research" in str((n.get("properties") or {}).get("payload", "")) for n in after
        )
    finally:
        await orch.stop()
