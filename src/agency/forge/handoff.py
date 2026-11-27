"""Forge provenance: a fail-closed handoff envelope between orchestrator roles.

An envelope is an immutable, self-contained record of *why* a task moved from
one role to another: the spec it implements, the artifacts and acceptance
criteria in scope, the budget, who produced it, and the evidence gathered.
Validation is fail-closed: any missing or ambiguous provenance yields a FAIL
verdict, never a silent pass.

Capture only. This module performs no I/O, schedules nothing, and does not
authorize merge, release, or deploy.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

RiskClass = Literal["low", "medium", "high", "critical"]
HandoffStatus = Literal["draft", "ready", "accepted", "rejected"]

MAX_BUDGET_KEYS = 16
_REQUIRED_TEXT = ("task_id", "from_", "to", "spec_ref", "producer")


class HandoffEnvelope(BaseModel):
    """Immutable provenance record attached to a role-to-role handoff."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    task_id: str = Field(min_length=1)
    parent_id: str | None = None
    from_: str = Field(min_length=1, alias="from")
    to: str = Field(min_length=1)
    spec_ref: str = Field(min_length=1)
    risk_class: RiskClass = "medium"
    artifacts: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    budget: dict[str, int] = Field(default_factory=dict)
    producer: str = Field(min_length=1)
    evidence: tuple[str, ...] = ()
    status: HandoffStatus = "draft"


class HandoffValidation(BaseModel):
    """Result of checking a handoff: gate, stable finding codes, validated value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gate: Literal["PASS", "FAIL"]
    findings: tuple[str, ...] = ()
    envelope: HandoffEnvelope | None = None

    @property
    def ok(self) -> bool:
        return self.gate == "PASS"

    def __bool__(self) -> bool:
        return self.ok


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _entries_nonblank(values: tuple[str, ...]) -> bool:
    return all(_nonblank(value) for value in values)


def _budget_valid(budget: dict[str, int]) -> bool:
    if not isinstance(budget, dict) or len(budget) > MAX_BUDGET_KEYS:
        return False
    for key, value in budget.items():
        if not _nonblank(key):
            return False
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return False
    return True


def make_handoff(
    *,
    task_id: str,
    from_: str,
    to: str,
    spec_ref: str,
    producer: str,
    parent_id: str | None = None,
    risk_class: RiskClass = "medium",
    artifacts: Iterable[str] = (),
    acceptance: Iterable[str] = (),
    budget: Mapping[str, int] | None = None,
    evidence: Iterable[str] = (),
    status: HandoffStatus = "draft",
) -> HandoffEnvelope:
    """Build an envelope from small inputs; normalize iterables, no side effects."""
    # Mypy generates __init__ from the alias "from" and cannot see populate_by_name.
    return HandoffEnvelope(  # type: ignore[call-arg]
        task_id=task_id,
        parent_id=parent_id,
        from_=from_,
        to=to,
        spec_ref=spec_ref,
        risk_class=risk_class,
        artifacts=tuple(artifacts),
        acceptance=tuple(acceptance),
        budget=dict(budget or {}),
        producer=producer,
        evidence=tuple(evidence),
        status=status,
    )


def validate_handoff(envelope: HandoffEnvelope | Mapping[str, Any]) -> HandoffValidation:
    """Validate provenance fail-closed; anything malformed or missing fails.

    Accepts an envelope or a raw mapping so callers can gate untrusted input.
    Returns a result (never raises for bad input) and always records why it
    failed. A PASS result carries the validated envelope for downstream use.
    """
    try:
        env = (
            envelope
            if isinstance(envelope, HandoffEnvelope)
            else HandoffEnvelope.model_validate(envelope)
        )
    except (ValidationError, TypeError, ValueError):
        return HandoffValidation(gate="FAIL", findings=("invalid_envelope",))

    findings: list[str] = []

    if any(not _nonblank(getattr(env, name, None)) for name in _REQUIRED_TEXT):
        findings.append("blank_field")
    if env.parent_id is not None and not _nonblank(env.parent_id):
        findings.append("blank_field")

    if not env.acceptance:
        findings.append("no_acceptance")
    elif not _entries_nonblank(env.acceptance):
        findings.append("blank_entry")

    if not env.artifacts:
        findings.append("no_artifacts")
    elif not _entries_nonblank(env.artifacts):
        findings.append("blank_entry")

    if not env.evidence:
        if env.risk_class != "low" or env.status == "accepted":
            findings.append("no_evidence")
    elif not _entries_nonblank(env.evidence):
        findings.append("blank_entry")

    if _nonblank(env.from_) and env.from_ == env.to:
        findings.append("self_handoff")
    if env.parent_id is not None and env.parent_id == env.task_id:
        findings.append("self_parent")
    if not _budget_valid(env.budget):
        findings.append("invalid_budget")

    ordered = tuple(dict.fromkeys(findings))
    return HandoffValidation(
        gate="FAIL" if ordered else "PASS",
        findings=ordered,
        envelope=env if not ordered else None,
    )
