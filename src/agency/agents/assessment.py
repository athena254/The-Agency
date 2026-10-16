"""Immutable local diagnostic snapshots, not authorization or factual proof.

Only runtime callers build these records. Validation and byte binding do not
provide signatures, independent provenance, durable receipts or peer approval.
"""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from agency.agents.verifier import VerificationResult
from agency.risk.engine.models import RiskCategory


class AssessmentStatus(str, Enum):
    NOT_PERFORMED = "not_performed"
    PASSED = "passed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class _Snapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CheckAssessment(_Snapshot):
    criterion: Literal["completeness", "accuracy", "safety"]
    status: AssessmentStatus
    method: Literal["not_performed", "legacy_keyword_coverage/v1", "legacy_denylist/v1"]
    reason: Literal[
        "tool_path_unchecked", "isolated_path_unchecked", "execution_not_completed",
        "no_trusted_accuracy_oracle", "heuristic_passed", "heuristic_failed",
    ]
    score: float | None


class RiskAssessment(_Snapshot):
    status: Literal["not_assessed", "assessed"]
    method: Literal["not_performed", "legacy_risk_engine/v1"]
    category: RiskCategory | None
    finding_id: str | None


class SubtaskAssessment(_Snapshot):
    subtask_id: str
    path: Literal["classic", "tool", "isolated"]
    execution_status: Literal["completed", "failed", "cancelled", "timeout"]
    output_sha256: str
    verification: AssessmentStatus
    checks: tuple[CheckAssessment, ...]
    risk: RiskAssessment


class ExecutionAssessment(_Snapshot):
    schema_version: Literal["a2.v1"]
    task_id: str
    execution_status: Literal["completed", "failed", "cancelled", "timeout"]
    output_sha256: str
    verification: AssessmentStatus
    risk_status: Literal["not_assessed", "assessed", "partially_assessed"]
    independently_verified: Literal[False]
    subtasks: tuple[SubtaskAssessment, ...]


def render_output(value: Any) -> str:
    return "" if value is None else value if isinstance(value, str) else str(value)


def output_digest(value: Any) -> str:
    return hashlib.sha256(render_output(value).encode("utf-8", errors="strict")).hexdigest()


def unchecked_subtask(
    subtask_id: str, *, path: Literal["classic", "tool", "isolated"],
    execution_status: str, output: Any,
) -> SubtaskAssessment:
    reason: Literal["execution_not_completed", "tool_path_unchecked", "isolated_path_unchecked"]
    reason = "execution_not_completed"
    if execution_status == "completed":
        reason = "isolated_path_unchecked" if path == "isolated" else "tool_path_unchecked"
    return SubtaskAssessment(
        subtask_id=subtask_id, path=path, execution_status=execution_status,
        output_sha256=output_digest(output), verification=AssessmentStatus.NOT_PERFORMED,
        checks=tuple(CheckAssessment(
            criterion=criterion, status=AssessmentStatus.NOT_PERFORMED,
            method="not_performed", reason=reason, score=None,
        ) for criterion in ("completeness", "accuracy", "safety")),
        risk=RiskAssessment(status="not_assessed", method="not_performed", category=None, finding_id=None),
    )


def build_assessment(
    task_id: str, *, execution_status: str, output: Any,
    subtasks: tuple[SubtaskAssessment, ...],
) -> ExecutionAssessment:
    return ExecutionAssessment(
        schema_version="a2.v1", task_id=task_id, execution_status=execution_status,
        output_sha256=output_digest(output),
        verification=_verification(tuple(c for s in subtasks for c in s.checks)),
        risk_status=_risk_status(subtasks), independently_verified=False, subtasks=subtasks,
    )


def _verification(checks: tuple[CheckAssessment, ...]) -> AssessmentStatus:
    statuses = {c.status for c in checks}
    if AssessmentStatus.FAILED in statuses:
        return AssessmentStatus.FAILED
    if not statuses or statuses == {AssessmentStatus.NOT_PERFORMED}:
        return AssessmentStatus.NOT_PERFORMED
    if AssessmentStatus.NEEDS_REVIEW in statuses or AssessmentStatus.NOT_PERFORMED in statuses:
        return AssessmentStatus.NEEDS_REVIEW
    return AssessmentStatus.PASSED


def _risk_status(
    subtasks: tuple[SubtaskAssessment, ...],
) -> Literal["not_assessed", "assessed", "partially_assessed"]:
    assessed = sum(s.risk.status == "assessed" for s in subtasks)
    if not assessed:
        return "not_assessed"
    return "assessed" if assessed == len(subtasks) else "partially_assessed"


def classic_subtask(
    subtask_id: str, *, execution_status: str, output: Any,
    verification: VerificationResult, category: RiskCategory, finding_id: str,
) -> SubtaskAssessment:
    checks: list[CheckAssessment] = []
    for criterion in ("completeness", "accuracy", "safety"):
        if criterion == "accuracy":
            checks.append(CheckAssessment(
                criterion="accuracy", status=AssessmentStatus.NOT_PERFORMED,
                method="not_performed", reason="no_trusted_accuracy_oracle", score=None,
            ))
        else:
            outcome = verification.checks[criterion]
            checks.append(CheckAssessment(
                criterion=criterion,
                status=AssessmentStatus.PASSED if outcome.passed else AssessmentStatus.FAILED,
                method="legacy_keyword_coverage/v1" if criterion == "completeness" else "legacy_denylist/v1",
                reason="heuristic_passed" if outcome.passed else "heuristic_failed",
                score=outcome.score,
            ))
    return SubtaskAssessment(
        subtask_id=subtask_id, path="classic", execution_status=execution_status,
        output_sha256=output_digest(output), verification=_verification(tuple(checks)),
        checks=tuple(checks), risk=RiskAssessment(
            status="assessed", method="legacy_risk_engine/v1", category=category, finding_id=finding_id,
        ),
    )
