"""Immutable local diagnostic snapshots, not authorization or factual proof.

Only runtime callers build these records. Validation and byte binding do not
provide signatures, independent provenance, durable receipts or peer approval.
"""

from __future__ import annotations

import hashlib
import re
from enum import Enum
from typing import Annotated, Any, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from agency.agents.verifier import VerificationResult
from agency.risk.engine.models import RiskCategory

_Criterion = Literal["completeness", "accuracy", "safety"]
_Terminal = Literal["completed", "failed", "cancelled", "timeout"]
_CRITERIA: tuple[_Criterion, ...] = ("completeness", "accuracy", "safety")
_Id = Annotated[StrictStr, Field(min_length=1)]
_Score = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]


class AssessmentStatus(str, Enum):
    NOT_PERFORMED = "not_performed"
    PASSED = "passed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class _Snapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")


class CheckAssessment(_Snapshot):
    criterion: _Criterion
    status: AssessmentStatus
    method: Literal["not_performed", "legacy_keyword_coverage/v1", "legacy_denylist/v1"]
    reason: Literal[
        "tool_path_unchecked",
        "isolated_path_unchecked",
        "execution_not_completed",
        "no_trusted_accuracy_oracle",
        "heuristic_passed",
        "heuristic_failed",
    ]
    score: _Score | None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.status is AssessmentStatus.NOT_PERFORMED:
            if (
                self.method != "not_performed"
                or self.score is not None
                or self.reason in ("heuristic_passed", "heuristic_failed")
            ):
                raise ValueError(
                    "unperformed check cannot claim a method, score or heuristic verdict"
                )
            if self.reason == "no_trusted_accuracy_oracle" and self.criterion != "accuracy":
                raise ValueError("only accuracy omits a trusted oracle")
        else:
            method = {
                "completeness": "legacy_keyword_coverage/v1",
                "safety": "legacy_denylist/v1",
            }.get(self.criterion)
            if method is None or self.method != method or self.score is None:
                raise ValueError("performed check must identify its actual heuristic")
            if self.status not in (AssessmentStatus.PASSED, AssessmentStatus.FAILED):
                raise ValueError("heuristics report passed or failed")
            reason = (
                "heuristic_passed" if self.status is AssessmentStatus.PASSED else "heuristic_failed"
            )
            if self.reason != reason:
                raise ValueError("heuristic reason contradicts status")
            passed = self.status is AssessmentStatus.PASSED
            if (passed and self.score != 1.0) or (not passed and self.score == 1.0):
                raise ValueError("score contradicts legacy coverage or denylist verdict")
            if self.criterion == "safety" and not passed and self.score != 0.0:
                raise ValueError("failed legacy denylist score must be zero")
        return self


class RiskAssessment(_Snapshot):
    status: Literal["not_assessed", "assessed"]
    method: Literal["not_performed", "legacy_risk_engine/v1"]
    category: RiskCategory | None
    finding_id: _Id | None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.status == "not_assessed":
            if (
                self.method != "not_performed"
                or self.category is not None
                or self.finding_id is not None
            ):
                raise ValueError("unassessed risk cannot claim results")
        elif (
            self.method != "legacy_risk_engine/v1"
            or self.category is None
            or self.finding_id is None
        ):
            raise ValueError("assessed risk requires category and actual finding ID")
        return self


class _Artifact(_Snapshot):
    execution_status: _Terminal
    output_sha256: StrictStr

    @field_validator("output_sha256")
    @classmethod
    def _digest(cls, value: str) -> str:
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("digest must be exactly 64 lowercase hex characters")
        return value


class SubtaskAssessment(_Artifact):
    subtask_id: _Id
    path: Literal["classic", "tool", "isolated"]
    verification: AssessmentStatus
    checks: tuple[CheckAssessment, ...]
    risk: RiskAssessment

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if tuple(c.criterion for c in self.checks) != _CRITERIA:
            raise ValueError("all three criteria must occur once in order")
        if self.verification != _verification(self.checks):
            raise ValueError("verification contradicts actual checks")
        if self.path != "classic" and self.subtask_id != (
            "tool_loop" if self.path == "tool" else "isolated"
        ):
            raise ValueError("tool and isolated paths have fixed unit IDs")
        if self.path == "classic" and self.execution_status == "completed":
            if self.risk.status != "assessed":
                raise ValueError("completed classic execution requires actual risk assessment")
            for check in self.checks:
                if check.criterion == "accuracy":
                    if check.reason != "no_trusted_accuracy_oracle":
                        raise ValueError("A2 has no trusted accuracy oracle")
                elif check.status not in (AssessmentStatus.PASSED, AssessmentStatus.FAILED):
                    raise ValueError("completed classic execution requires actual heuristic checks")
        else:
            reason = _omission(self.path, self.execution_status)
            if self.risk.status != "not_assessed" or any(
                c.status is not AssessmentStatus.NOT_PERFORMED or c.reason != reason
                for c in self.checks
            ):
                raise ValueError("unchecked execution cannot claim performed checks or risk")
        return self


class ExecutionAssessment(_Artifact):
    schema_version: Literal["a2.v1"]
    task_id: _Id
    verification: AssessmentStatus
    risk_status: Literal["not_assessed", "assessed", "partially_assessed"]
    independently_verified: Literal[False]
    subtasks: tuple[SubtaskAssessment, ...]

    @field_validator("independently_verified", mode="before")
    @classmethod
    def _no_independent_verification(cls, value: Any) -> Any:
        if value is not False:
            raise ValueError("A2 never provides independent verification")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not self.subtasks and self.execution_status != "failed":
            raise ValueError("only failed zero-work execution can have no subtasks")
        if self.execution_status == "completed" and any(
            s.execution_status != "completed" for s in self.subtasks
        ):
            raise ValueError("aggregate completion cannot conceal a failed unit")
        if self.verification != _verification(tuple(c for s in self.subtasks for c in s.checks)):
            raise ValueError("aggregate verification contradicts actual checks")
        if self.risk_status != _risk_status(self.subtasks):
            raise ValueError("aggregate risk contradicts coverage")
        return self


def render_output(value: Any) -> str:
    return "" if value is None else value if isinstance(value, str) else str(value)


def output_digest(value: Any) -> str:
    return hashlib.sha256(render_output(value).encode("utf-8", errors="strict")).hexdigest()


def unchecked_subtask(
    subtask_id: str,
    *,
    path: Literal["classic", "tool", "isolated"],
    execution_status: str,
    output: Any,
) -> SubtaskAssessment:
    if execution_status == "completed" and not render_output(output).strip():
        raise ValueError("blank output cannot complete execution")
    reason = _omission(path, execution_status)
    return SubtaskAssessment(
        subtask_id=subtask_id,
        path=path,
        execution_status=_terminal(execution_status),
        output_sha256=output_digest(output),
        verification=AssessmentStatus.NOT_PERFORMED,
        checks=tuple(
            CheckAssessment(
                criterion=criterion,
                status=AssessmentStatus.NOT_PERFORMED,
                method="not_performed",
                reason=reason,
                score=None,
            )
            for criterion in _CRITERIA
        ),
        risk=RiskAssessment(
            status="not_assessed", method="not_performed", category=None, finding_id=None
        ),
    )


def build_assessment(
    task_id: str,
    *,
    execution_status: str,
    output: Any,
    subtasks: tuple[SubtaskAssessment, ...],
) -> ExecutionAssessment:
    if execution_status == "completed" and not render_output(output).strip():
        raise ValueError("blank output cannot complete execution")
    return ExecutionAssessment(
        schema_version="a2.v1",
        task_id=task_id,
        execution_status=_terminal(execution_status),
        output_sha256=output_digest(output),
        verification=_verification(tuple(c for s in subtasks for c in s.checks)),
        risk_status=_risk_status(subtasks),
        independently_verified=False,
        subtasks=subtasks,
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
    subtask_id: str,
    *,
    execution_status: str,
    output: Any,
    verification: VerificationResult,
    category: RiskCategory,
    finding_id: str,
) -> SubtaskAssessment:
    if execution_status != "completed" or not render_output(output).strip():
        raise ValueError("classic checks require completed nonblank execution")
    checks: list[CheckAssessment] = []
    for criterion in _CRITERIA:
        if criterion == "accuracy":
            checks.append(
                CheckAssessment(
                    criterion="accuracy",
                    status=AssessmentStatus.NOT_PERFORMED,
                    method="not_performed",
                    reason="no_trusted_accuracy_oracle",
                    score=None,
                )
            )
        else:
            outcome = verification.checks[criterion]
            checks.append(
                CheckAssessment(
                    criterion=criterion,
                    status=AssessmentStatus.PASSED if outcome.passed else AssessmentStatus.FAILED,
                    method="legacy_keyword_coverage/v1"
                    if criterion == "completeness"
                    else "legacy_denylist/v1",
                    reason="heuristic_passed" if outcome.passed else "heuristic_failed",
                    score=outcome.score,
                )
            )
    return SubtaskAssessment(
        subtask_id=subtask_id,
        path="classic",
        execution_status=_terminal(execution_status),
        output_sha256=output_digest(output),
        verification=_verification(tuple(checks)),
        checks=tuple(checks),
        risk=RiskAssessment(
            status="assessed",
            method="legacy_risk_engine/v1",
            category=category,
            finding_id=finding_id,
        ),
    )


def _terminal(value: str) -> _Terminal:
    if value not in ("completed", "failed", "cancelled", "timeout"):
        raise ValueError("report requires terminal execution status")
    return cast(_Terminal, value)


def _omission(
    path: str, status: str
) -> Literal["execution_not_completed", "tool_path_unchecked", "isolated_path_unchecked"]:
    if status != "completed":
        return "execution_not_completed"
    return "isolated_path_unchecked" if path == "isolated" else "tool_path_unchecked"


def bound_assessment(
    assessment: ExecutionAssessment | None,
    *,
    task_id: str,
    execution_status: str,
    output: Any,
) -> ExecutionAssessment | None:
    if not isinstance(assessment, ExecutionAssessment):
        return None
    try:
        report = ExecutionAssessment.model_validate(assessment)
        if (
            report.task_id != task_id
            or report.execution_status != execution_status
            or report.output_sha256 != output_digest(output)
        ):
            return None
    except (ValidationError, ValueError, TypeError, UnicodeError):
        return None
    return report


def assessment_notice(assessment: ExecutionAssessment | None) -> str:
    if assessment is None:
        return "Runtime assessment: unavailable. No independent verification or authorization."
    checks = assessment.verification.value.replace("_", " ")
    heuristic = any(c.method != "not_performed" for s in assessment.subtasks for c in s.checks)
    detail = " (heuristic checks only; accuracy not checked)" if heuristic else ""
    risk = assessment.risk_status.replace("_", " ")
    risk_detail = (
        " (synthetic-finding heuristic, not a safety clearance)" if risk != "not assessed" else ""
    )
    return (
        f"Runtime assessment: execution {assessment.execution_status}; checks {checks}{detail}; "
        f"risk {risk}{risk_detail}. No independent verification or authorization."
    )


def assessed_output(
    output: Any,
    *,
    assessment: ExecutionAssessment | None,
    task_id: str,
    execution_status: str,
) -> str:
    report = bound_assessment(
        assessment, task_id=task_id, execution_status=execution_status, output=output
    )
    return render_output(output) + "\n\n" + assessment_notice(report)
