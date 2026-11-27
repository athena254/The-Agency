"""Human-only approval surface for Forge jobs awaiting a decision.

The module records decisions; it never merges, releases or deploys. Those stay
behind the existing gated tools.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from agency.forge.human_gates import required_human_gates
from agency.forge.orchestration import JobState

if TYPE_CHECKING:
    from agency.forge.orchestration import BlacksmithOps, ForgeJob

_NON_HUMAN = frozenset({"agent", "forge", "auto", "system", "cli", "bot", "none"})
MAX_NOTE_CHARS = 1000


class ApprovalError(Exception):
    """Raised when a decision is not allowed."""


class ApprovalBrief(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    title: str
    state: str
    gate: str = ""
    report_id: str = ""
    changed_paths: tuple[str, ...] = ()
    repo: str = ""
    updated_at: str = ""
    caveats: tuple[str, ...] = ("not_a_human_review", "no_auto_merge")
    required_human_gates: tuple[str, ...] = ()


class ApprovalRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    approver: str
    decision: str
    at: str
    note: str = Field(default="", max_length=MAX_NOTE_CHARS)
    state_after: str
    caveats: tuple[str, ...] = ("no_auto_merge", "no_release_authorization")


def _brief(job: ForgeJob) -> ApprovalBrief:
    return ApprovalBrief(
        job_id=job.job_id,
        title=job.title,
        state=job.state.value,
        gate=job.gate,
        report_id=job.report_id,
        changed_paths=job.changed_paths,
        repo=job.repo,
        updated_at=job.updated_at,
        required_human_gates=required_human_gates(job.changed_paths),
    )


def list_briefs(ops: BlacksmithOps) -> tuple[ApprovalBrief, ...]:
    return tuple(_brief(job) for job in ops.awaiting_human())


def decide(
    ops: BlacksmithOps,
    job_id: str,
    approver: str,
    decision: str,
    *,
    note: str = "",
) -> ApprovalRecord:
    approver = str(approver).strip()
    decision = str(decision).strip().lower()
    if not approver or approver.lower() in _NON_HUMAN:
        raise ApprovalError("a named human approver is required")
    if decision not in ("approve", "reject"):
        raise ApprovalError("decision must be approve or reject")
    job = ops.get(str(job_id).strip())
    if job.state is not JobState.AWAITING_HUMAN:
        raise ApprovalError(f"job is not awaiting a human decision: {job.state.value}")

    at = datetime.now(UTC).isoformat()
    bounded_note = str(note).strip()[:MAX_NOTE_CHARS]
    gates = required_human_gates(job.changed_paths)
    gate_note = f"human_gates={','.join(gates)}" if gates else "human_gates=none"
    detail = f"approver={approver}; {gate_note}; note={bounded_note}"[:MAX_NOTE_CHARS]
    if decision == "approve":
        # Keep the state as AWAITING_HUMAN until a gated ship actually runs.
        ops._event(ops.get(job.job_id), "human_approval", job.state.value, job.state.value, detail)
        state_after = JobState.AWAITING_HUMAN.value
    else:
        updated = ops._transition(job, JobState.BLOCKED, "human_rejection", detail)
        state_after = updated.state.value
    return ApprovalRecord(
        job_id=job.job_id,
        approver=approver,
        decision=decision,
        at=at,
        note=bounded_note,
        state_after=state_after,
        caveats=(
            "no_auto_merge",
            "no_release_authorization",
            *[f"gate_reviewed:{g}" for g in gates],
        ),
    )
