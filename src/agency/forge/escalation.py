"""Bounded escalation SLA for Forge jobs awaiting a human decision.

A job in ``AWAITING_HUMAN`` is a human gate, not a timeout to be silently
bypassed. This module measures how long the gate has been waiting and returns
a *suggestion* to escalate when a bounded SLA is exceeded. It never changes
state, never approves, and never invents a wait time when the timestamp is
missing or invalid — those cases carry honest caveats instead.

The interface is deliberately narrow: one policy value object, one result
object, one pure evaluator. Wiring into a watcher or CLI belongs to the
orchestrator wave; this module has no I/O and no dependency on orchestration.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_SLA_SECONDS = 24 * 60 * 60  # one day
MAX_SLA_SECONDS = 30 * 24 * 60 * 60  # hard upper bound on any configured SLA
AWAITING_HUMAN = "AWAITING_HUMAN"

_NEVER_APPROVE = "never_auto_approves"


class EscalationPolicy(BaseModel):
    """Immutable, bounded wait budget before escalation is suggested."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sla_seconds: int = Field(default=DEFAULT_SLA_SECONDS, ge=1, le=MAX_SLA_SECONDS)


DEFAULT_POLICY = EscalationPolicy()


class EscalationSubject(Protocol):
    """Read-only shape an escalation candidate must provide."""

    @property
    def job_id(self) -> str: ...

    @property
    def state(self) -> object: ...

    @property
    def updated_at(self) -> str: ...


class EscalationAssessment(BaseModel):
    """What was observed and what a human should do — never an approval."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    state: str
    age_seconds: float | None
    within_sla: bool | None
    should_escalate: bool
    suggestion: str
    caveats: tuple[str, ...] = (_NEVER_APPROVE,)

    def render(self) -> str:
        """One-line decision brief for a human, caveats included."""
        age = "unknown" if self.age_seconds is None else f"{self.age_seconds:.0f}s"
        within = "unknown" if self.within_sla is None else str(self.within_sla).lower()
        return (
            f"job={self.job_id} state={self.state} age={age} "
            f"within_sla={within} should_escalate={str(self.should_escalate).lower()} "
            f"caveats={','.join(self.caveats)} :: {self.suggestion}"
        )


def _state_value(state: object) -> str:
    """Accept both a JobState enum (``.value``) and a plain string."""
    return str(getattr(state, "value", state))


def _age_seconds(updated_at: str, now: datetime) -> float | None:
    """Age in seconds; None when missing, malformed or in the future."""
    if not updated_at or not isinstance(updated_at, str):
        return None
    try:
        parsed = datetime.fromisoformat(updated_at)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    age = (now - parsed).total_seconds()
    return age if age >= 0 else None


def evaluate_escalation(
    job: EscalationSubject,
    policy: EscalationPolicy = DEFAULT_POLICY,
    *,
    now: datetime | None = None,
) -> EscalationAssessment:
    """Assess one job against the SLA without ever approving it.

    ``now`` is injectable so callers and tests use a deterministic clock.
    Only an ``AWAITING_HUMAN`` job with a known, past-due wait time is
    suggested for escalation; every other case is reported with caveats.
    """
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)

    state = _state_value(job.state)
    caveats = [_NEVER_APPROVE]

    if state != AWAITING_HUMAN:
        caveats.append("not_awaiting_human")
        return EscalationAssessment(
            job_id=job.job_id,
            state=state,
            age_seconds=None,
            within_sla=None,
            should_escalate=False,
            suggestion=(
                f"Job {job.job_id} is {state}, not AWAITING_HUMAN; "
                "no escalation. Do not auto-approve."
            ),
            caveats=tuple(caveats),
        )

    age = _age_seconds(job.updated_at, current)
    if age is None:
        caveats.append("missing_updated_at" if not job.updated_at else "invalid_timestamp")
        return EscalationAssessment(
            job_id=job.job_id,
            state=state,
            age_seconds=None,
            within_sla=None,
            should_escalate=False,
            suggestion=(
                f"Wait time for job {job.job_id} is unknown; escalate to a named human "
                "to review manually. Do not auto-approve."
            ),
            caveats=tuple(caveats),
        )

    within = age <= policy.sla_seconds
    if not within:
        caveats.append("past_sla")
        suggestion = (
            f"Escalate job {job.job_id} to a named human owner: waited "
            f"{age:.0f}s beyond the {policy.sla_seconds}s SLA. Do not auto-approve."
        )
    else:
        suggestion = (
            f"Job {job.job_id} has waited {age:.0f}s of {policy.sla_seconds}s SLA; "
            "no escalation yet. Do not auto-approve."
        )

    return EscalationAssessment(
        job_id=job.job_id,
        state=state,
        age_seconds=age,
        within_sla=within,
        should_escalate=not within,
        suggestion=suggestion,
        caveats=tuple(caveats),
    )
