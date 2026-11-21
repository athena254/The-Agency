"""Autonomous engine: task -> decompose -> assign/spawn -> tool gap -> proof.

Fully internal: no external input mid-run. Decomposition reuses
AgentPlanner; assignment uses router; spawn uses deterministic
rules; tool gaps go to Forge; every step appends to ProofBundle.

``run_autonomous`` plans and assigns; :func:`dispatch_assignments` is the
materialisation leg (gap #20): it turns each planner-assigned subtask into a
delegated child task through a caller-supplied delegate (the orchestrator
binds :meth:`AgencyOrchestrator.delegate_task`, so depth limits, per-child
deadlines and metering stay the existing ones), records the durable
assignment lifecycle in the A5 :class:`AssignmentJournal` when one is
supplied, and appends honest proof events. Journal errors fail closed;
delegation outcomes are never faked.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from agency.agents.planner import AgentPlanner, Subtask
from agency.agents.registry import AgentRegistry
from agency.autonomy.assignment_journal import (
    AssignmentJournal,
    AssignmentJournalError,
    AssignmentRecord,
)
from agency.autonomy.blacksmith import activate_blacksmith
from agency.autonomy.decisions import decide
from agency.autonomy.proof import ProofBundle
from agency.autonomy.router import assign_task
from agency.autonomy.spawner import decide_spawn
from agency.autonomy.toolforge import decide_tool_gap
from agency.factory.service import AgentFactory
from agency.forge.orchestration import BlacksmithOps
from agency.kernel.identity import Agent, Capability
from agency.peers.canonical import digest_json


class RunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    plan_id: str
    assignments: list[dict[str, object]]
    spawns: list[dict[str, object]]
    tool_gaps: list[dict[str, object]]
    proof: ProofBundle
    dispatches: list[dict[str, object]] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    """Honest result of one delegated child task.

    ``failed`` means the child was rejected or terminally failed before/while
    executing; ``uncertain`` means a child may still be running (never
    auto-resumed). The engine maps any delegate exception to ``uncertain``
    because the child's fate is unknown from here.
    """

    status: Literal["completed", "failed", "uncertain"]
    child_task_id: str | None = None
    detail: str = ""


AssignmentDelegate = Callable[[Subtask, str], Awaitable[DispatchOutcome]]
"""Materialises one engine assignment: called with ``(subtask, agent_id)``.

The orchestrator binds :meth:`AgencyOrchestrator.delegate_task` (depth-bounded,
deadline-bounded, metered); hermetic tests bind fakes. The delegate itself is
responsible for precise pre-creation vs post-creation failure mapping; the
engine treats a raising delegate as ``uncertain``.
"""


def _epoch_now() -> int:
    """Integer seconds for journal timestamps (the journal reads no clock)."""
    return int(datetime.now(UTC).timestamp())


def _journal_id(prefix: str, *parts: str) -> str:
    """Deterministic journal id (``[a-z]{2,16}_[0-9a-f]{32}``) for real ids.

    The journal only stores bounded identifiers, so engine/plan/subtask/agent
    ids are hashed into that scheme; the real ids stay in the proof bundle and
    the returned dispatch records.
    """
    digest = sha256(":".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}_{digest}"


def _bounded(text: str) -> str:
    return text[:200]


_JOURNAL_TERMINAL: Final = {
    "completed": ("COMPLETED", True),
    "failed": ("FAILED", False),
    "uncertain": ("UNCERTAIN", False),
}


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
                assigned_graph = planner.get_plan(graph.plan_id)
                assigned_sub = (
                    assigned_graph.get(a.subtask_id) if assigned_graph is not None else None
                )
                if agent_id is not None and assigned_sub is not None:
                    with suppress(ValueError, KeyError):
                        planner.assign_subtask(agent_id, assigned_sub, plan_id=graph.plan_id)
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


async def dispatch_assignments(
    result: RunResult,
    planner: AgentPlanner,
    *,
    delegate: AssignmentDelegate,
    journal: AssignmentJournal | None = None,
) -> RunResult:
    """Materialise every planner-assigned subtask as a delegated child task.

    For each subtask the engine assigned (``subtask.assigned_agent_id``), the
    ``delegate`` (the orchestrator's ``delegate_task`` binding) creates and
    awaits one depth-bounded child task. With a ``journal``, the assignment
    lifecycle is durably recorded first (ASSIGNED -> RUNNING -> terminal) in
    the journal's own id scheme; a record that already progressed past
    ASSIGNED is never re-dispatched, so UNCERTAIN/RUNNING work is never
    auto-resumed. Outcomes and skips are honest: nothing is invented, journal
    errors fail closed by propagating.
    """
    graph = planner.get_plan(result.plan_id)
    if graph is None:
        raise ValueError(f"unknown plan {result.plan_id!r}")
    dispatches: list[dict[str, object]] = []
    for sub in graph.subtasks:
        agent_id = sub.assigned_agent_id
        if agent_id is None:
            detail = "no assigned agent; nothing to dispatch"
            result.proof.append("dispatch", "dispatch_skipped", {"subtask": sub.subtask_id})
            dispatches.append(
                {
                    "subtask_id": sub.subtask_id,
                    "agent_id": None,
                    "status": "skipped",
                    "child_task_id": None,
                    "detail": detail,
                    "assignment_id": None,
                }
            )
            continue
        assignment_id = _journal_id("assign", result.plan_id, sub.subtask_id)
        generation = 0
        if journal is not None:
            record = _journal_assign(journal, result.plan_id, sub.subtask_id, agent_id)
            generation = record.generation
            if record.state != "ASSIGNED":
                # Already dispatched (or terminal) under this assignment id:
                # never duplicate the work, never resume uncertain work.
                detail = f"assignment already {record.state}; not re-dispatched"
                result.proof.append("dispatch", "dispatch_skipped", {"subtask": sub.subtask_id})
                dispatches.append(
                    {
                        "subtask_id": sub.subtask_id,
                        "agent_id": agent_id,
                        "status": "skipped",
                        "child_task_id": None,
                        "detail": detail,
                        "assignment_id": assignment_id,
                    }
                )
                continue
            journal.transition(
                assignment_id=assignment_id,
                generation=generation,
                state="RUNNING",
                at=_epoch_now(),
            )
        outcome = await _delegate_one(delegate, sub, agent_id)
        journal_assignment_id = assignment_id if journal is not None else None
        if journal is not None:
            _journal_terminal(journal, assignment_id, generation, outcome)
        action = {
            "completed": "dispatched",
            "failed": "dispatch_failed",
            "uncertain": "dispatch_uncertain",
        }[outcome.status]
        result.proof.append(
            "dispatch",
            action,
            {
                "subtask": sub.subtask_id,
                "agent": agent_id,
                "child_task_id": outcome.child_task_id,
                "detail": _bounded(outcome.detail),
            },
        )
        dispatches.append(
            {
                "subtask_id": sub.subtask_id,
                "agent_id": agent_id,
                "status": outcome.status,
                "child_task_id": outcome.child_task_id,
                "detail": _bounded(outcome.detail),
                "assignment_id": journal_assignment_id,
            }
        )
    return result.model_copy(update={"dispatches": dispatches})


async def _delegate_one(
    delegate: AssignmentDelegate, sub: Subtask, agent_id: str
) -> DispatchOutcome:
    """Await one delegate call; a raising delegate means the outcome is unknown."""
    try:
        return await delegate(sub, agent_id)
    except Exception as exc:  # noqa: BLE001 — unknown child fate maps to UNCERTAIN
        return DispatchOutcome(
            status="uncertain",
            child_task_id=None,
            detail=f"delegate error: {type(exc).__name__}",
        )


def _journal_assign(
    journal: AssignmentJournal, plan_id: str, subtask_id: str, agent_id: str
) -> AssignmentRecord:
    """Create (or fetch) the durable ASSIGNED record for one engine assignment."""
    assignment_id = _journal_id("assign", plan_id, subtask_id)
    try:
        return journal.get(assignment_id)
    except AssignmentJournalError:
        pass
    journal.assign(
        assignment_id=assignment_id,
        task_id=_journal_id("task", plan_id, subtask_id),
        worker_id=_journal_id("worker", agent_id),
        generation=0,
        at=_epoch_now(),
    )
    return journal.get(assignment_id)


def _journal_terminal(
    journal: AssignmentJournal, assignment_id: str, generation: int, outcome: DispatchOutcome
) -> None:
    """Record the honest terminal state of one delegated child."""
    state, wants_digest = _JOURNAL_TERMINAL[outcome.status]
    result_digest = None
    if wants_digest:
        result_digest = digest_json(
            {
                "child_task_id": outcome.child_task_id,
                "detail": _bounded(outcome.detail),
                "status": outcome.status,
            }
        )
    journal.transition(
        assignment_id=assignment_id,
        generation=generation,
        state=state,
        at=_epoch_now(),
        result_digest=result_digest,
    )
