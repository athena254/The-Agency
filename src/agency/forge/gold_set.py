"""Forge ECC gold set: immutable, human-owned tasks with deterministic grading.

The gold set is a frozen artifact: tasks are redacted at construction, ordered
by id, and cannot be changed by a run. Evaluation accepts the system-under-test
as an injected adapter and returns an immutable report; no network, no side
effects, no release authorization.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TaskVerdict = Literal["PASS", "FAIL", "ERROR"]
Gate = Literal["PASS", "FAIL"]

GOLD_SET_CAVEATS: tuple[str, ...] = (
    "human_owned_tasks_only",
    "secret_redaction_best_effort",
    "not_a_security_review",
    "no_network_calls",
    "does_not_authorize_release",
)

REDACTED = "[REDACTED]"

_FULL_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{10,}"),
)
_ASSIGNMENT_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?key|secret|token|password|passwd|pwd)"
    r"\s*[:=]\s*[\"']?(?P<value>[^\s\"',;]{6,})"
)
_AUTOMATED_OWNER_PREFIXES: tuple[str, ...] = (
    "agent:",
    "bot:",
    "ci:",
    "forge:",
    "service-",
)


def redact_secrets(text: str) -> str:
    """Deterministically mask common secret shapes in untrusted text."""
    redacted = text
    for pattern in _FULL_SECRET_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)

    def _mask(match: re.Match[str]) -> str:
        value = match.group("value")
        return match.group(0).replace(value, REDACTED, 1)

    return _ASSIGNMENT_SECRET_PATTERN.sub(_mask, redacted)


class GoldTask(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1, max_length=20_000)
    expected: str = Field(max_length=20_000)

    @field_validator("prompt", "expected", mode="after")
    @classmethod
    def _redact(cls, value: str) -> str:
        return redact_secrets(value)


class GoldSet(BaseModel):
    """Frozen, human-owned, canonically ordered list of grading tasks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    set_id: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    tasks: tuple[GoldTask, ...] = Field(min_length=1)
    version: int = Field(default=1, ge=1)

    @field_validator("owner", mode="after")
    @classmethod
    def _human_owner(cls, value: str) -> str:
        lowered = value.strip().lower()
        if any(lowered.startswith(prefix) for prefix in _AUTOMATED_OWNER_PREFIXES):
            raise ValueError("gold set owner must be a human identity")
        return value

    @field_validator("tasks", mode="after")
    @classmethod
    def _canonical_tasks(cls, tasks: tuple[GoldTask, ...]) -> tuple[GoldTask, ...]:
        ids = [task.task_id for task in tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate task_id in gold set")
        return tuple(sorted(tasks, key=lambda task: task.task_id))


class TaskResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(min_length=1)
    verdict: TaskVerdict
    observed: str = ""


class GoldSetReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    set_id: str = Field(min_length=1)
    set_version: int = Field(ge=1)
    gate: Gate
    results: tuple[TaskResult, ...] = ()
    caveats: tuple[str, ...] = GOLD_SET_CAVEATS
    note: str = "Bounded gold-set grading; redaction is best effort; no release authorization."


RunAdapter = Callable[[str], str] | Mapping[str, str]


def evaluate_gold_set(
    gold_set: GoldSet,
    run: RunAdapter,
    max_output_chars: int = 20_000,
) -> GoldSetReport:
    """Grade every task with the injected adapter and return a deterministic report."""
    if max_output_chars < 1:
        raise ValueError("max_output_chars must be positive")

    results: list[TaskResult] = []
    for task in gold_set.tasks:
        observed = ""
        verdict: TaskVerdict = "ERROR"
        try:
            raw = run(task.prompt) if callable(run) else run.get(task.prompt, "")
            if not isinstance(raw, str):
                raise TypeError("run adapter must return a string")
            observed = redact_secrets(raw)[:max_output_chars]
            verdict = "PASS" if observed.strip() == task.expected.strip() else "FAIL"
        except Exception:  # noqa: BLE001 - any adapter failure is an ERROR verdict
            observed = ""
            verdict = "ERROR"
        results.append(TaskResult(task_id=task.task_id, verdict=verdict, observed=observed))

    gate: Gate = "PASS" if all(result.verdict == "PASS" for result in results) else "FAIL"
    return GoldSetReport(
        set_id=gold_set.set_id,
        set_version=gold_set.version,
        gate=gate,
        results=tuple(results),
    )
