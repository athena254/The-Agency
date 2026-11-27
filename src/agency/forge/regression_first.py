"""Forge regression-first gate: a fix is trusted only after a captured failure.

``RegressionProof`` is the immutable evidence that a specific test failed for
a specific bug before a fix was written. ``check_regression_first`` is the
fail-closed gate: without a non-blank failure record and a non-zero exit code
it refuses to trust the fix, and it can enforce that the proof predates the
fix.

Pure functions only: no subprocess, no network, no filesystem, no side
effects. Callers supply already-captured evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_PROOF_OUTPUT_CHARS = 64 * 1024
_IDENTITY_FIELDS = ("bug_ref", "test_command", "failing_test")


class RegressionProof(BaseModel):
    """Captured evidence that a test failed before a fix was attempted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bug_ref: str = Field(min_length=1)
    test_command: str = Field(min_length=1)
    failing_test: str = Field(min_length=1)
    failure_output: str = ""
    returncode: int | None = None
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RegressionVerdict(BaseModel):
    """Result of gating a fix on its regression proof."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gate: Literal["PASS", "FAIL"]
    findings: tuple[str, ...] = ()
    proof: RegressionProof | None = None

    @property
    def ok(self) -> bool:
        return self.gate == "PASS"

    def __bool__(self) -> bool:
        return self.ok


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def check_regression_first(
    proof: RegressionProof | Mapping[str, Any],
    *,
    fix_time: datetime | None = None,
) -> RegressionVerdict:
    """Trust a fix only when backed by captured, pre-fix failing-test evidence.

    ``fix_time``, when supplied, must be at or after ``captured_at``; otherwise
    the proof is rejected as having been recorded after the fix. Both naive and
    aware datetimes are rejected for comparison to avoid silent timezone skew.
    """
    try:
        captured = (
            proof if isinstance(proof, RegressionProof) else RegressionProof.model_validate(proof)
        )
    except (ValidationError, TypeError, ValueError):
        return RegressionVerdict(gate="FAIL", findings=("invalid_proof",))

    findings: list[str] = []

    if any(not _nonblank(getattr(captured, name, None)) for name in _IDENTITY_FIELDS):
        findings.append("blank_field")

    if captured.returncode is None or captured.returncode == 0:
        findings.append("no_failure_returncode")

    if not _nonblank(captured.failure_output):
        findings.append("no_failure_output")
    elif len(captured.failure_output) > MAX_PROOF_OUTPUT_CHARS:
        findings.append("output_too_large")

    if fix_time is not None:
        if captured.captured_at.tzinfo is None or fix_time.tzinfo is None:
            findings.append("ambiguous_time")
        elif captured.captured_at > fix_time:
            findings.append("proof_after_fix")

    ordered = tuple(dict.fromkeys(findings))
    return RegressionVerdict(
        gate="FAIL" if ordered else "PASS",
        findings=ordered,
        proof=captured if not ordered else None,
    )
