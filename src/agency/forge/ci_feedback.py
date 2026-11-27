"""Bounded CI/gate-failure ingestion for Forge self-improvement.

Closes the feedback gap: turns gate/CI failures (JSON or plain text) into
deduplicated, idempotent candidate job proposals. This module is deliberately
capture-only. It never dispatches a job, never merges, never pushes, and never
calls the network or a subprocess. An explicit, named (non-generic) approver is
required before an action proposal is produced, and every emitted string is
redacted so secret material is never copied into output.

The public helpers are intentionally small so a CLI can wire them later without
importing any orchestration side effects.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agency.forge.correlation import make_key

MAX_INPUT_CHARS = 200_000
MAX_EVENTS = 100
MAX_DETAIL_CHARS = 1000
MAX_TEST_PATHS = 20
MAX_PATH_CHARS = 300
MAX_TITLE_CHARS = 200
MAX_TASK_CHARS = 2000
MAX_SOURCE_CHARS = 80
MAX_TASK_ID_CHARS = 120

# Only failing outcomes are ingested. A passing check is never a candidate.
FAIL_GATES = frozenset({"FAIL", "FAILED", "FAILURE", "ERROR", "BLOCKED", "TIMED_OUT"})
DEFAULT_ROLE = "striker"

# Approver names that are labels, not an accountable named human.
_NON_HUMAN_APPROVERS = frozenset(
    {
        "admin",
        "agent",
        "anonymous",
        "approver",
        "auto",
        "automation",
        "bot",
        "ci",
        "cli",
        "default",
        "false",
        "forge",
        "human",
        "maintainer",
        "n/a",
        "na",
        "none",
        "reviewer",
        "root",
        "someone",
        "system",
        "test",
        "tester",
        "true",
        "unknown",
        "user",
        "yes",
        "no",
    }
)
_APPROVER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 ._'-]{1,119}$")

CAVEATS: tuple[str, ...] = (
    "capture_only",
    "no_auto_dispatch",
    "no_auto_merge",
    "no_push",
    "secrets_redacted",
)

# Conservative secret shapes. Redaction replaces the matched material with a
# fixed placeholder; it never echoes the matched value.
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password|passwd|authorization|key)"
            r"\s*[:=]\s*['\"]?[^\s'\"',;]{8,}"
        ),
        "[REDACTED_SECRET]",
    ),
    (
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
            r".*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "[REDACTED_API_TOKEN]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{12,}"), "Bearer [REDACTED_TOKEN]"),
)

_LINE_GATE_RE = re.compile(
    r"^(?P<gate>FAIL(?:ED|URE)?|ERROR|BLOCKED|TIMED_OUT)\b[\s:\-]*(?P<detail>.*)$",
    re.IGNORECASE,
)
_PASS_LINE_RE = re.compile(r"^(?:PASS(?:ED)?|SUCCESS(?:FUL(?:LY)?)?|OK)\b", re.IGNORECASE)
_JSON_LIST_KEYS = ("failures", "reports", "results", "jobs", "checks", "events", "items")
_DETAIL_KEYS = ("detail", "message", "failure_detail", "output", "log", "error", "text")
_TASK_ID_KEYS = ("task_id", "job_id", "id", "name", "check", "test")
_SOURCE_KEYS = ("source", "kind", "provider", "check_name", "workflow")
_PATH_KEYS = ("test_paths", "paths", "changed_paths", "files", "path")


class ApprovalError(ValueError):
    """Raised when the approver is missing, generic, or not explicit."""


class FailureEvent(BaseModel):
    """One bounded, redacted failure parsed from a CI/gate report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=MAX_TASK_ID_CHARS)
    source: str = Field(min_length=1, max_length=MAX_SOURCE_CHARS)
    gate: str = Field(min_length=1, max_length=16)
    detail: str = Field(default="", max_length=MAX_DETAIL_CHARS)
    test_paths: tuple[str, ...] = ()
    key: str = Field(min_length=1, max_length=64)


class CandidateJob(BaseModel):
    """A proposal only. It is never dispatched by this module."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1, max_length=64)
    task_id: str = Field(min_length=1, max_length=MAX_TASK_ID_CHARS)
    source: str = Field(min_length=1, max_length=MAX_SOURCE_CHARS)
    gate: str = Field(min_length=1, max_length=16)
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    task: str = Field(min_length=1, max_length=MAX_TASK_CHARS)
    role: str = Field(min_length=1, max_length=32)
    test_paths: tuple[str, ...] = ()


class IngestionReport(BaseModel):
    """Bounded outcome of one ingestion pass; contains no actionable side effect."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    received: int = Field(ge=0)
    failures: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    candidates: tuple[CandidateJob, ...] = ()
    keys: tuple[str, ...] = ()
    caveats: tuple[str, ...] = CAVEATS


class ActionProposal(BaseModel):
    """A human-actionable proposal after approval. Still capture-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_key: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    task: str = Field(min_length=1, max_length=MAX_TASK_CHARS)
    role: str = Field(min_length=1, max_length=32)
    test_paths: tuple[str, ...] = ()
    approved_by: str = Field(min_length=1, max_length=120)
    caveats: tuple[str, ...] = CAVEATS

    @field_validator("approved_by")
    @classmethod
    def _require_named_human(cls, value: str) -> str:
        if not is_valid_approver(value):
            raise ValueError("an explicit, named human approver is required")
        return value.strip()


def redact_secrets(text: object) -> str:
    """Replace known secret shapes with fixed placeholders. Never echoes input."""
    value = str(text) if text is not None else ""
    for pattern, replacement in _SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def is_valid_approver(approver: object) -> bool:
    """True only for a nonblank, non-generic, named human approver."""
    name = str(approver).strip() if approver is not None else ""
    return bool(
        name
        and name.lower() not in _NON_HUMAN_APPROVERS
        and "  " not in name
        and _APPROVER_RE.fullmatch(name)
    )


def _require_approver(approver: object) -> str:
    if not is_valid_approver(approver):
        raise ApprovalError("an explicit, named human approver is required before any action")
    return str(approver).strip()[:120]


def _first_string(record: dict[str, Any], *names: str) -> str:
    for name in names:
        value = record.get(name)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _first_value(record: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in record:
            value = record[name]
            if value not in (None, "", [], ()):
                return value
    return None


def _clean_paths(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        items = re.split(r"[\n,]+", str(value))
    paths: list[str] = []
    seen: set[str] = set()
    for item in items:
        path = redact_secrets(item).strip()[:MAX_PATH_CHARS]
        if not path or path in seen or ".." in path or "\x00" in path:
            continue
        seen.add(path)
        paths.append(path)
        if len(paths) >= MAX_TEST_PATHS:
            break
    return tuple(paths)


def _records_from_json(decoded: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(decoded, dict):
        for name in _JSON_LIST_KEYS:
            value = decoded.get(name)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)][:MAX_EVENTS]
        return [decoded]
    return [item for item in decoded if isinstance(item, dict)][:MAX_EVENTS]


def _records_from_text(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or _PASS_LINE_RE.match(line):
            continue
        gate, detail = "FAIL", line
        match = _LINE_GATE_RE.match(line)
        if match:
            gate = match.group("gate").upper()
            detail = match.group("detail").strip() or line
        records.append({"gate": gate, "detail": detail, "source": "ci_text"})
        if len(records) >= MAX_EVENTS:
            break
    return records


def _coerce_payload(payload: str | bytes | dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        text = payload[:MAX_INPUT_CHARS]
        stripped = text.lstrip()
        if stripped.startswith(("{", "[")):
            try:
                decoded = json.loads(text)
            except (ValueError, TypeError):
                decoded = None
            if isinstance(decoded, (dict, list)):
                return _records_from_json(decoded)
        return _records_from_text(text)
    if isinstance(payload, dict):
        return _records_from_json(payload)
    if isinstance(payload, list):
        return _records_from_json(payload)
    raise TypeError("payload must be a JSON object/list or CI text")


def _normalize_gate(gate: str) -> str:
    """Fold synonyms onto canonical gate names so keys stay stable."""
    if gate in {"FAILED", "FAILURE", "TIMED_OUT"}:
        return "FAIL"
    return gate


def _event_from_record(record: dict[str, Any], index: int) -> FailureEvent | None:
    gate = _normalize_gate(
        _first_string(record, "gate", "status", "result", "conclusion", "outcome").upper()
    )
    if gate and gate not in FAIL_GATES:
        return None
    if not gate:
        if any(record.get(flag) is False for flag in ("success", "ok", "passed")):
            gate = "FAIL"
        else:
            return None
    gate = gate if gate in FAIL_GATES else "FAIL"

    task_id = redact_secrets(_first_string(record, *_TASK_ID_KEYS) or f"ci-{index}").strip()[
        :MAX_TASK_ID_CHARS
    ]
    source = redact_secrets(_first_string(record, *_SOURCE_KEYS) or "ci").strip()[:MAX_SOURCE_CHARS]
    detail = redact_secrets(_first_string(record, *_DETAIL_KEYS))[:MAX_DETAIL_CHARS]
    test_paths = _clean_paths(_first_value(record, *_PATH_KEYS))
    key = make_key(
        task_id,
        f"ci_failure:{gate}:{source}:{detail[:80]}",
        "|".join(test_paths)[:80],
    )
    return FailureEvent(
        task_id=task_id or f"ci-{index}",
        source=source or "ci",
        gate=gate,
        detail=detail,
        test_paths=test_paths,
        key=key,
    )


def _events_from_records(records: list[dict[str, Any]]) -> tuple[FailureEvent, ...]:
    events: list[FailureEvent] = []
    for index, record in enumerate(records[:MAX_EVENTS], start=1):
        event = _event_from_record(record, index)
        if event is not None:
            events.append(event)
    return tuple(events)


def parse_ci_failures(
    payload: str | bytes | dict[str, Any] | list[Any],
) -> tuple[FailureEvent, ...]:
    """Parse bounded failure events from JSON or plain text; never executes."""
    return _events_from_records(_coerce_payload(payload))


def build_candidate_jobs(
    events: Collection[FailureEvent],
    *,
    role: str = DEFAULT_ROLE,
    seen_keys: Collection[str] = (),
) -> tuple[CandidateJob, ...]:
    """Deduplicate events into proposals; stable keys make reruns idempotent."""
    safe_role = redact_secrets(role).strip()[:32] or DEFAULT_ROLE
    already = {str(key) for key in seen_keys}
    candidates: list[CandidateJob] = []
    for event in list(events)[:MAX_EVENTS]:
        if event.key in already:
            continue
        already.add(event.key)
        title = f"Fix CI failure: {event.task_id} ({event.gate})"[:MAX_TITLE_CHARS]
        task = (
            f"Investigate and fix the {event.gate} gate failure from {event.source} "
            f"on task {event.task_id}. Observed: {event.detail or 'no detail provided'}."
        )[:MAX_TASK_CHARS]
        candidates.append(
            CandidateJob(
                key=event.key,
                task_id=event.task_id,
                source=event.source,
                gate=event.gate,
                title=redact_secrets(title)[:MAX_TITLE_CHARS],
                task=redact_secrets(task)[:MAX_TASK_CHARS],
                role=safe_role,
                test_paths=event.test_paths,
            )
        )
    return tuple(candidates)


def ingest_ci_failures(
    payload: str | bytes | dict[str, Any] | list[Any],
    *,
    role: str = DEFAULT_ROLE,
    seen_keys: Collection[str] = (),
) -> IngestionReport:
    """Parse, bound and deduplicate CI failures into candidate jobs.

    Pure capture: no dispatch, no merge, no push, no I/O. Pass the returned
    ``keys`` back as ``seen_keys`` on the next run to keep ingestion idempotent.
    """
    records = _coerce_payload(payload)
    events = _events_from_records(records)
    candidates = build_candidate_jobs(events, role=role, seen_keys=seen_keys)
    return IngestionReport(
        received=len(records),
        failures=len(events),
        duplicates=len(events) - len(candidates),
        candidates=candidates,
        keys=tuple(candidate.key for candidate in candidates),
    )


def authorize_candidate(
    candidate: CandidateJob,
    *,
    approved_by: str,
) -> ActionProposal:
    """Produce a proposal only after an explicit non-generic human approver.

    The caller must dispatch any job itself through the existing gated tools;
    this function never does so and never merges or pushes.
    """
    approver = _require_approver(approved_by)
    return ActionProposal(
        candidate_key=candidate.key,
        title=redact_secrets(candidate.title)[:MAX_TITLE_CHARS],
        task=redact_secrets(candidate.task)[:MAX_TASK_CHARS],
        role=redact_secrets(candidate.role)[:32],
        test_paths=tuple(redact_secrets(path)[:MAX_PATH_CHARS] for path in candidate.test_paths),
        approved_by=approver,
    )


__all__ = [
    "CAVEATS",
    "DEFAULT_ROLE",
    "FAIL_GATES",
    "MAX_EVENTS",
    "ActionProposal",
    "ApprovalError",
    "CandidateJob",
    "FailureEvent",
    "IngestionReport",
    "authorize_candidate",
    "build_candidate_jobs",
    "ingest_ci_failures",
    "is_valid_approver",
    "parse_ci_failures",
    "redact_secrets",
]
