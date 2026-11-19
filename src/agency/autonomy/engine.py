"""Autonomous engine: task -> decompose -> assign/spawn -> tool gap -> proof.

Fully internal: no external input mid-run. Decomposition reuses
AgentPlanner; assignment uses router; spawn uses deterministic
rules; tool gaps go to Forge; every step appends to ProofBundle.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from pydantic import BaseModel, ConfigDict

from agency.agents.planner import AgentPlanner
from agency.agents.registry import AgentRegistry
from agency.autonomy.blacksmith import activate_blacksmith
from agency.autonomy.decisions import decide
from agency.autonomy.proof import ProofBundle
from agency.autonomy.router import assign_task
from agency.autonomy.spawner import decide_spawn
from agency.autonomy.toolforge import decide_tool_gap
from agency.factory.service import AgentFactory
from agency.forge.orchestration import BlacksmithOps
from agency.kernel.identity import Agent, Capability


class RunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    plan_id: str
    assignments: list[dict[str, object]]
    spawns: list[dict[str, object]]
    tool_gaps: list[dict[str, object]]
    proof: ProofBundle


def _spawn_agent(
    registry: AgentRegistry,
    planner: AgentPlanner,
    plan_id: str,
    subtask_id: str,
    caps: list[str],
) -> str | None:
    """Register a new runtime agent for ``caps`` and assign subtask. KISS."""
    if not caps:
        caps = ["general"]
    agent_id = f"spawned-{subtask_id[:8]}-{caps[0]}"
    if agent_id not in registry:
        agent = Agent(
            id=agent_id,
            name=agent_id,
            domain="general",
            capabilities=[Capability(name=c) for c in caps],
        )
        with suppress(ValueError):
            registry.register(agent)
    graph = planner.get_plan(plan_id)
    sub = graph.get(subtask_id) if graph is not None else None
    if sub is not None and sub.assigned_agent_id != agent_id:
        with suppress(ValueError, KeyError):
            planner.assign_subtask(agent_id, sub, plan_id=plan_id)
    return agent_id


def run_autonomous(
    task: str,
    registry: AgentRegistry,
    planner: AgentPlanner,
    available_tools: set[str],
    required_tools: list[str] | None = None,
    budget: int = 10,
    factory: AgentFactory | None = None,
    ops: BlacksmithOps | None = None,
    tool_specs: dict[str, tuple[str, str]] | None = None,
    tool_registry: Any = None,
) -> RunResult:
    """Run one autonomous task: plan, assign/spawn, forge gaps, prove.

    Tool gaps are built for real through ``ops`` (BlacksmithOps) when both a
    manager and an explicit ``(code, tests)`` spec are supplied; otherwise the
    gap is recorded as ``build_blocked``. No simulated builds or publications.
    """
    proof = ProofBundle()
    graph = planner.plan(task)
    proof.append(
        "planner", "decompose", {"plan_id": graph.plan_id, "subtasks": len(graph.subtasks)}
    )
    records = registry.list_agents()
    existing_caps: set[str] = set()
    for r in records:
        for c in r.agent.capabilities:
            existing_caps.add(getattr(c, "name", str(c)))
    assignments = assign_task(graph, records)
    spawns: list[dict[str, object]] = []
    for a in assignments:
        sub = graph.get(a.subtask_id)
        req = sub.required_capabilities if sub else []
        d = decide_spawn(req, existing_caps)
        if d.spawn:
            if "forge" in d.capabilities and factory is not None:
                agent_id: str | None = activate_blacksmith(factory)
            else:
                agent_id = _spawn_agent(
                    registry, planner, graph.plan_id, a.subtask_id, d.capabilities
                )
            existing_caps.update(d.capabilities)
            spawns.append(
                {
                    "subtask_id": a.subtask_id,
                    "capabilities": d.capabilities,
                    "rule": d.rule_id,
                    "agent_id": agent_id,
                }
            )
            proof.append(
                "router",
                "spawn_required",
                {"subtask": a.subtask_id, "caps": d.capabilities, "agent": agent_id},
            )
        elif sub is not None and sub.assigned_agent_id is not None:
            proof.append(
                "router",
                "assign",
                {
                    "subtask": a.subtask_id,
                    "agent": sub.assigned_agent_id,
                    "score": a.score,
                },
            )
        elif a.agent_id is not None and sub is not None:
            with suppress(ValueError, KeyError):
                planner.assign_subtask(a.agent_id, sub, plan_id=graph.plan_id)
            proof.append(
                "router",
                "assign",
                {"subtask": a.subtask_id, "agent": a.agent_id, "score": a.score},
            )
        else:
            # No capable agent and no missing cap (e.g. empty registry, no
            # caps required): spawn a generic worker so the subtask assigns.
            agent_id = _spawn_agent(registry, planner, graph.plan_id, a.subtask_id, ["general"])
            spawns.append(
                {
                    "subtask_id": a.subtask_id,
                    "capabilities": ["general"],
                    "rule": "S-SPAWN",
                    "agent_id": agent_id,
                }
            )
            existing_caps.add("general")
            proof.append(
                "router",
                "spawn_required",
                {"subtask": a.subtask_id, "caps": ["general"], "agent": agent_id},
            )
        decision = decide(
            "execute",
            has_capability=not d.spawn,
            has_tool=True,
            budget_left=budget,
            risk_trusted=True,
        )
        proof.append("policy", decision.rule_id, {"decision": str(decision.decision)})
    gaps: list[dict[str, object]] = []
    for t in required_tools or []:
        g = decide_tool_gap(t, available_tools)
        gaps.append({"tool": t, "action": g.action, "rule": g.rule_id})
        proof.append("forge", g.rule_id, {"tool": t, "action": g.action})
        if g.action == "BUILD":
            spec = (tool_specs or {}).get(t)
            if ops is None:
                proof.append(
                    "forge", "build_blocked", {"tool": t, "reason": "no forge ops configured"}
                )
                continue
            if spec is None:
                proof.append(
                    "forge", "build_blocked", {"tool": t, "reason": "no build spec supplied"}
                )
                continue
            code, tests = spec
            result = ops.build_tool_job(t, code, tests, tool_registry)
            proof.append(
                "forge",
                "self_publish",
                {
                    "tool": t,
                    "published": result.gate == "PASS",
                    "registered": result.registered,
                    "gate": result.gate,
                    "report_id": result.report_id,
                    "sha256": result.sha256,
                },
            )
    object.__setattr__(
        proof,
        "artifact",
        {
            "task": task,
            "plan_id": graph.plan_id,
            "assignments": [a.model_dump() for a in assignments],
        },
    )
    return RunResult(
        plan_id=graph.plan_id,
        assignments=[a.model_dump() for a in assignments],
        spawns=spawns,
        tool_gaps=gaps,
        proof=proof,
    )
