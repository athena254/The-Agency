"""Forge ECC critic quorum: two-family review with escalation, never self-approval.

The module is a deep seam: callers supply critic adapters (dependencies, not
network clients) and receive one immutable report. It bounds the diff and the
retry count, discards self-approvals, and refuses to auto-approve on any
disagreement, abstention or unavailable critic. It never authorizes a merge.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)

Verdict = Literal["APPROVE", "REJECT", "UNCERTAIN"]
Outcome = Literal["APPROVE", "REJECT", "ESCALATE", "UNCERTAIN"]

QUORUM_CAVEATS: tuple[str, ...] = (
    "not_a_human_gate",
    "does_not_authorize_merge",
    "no_network_calls",
    "critics_supplied_by_caller",
)

_VALID_VERDICTS: frozenset[str] = frozenset({"APPROVE", "REJECT", "UNCERTAIN"})


@dataclass(frozen=True)
class CriticAdapter:
    """One reviewer at the seam: identity, model family, bounded review call."""

    critic_id: str
    model_family: str
    review: Callable[[str], str]


CriticLike = CriticAdapter | Mapping[str, object]


class CriticResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    critic_id: str = Field(min_length=1)
    model_family: str = Field(min_length=1)
    verdict: Verdict
    attempts: int = Field(ge=0)
    exhausted: bool = False
    invalid: bool = False


class QuorumReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(min_length=1)
    outcome: Outcome
    results: tuple[CriticResult, ...] = ()
    reasons: tuple[str, ...] = ()
    caveats: tuple[str, ...] = QUORUM_CAVEATS
    note: str = (
        "Bounded two-family critic quorum; never self-approves and never authorizes a merge."
    )


def _nonempty(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _as_verdict(value: object) -> Verdict:
    if isinstance(value, str) and value in _VALID_VERDICTS:
        return cast(Verdict, value)
    raise ValueError("critic returned an invalid verdict")


def _static_review(fixed: Verdict) -> Callable[[str], Verdict]:
    def review(_diff: str) -> Verdict:
        return fixed

    return review


def _coerce_critic(raw: CriticLike) -> CriticAdapter:
    if isinstance(raw, CriticAdapter):
        return raw

    critic_id = _nonempty(raw.get("critic_id"), "critic_id")
    family = _nonempty(raw.get("model_family"), "model_family")
    review_raw = raw.get("review")
    verdict_raw = raw.get("verdict")

    if callable(review_raw):
        call = cast(Callable[[str], object], review_raw)
    elif isinstance(verdict_raw, str):
        call = _static_review(_as_verdict(verdict_raw))
    else:
        raise TypeError("critic needs a review callable or a verdict")

    def review(diff: str) -> str:
        return _as_verdict(call(diff))

    return CriticAdapter(critic_id=critic_id, model_family=family, review=review)


def run_quorum(
    task_id: str,
    diff: str,
    critics: Sequence[CriticLike],
    author_id: str = "",
    max_retries: int = 2,
    max_diff_chars: int = 20_000,
) -> QuorumReport:
    """Run a bounded, two-family critic quorum and return an immutable report."""
    _nonempty(task_id, "task_id")
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")
    if max_diff_chars < 1:
        raise ValueError("max_diff_chars must be positive")

    bounded = diff[:max_diff_chars]
    results: list[CriticResult] = []
    reasons: list[str] = []

    for raw in critics:
        adapter = _coerce_critic(raw)
        attempts = 0
        verdict: Verdict = "UNCERTAIN"
        exhausted = False
        for _ in range(max_retries + 1):
            attempts += 1
            try:
                verdict = _as_verdict(adapter.review(bounded))
                break
            except Exception:  # noqa: BLE001 - any adapter failure is a retryable miss
                logger.warning(
                    "forge.quorum.critic_failed", critic_id=adapter.critic_id, attempt=attempts
                )
                continue
        else:
            exhausted = True
            verdict = "UNCERTAIN"

        invalid = bool(author_id) and adapter.critic_id == author_id and verdict == "APPROVE"
        effective: Verdict = "UNCERTAIN" if invalid else verdict
        results.append(
            CriticResult(
                critic_id=adapter.critic_id,
                model_family=adapter.model_family,
                verdict=effective,
                attempts=attempts,
                exhausted=exhausted,
                invalid=invalid,
            )
        )
        if invalid:
            reasons.append("self_approval_rejected")
        if exhausted:
            reasons.append("critic_unavailable")

    families = {result.model_family for result in results}
    if len(results) < 2:
        outcome: Outcome = "ESCALATE"
        reasons.append("quorum_requires_two_critics")
    elif len(families) < 2:
        outcome = "ESCALATE"
        reasons.append("insufficient_model_family_diversity")
    elif all(result.exhausted for result in results):
        outcome = "UNCERTAIN"
    elif any(result.invalid for result in results):
        outcome = "ESCALATE"
    elif any(result.verdict == "UNCERTAIN" for result in results):
        outcome = "ESCALATE"
        reasons.append("critic_abstained")
    elif all(result.verdict == "APPROVE" for result in results):
        outcome = "APPROVE"
    elif all(result.verdict == "REJECT" for result in results):
        outcome = "REJECT"
    else:
        outcome = "ESCALATE"
        reasons.append("critic_disagreement")

    return QuorumReport(
        task_id=task_id,
        outcome=outcome,
        results=tuple(results),
        reasons=tuple(dict.fromkeys(reasons)),
    )
