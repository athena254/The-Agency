"""Output validation for agent results.

:class:`AgentVerifier` runs three built-in checks — completeness, accuracy
(against ground truth), and safety (denylist + size bounds) — plus optional
external *multi-model* verifiers whose votes are aggregated by majority.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)

# External judge: ``(result_text, criteria_names) -> True/False`` (pass) or a
# ``float`` score in ``[0, 1]``. Sync callables; keep judges side-effect free.
VerifierFn = Callable[[str, list[str]], bool | float]

_DEFAULT_DENYLIST = (
    r"(?i)\b(api[_-]?key|secret|password|passwd| bearer [\w\-.~+/]+|-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    r"(?i)\b(delete\s+from|drop\s+table|rm\s+-rf\s+/)",
    r"(?i)\b(ignore\s+(all\s+)?previous\s+instructions|jailbreak|system\s+prompt\s+leak)\b",
)


class VerificationStatus(str, Enum):
    """Terminal verdict of a verification run."""

    PASSED = "passed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"

    def __str__(self) -> str:
        return self.value


class VerificationCriterion(BaseModel):
    """One named requirement a result is judged against."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(default="")
    weight: float = Field(default=1.0, gt=0)
    required: bool = Field(default=True)


class CheckOutcome(BaseModel):
    """Outcome of a single check (completeness / accuracy / safety / model)."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    passed: bool
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    details: str = Field(default="")


class VerificationResult(BaseModel):
    """Aggregated verdict across all checks and model judges."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    status: VerificationStatus
    checks: dict[str, CheckOutcome] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    verified_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def _as_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ("output", "text", "content", "result"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return str(result)
    return str(result)


class AgentVerifier:
    """Validates agent outputs with built-in checks and optional model judges.

    Parameters
    ----------
    verifiers:
        Extra judge callables for multi-model verification. Each returns a
        pass/fail vote or a ``[0, 1]`` score; the mean vote must clear
        ``majority_threshold``.
    majority_threshold:
        Fraction of weighted judge support required (default ``0.5``).
    safety_patterns:
        Extra regexes (in addition to the built-in denylist) that fail safety.
    pass_threshold:
        Weighted overall score required to pass (default ``0.7``).
    max_output_chars:
        Outputs longer than this fail safety (runaway guard).
    """

    def __init__(
        self,
        verifiers: list[VerifierFn] | None = None,
        *,
        majority_threshold: float = 0.5,
        safety_patterns: list[str] | None = None,
        pass_threshold: float = 0.7,
        max_output_chars: int = 100_000,
    ) -> None:
        if not 0.0 < majority_threshold <= 1.0:
            raise ValueError("majority_threshold must be in (0, 1].")
        if not 0.0 <= pass_threshold <= 1.0:
            raise ValueError("pass_threshold must be in [0, 1].")
        self._verifiers: list[VerifierFn] = list(verifiers or [])
        self._majority_threshold = majority_threshold
        self._pass_threshold = pass_threshold
        self._max_output_chars = max_output_chars
        self._safety_res = [re.compile(p) for p in (*_DEFAULT_DENYLIST, *(safety_patterns or []))]
        self._log = structlog.get_logger(__name__)

    # ------------------------------------------------------------------ #
    # Full verification
    # ------------------------------------------------------------------ #

    def verify(
        self,
        result: Any,
        criteria: list[VerificationCriterion | str] | None = None,
    ) -> VerificationResult:
        """Run every applicable check and aggregate into one verdict."""
        norms = self._normalize_criteria(criteria)
        names = [c.name for c in norms]
        text = _as_text(result)

        checks: dict[str, CheckOutcome] = {
            "completeness": self.check_completeness(result, criteria=norms),
            "safety": self.check_safety(result),
        }
        if isinstance(result, dict) and "ground_truth" in result:
            checks["accuracy"] = self.check_accuracy(result, result["ground_truth"])
        if self._verifiers:
            checks["multi_model"] = self._run_model_judges(text, names)

        weights = self._weights(norms, checks)
        total = sum(weights.values())
        score = sum(checks[k].score * w for k, w in weights.items()) / total if total else 0.0
        issues = [f"{name}: {check.details}" for name, check in checks.items() if not check.passed]
        required_failed = any(
            not check.passed for name, check in checks.items() if name != "multi_model"
        )
        if self._verifiers and not checks["multi_model"].passed:
            required_failed = True

        passed = (score >= self._pass_threshold) and not required_failed
        status = (
            VerificationStatus.PASSED
            if passed
            else VerificationStatus.FAILED
            if required_failed or score < self._pass_threshold / 2
            else VerificationStatus.NEEDS_REVIEW
        )
        self._log.info(
            "verifier.verdict", passed=passed, score=round(score, 3), status=status.value
        )
        return VerificationResult(
            passed=passed, score=score, status=status, checks=checks, issues=issues
        )

    # ------------------------------------------------------------------ #
    # Individual checks
    # ------------------------------------------------------------------ #

    def check_completeness(
        self,
        result: Any,
        *,
        criteria: list[VerificationCriterion] | None = None,
    ) -> CheckOutcome:
        """Ensure the result is non-empty and mentions each criterion."""
        text = _as_text(result).strip()
        if not text:
            return CheckOutcome(passed=False, score=0.0, details="result is empty.")
        if not criteria:
            return CheckOutcome(passed=True, score=1.0, details="non-empty output, no criteria.")
        lowered = text.lower()
        missing = [c.name for c in criteria if c.name.lower() not in lowered]
        if missing:
            coverage = 1.0 - len(missing) / len(criteria)
            return CheckOutcome(
                passed=False, score=coverage, details=f"missing criteria: {sorted(missing)}"
            )
        return CheckOutcome(passed=True, score=1.0, details=f"covers all {len(criteria)} criteria.")

    def check_accuracy(self, result: Any, ground_truth: Any) -> CheckOutcome:
        """Score textual similarity between result and ground truth.

        Uses :class:`difflib.SequenceMatcher` ratio: ``>= 0.85`` passes,
        ``>= 0.5`` is partial credit, below that fails.
        """
        text = _as_text(result).strip()
        truth = _as_text(ground_truth).strip()
        if not truth:
            return CheckOutcome(passed=True, score=1.0, details="no ground truth; skipped.")
        if not text:
            return CheckOutcome(passed=False, score=0.0, details="empty result.")
        ratio = difflib.SequenceMatcher(None, text.lower(), truth.lower()).ratio()
        if ratio >= 0.85:
            return CheckOutcome(passed=True, score=ratio, details=f"match ratio {ratio:.2f}.")
        if ratio >= 0.5:
            return CheckOutcome(
                passed=False, score=ratio, details=f"partial match {ratio:.2f}; needs review."
            )
        return CheckOutcome(passed=False, score=ratio, details=f"mismatch {ratio:.2f}.")

    def check_safety(self, result: Any) -> CheckOutcome:
        """Fail outputs matching the denylist or exceeding size bounds."""
        text = _as_text(result)
        if len(text) > self._max_output_chars:
            return CheckOutcome(
                passed=False,
                score=0.0,
                details=f"output {len(text)} chars exceeds {self._max_output_chars}.",
            )
        hits = sorted({rx.pattern for rx in self._safety_res if rx.search(text)})
        if hits:
            return CheckOutcome(passed=False, score=0.0, details=f"denylist hit: {hits[0][:80]}")
        return CheckOutcome(passed=True, score=1.0, details="no safety issues.")

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _run_model_judges(self, text: str, criteria_names: list[str]) -> CheckOutcome:
        votes: list[float] = []
        for index, judge in enumerate(self._verifiers):
            try:
                vote = judge(text, criteria_names)
                votes.append(1.0 if vote is True else 0.0 if vote is False else float(vote))
            except Exception as exc:  # noqa: BLE001 — one bad judge must not sink verification
                self._log.warning("verifier.judge_error", index=index, error=str(exc))
        if not votes:
            return CheckOutcome(passed=False, score=0.0, details="all model judges errored.")
        support = sum(votes) / len(votes)
        passed = support >= self._majority_threshold
        return CheckOutcome(
            passed=passed,
            score=max(0.0, min(1.0, support)),
            details=f"{support:.2f} judge support.",
        )

    @staticmethod
    def _normalize_criteria(
        criteria: list[VerificationCriterion | str] | None,
    ) -> list[VerificationCriterion]:
        norms: list[VerificationCriterion] = []
        for entry in criteria or []:
            if isinstance(entry, VerificationCriterion):
                norms.append(entry)
            else:
                name = str(entry).strip()
                if name:
                    norms.append(VerificationCriterion(name=name))
        return norms

    @staticmethod
    def _weights(
        criteria: list[VerificationCriterion], checks: dict[str, CheckOutcome]
    ) -> dict[str, float]:
        weights = {"completeness": 1.0, "safety": 2.0, "accuracy": 1.5, "multi_model": 1.5}
        if criteria:
            weights["completeness"] = max(c.weight for c in criteria)
        return {name: weights.get(name, 1.0) for name in checks}


__all__ = [
    "AgentVerifier",
    "CheckOutcome",
    "VerificationCriterion",
    "VerificationResult",
    "VerificationStatus",
]
