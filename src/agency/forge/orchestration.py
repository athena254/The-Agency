"""BlacksmithOps — the Blacksmith's Forge job lifecycle (manager, not coder).

Deterministic state machine over the EXISTING Forge entry points: striker
workers (external CLI agents) build via :func:`run_external_agent`, tool builds go through
:func:`agency.autonomy.forge_auto.build_tool`. Every transition is persisted
(append-only events) and reported to an optional audit sink. A PASS lands in
the merge queue awaiting human approval; nothing here authorizes merge or
release. Crashed DISPATCHED jobs become UNCERTAIN at reopen and are never
auto-resumed.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

from agency.forge.backlog import BacklogStore, slice_prd
from agency.forge.cost import CostStore, record_cost
from agency.forge.evidence import EvidenceReport, EvidenceStore
from agency.forge.external_agent import ExternalAgentReport, run_external_agent
from agency.forge.merge_queue import MergeQueueReport, MergeQueueStore, evaluate_merge
from agency.forge.reviewer import ReviewReport
from agency.forge.work_order import WorkOrder, render_work_order

if TYPE_CHECKING:
    # Imported lazily at runtime: agency.autonomy.__init__ imports engine,
    # which imports this module; a module-level autonomy import here is a
    # circular import when forge.orchestration is imported first.
    from agency.autonomy.forge_auto import ToolBuildResult

logger = structlog.get_logger(__name__)

MAX_TITLE_CHARS = 200
MAX_TASK_CHARS = 8000
MAX_DETAIL_CHARS = 1000
MAX_ACCEPTANCE = 16
MAX_TEST_PATHS = 16
DEFAULT_MAX_RETRIES = 2
DEFAULT_COMMAND = ("opencode", "run", "--auto", "--dir", "{workdir}")


class JobState(str, Enum):
    QUEUED = "QUEUED"
    DISPATCHED = "DISPATCHED"
    PASSED = "PASSED"
    AWAITING_HUMAN = "AWAITING_HUMAN"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    UNCERTAIN = "UNCERTAIN"


class ForgeJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    task: str = Field(default="", max_length=MAX_TASK_CHARS)
    repo: str = ""
    acceptance: tuple[str, ...] = ()
    test_paths: tuple[str, ...] = ()
    state: JobState = JobState.QUEUED
    attempts: int = Field(default=0, ge=0)
    report_id: str = ""
    gate: str = ""
    changed_paths: tuple[str, ...] = ()
    artifact_sha256: str = ""
    merge_report_id: str = ""
    failure_detail: str = Field(default="", max_length=MAX_DETAIL_CHARS)
    cost_usd: float = Field(default=0.0, ge=0)
    created_at: str = ""
    updated_at: str = ""


class BlacksmithStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    total: int = Field(ge=0)
    by_state: dict[str, int] = Field(default_factory=dict)
    blocked: tuple[str, ...] = ()
    recent: tuple[ForgeJob, ...] = ()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS forge_jobs (
    job_id     TEXT PRIMARY KEY,
    state      TEXT NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    document   TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS forge_job_events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT NOT NULL,
    event       TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state   TEXT NOT NULL,
    at         TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT ''
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class BlacksmithOps:
    """Manage Forge build jobs: submit, dispatch, gate, retry, escalate."""

    def __init__(
        self,
        db_path: str | Path = "forge_jobs.db",
        *,
        runner: Callable[..., ExternalAgentReport] | None = None,
        command: tuple[str, ...] | list[str] | None = None,
        model: str = "opencode/beam-free",
        evidence: EvidenceStore | None = None,
        merge_queue: MergeQueueStore | None = None,
        costs: CostStore | None = None,
        backlog: BacklogStore | None = None,
        audit_sink: Callable[[str, dict[str, Any]], None] | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        worktree_root: str | Path | None = None,
    ) -> None:
        if max_retries < 0 or max_retries > 5:
            raise ValueError("max_retries must be in [0, 5].")
        self._db_path = Path(db_path)
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._runner = runner if runner is not None else run_external_agent
        self._model = model
        self._command = (
            tuple(command) if command is not None else (*DEFAULT_COMMAND, "--model", model)
        )
        self._max_attempts = max_retries + 1
        self._worktree_root = worktree_root
        self._audit_sink = audit_sink
        self._owned: list[Any] = []
        self.evidence = evidence if evidence is not None else self._own(EvidenceStore(":memory:"))
        self.merge_queue = (
            merge_queue if merge_queue is not None else self._own(MergeQueueStore(":memory:"))
        )
        self._costs = costs if costs is not None else self._own(CostStore(":memory:"))
        self.backlog = backlog if backlog is not None else self._own(BacklogStore(":memory:"))
        self._recover_uncertain()

    def _own(self, store: Any) -> Any:
        self._owned.append(store)
        return store

    def _recover_uncertain(self) -> None:
        rows = self._conn.execute(
            "SELECT job_id, document FROM forge_jobs WHERE state = ?",
            (JobState.DISPATCHED.value,),
        ).fetchall()
        for job_id, document in rows:
            job = ForgeJob.model_validate_json(document)
            self._transition(
                job, JobState.UNCERTAIN, "job_uncertain", "crash recovery; not resumed"
            )

    # ------------------------------------------------------------------ jobs

    def submit(
        self,
        title: str,
        task: str,
        repo: str,
        *,
        acceptance: tuple[str, ...] | list[str] = (),
        test_paths: tuple[str, ...] | list[str] = (),
    ) -> ForgeJob:
        if not isinstance(repo, str) or not Path(repo).is_absolute():
            raise ValueError(f"repo must be an absolute path, got {repo!r}.")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be a nonblank string.")
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task must be a nonblank string.")
        acc = tuple(str(a) for a in acceptance)[:MAX_ACCEPTANCE]
        tests = tuple(str(t) for t in test_paths)[:MAX_TEST_PATHS]
        now = _now()
        job = ForgeJob(
            job_id=str(uuid4()),
            title=title.strip()[:MAX_TITLE_CHARS],
            task=task.strip()[:MAX_TASK_CHARS],
            repo=repo,
            acceptance=acc,
            test_paths=tests,
            state=JobState.QUEUED,
            created_at=now,
            updated_at=now,
        )
        self._insert(job)
        self._event(job, "job_submitted", "", JobState.QUEUED.value, "")
        self._notify(job, "job_submitted", "", JobState.QUEUED.value, "")
        logger.info("blacksmith.job_submitted", job_id=job.job_id, title=job.title)
        return job

    def ingest_backlog(
        self,
        prd_text: str,
        repo: str,
        *,
        test_paths: tuple[str, ...] | list[str] = (),
    ) -> tuple[ForgeJob, ...]:
        """Slice a PRD into bounded backlog items and queue one job per item."""
        report = slice_prd(prd_text)
        self.backlog.save(report)
        jobs: list[ForgeJob] = []
        for item in report.items:
            task = item.body or item.title
            jobs.append(self.submit(item.title, task, repo, test_paths=test_paths))
        logger.info("blacksmith.backlog_ingested", report_id=report.report_id, jobs=len(jobs))
        return tuple(jobs)

    def get(self, job_id: str) -> ForgeJob:
        row = self._conn.execute(
            "SELECT document FROM forge_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown job {job_id!r}.")
        return ForgeJob.model_validate_json(row[0])

    def dispatch_next(self) -> ForgeJob | None:
        row = self._conn.execute(
            "SELECT document FROM forge_jobs WHERE state = ?"
            " OR (state = ? AND attempts < ?)"
            " ORDER BY created_at, rowid LIMIT 1",
            (JobState.QUEUED.value, JobState.FAILED.value, self._max_attempts),
        ).fetchone()
        if row is None:
            return None
        job = ForgeJob.model_validate_json(row[0])
        job = self._transition(
            job.model_copy(update={"attempts": job.attempts + 1}),
            JobState.DISPATCHED,
            "job_dispatched",
            f"attempt {job.attempts + 1}",
        )
        # The striker receives a rendered work order, not a bare task string:
        # boundaries, work rules, acceptance criteria, the test command, and —
        # on retries — the previous attempt's bounded failure tail.
        order = WorkOrder(
            title=job.title,
            task=job.task,
            repo=job.repo,
            acceptance=job.acceptance,
            test_paths=job.test_paths,
            attempt=job.attempts,
            max_attempts=self._max_attempts,
            failure_tail=job.failure_detail if job.attempts > 1 else "",
        )
        try:
            report = self._runner(
                job.repo,
                list(self._command),
                render_work_order(order),
                branch=f"blacksmith-{job.job_id[:8]}-a{job.attempts}",
                worktree_root=self._worktree_root,
                run_tests_flag=bool(job.test_paths),
                test_paths=list(job.test_paths) or None,
            )
        except Exception as exc:  # noqa: BLE001 - a worker crash must fail the job honestly
            detail = f"{type(exc).__name__}: {exc}"[:MAX_DETAIL_CHARS]
            job = self._transition(
                job.model_copy(update={"failure_detail": detail}),
                JobState.FAILED,
                "job_failed",
                detail,
            )
            return self._maybe_block(job)
        # Dispatch ledger: one entry per attempt. External CLI agents expose no
        # token metering, so cost stays 0.0 — an honest "not metered", never a
        # fabricated bill.
        record_cost(
            self._costs,
            run_id=f"{job.job_id[:40]}#a{job.attempts}"[:128],
            model=self._model,
        )
        return self.record_verdict(job.job_id, report)

    def record_verdict(self, job_id: str, report: ExternalAgentReport) -> ForgeJob:
        job = self.get(job_id)
        digest = hashlib.sha256(report.model_dump_json().encode("utf-8")).hexdigest()
        base = {
            "report_id": report.report_id,
            "gate": str(report.gate),
            "changed_paths": tuple(report.changed_paths),
            "artifact_sha256": digest,
        }
        if report.isolation_violations:
            detail = "isolation violations: " + ", ".join(report.isolation_violations)
            job = self._transition(
                job.model_copy(update={**base, "failure_detail": detail[:MAX_DETAIL_CHARS]}),
                JobState.FAILED,
                "job_failed",
                detail[:MAX_DETAIL_CHARS],
            )
            return self._maybe_block(job)
        if str(report.gate) != "PASS":
            detail = (report.failure_detail or f"gate {report.gate}")[:MAX_DETAIL_CHARS]
            citations = _review_citations(report.review)
            if citations:
                detail = f"{detail} | review findings: {citations}"
            detail = detail[:MAX_DETAIL_CHARS]
            job = self._transition(
                job.model_copy(update={**base, "failure_detail": detail}),
                JobState.FAILED,
                "job_failed",
                detail,
            )
            return self._maybe_block(job)
        job = self._transition(
            job.model_copy(update={**base, "failure_detail": ""}),
            JobState.PASSED,
            "job_passed",
            f"report {report.report_id}",
        )
        self.evidence.save(
            EvidenceReport(
                report_id=report.report_id,
                created_at=report.created_at,
                root=report.worktree_path,
                command=tuple(report.command),
                returncode=report.exit_code,
                stdout=report.stdout[:8000],
                stderr=report.stderr[:8000],
                workdir=report.worktree_path,
                gate="PASS",
            )
        )
        mq_report: MergeQueueReport = evaluate_merge(
            inspection=report.inspection.gate,
            security=report.security.gate,
            tests=report.tests.gate if report.tests is not None else None,
            review=report.review.gate if report.review is not None else None,
            changed_paths=list(report.changed_paths),
            protected_paths=list(report.protected_paths),
            root=report.worktree_path,
        )
        mq_id = self.merge_queue.save(mq_report)
        return self._transition(
            job.model_copy(update={"merge_report_id": mq_id}),
            JobState.AWAITING_HUMAN,
            "job_awaiting_human",
            f"merge queue {mq_id}; human approval required",
        )

    def _maybe_block(self, job: ForgeJob) -> ForgeJob:
        if job.state is JobState.FAILED and job.attempts >= self._max_attempts:
            return self._transition(
                job, JobState.BLOCKED, "job_blocked", "retries exhausted; escalate to human"
            )
        return job

    def build_tool_job(
        self,
        name: str,
        code: str,
        tests: str,
        registry: Any = None,
    ) -> ToolBuildResult:
        from agency.autonomy.forge_auto import build_tool

        result = build_tool(name, code, tests, registry)
        now = _now()
        job = ForgeJob(
            job_id=str(uuid4()),
            title=f"tool:{name}"[:MAX_TITLE_CHARS],
            task=f"build tool {name}",
            state=JobState.PASSED if result.gate == "PASS" else JobState.FAILED,
            attempts=1,
            report_id=result.report_id,
            gate=result.gate,
            artifact_sha256=result.sha256,
            failure_detail="" if result.gate == "PASS" else result.detail[:MAX_DETAIL_CHARS],
            created_at=now,
            updated_at=now,
        )
        self._insert(job)
        self._event(
            job, "tool_build_recorded", "", job.state.value, result.detail[:MAX_DETAIL_CHARS]
        )
        self._notify(
            job, "tool_build_recorded", "", job.state.value, result.detail[:MAX_DETAIL_CHARS]
        )
        logger.info(
            "blacksmith.tool_build_recorded",
            job_id=job.job_id,
            tool=name,
            gate=result.gate,
            report_id=result.report_id,
        )
        return result

    def status(self) -> BlacksmithStatus:
        rows = self._conn.execute(
            "SELECT state, document FROM forge_jobs ORDER BY updated_at DESC"
        ).fetchall()
        by_state: dict[str, int] = {}
        blocked: list[str] = []
        recent: list[ForgeJob] = []
        for state, document in rows:
            by_state[state] = by_state.get(state, 0) + 1
            job = ForgeJob.model_validate_json(document)
            if state == JobState.BLOCKED.value:
                blocked.append(job.job_id)
            if len(recent) < 10:
                recent.append(job)
        return BlacksmithStatus(
            total=len(rows), by_state=by_state, blocked=tuple(blocked), recent=tuple(recent)
        )

    # ------------------------------------------------------------ persistence

    def _insert(self, job: ForgeJob) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO forge_jobs (job_id, state, attempts, document, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    job.job_id,
                    job.state.value,
                    job.attempts,
                    job.model_dump_json(),
                    job.created_at,
                    job.updated_at,
                ),
            )

    def _transition(
        self, job: ForgeJob, to_state: JobState, event: str, detail: str = ""
    ) -> ForgeJob:
        from_state = job.state.value
        updated = job.model_copy(update={"state": to_state, "updated_at": _now()})
        with self._conn:
            self._conn.execute(
                "UPDATE forge_jobs SET state = ?, attempts = ?, document = ?, updated_at = ?"
                " WHERE job_id = ?",
                (
                    to_state.value,
                    updated.attempts,
                    updated.model_dump_json(),
                    updated.updated_at,
                    updated.job_id,
                ),
            )
        self._event(updated, event, from_state, to_state.value, detail)
        self._notify(updated, event, from_state, to_state.value, detail)
        return updated

    def _notify(
        self, job: ForgeJob, event: str, from_state: str, to_state: str, detail: str
    ) -> None:
        if self._audit_sink is not None:
            self._audit_sink(
                event,
                {
                    "job_id": job.job_id,
                    "from_state": from_state,
                    "to_state": to_state,
                    "detail": detail[:MAX_DETAIL_CHARS],
                },
            )

    def _event(
        self, job: ForgeJob, event: str, from_state: str, to_state: str, detail: str
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO forge_job_events (job_id, event, from_state, to_state, at, detail)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (job.job_id, event, from_state, to_state, _now(), detail[:MAX_DETAIL_CHARS]),
            )

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()
        for store in self._owned:
            store.close()


def _review_citations(review: ReviewReport | None, limit: int = 5) -> str:
    """Bounded structured citations of a review's blocking findings.

    Path:line:rule:severity only — no LLM prose is ever copied into the job
    record. Unresolved high/critical findings first; falls back to any
    unresolved finding so retries always know what to address.
    """
    if review is None:
        return ""
    unresolved = [f for f in review.findings if not f.resolved]
    blocking = [f for f in unresolved if f.severity in ("high", "critical")]
    flagged = blocking or unresolved
    parts = [f"{f.path or '-'}:{f.line} {f.rule_id} ({f.severity})" for f in flagged[:limit]]
    return "; ".join(parts)


__all__ = [
    "BlacksmithOps",
    "BlacksmithStatus",
    "ForgeJob",
    "JobState",
]
