"""Owner-only identities and sanitized failures, never peer authority."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Literal

if TYPE_CHECKING:
    from agency.admission.budget import OwnerBudgetAdapter

from pydantic import BaseModel, ConfigDict, Field

from agency.agents.assessment import ExecutionAssessment

_ERRORS = {
    "unauthorized": (401, "Owner authentication required."),
    "forbidden": (403, "Owner access denied."),
    "not_found": (404, "Resource not found."),
    "conflict": (409, "Request conflicts with stored state."),
    "too_large": (413, "Request exceeds size limit."),
    "media": (415, "Unsupported media type."),
    "invalid_request": (422, "Invalid request."),
    "quota": (429, "Owner quota exceeded."),
    "disabled": (503, "Owner pilot unavailable."),
    "audit": (503, "Owner audit unavailable."),
    "store": (503, "Owner store unavailable."),
    "uncertainty": (503, "Owner execution outcome uncertain."),
}


class PilotError(Exception):
    def __init__(self, code: str, status_code: int = 503, uncertain: bool = False) -> None:
        if type(code) is not str or code not in _ERRORS:
            code = "disabled"
        self.code = code
        self.status_code = _ERRORS[code][0] if status_code == 503 else status_code
        self.uncertain = uncertain is True
        super().__init__(_ERRORS[code][1])


def _identifier(value: str) -> None:
    if type(value) is not str or not value.strip() or len(value) > 128:
        raise PilotError("invalid_request")


@dataclass(frozen=True)
class OwnerPrincipal:
    owner_id: str
    transport: Literal["local", "telegram"]
    subject_id: str

    def __post_init__(self) -> None:
        _identifier(self.owner_id)
        _identifier(self.subject_id)
        if type(self.transport) is not str or self.transport not in ("local", "telegram"):
            raise PilotError("invalid_request")


@dataclass(frozen=True)
class RequestLease:
    principal: OwnerPrincipal
    request_id: str
    task_id: str
    sequence: int
    quota_subject_id: int
    capability: str = field(repr=False)
    deadline_at: float

    def __post_init__(self) -> None:
        if type(self.principal) is not OwnerPrincipal:
            raise PilotError("invalid_request")
        self.principal.__post_init__()
        if (
            type(self.request_id) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", self.request_id) is None
        ):
            raise PilotError("invalid_request")
        _identifier(self.task_id)
        if any(
            type(value) is not int or not 1 <= value <= 2**63 - 1
            for value in (self.sequence, self.quota_subject_id)
        ):
            raise PilotError("invalid_request")
        if type(self.capability) is not str or not 32 <= len(self.capability) <= 128:
            raise PilotError("invalid_request")
        if (
            type(self.deadline_at) is not float
            or not math.isfinite(self.deadline_at)
            or self.deadline_at <= 0
        ):
            raise PilotError("invalid_request")


@dataclass(frozen=True)
class OwnerExecutionGrant:
    lease: RequestLease
    budget: OwnerBudgetAdapter = field(repr=False)
    allowed_tools: frozenset[str] = frozenset({"calculator", "utc_time", "web_search"})
    max_tool_calls: int = 4
    max_model_calls: int = 8

    def __post_init__(self) -> None:
        from agency.admission.budget import OwnerBudgetAdapter

        if (
            type(self.lease) is not RequestLease
            or type(self.budget) is not OwnerBudgetAdapter
            or not self.budget.owns(self.lease)
        ):
            raise PilotError("forbidden")
        if (
            type(self.allowed_tools) is not frozenset
            or any(type(tool) is not str for tool in self.allowed_tools)
            or not self.allowed_tools <= {"calculator", "utc_time", "web_search"}
        ):
            raise PilotError("invalid_request")
        if (
            type(self.max_tool_calls) is not int
            or not 1 <= self.max_tool_calls <= 4
            or type(self.max_model_calls) is not int
            or not 1 <= self.max_model_calls <= 8
        ):
            raise PilotError("invalid_request")


@dataclass(frozen=True)
class OwnerMeteredBinding:
    grant: OwnerExecutionGrant
    provider: str
    model: str
    provider_config_id: str

    def __post_init__(self) -> None:
        if type(self.grant) is not OwnerExecutionGrant:
            raise PilotError("forbidden")
        self.grant.__post_init__()
        if type(self.provider) is not str or self.provider not in {"openai", "echo"}:
            raise PilotError("invalid_request")
        _identifier(self.model)
        _identifier(self.provider_config_id)


_Id = Annotated[str, Field(min_length=1, max_length=128)]
_RequestId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", max_length=128)]


class _Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PilotUsage(_Wire):
    model_attempts: Annotated[int, Field(ge=0, le=8)]
    tool_attempts: Annotated[int, Field(ge=0, le=4)]


class PilotLimits(_Wire):
    requests_per_hour: Annotated[int, Field(ge=1, le=5)]
    requests_per_day: Annotated[int, Field(ge=1, le=20)]
    model_calls_per_hour: Annotated[int, Field(ge=1, le=40)]
    model_calls_per_day: Annotated[int, Field(ge=1, le=160)]
    model_calls_per_request: Annotated[int, Field(ge=1, le=8)]
    tool_calls_per_request: Annotated[int, Field(ge=1, le=4)]


class PilotCapabilities(_Wire):
    enabled: bool
    ready: bool
    mode: Literal["single_owner_pilot"]
    tools: list[str]
    disabled: list[str]
    limits: PilotLimits
    provider_ready: bool
    uncertainty_blocked: bool


class ThreadView(_Wire):
    id: _Id
    title: Annotated[str, Field(min_length=1, max_length=120)]
    parent_thread_id: _Id | None
    state: str
    created_at: str
    updated_at: str


class MessageView(_Wire):
    id: _Id
    thread_id: _Id
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: str


class RequestReceipt(_Wire):
    request_id: _RequestId
    state: str
    replayed: bool
    resource_id: _Id | None


class OwnerTaskRequest(_Wire):
    request_id: _RequestId
    thread_id: _Id
    message: Annotated[str, Field(min_length=1, max_length=8000)]


class OwnerTaskView(_Wire):
    request_id: _RequestId
    task_id: _Id
    thread_id: _Id
    state: Literal["ADMITTED", "RUNNING", "COMPLETED", "FAILED", "DENIED", "CANCELLED", "UNCERTAIN"]
    replayed: bool
    cancel_requested: bool
    response: str | None
    assessment: ExecutionAssessment | None
    error_code: str | None
    uncertain: bool
    usage: PilotUsage
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AdmissionRecord:
    sequence: int
    owner_id: str
    request_id: str
    operation: str
    input_digest: str
    input_json: str
    task_id: str | None
    thread_id: str | None
    resource_id: str | None
    state: str
    phase: str
    cancel_requested: bool
    model_attempts: int
    tool_attempts: int
    result_json: str | None
    result_digest: str | None
    error_code: str | None
    provider_config_id: str | None
    user_message_id: str | None
    assistant_message_id: str | None
    created_at: float
    updated_at: float
    replayed: bool = False


class OwnerError(_Wire):
    error: str
    code: str
    request_id: _RequestId | None
    task_id: _Id | None
    uncertain: bool
    retryable: bool
