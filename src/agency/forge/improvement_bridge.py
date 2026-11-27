"""Guarded bridge from CI-failure candidates to improvement proposals.

This slice closes the ``forge learn`` half of self-improvement. It consumes the
capture-only candidate jobs produced by :mod:`agency.forge.ci_feedback`,
deduplicates them on their stable keys, and emits proposed jobs *only* when an
explicit, named human approver is supplied.

The bridge is deliberately inert: it returns a :class:`BridgeReport` and has no
dispatch, merge, push, subprocess, network, or filesystem side effects. It never
imports orchestration. The parent wires it into ``forge learn`` like this::

    from agency.forge.ci_feedback import ingest_ci_failures
    from agency.forge.improvement_bridge import bridge_failures

    candidates = ingest_ci_failures(payload, seen_keys=prior_keys).candidates
    report = bridge_failures(candidates, approved_by=approver, seen_keys=prior_keys)
    # print report.candidates / report.proposals; persist report.keys for next run

Proposals are still capture-only: the caller dispatches any accepted job through
the existing gated tools, never this module.
"""

from __future__ import annotations

from collections.abc import Collection

from pydantic import BaseModel, ConfigDict, Field

from agency.forge.ci_feedback import (
    CAVEATS,
    ActionProposal,
    CandidateJob,
    authorize_candidate,
    is_valid_approver,
)

# Hard ceiling so a hostile or pathological input cannot grow the result without
# bound. Extra deduplicated candidates past this cap are reported as truncated.
MAX_BRIDGED_JOBS = 100

_HUMAN_APPROVAL_REQUIRED = "human_approval_required"


class BridgeReport(BaseModel):
    """Bounded, side-effect-free outcome of one bridging pass."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gate: str = "BLOCKED"
    received: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    truncated: int = Field(ge=0)
    approved: bool = False
    approved_by: str = Field(default="", max_length=120)
    candidates: tuple[CandidateJob, ...] = ()
    proposals: tuple[ActionProposal, ...] = ()
    keys: tuple[str, ...] = ()
    blocked_reasons: tuple[str, ...] = ()
    caveats: tuple[str, ...] = CAVEATS


def _dedupe(
    candidates: Collection[CandidateJob], seen_keys: Collection[str]
) -> tuple[tuple[CandidateJob, ...], int]:
    """Drop repeat keys and prior-run keys, preserving first-seen order."""
    already = {str(key) for key in seen_keys}
    unique: list[CandidateJob] = []
    duplicates = 0
    for candidate in candidates:
        key = str(candidate.key)
        if not key or key in already:
            duplicates += 1
            continue
        already.add(key)
        unique.append(candidate)
    return tuple(unique), duplicates


def bridge_failures(
    candidates: Collection[CandidateJob],
    *,
    approved_by: str = "",
    seen_keys: Collection[str] = (),
) -> BridgeReport:
    """Turn ci_feedback candidates into proposals under a named human approver.

    Deduping is on the candidates' stable ``key`` and is idempotent across runs
    when ``seen_keys`` from a prior :class:`BridgeReport` is passed back. Without
    a valid named human approver nothing is proposed: candidates are still
    captured and the report is ``BLOCKED`` with ``human_approval_required``.
    This function performs no I/O and creates no side effects outside its result.
    """
    received = len(candidates)
    unique, duplicates = _dedupe(candidates, seen_keys)
    kept = unique[:MAX_BRIDGED_JOBS]
    truncated = len(unique) - len(kept)

    valid = is_valid_approver(approved_by)
    approver = str(approved_by).strip()[:120] if valid else ""
    proposals: tuple[ActionProposal, ...] = (
        tuple(authorize_candidate(candidate, approved_by=approver) for candidate in kept)
        if valid
        else ()
    )
    blocked = (_HUMAN_APPROVAL_REQUIRED,) if kept and not valid else ()

    return BridgeReport(
        gate="BLOCKED" if blocked else "PASS",
        received=received,
        duplicates=duplicates,
        truncated=truncated,
        approved=valid,
        approved_by=approver,
        candidates=kept,
        proposals=proposals,
        keys=tuple(candidate.key for candidate in kept),
        blocked_reasons=blocked,
    )


__all__ = [
    "MAX_BRIDGED_JOBS",
    "BridgeReport",
    "bridge_failures",
]
