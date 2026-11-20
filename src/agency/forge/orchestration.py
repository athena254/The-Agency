"""Blacksmith Forge orchestrator and manager (skeleton).

Deterministic job lifecycle over the existing Forge entry points. Full
implementation tracked in docs/PLAN_BLACKSMITH_FORGE_ORCHESTRATOR.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class JobState(str, Enum):
    QUEUED = "QUEUED"
    DISPATCHED = "DISPATCHED"
    GATED = "GATED"
    PASSED = "PASSED"
    AWAITING_HUMAN = "AWAITING_HUMAN"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    BLOCKED = "BLOCKED"
    UNCERTAIN = "UNCERTAIN"


@dataclass
class ForgeJob:
    job_id: str
    title: str
    task: str
    repo: str
    acceptance: tuple[str, ...] = ()
    test_paths: tuple[str, ...] = ()
    state: JobState = JobState.QUEUED
    attempts: int = 0
    report_id: str | None = None
    gate: str | None = None
    changed_paths: tuple[str, ...] = ()
    artifact_sha256: str | None = None
    cost_usd: float = 0.0
    max_retries: int = 2


@dataclass
class BlacksmithStatus:
    queued: int = 0
    in_flight: str | None = None
    blocked: list[str] = field(default_factory=list)
    recent_verdicts: list[str] = field(default_factory=list)


class BlacksmithOps:
    """Manager service over the existing Forge pipeline (hermetic-friendly)."""

    def __init__(
        self,
        *,
        max_retries: int = 2,
        worker: Callable[[ForgeJob], dict[str, Any]] | None = None,
    ) -> None:
        self._jobs: dict[str, ForgeJob] = {}
        self._order: list[str] = []
        self._max_retries = max_retries
        self._worker = worker
        self._in_flight: str | None = None

    def submit(self, title: str, task: str, repo: str, *, acceptance: tuple[str, ...] | list[str] = (), test_paths: tuple[str, ...] | list[str] = ()) -> ForgeJob:
        import uuid

        job = ForgeJob(
            job_id=str(uuid.uuid4()),
            title=title,
            task=task,
            repo=repo,
            acceptance=tuple(acceptance),
            test_paths=tuple(test_paths),
            max_retries=self._max_retries,
        )
        self._jobs[job.job_id] = job
        self._order.append(job.job_id)
        return job

    def dispatch_next(self) -> ForgeJob | None:
        if self._in_flight is not None:
            return None
        for jid in self._order:
            job = self._jobs[jid]
            if job.state in (JobState.QUEUED, JobState.RETRYING):
                job.state = JobState.DISPATCHED
                job.attempts += 1
                self._in_flight = jid
                return job
        return None

    def record_verdict(self, job_id: str, report: dict[str, Any]) -> ForgeJob:
        job = self._jobs[job_id]
        self._in_flight = None
        job.report_id = report.get("report_id")
        job.gate = report.get("gate")
        job.changed_paths = tuple(report.get("changed_paths", ()))
        job.artifact_sha256 = report.get("artifact_sha256")
        job.cost_usd = float(report.get("cost_usd", 0.0) or 0.0)
        passed = bool(report.get("passed", False))
        if passed:
            job.state = JobState.AWAITING_HUMAN
        else:
            if job.attempts <= job.max_retries:
                job.state = JobState.RETRYING
            else:
                job.state = JobState.BLOCKED
        return job

    def status(self) -> BlacksmithStatus:
        blocked = [jid for jid, j in self._jobs.items() if j.state == JobState.BLOCKED]
        recent = [j.state.value for j in list(self._jobs.values())[-5:]]
        queued = sum(1 for j in self._jobs.values() if j.state in (JobState.QUEUED, JobState.RETRYING))
        return BlacksmithStatus(queued=queued, in_flight=self._in_flight, blocked=blocked, recent_verdicts=recent)

    def get(self, job_id: str) -> ForgeJob | None:
        return self._jobs.get(job_id)
