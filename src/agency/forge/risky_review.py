"""Automatic risky review: one bounded call that composes the existing seams.

A risky job must clear both a two-family critic quorum and a human-owned gold
set. This module is a deep seam: callers inject the critic and run adapters and
receive a single immutable report. It never authorizes a merge, makes no
network calls, and fails closed whenever evidence is missing or ambiguous.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agency.forge.gold_set import GoldSet, GoldSetReport, RunAdapter, evaluate_gold_set
from agency.forge.quorum import CriticLike, QuorumReport, run_quorum

ReviewDecision = Literal["PASS", "BLOCK", "UNCERTAIN"]

RISKY_REVIEW_CAVEATS: tuple[str, ...] = (
    "not_a_human_gate",
    "does_not_authorize_merge",
    "no_network_calls",
    "adapters_supplied_by_caller",
    "fail_closed",
    "gold_set_human_owned_best_effort_redaction",
)


class RiskyReviewReport(BaseModel):
    """Immutable combined result of the two automatic risky-review gates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str = Field(min_length=1)
    decision: ReviewDecision
    allowed: bool
    quorum: QuorumReport | None = None
    gold: GoldSetReport | None = None
    reasons: tuple[str, ...] = ()
    caveats: tuple[str, ...] = RISKY_REVIEW_CAVEATS
    note: str = (
        "Bounded automatic review of a risky job; both gates must pass, "
        "but this never authorizes a merge."
    )

    @classmethod
    def build(
        cls,
        job_id: str,
        quorum: QuorumReport | None,
        gold: GoldSetReport | None,
        reasons: tuple[str, ...],
    ) -> RiskyReviewReport:
        quorum_ok = quorum is not None and quorum.outcome == "APPROVE"
        gold_ok = gold is not None and gold.gate == "PASS"

        definitive_failure = (quorum is not None and quorum.outcome == "REJECT") or (
            gold is not None and gold.gate == "FAIL"
        )

        decision: ReviewDecision
        if quorum_ok and gold_ok:
            decision = "PASS"
        elif definitive_failure:
            decision = "BLOCK"
        else:
            decision = "UNCERTAIN"

        return cls(
            job_id=job_id,
            decision=decision,
            allowed=decision == "PASS",
            quorum=quorum,
            gold=gold,
            reasons=tuple(dict.fromkeys(reasons)),
        )


def apply_risky_review(
    job_id: str,
    diff: str,
    *,
    critics: Sequence[CriticLike],
    gold_set: GoldSet | None = None,
    run: RunAdapter | None = None,
    author_id: str = "",
    max_retries: int = 2,
    max_diff_chars: int = 20_000,
    max_output_chars: int = 20_000,
) -> RiskyReviewReport:
    """Run quorum and gold-set grading for a risky job and return one report.

    Both underlying calls are bounded and injected. Missing evidence (no gold
    set, no run adapter) or a non-approving outcome downgrades the result to
    ``UNCERTAIN``/``BLOCK`` so the caller fails closed.
    """
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("job_id must be a non-empty string")

    reasons: list[str] = []

    quorum = run_quorum(
        job_id,
        diff,
        critics,
        author_id=author_id,
        max_retries=max_retries,
        max_diff_chars=max_diff_chars,
    )
    reasons.extend(quorum.reasons)

    gold: GoldSetReport | None = None
    if gold_set is None:
        reasons.append("gold_set_missing")
    elif run is None:
        reasons.append("run_adapter_missing")
    else:
        gold = evaluate_gold_set(gold_set, run, max_output_chars=max_output_chars)

    return RiskyReviewReport.build(job_id, quorum, gold, tuple(reasons))


__all__ = [
    "RISKY_REVIEW_CAVEATS",
    "ReviewDecision",
    "RiskyReviewReport",
    "apply_risky_review",
]
