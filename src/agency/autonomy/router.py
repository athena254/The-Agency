"""Deterministic capability router.

Score = capability overlap (primary) + trust/reliability - busy
penalty. Replaces candidates[0] pick. Pure function of registry
state; no LLM, no randomness.
"""

from __future__ import annotations

from collections import deque

from pydantic import BaseModel, ConfigDict

from agency.agents.planner import TaskGraph
from agency.agents.registry import AgentRecord


def score_agent(record: AgentRecord, required: list[str]) -> float:
    caps = record.agent.capabilities
    # capabilities are Capability objects with .name
    raw: list[str] = [c.name if hasattr(c, "name") else str(c) for c in caps]
    agent_caps = {str(c).strip().lower() for c in raw}
    req = {c.strip().lower() for c in required}
    overlap = (len(req & agent_caps) / len(req)) if req else 1.0
    base = overlap
    # trust / reliability: penalize consecutive failures
    base -= min(0.3, record.consecutive_failures * 0.05)
    # busy penalty
    if record.status.value != "idle":
        base -= 0.3
    if record.agent.revoked:
        return -1.0
    return base


def detect_cycle(graph: TaskGraph) -> list[str] | None:
    ids = {s.subtask_id for s in graph.subtasks}
    indegree = {sid: 0 for sid in ids}
    # Build dep -> sid
    edges: dict[str, list[str]] = {sid: [] for sid in ids}
    for s in graph.subtasks:
        for d in s.depends_on:
            if d in ids:
                edges[d].append(s.subtask_id)
                indegree[s.subtask_id] += 1
    q: deque[str] = deque([sid for sid, deg in indegree.items() if deg == 0])
    seen = 0
    while q:
        u = q.popleft()
        seen += 1
        for v in edges[u]:
            indegree[v] -= 1
            if indegree[v] == 0:
                q.append(v)
    if seen != len(ids):
        return sorted(sid for sid, deg in indegree.items() if deg > 0)
    return None


class Assignment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    subtask_id: str
    agent_id: str | None
    score: float
    needs_spawn: bool


def assign_task(graph: TaskGraph, records: list[AgentRecord]) -> list[Assignment]:
    cycle = detect_cycle(graph)
    if cycle:
        raise ValueError(f"dependency cycle: {cycle}")
    out: list[Assignment] = []
    for sub in graph.subtasks:
        best: AgentRecord | None = None
        best_score = -2.0
        for rec in records:
            if rec.agent.revoked:
                continue
            s = score_agent(rec, sub.required_capabilities)
            if s > best_score or (
                s == best_score and (best is None or rec.agent_id < best.agent_id)
            ):
                best_score = s
                best = rec
        needs_spawn = (
            best is None or best_score < 1.0 if sub.required_capabilities else (best is None)
        )
        # If no required caps, best available idle wins; best_score may be <1 due to busy
        if not sub.required_capabilities:
            needs_spawn = best is None
        out.append(
            Assignment(
                subtask_id=sub.subtask_id,
                agent_id=best.agent_id if best and not needs_spawn else None,
                score=best_score,
                needs_spawn=needs_spawn,
            )
        )
    return out
