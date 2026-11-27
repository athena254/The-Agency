"""Read-only live view over Forge job records.

Deep module: callers inject job records (objects or mappings); the module
extracts state, phase timings, reported cost, gates and stuck reasons without
mutating anything, and flags every missing signal with an honest caveat.

There is no DB access, no orchestration import and no polling here -- this is
a snapshot of records the caller already has. Nothing in this module can
transition a job, approve a gate or dispatch work.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field

from agency.forge.human_gates import required_human_gates

MAX_DETAIL_CHARS = 200
MAX_GATES = 16
_UNKNOWN = "UNKNOWN"

_STUCK_BASE = {
    "UNCERTAIN": "uncertain_outcome_unresolved",
    "BLOCKED": "blocked_escalated_to_human",
    "FAILED": "failed",
}


class PhaseView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    duration_s: float = Field(ge=0)


class JobView(BaseModel):
    """One job's read-only view; missing signals are caveats, never guesses."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str = Field(min_length=1)
    state: str = Field(min_length=1)
    phases: tuple[PhaseView, ...] = ()
    total_s: float = Field(ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    gate: str = ""
    required_gates: tuple[str, ...] = ()
    stuck_reason: str = ""
    caveats: tuple[str, ...] = ()


class JobsView(BaseModel):
    """Snapshot of many jobs plus collection-level honesty caveats."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    jobs: tuple[JobView, ...] = ()
    caveats: tuple[str, ...] = ("read_only_snapshot", "as_of_render_time")


def _read(obj: object, key: str, default: object) -> object:
    """Read a field from an object or mapping, never raising on absence."""
    value: object
    if isinstance(obj, Mapping):
        value = obj.get(key, default)
    else:
        value = getattr(obj, key, default)
    return value


def _as_text(value: object) -> str:
    """Stringify a plain string or a string-valued enum; else empty string."""
    candidate: object = getattr(value, "value", value)
    return candidate.strip() if isinstance(candidate, str) else ""


def _as_number(value: object) -> float | None:
    """Coerce a reported non-negative finite number, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _stuck_reason(record: object, state: str) -> str:
    explicit = _as_text(_read(record, "stuck_reason", ""))
    if explicit:
        return explicit[:MAX_DETAIL_CHARS]
    base = _STUCK_BASE.get(state, "")
    if not base:
        return ""
    detail = _as_text(_read(record, "failure_detail", ""))
    if detail:
        return f"{base}: {detail}"[:MAX_DETAIL_CHARS]
    return base


def view_job(record: object) -> JobView:
    """Build one read-only view; the record is never modified."""
    job_id = _as_text(_read(record, "job_id", "")) or "unknown"
    state = _as_text(_read(record, "state", "")).upper() or _UNKNOWN

    caveats: list[str] = []
    if state == _UNKNOWN:
        caveats.append("state_not_reported")

    trace = _read(record, "trace", None)
    raw_phases = _read(trace, "phases", ()) if trace is not None else ()
    if not raw_phases:
        raw_phases = _read(record, "phases", ())
    phases: list[PhaseView] = []
    if isinstance(raw_phases, (list, tuple)):
        for raw in raw_phases:
            name = _as_text(_read(raw, "name", ""))
            duration = _as_number(_read(raw, "duration_s", None))
            if name and duration is not None:
                phases.append(PhaseView(name=name, duration_s=round(duration, 3)))
    if not phases:
        caveats.append("phases_not_reported")
    total_s = round(sum(phase.duration_s for phase in phases), 3)

    cost_usd = _as_number(_read(trace, "estimated_cost_usd", None)) if trace is not None else None
    if cost_usd is None:
        # A default 0.0 on a job record means "not measured"; only a positive
        # record-level value is treated as a real report. A trace value (even
        # 0.0) counts as explicitly reported by the worker.
        record_cost = _as_number(_read(record, "cost_usd", None))
        if record_cost is not None and record_cost > 0:
            cost_usd = record_cost
    if cost_usd is None:
        caveats.append("cost_not_reported")

    gate = _as_text(_read(record, "gate", ""))
    if not gate:
        caveats.append("gate_not_reported")

    raw_paths = _read(record, "changed_paths", ())
    paths: tuple[str, ...] = ()
    if isinstance(raw_paths, (list, tuple)):
        paths = tuple(path for path in raw_paths if isinstance(path, str))
    required = required_human_gates(paths)[:MAX_GATES]

    return JobView(
        job_id=job_id,
        state=state,
        phases=tuple(phases),
        total_s=total_s,
        cost_usd=round(cost_usd, 6) if cost_usd is not None else None,
        gate=gate,
        required_gates=required,
        stuck_reason=_stuck_reason(record, state),
        caveats=tuple(caveats),
    )


def build_jobs_view(records: Iterable[object]) -> JobsView:
    """Build a frozen snapshot over injected records without mutating them."""
    return JobsView(jobs=tuple(view_job(record) for record in records))


def render_jobs_view(records: Iterable[object]) -> str:
    """Render the snapshot as human-readable text (for CLI wiring)."""
    collection = build_jobs_view(records)
    lines = [f"Jobs: {len(collection.jobs)}"]
    for job in collection.jobs:
        cost = f"${job.cost_usd:.4f}" if job.cost_usd is not None else "n/a"
        gates = ",".join(job.required_gates) or "-"
        stuck = job.stuck_reason or "-"
        lines.append(
            f"  {job.job_id}  {job.state:<14} phases={len(job.phases)} "
            f"total={job.total_s:.3f}s cost={cost} gate={job.gate or '-'} "
            f"gates={gates} stuck={stuck}"
        )
        if job.caveats:
            lines.append("      caveats: " + ", ".join(job.caveats))
    lines.append("  caveats: " + ", ".join(collection.caveats))
    return "\n".join(lines)


__all__ = [
    "JobView",
    "JobsView",
    "PhaseView",
    "build_jobs_view",
    "render_jobs_view",
    "view_job",
]
