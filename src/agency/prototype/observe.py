"""Read-only local observability over the Agency's own runtime state.

This module deliberately sits *outside* the closed ``/v1/*`` business
boundary. It is served only by the prototype app, behind the same loopback +
owner-bearer boundary as the owner pilot, and it performs **no writes**:

* every endpoint is ``GET``; there is no mutation path of any kind;
* the ``/v1/*`` middleware (:mod:`agency.api.authority`) is untouched, so the
  business routers stay closed behind peer authority;
* every store access is a read (``list_*``/``get_*``/``count``), never
  ``store``/``add_*``/``update_*``/``transition``.

Honesty contract: the payload always carries ``mode: "read_only"``,
``peer_authority: "unavailable"`` and ``write_controls: false``. Nothing here
is presented as verified, authorized, or independently attested — the risk
numbers are the local legacy heuristic's estimate over local findings, and the
memory content is the local store's own text.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Awaitable
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any, Literal, TypeVar

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from agency.admission.models import OwnerPrincipal, PilotError
from agency.prototype.owner_guard import guard_owner_request

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/pilot/observe", tags=["owner-observe"])

READ_ONLY_NOTICE = (
    "Read-only local snapshot. Peer authority is unavailable: the /v1 business "
    "boundary stays closed, and no value here is independently verified or authorized."
)
PEER_AUTHORITY = "unavailable"

# A read-only surface must still bound what it returns.
_LIMIT = Annotated[int, Field(ge=1, le=100)]
_MAX_IDS = 200
_SKILL_LIMIT = 200


class _Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


_WireT = TypeVar("_WireT", bound=_Wire)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _try(label: str, work: Awaitable[dict[str, Any]]) -> Awaitable[dict[str, Any]]:
    """Read one local component; a failing component is reported, never fatal.

    Observability must not take the console down because one store is mid-write
    or uninitialized. The failure is reduced to a type name and a fixed reason
    so no store internals, paths, or exception text leak to the browser.
    """

    async def guarded() -> dict[str, Any]:
        try:
            return await work
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a degraded component is data, not a 500.
            log.warning("observe.component_unavailable", component=label)
            return {"available": False, "reason": "unavailable"}

    return guarded()


def _counter(values: list[str]) -> dict[str, int]:
    counts = Counter(value for value in values if value)
    return {key: counts[key] for key in sorted(counts)}


class ObserveMode(_Wire):
    """Fixed honesty envelope attached to every observability payload."""

    mode: Literal["read_only"] = "read_only"
    peer_authority: Literal["unavailable"] = "unavailable"
    write_controls: Literal[False] = False
    notice: str = READ_ONLY_NOTICE


class RuntimeStatus(str, Enum):
    STARTED = "started"
    STARTING = "starting"
    UNAVAILABLE = "unavailable"


class RuntimeStatusView(ObserveMode):
    status: RuntimeStatus
    butler_started: bool
    pilot_started: bool
    pilot_configured: bool


class AgentView(_Wire):
    id: str
    name: str
    domain: str
    trust_level: str
    revoked: bool
    capabilities: list[dict[str, str]]
    runtime_status: str | None
    current_task_id: str | None
    consecutive_failures: int
    last_heartbeat: str | None
    available: bool


class AgentsView(ObserveMode):
    agents: list[AgentView]
    total: int
    by_domain: dict[str, int]
    by_trust_level: dict[str, int]
    revoked: int
    available: bool
    reason: str | None = None


class TaskView(_Wire):
    task_id: str
    title: str
    status: str
    created_by: str
    parent_id: str | None
    priority: int
    message_count: int
    error: str | None
    created_at: str | None
    updated_at: str | None


class TasksView(ObserveMode):
    tasks: list[TaskView]
    total: int
    by_status: dict[str, int]
    available: bool
    reason: str | None = None


class MemoryItemView(_Wire):
    id: str
    agent_id: str
    tier: str
    importance: float
    tags: list[str]
    content: str
    created_at: str | None
    accessed_at: str | None


class MemoryView(ObserveMode):
    items: list[MemoryItemView]
    total: int
    limit: int
    by_tier: dict[str, int]
    by_agent: dict[str, int]
    db_path: str | None
    available: bool
    reason: str | None = None


class EvidenceEntryView(_Wire):
    id: str
    level: str
    source_agent: str | None
    timestamp: str | None


class FindingView(_Wire):
    id: str
    target: str
    evidence: str
    methodology: str
    confidence: float
    affected_component: str | None
    reproduction_status: str | None
    severity: str | None
    remediation: str | None
    verification_status: str
    timestamp: str | None
    provenance_agent: str | None
    provenance_source: str | None
    evidence_count: int
    strongest_level: str | None
    risk_category: str
    risk_impact: float
    risk_likelihood: float
    risk_confidence: float
    risk_exposure: float
    risk_blast_radius: float
    risk_exploitability: float
    risk_detectability: float
    risk_reversibility: float
    risk_affected_assets: list[str]


class RiskDimensionView(_Wire):
    label: str
    value: float


class RiskView(ObserveMode):
    findings: list[FindingView]
    total: int
    limit: int
    by_verification_status: dict[str, int]
    by_severity: dict[str, int]
    by_risk_category: dict[str, int]
    by_strongest_level: dict[str, int]
    assessed: int
    unassessed: int
    max_score: float | None
    dimensions: list[RiskDimensionView]
    method: Literal["legacy_risk_engine/v1"]
    heuristic_only: Literal[True] = True
    available: bool
    reason: str | None = None


class AuditEntryView(_Wire):
    entry_id: str
    timestamp: str | None
    agent: str | None
    task: str | None
    target: str | None
    authorization: str | None
    capability: str | None
    action: str | None
    result: str


class AuditView(ObserveMode):
    entries: list[AuditEntryView]
    total: int
    limit: int
    by_result: dict[str, int]
    by_action: dict[str, int]
    available: bool
    reason: str | None = None


class ThreadView(_Wire):
    id: str
    title: str
    parent_thread_id: str | None
    state: str
    created_at: str | None
    updated_at: str | None
    message_count: int | None


class ThreadsView(ObserveMode):
    threads: list[ThreadView]
    total: int


class OwnerTaskView(_Wire):
    task_id: str
    request_id: str
    thread_id: str
    state: str
    uncertain: bool
    replayed: bool
    error_code: str | None
    model_attempts: int
    tool_attempts: int
    created_at: str | None
    updated_at: str | None


class OwnerTasksView(ObserveMode):
    tasks: list[OwnerTaskView]
    total: int
    by_state: dict[str, int]
    uncertain: int


class SkillView(_Wire):
    skill_id: str
    version: str
    name: str
    description: str
    status: str
    permissions: list[str]
    provenance: str
    event_count: int | None


class SkillsView(ObserveMode):
    skills: list[SkillView]
    total: int
    available: bool
    reason: str | None = None
    truncated: bool = False


class ToolView(_Wire):
    name: str
    description: str
    risk: str


class ToolsView(ObserveMode):
    tools: list[ToolView]
    total: int
    available: bool
    reason: str | None = None


class OverviewView(ObserveMode):
    """One-call summary; each section is independently best-effort."""

    runtime: RuntimeStatusView
    agents: dict[str, Any]
    tasks: dict[str, Any]
    memories: dict[str, Any]
    evidence: dict[str, Any]
    risk: dict[str, Any]
    audit: dict[str, Any]
    owner: dict[str, Any]
    collected_at: str


# --------------------------------------------------------------------------- #
# Local readers — every one is a pure read over the Agency's own state.
# --------------------------------------------------------------------------- #


def _orchestrator(service: Any) -> Any | None:
    """Return the orchestrator behind a Butler service or an orchestrator itself."""
    candidate = getattr(service, "orchestrator", None)
    if candidate is not None:
        return candidate
    if getattr(service, "_task_manager", None) is not None:
        return service
    return None


def _agents_section(service: Any) -> dict[str, Any]:
    """Merge the kernel identity registry with the runtime worker registry.

    Both are read in memory; the router roster is *not* used here because it is
    a message-routing cache, not the identity of record.
    """
    orchestrator = _orchestrator(service)
    if orchestrator is None:
        return {"available": False, "reason": "unavailable", "agents": [], "total": 0}
    identity = getattr(orchestrator, "_identity_registry", None)
    runtime = getattr(orchestrator, "_runtime_registry", None)
    if identity is None:
        return {"available": False, "reason": "unavailable", "agents": [], "total": 0}
    records = {record.agent_id: record for record in runtime.list_agents()} if runtime else {}
    agents: list[AgentView] = []
    for agent in identity.list_agents(include_revoked=True):
        record = records.get(agent.id)
        agents.append(
            AgentView(
                id=str(agent.id),
                name=str(agent.name),
                domain=str(agent.domain),
                trust_level=str(agent.trust_level.value),
                revoked=bool(agent.revoked),
                capabilities=[
                    {"name": str(cap.name), "max_level": str(cap.max_level.value)}
                    for cap in agent.capabilities
                ],
                runtime_status=str(record.status.value) if record is not None else None,
                current_task_id=record.current_task_id if record is not None else None,
                consecutive_failures=record.consecutive_failures if record is not None else 0,
                last_heartbeat=_iso(record.last_heartbeat) if record is not None else None,
                available=bool(record.available) if record is not None else False,
            )
        )
    return {
        "available": True,
        "reason": None,
        "agents": agents,
        "total": len(agents),
        "by_domain": _counter([agent.domain for agent in agents]),
        "by_trust_level": _counter([agent.trust_level for agent in agents]),
        "revoked": sum(1 for agent in agents if agent.revoked),
    }


async def _tasks_section(service: Any) -> dict[str, Any]:
    orchestrator = _orchestrator(service)
    manager = getattr(orchestrator, "_task_manager", None) if orchestrator else None
    if manager is None:
        return {"available": False, "reason": "unavailable", "tasks": [], "total": 0}
    rows = await manager.list_tasks()
    tasks = [
        TaskView(
            task_id=str(row.task_id),
            title=str(row.title),
            status=str(row.status.value),
            created_by=str(row.created_by),
            parent_id=row.parent_id,
            priority=int(row.priority),
            message_count=len(row.messages),
            error=row.error,
            created_at=_iso(row.created_at),
            updated_at=_iso(row.updated_at),
        )
        for row in rows
    ]
    return {
        "available": True,
        "reason": None,
        "tasks": tasks,
        "total": len(tasks),
        "by_status": _counter([task.status for task in tasks]),
    }


async def _memories_section(service: Any, limit: int) -> dict[str, Any]:
    orchestrator = _orchestrator(service)
    store = getattr(orchestrator, "_memory_store", None) if orchestrator else None
    if store is None:
        return {
            "available": False,
            "reason": "unavailable",
            "items": [],
            "total": 0,
            "limit": limit,
        }
    items = await store.list_all(limit=limit)
    total = await store.count()
    rows = [
        MemoryItemView(
            id=str(item.id),
            agent_id=str(item.agent_id),
            tier=str(item.tier.value),
            importance=float(item.importance),
            tags=[str(tag) for tag in item.tags],
            content=str(item.content),
            created_at=_iso(item.created_at),
            accessed_at=_iso(item.accessed_at),
        )
        for item in items
    ]
    return {
        "available": True,
        "reason": None,
        "items": rows,
        "total": int(total),
        "limit": limit,
        "by_tier": _counter([row.tier for row in rows]),
        "by_agent": _counter([row.agent_id for row in rows]),
        "db_path": getattr(store, "db_path", None),
    }


async def _evidence_section(service: Any, limit: int) -> dict[str, Any]:
    orchestrator = _orchestrator(service)
    store = getattr(orchestrator, "_evidence_store", None) if orchestrator else None
    engine = getattr(orchestrator, "_risk_engine", None) if orchestrator else None
    if store is None or engine is None:
        return {
            "available": False,
            "reason": "unavailable",
            "findings": [],
            "total": 0,
            "limit": limit,
            "heuristic_only": True,
        }
    from agency.evidence.levels import EvidenceLevelManager
    from agency.evidence.store.store import FindingsFilter

    findings = await store.list_findings(FindingsFilter(limit=limit, descending=True))
    rows: list[FindingView] = []
    for finding in findings:
        entries = await store.evidence_for_finding(finding.id)
        levels = [entry.level for entry in entries]
        strongest = EvidenceLevelManager.strongest_level(levels)
        risk, category = engine.assess(finding, entries)
        rows.append(
            FindingView(
                id=str(finding.id),
                target=str(finding.target),
                evidence=str(finding.evidence),
                methodology=str(finding.methodology),
                confidence=float(finding.confidence),
                affected_component=finding.affected_component,
                reproduction_status=finding.reproduction_status,
                severity=finding.severity.value if finding.severity else None,
                remediation=finding.remediation,
                verification_status=str(finding.verification_status.value),
                timestamp=_iso(finding.timestamp),
                provenance_agent=finding.provenance.agent_id,
                provenance_source=str(finding.provenance.source),
                evidence_count=len(entries),
                strongest_level=strongest.value if strongest else None,
                risk_category=str(category.value),
                risk_impact=float(risk.impact),
                risk_likelihood=float(risk.likelihood),
                risk_confidence=float(risk.confidence),
                risk_exposure=float(risk.exposure),
                risk_blast_radius=float(risk.blast_radius),
                risk_exploitability=float(risk.exploitability),
                risk_detectability=float(risk.detectability),
                risk_reversibility=float(risk.reversibility),
                risk_affected_assets=[str(asset) for asset in risk.affected_assets],
            )
        )
    return {
        "available": True,
        "reason": None,
        "findings": rows,
        "total": len(rows),
        "limit": limit,
        "heuristic_only": True,
        "by_verification_status": _counter([row.verification_status for row in rows]),
        "by_severity": _counter([row.severity or "ungraded" for row in rows]),
        "by_risk_category": _counter([row.risk_category for row in rows]),
        "by_strongest_level": _counter(
            [row.strongest_level or "LEVEL_0_HYPOTHESIS" for row in rows]
        ),
        "assessed": sum(1 for row in rows if row.risk_category),
        "unassessed": sum(1 for row in rows if not row.risk_category),
        "max_score": max((row.risk_impact for row in rows), default=None),
    }


async def _audit_section(service: Any, limit: int) -> dict[str, Any]:
    orchestrator = _orchestrator(service)
    audit = getattr(orchestrator, "_audit_log", None) if orchestrator else None
    if audit is None:
        return {
            "available": False,
            "reason": "unavailable",
            "entries": [],
            "total": 0,
            "limit": limit,
        }
    from agency.kernel.audit import AuditFilter

    total = await audit.count(AuditFilter(limit=1))
    rows = await audit.query(AuditFilter(limit=limit))
    entries = [
        AuditEntryView(
            entry_id=str(row.entry_id),
            timestamp=_iso(row.timestamp),
            agent=row.agent,
            task=row.task,
            target=row.target,
            authorization=row.authorization,
            capability=row.capability,
            action=str(row.action) if row.action is not None else None,
            result=str(row.result),
        )
        for row in rows
    ]
    return {
        "available": True,
        "reason": None,
        "entries": entries,
        "total": int(total),
        "limit": limit,
        "by_result": _counter([entry.result for entry in entries]),
        "by_action": _counter([entry.action or "unclassified" for entry in entries]),
    }


async def _owner_section(pilot: Any, principal: OwnerPrincipal) -> dict[str, Any]:
    """Owner-scoped admission records, read through the owner service itself."""
    if pilot is None:
        return {
            "available": False,
            "reason": "not_configured",
            "tasks": [],
            "total": 0,
            "threads": [],
            "thread_total": 0,
        }
    tasks = await pilot.list_tasks(principal, limit=100)
    threads = await pilot.list_threads(principal)
    rows = [
        OwnerTaskView(
            task_id=str(row.task_id),
            request_id=str(row.request_id),
            thread_id=str(row.thread_id),
            state=str(row.state),
            uncertain=bool(row.uncertain),
            replayed=bool(row.replayed),
            error_code=row.error_code,
            model_attempts=int(row.usage.model_attempts),
            tool_attempts=int(row.usage.tool_attempts),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in tasks
    ]
    thread_rows = [
        ThreadView(
            id=str(row.id),
            title=str(row.title),
            parent_thread_id=row.parent_thread_id,
            state=str(row.state),
            created_at=row.created_at,
            updated_at=row.updated_at,
            message_count=None,
        )
        for row in threads
    ]
    return {
        "available": True,
        "reason": None,
        "tasks": rows,
        "total": len(rows),
        "by_state": _counter([row.state for row in rows]),
        "uncertain": sum(1 for row in rows if row.uncertain),
        "threads": thread_rows,
        "thread_total": len(thread_rows),
    }


async def _skills_section(service: Any) -> dict[str, Any]:
    orchestrator = _orchestrator(service)
    registry = getattr(orchestrator, "_skill_registry", None) if orchestrator else None
    if registry is None:
        return {"available": False, "reason": "not_configured", "skills": [], "total": 0}
    # SkillRegistry keeps no global list; enumerate the distinct ids present in
    # its own table, then read each version. Read-only SELECTs only.
    rows = registry._conn.execute(
        "SELECT skill_id, version, name, description, permissions_json,"
        " provenance, status FROM skills ORDER BY skill_id, version LIMIT ?",
        (_SKILL_LIMIT,),
    ).fetchall()
    skills: list[SkillView] = []
    for row in rows:
        permissions = json.loads(row[4] or "[]")
        skills.append(
            SkillView(
                skill_id=str(row[0]),
                version=str(row[1]),
                name=str(row[2]),
                description=str(row[3]),
                status=str(row[6]),
                permissions=[str(item) for item in permissions],
                provenance=str(row[5]),
                event_count=None,
            )
        )
    return {
        "available": True,
        "reason": None,
        "skills": skills,
        "total": len(skills),
        "truncated": len(skills) >= _SKILL_LIMIT,
    }


def _tools_section(service: Any) -> dict[str, Any]:
    orchestrator = _orchestrator(service)
    registry = getattr(orchestrator, "_tool_registry", None) if orchestrator else None
    if registry is None:
        return {"available": False, "reason": "not_configured", "tools": [], "total": 0}
    specs = registry.list_specs()
    tools = [
        ToolView(
            name=str(spec.name),
            description=str(spec.description),
            risk=str(spec.risk.value),
        )
        for spec in specs
    ]
    return {"available": True, "reason": None, "tools": tools, "total": len(tools)}


async def _boundary(request: Request) -> tuple[Any, OwnerPrincipal]:
    """Owner boundary identical to the pilot router: loopback + Host + Origin + bearer.

    These are reads, so no CSRF header and no body are involved — ``write`` stays
    ``False`` and the guard never touches the request stream.
    """
    principal, _ = await guard_owner_request(
        request,
        getattr(request.app.state, "owner_authenticator", None),
        csrf_token=None,
        write=False,
    )
    pilot = getattr(request.app.state, "pilot", None)
    if pilot is None:
        raise PilotError("disabled")
    butler = getattr(request.app.state, "butler", None)
    if butler is None:
        raise PilotError("disabled")
    return butler, principal


def _runtime(request: Request) -> RuntimeStatusView:
    butler_started = bool(getattr(request.app.state, "butler_started", False))
    pilot_started = bool(getattr(request.app.state, "pilot_started", False))
    configured = bool(getattr(request.app.state, "pilot_configured", False))
    if configured:
        status = RuntimeStatus.STARTED if pilot_started else RuntimeStatus.STARTING
    else:
        status = RuntimeStatus.STARTED if butler_started else RuntimeStatus.STARTING
    return RuntimeStatusView(
        status=status,
        butler_started=butler_started,
        pilot_started=pilot_started,
        pilot_configured=configured,
    )


# --------------------------------------------------------------------------- #
# Routes — GET only. No POST/PATCH/DELETE exists in this module.
# --------------------------------------------------------------------------- #


@router.get("/status", response_model=ObserveMode)
async def status(request: Request) -> ObserveMode:
    """Fixed honesty envelope, so the console can label the surface before data arrives."""
    await _boundary(request)
    return ObserveMode()


@router.get("/runtime", response_model=RuntimeStatusView)
async def runtime(request: Request) -> RuntimeStatusView:
    await _boundary(request)
    return _runtime(request)


@router.get("/agents", response_model=AgentsView)
async def agents(request: Request) -> AgentsView:
    butler, _ = await _boundary(request)
    section = _agents_section(butler)
    return _view(AgentsView, section, _EMPTY_AGENTS)


@router.get("/tasks", response_model=TasksView)
async def tasks(request: Request) -> TasksView:
    butler, _ = await _boundary(request)
    section = await _try("tasks", _tasks_section(butler))
    return _view(TasksView, section, _EMPTY_TASKS)


@router.get("/memories", response_model=MemoryView)
async def memories(request: Request, limit: _LIMIT = 50) -> MemoryView:
    butler, _ = await _boundary(request)
    section = await _try("memories", _memories_section(butler, int(limit)))
    return _view(MemoryView, section, _EMPTY_MEMORIES)


@router.get("/evidence", response_model=RiskView)
async def evidence(request: Request, limit: _LIMIT = 50) -> RiskView:
    butler, _ = await _boundary(request)
    section = await _try("evidence", _evidence_section(butler, int(limit)))
    section.setdefault("method", "legacy_risk_engine/v1")
    section.setdefault("heuristic_only", True)
    section.setdefault("dimensions", _risk_dimensions(section))
    return _view(RiskView, section, _EMPTY_RISK)


@router.get("/audit", response_model=AuditView)
async def audit(request: Request, limit: _LIMIT = 50) -> AuditView:
    butler, _ = await _boundary(request)
    section = await _try("audit", _audit_section(butler, int(limit)))
    return _view(AuditView, section, _EMPTY_AUDIT)


@router.get("/owner", response_model=dict[str, Any])
async def owner(request: Request) -> dict[str, Any]:
    _butler, principal = await _boundary(request)
    pilot = getattr(request.app.state, "pilot", None)
    section = await _try("owner", _owner_section(pilot, principal))
    return {
        **ObserveMode().model_dump(mode="json"),
        **section,
        "owner_id": principal.owner_id,
        "subject_id": principal.subject_id,
    }


@router.get("/skills", response_model=SkillsView)
async def skills(request: Request) -> SkillsView:
    butler, _ = await _boundary(request)
    section = await _try("skills", _skills_section(butler))
    return _view(SkillsView, section, _EMPTY_SKILLS)


@router.get("/tools", response_model=ToolsView)
async def tools(request: Request) -> ToolsView:
    butler, _ = await _boundary(request)
    section = await _try("tools", _adapt(_tools_section(butler)))
    return _view(ToolsView, section, _EMPTY_TOOLS)


@router.get("/overview", response_model=OverviewView)
async def overview(request: Request, limit: _LIMIT = 50) -> OverviewView:
    """Best-effort combined snapshot; one failing component degrades only itself."""
    butler, principal = await _boundary(request)
    pilot = getattr(request.app.state, "pilot", None)
    capped = int(limit)
    agents = _agents_section(butler)
    tasks, memories, evidence, audit, owner = await asyncio.gather(
        _try("tasks", _tasks_section(butler)),
        _try("memories", _memories_section(butler, capped)),
        _try("evidence", _evidence_section(butler, capped)),
        _try("audit", _audit_section(butler, capped)),
        _try("owner", _owner_section(pilot, principal)),
    )
    risk = {**evidence, "dimensions": _risk_dimensions(evidence)}
    # The standalone /owner payload carries the honesty envelope and the owner
    # identity; the overview's owner section must match it.
    owner_section = {
        **ObserveMode().model_dump(mode="json"),
        **owner,
        "owner_id": principal.owner_id,
        "subject_id": principal.subject_id,
    }
    return OverviewView(
        runtime=_runtime(request),
        agents=agents,
        tasks=tasks,
        memories=memories,
        evidence=risk,
        risk=risk,
        audit=audit,
        owner=owner_section,
        collected_at=datetime.now(UTC).isoformat(),
    )


def _adapt(value: dict[str, Any]) -> Awaitable[dict[str, Any]]:
    """Wrap an already-computed local read so it shares the async error path."""

    async def ready() -> dict[str, Any]:
        return value

    return ready()


def _view(
    model: type[_WireT],
    section: dict[str, Any],
    fallback: dict[str, Any],
) -> _WireT:
    """Build a response model, filling required fields a degraded read omits.

    A component that cannot be read is reported as ``available: False`` with a
    complete, valid payload — never a 500 and never a partial shape.
    """
    payload = {**fallback, **section}
    payload.setdefault("available", section.get("available") is True)
    payload.setdefault("reason", None)
    return model.model_validate(payload)


# Neutral shapes for a component that is present but unreadable. Every field
# here is required by its view model, so a degraded read still serialises.
_EMPTY_AGENTS: dict[str, Any] = {
    "agents": [],
    "total": 0,
    "by_domain": {},
    "by_trust_level": {},
    "revoked": 0,
}
_EMPTY_TASKS: dict[str, Any] = {"tasks": [], "total": 0, "by_status": {}}
_EMPTY_MEMORIES: dict[str, Any] = {
    "items": [],
    "total": 0,
    "limit": 0,
    "by_tier": {},
    "by_agent": {},
    "db_path": None,
}
_EMPTY_RISK: dict[str, Any] = {
    "findings": [],
    "total": 0,
    "limit": 0,
    "by_verification_status": {},
    "by_severity": {},
    "by_risk_category": {},
    "by_strongest_level": {},
    "assessed": 0,
    "unassessed": 0,
    "max_score": None,
    "dimensions": [],
    "method": "legacy_risk_engine/v1",
    "heuristic_only": True,
}
_EMPTY_AUDIT: dict[str, Any] = {
    "entries": [],
    "total": 0,
    "limit": 0,
    "by_result": {},
    "by_action": {},
}
_EMPTY_SKILLS: dict[str, Any] = {"skills": [], "total": 0, "truncated": False}
_EMPTY_TOOLS: dict[str, Any] = {"tools": [], "total": 0}


def _risk_dimensions(section: dict[str, Any]) -> list[RiskDimensionView]:
    """Aggregate the local heuristic's dimension means over the findings returned."""
    findings: list[FindingView] = section.get("findings") or []
    if not findings:
        return []
    labels = (
        ("risk_impact", "impact"),
        ("risk_likelihood", "likelihood"),
        ("risk_confidence", "confidence"),
        ("risk_exposure", "exposure"),
        ("risk_blast_radius", "blast_radius"),
        ("risk_exploitability", "exploitability"),
        ("risk_detectability", "detectability"),
        ("risk_reversibility", "reversibility"),
    )
    count = len(findings)
    return [
        RiskDimensionView(
            label=label,
            value=round(
                sum(float(getattr(row, key)) for row in findings) / count,
                4,
            ),
        )
        for key, label in labels
    ]


__all__ = [
    "READ_ONLY_NOTICE",
    "AgentsView",
    "AuditView",
    "FindingView",
    "MemoryView",
    "ObserveMode",
    "OverviewView",
    "RiskView",
    "RuntimeStatusView",
    "SkillsView",
    "TasksView",
    "ThreadsView",
    "ToolsView",
    "router",
]
