"""Live multi-restart and concurrency soak for the dispatcher/task line (gaps.md #10).

Runs two legs against a real provider (the untracked ``.env``; echo refuses):

Concurrency leg — N tasks on one file-backed queue
(:env:`AGENCY_TASKS_DB` + shared ``AGENCY_AUDIT_DB`` / ``AGENCY_EVIDENCE_DB`` /
``AGENCY_MEMORY_DB``), drained by TWO independent dispatcher processes. Every
task must reach a terminal state exactly once (CAS terminal writes), completed
outputs must carry their expected marker/number, and provider dips (503s,
empty replies) are observed and recorded, never treated as soak failures.

Restart leg — M tasks, two dispatcher processes, one killed mid-flight
(TerminateProcess). Its leased claims must be left alone until the lease
expires, then requeued by a fresh ``AGENCY_TASKS_RECOVER=1`` dispatcher and
re-executed to terminal; no task lost, no terminal record overwritten, the
killed process's partial audit trail intact (append-only).

Evidence JSON records per-task final status + output digest, audit/evidence
counts, CAS yields, backoff counts and the restart timeline. No API keys are
ever printed or stored. Not part of the test suite (tests stay hermetic).

Usage (from a worktree with a live ``.env``)::

    uv run python scripts/quality_soak.py                       # both legs
    uv run python scripts/quality_soak.py --leg concurrency
    uv run python scripts/quality_soak.py --worker w1 --run-dir D:/tmp/soak
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_PATH = Path("D:/agency-merge-backups/soak-evidence.json")
COMMIT_PLACEHOLDER = "unknown"

TERMINAL_VALUES = frozenset({"completed", "failed"})

ARITHMETIC_PAIRS = [(17, 25), (63, 29), (128, 574), (346, 655), (91, 88), (52, 68)]
MARKERS = ["BK-7314", "QX-5281", "MN-9047", "ZT-3162", "VR-8853", "HD-2049"]

# Worker log events the harness turns into evidence counts.
EXECUTION_EVENT = "task_executed"
TERMINAL_WRITE_EVENT = "task.status"
CAS_YIELD_EVENTS = (
    "task.status_write_yielded",
    "task_store.terminal_write_refused",
    "task_store.stale_write_refused",
)
BACKOFF_EVENT = "llm_provider_retry"
PLANNER_FALLBACK_EVENT = "planner.llm_decompose_failed"
EXECUTOR_RETRY_EVENTS = ("executor.retry", "executor.timeout")
REQUEUE_EVENT = "task_store.requeued_running"
ANOMALY_EVENTS = ("dispatcher.execute_failed", "dispatcher.poll_failed", "llm_error")

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_LOG_LINE_RE = re.compile(r"\[[a-z]+\s*\]\s+(\S+)")
_KV_RE = re.compile(r"([A-Za-z_][\w.]*)=(\S+)")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _digest(text: str | None) -> str:
    if not text:
        return ""
    return sha256(text.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Task fixtures
# --------------------------------------------------------------------------- #


def build_fixtures(count: int) -> list[dict[str, str]]:
    """Interleave trivial arithmetic and exact-marker tasks; self-verifiable."""
    arith = itertools.cycle(ARITHMETIC_PAIRS)
    markers = itertools.cycle(MARKERS)
    fixtures: list[dict[str, str]] = []
    for index in range(count):
        if index % 2 == 0:
            a, b = next(arith)
            fixtures.append(
                {
                    "title": f"arith {a}+{b}",
                    "description": f"Compute {a}+{b}. Reply with the integer result only.",
                    "expected": str(a + b),
                    "kind": "arithmetic",
                }
            )
        else:
            marker = next(markers)
            fixtures.append(
                {
                    "title": f"marker {marker}",
                    "description": (f"Reply with exactly the marker {marker} and nothing else."),
                    "expected": marker,
                    "kind": "marker",
                }
            )
    return fixtures


def output_matches(summary: str, expected: str) -> bool:
    """Exact-token match, digit-guarded so 92 does not match inside 923."""
    if not summary:
        return False
    pattern = rf"(?<![\w]){re.escape(expected)}(?![\w])"
    return re.search(pattern, summary, re.IGNORECASE) is not None


# --------------------------------------------------------------------------- #
# Worker log parsing
# --------------------------------------------------------------------------- #


def parse_worker_log(path: Path) -> list[tuple[str, dict[str, str]]]:
    """Return (event, key=value pairs) for every structlog line of one worker."""
    events: list[tuple[str, dict[str, str]]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return events
    for line in text.splitlines():
        line = _ANSI_RE.sub("", line)
        match = _LOG_LINE_RE.search(line)
        if match is None:
            continue
        pairs = {key: value for key, value in _KV_RE.findall(line)}
        events.append((match.group(1), pairs))
    return events


def count_event(events: list[tuple[str, dict[str, str]]], name: str) -> int:
    return sum(1 for event, _ in events if event == name)


def event_values(events: list[tuple[str, dict[str, str]]], name: str, key: str) -> list[str]:
    return [pairs.get(key, "") for event, pairs in events if event == name and pairs.get(key)]


@dataclass
class WorkerStats:
    role: str
    pid: int | None = None
    exit_code: int | None = None
    ready: bool = False
    log_path: str = ""
    executions: dict[str, int] = field(default_factory=dict)
    terminal_writes: dict[str, int] = field(default_factory=dict)
    cas_yields: int = 0
    backoffs: int = 0
    backoff_statuses: list[str] = field(default_factory=list)
    planner_fallbacks: int = 0
    executor_retries: int = 0
    requeued: int = 0
    anomalies: int = 0


def summarize_worker(role: str, log_path: Path, stats: WorkerStats) -> WorkerStats:
    events = parse_worker_log(log_path)
    stats.role = role
    stats.log_path = str(log_path)
    for event, pairs in events:
        if event == EXECUTION_EVENT and pairs.get("task_id"):
            task_id = pairs["task_id"]
            stats.executions[task_id] = stats.executions.get(task_id, 0) + 1
        elif event == TERMINAL_WRITE_EVENT and pairs.get("status") in TERMINAL_VALUES:
            task_id = pairs.get("task_id", "")
            stats.terminal_writes[task_id] = stats.terminal_writes.get(task_id, 0) + 1
        elif event in CAS_YIELD_EVENTS:
            stats.cas_yields += 1
        elif event == BACKOFF_EVENT:
            stats.backoffs += 1
            status = pairs.get("status")
            if status:
                stats.backoff_statuses.append(status)
        elif event == PLANNER_FALLBACK_EVENT:
            stats.planner_fallbacks += 1
        elif event in EXECUTOR_RETRY_EVENTS:
            stats.executor_retries += 1
        elif event == REQUEUE_EVENT:
            raw = pairs.get("count", "0")
            try:
                stats.requeued += int(raw)
            except ValueError:
                stats.requeued += 0
        elif event in ANOMALY_EVENTS:
            stats.anomalies += 1
    return stats


# --------------------------------------------------------------------------- #
# Worker process (child mode)
# --------------------------------------------------------------------------- #


def worker_env(run_dir: Path, *, lease_seconds: int, concurrency: int, recover: bool) -> dict:
    """Production configuration surface: file-backed shared stores via env."""
    env = os.environ.copy()
    env.update(
        {
            "AGENCY_TASKS_DB": str(run_dir / "tasks.db"),
            "AGENCY_AUDIT_DB": str(run_dir / "audit.db"),
            "AGENCY_EVIDENCE_DB": str(run_dir / "evidence.db"),
            "AGENCY_MEMORY_DB": str(run_dir / "memory.db"),
            "AGENCY_SKILL_DB_PATH": str(run_dir / "skills.db"),
            "AGENCY_TASKS_LEASE_SECONDS": str(lease_seconds),
            "AGENCY_MAX_CONCURRENT_TASKS": str(concurrency),
            "PYTHONIOENCODING": "utf-8",
        }
    )
    if recover:
        env["AGENCY_TASKS_RECOVER"] = "1"
    else:
        env.pop("AGENCY_TASKS_RECOVER", None)
    return env


async def worker_main(role: str, run_dir: Path) -> int:
    """One dispatcher process sharing the file-backed stores (child mode)."""
    import structlog

    from agency.kernel.task_store import TaskStore
    from agency.kernel.tasks import TaskManager
    from agency.orchestrator import AgencyOrchestrator

    log = structlog.get_logger("agency.soak.worker")
    store = TaskStore(os.environ["AGENCY_TASKS_DB"])
    await store.initialize()
    recovered = 0
    if os.environ.get("AGENCY_TASKS_RECOVER", "").strip() == "1":
        recovered = await store.requeue_running(
            note="soak: requeued after restart (lease expired)", expired_only=True
        )
    orchestrator = AgencyOrchestrator(
        task_manager=TaskManager(store),
        audit_db_path=os.environ["AGENCY_AUDIT_DB"],
        evidence_db_path=os.environ["AGENCY_EVIDENCE_DB"],
        memory_db_path=os.environ["AGENCY_MEMORY_DB"],
    )
    await orchestrator.start()
    await orchestrator.start_dispatcher()
    (run_dir / f"ready.{role}").write_text(str(os.getpid()), encoding="utf-8")
    log.info(
        "soak_worker_ready",
        role=role,
        pid=os.getpid(),
        recovered=recovered,
        lease_seconds=store.lease_seconds,
    )
    stop_path = run_dir / f"stop.{role}"
    try:
        while not stop_path.exists():
            await asyncio.sleep(0.5)
    finally:
        await orchestrator.stop()
        await store.close()
        log.info("soak_worker_stopped", role=role)
    return 0


@dataclass
class WorkerHandle:
    role: str
    proc: subprocess.Popen[bytes]
    log_path: Path
    pid: int | None = None

    def ready_path(self, run_dir: Path) -> Path:
        return run_dir / f"ready.{self.role}"


def spawn_worker(
    role: str, run_dir: Path, *, lease_seconds: int, concurrency: int, recover: bool
) -> WorkerHandle:
    log_path = run_dir / f"worker_{role}.log"
    log_handle = open(log_path, "wb")  # noqa: SIM115 - closed when the process is reaped
    proc = subprocess.Popen(
        [
            sys.executable,
            "-u",
            str(Path(__file__).resolve()),
            "--worker",
            role,
            "--run-dir",
            str(run_dir),
        ],
        cwd=str(REPO_ROOT),
        env=worker_env(
            run_dir, lease_seconds=lease_seconds, concurrency=concurrency, recover=recover
        ),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    return WorkerHandle(role=role, proc=proc, log_path=log_path, pid=proc.pid)


async def wait_ready(handles: list[WorkerHandle], run_dir: Path, timeout_s: float = 150.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for handle in handles:
            if handle.proc.poll() is not None:
                return False  # died before becoming ready
        if all(handle.ready_path(run_dir).exists() for handle in handles):
            for handle in handles:
                reported = _read_reported_pid(handle, run_dir)
                if reported is not None:
                    handle.pid = reported
            return True
        await asyncio.sleep(0.5)
    return False


def _read_reported_pid(handle: WorkerHandle, run_dir: Path) -> int | None:
    """The worker's own os.getpid() — uv's venv python.exe is a trampoline, so
    ``proc.pid`` is the launcher, not the interpreter that claims tasks and
    stamps lease owners. The ready file carries the authoritative pid."""
    try:
        return int(handle.ready_path(run_dir).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _owner_pid(handle: WorkerHandle) -> int:
    """The pid stamped into lease owners (worker-reported when available)."""
    return handle.pid if handle.pid is not None else handle.proc.pid


def kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """Terminate the whole process tree (uv venv trampoline + interpreter)."""
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except OSError:
        proc.kill()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=30)


def pid_alive(pid: int | None) -> bool | None:
    """Best-effort liveness probe; None when the pid is unknown."""
    if pid is None:
        return None
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ).stdout
    except OSError:
        return None
    if "INFO:" in out:
        return False
    return bool(re.search(rf"\b{pid}\b", out))


def _force_kill_pid(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except OSError:
        return


async def stop_workers(
    handles: list[WorkerHandle], run_dir: Path, timeout_s: float = 120.0
) -> list[int]:
    for handle in handles:
        (run_dir / f"stop.{handle.role}").write_text(_now(), encoding="utf-8")
    codes: list[int] = []
    deadline = time.monotonic() + timeout_s
    for handle in handles:
        remaining = max(5.0, deadline - time.monotonic())
        try:
            code = await asyncio.wait_for(asyncio.to_thread(handle.proc.wait), timeout=remaining)
        except TimeoutError:
            await asyncio.to_thread(kill_tree, handle.proc)
            code = await asyncio.to_thread(handle.proc.wait)
        codes.append(code if isinstance(code, int) else -1)
    return codes


# --------------------------------------------------------------------------- #
# Shared-store inspection helpers (harness side)
# --------------------------------------------------------------------------- #


def read_task_rows(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT task_id, status, lease_owner, lease_expires_at FROM tasks"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _lease_expired(row: dict, now: datetime) -> bool:
    raw = row.get("lease_expires_at")
    if not raw:
        return True  # pre-lease row: recoverable
    try:
        return datetime.fromisoformat(str(raw)) <= now
    except ValueError:
        return True


def rows_owned_by(rows: list[dict], pid: int, *, running_only: bool = True) -> list[dict]:
    selected = []
    for row in rows:
        owner = row.get("lease_owner") or ""
        if f":{pid}:" not in owner:
            continue
        if running_only and row.get("status") != "running":
            continue
        selected.append(row)
    return selected


async def audit_digests(audit_db: str) -> dict[str, str]:
    from agency.kernel.audit import AuditFilter, AuditLog

    async with AuditLog(audit_db) as log:
        entries = await log.query(AuditFilter(limit=10000))
    digests: dict[str, str] = {}
    for entry in entries:
        payload = "|".join(
            [
                entry.as_ts(),
                entry.agent or "",
                entry.task or "",
                entry.target or "",
                entry.action if isinstance(entry.action, str) else str(entry.action),
                entry.result,
            ]
        )
        digests[entry.entry_id] = _digest(payload)
    return digests


async def audit_action_counts(audit_db: str) -> dict[str, int]:
    from agency.kernel.audit import AuditFilter, AuditLog

    async with AuditLog(audit_db) as log:
        entries = await log.query(AuditFilter(limit=10000))
    counts: dict[str, int] = {}
    for entry in entries:
        action = entry.action if isinstance(entry.action, str) else str(entry.action)
        counts[action] = counts.get(action, 0) + 1
    return counts


async def evidence_finding_count(evidence_db: str) -> int:
    from agency.evidence.store.store import EvidenceStore, FindingsFilter

    store = EvidenceStore(evidence_db)
    await store.initialize()
    try:
        findings = await store.list_findings(FindingsFilter(limit=1000))
        return len(findings)
    finally:
        await store.close()


# --------------------------------------------------------------------------- #
# Legs
# --------------------------------------------------------------------------- #


@dataclass
class LegContext:
    name: str
    run_dir: Path
    tasks_db: str
    audit_db: str
    evidence_db: str
    lease_seconds: int
    concurrency: int
    timeout_s: float


async def submit_tasks(manager, fixtures: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    submitted: dict[str, dict[str, str]] = {}
    for fixture in fixtures:
        task = await manager.create_task(
            title=fixture["title"],
            created_by="soak-harness",
            input={"description": fixture["description"]},
        )
        submitted[task.task_id] = fixture
    return submitted


async def wait_all_terminal(
    manager, task_ids: list[str], timeout_s: float
) -> tuple[bool, dict[str, list[tuple[str, str]]]]:
    transitions: dict[str, list[tuple[str, str]]] = {task_id: [] for task_id in task_ids}
    deadline = time.monotonic() + timeout_s
    while True:
        all_terminal = True
        for task_id in task_ids:
            task = await manager.get_task(task_id)
            status = task.status.value if task is not None else "missing"
            history = transitions[task_id]
            if not history or history[-1][1] != status:
                history.append((_now(), status))
            if task is None or task.status.value not in TERMINAL_VALUES:
                all_terminal = False
        if all_terminal:
            return True, transitions
        if time.monotonic() >= deadline:
            return False, transitions
        await asyncio.sleep(2.0)


async def cas_terminal_probe(store, task_id: str) -> tuple[bool, str]:
    """Attempt a write against an immutable terminal row; the store must yield."""
    from agency.kernel.tasks import TaskStatus, TaskWriteResult

    task = await store.get_task(task_id)
    if task is None:
        return False, "missing"
    status = TaskStatus(task.status.value)
    outcome = await store.update_task(task_id, status=status, expected_status=status)
    return outcome is TaskWriteResult.YIELDED_TERMINAL, outcome.value


def aggregate_counts(
    workers: list[WorkerStats],
    task_records: list[dict],
    audit_counts: dict[str, int],
    findings: int,
    transitions: dict[str, list[tuple[str, str]]],
) -> dict:
    executions = aggregate_per_task(workers, "executions")
    terminal_writes = aggregate_per_task(workers, "terminal_writes")
    statuses = {record["task_id"]: record["final_status"] for record in task_records}
    return {
        "tasks_total": len(task_records),
        "terminal": sum(1 for status in statuses.values() if status in TERMINAL_VALUES),
        "completed": sum(1 for status in statuses.values() if status == "completed"),
        "failed": sum(1 for status in statuses.values() if status == "failed"),
        "blocked": sum(1 for status in statuses.values() if status == "blocked"),
        "backoffs_total": sum(worker.backoffs for worker in workers),
        "planner_fallbacks_total": sum(worker.planner_fallbacks for worker in workers),
        "executor_retries_total": sum(worker.executor_retries for worker in workers),
        "cas_yields_observed": sum(worker.cas_yields for worker in workers),
        "worker_anomalies_total": sum(worker.anomalies for worker in workers),
        "audit_entries_total": sum(audit_counts.values()),
        "audit_subtask_entries": sum(
            count for action, count in audit_counts.items() if action.startswith("subtask_")
        ),
        "evidence_findings": findings,
        "status_transition_counts": {
            task_id: len(history) for task_id, history in transitions.items()
        },
        "executions_per_task": executions,
        "terminal_writes_per_task": terminal_writes,
    }


def aggregate_per_task(workers: list[WorkerStats], attribute: str) -> dict[str, int]:
    merged: dict[str, int] = {}
    for worker in workers:
        for task_id, count in getattr(worker, attribute).items():
            merged[task_id] = merged.get(task_id, 0) + count
    return merged


def task_record(
    task_id: str, fixture: dict[str, str], task, executions: int, terminal_writes: int
) -> dict:
    summary = ""
    if task is not None and isinstance(task.output, dict):
        summary = str(task.output.get("summary", "") or "")
    return {
        "task_id": task_id,
        "kind": fixture["kind"],
        "expected": fixture["expected"],
        "final_status": task.status.value if task is not None else "missing",
        "output_digest": _digest(summary),
        "output_head": summary[:120],
        "output_match": output_matches(summary, fixture["expected"])
        if task is not None and task.status.value == "completed"
        else None,
        "executions": executions,
        "terminal_writes": terminal_writes,
        "error": task.error if task is not None else None,
    }


def build_worker_summaries(handles: list[WorkerHandle], codes: list[int]) -> list[WorkerStats]:
    summaries = [
        summarize_worker(handle.role, handle.log_path, WorkerStats(role=handle.role))
        for handle in handles
    ]
    for summary, handle, code in zip(summaries, handles, codes, strict=False):
        summary.pid = handle.proc.pid
        summary.exit_code = code
    return summaries


# --------------------------------------------------------------------------- #
# Leg 1: concurrency
# --------------------------------------------------------------------------- #


async def run_concurrency_leg(ctx: LegContext, fixtures: list[dict[str, str]]) -> dict:
    from agency.kernel.task_store import TaskStore
    from agency.kernel.tasks import TaskManager

    timeline: dict[str, str | int | None] = {}
    reasons: list[str] = []
    store = TaskStore(ctx.tasks_db)
    await store.initialize()
    manager = TaskManager(store)
    timeline["submitted_at"] = _now()
    submitted = await submit_tasks(manager, fixtures)
    task_ids = list(submitted)

    handles = [
        spawn_worker(
            role,
            ctx.run_dir,
            lease_seconds=ctx.lease_seconds,
            concurrency=ctx.concurrency,
            recover=False,
        )
        for role in ("w1", "w2")
    ]
    timeline["workers_spawned_at"] = _now()
    ready = await wait_ready(handles, ctx.run_dir)
    timeline["workers_ready_at"] = _now() if ready else None
    if not ready:
        reasons.append("workers failed to become ready")

    all_terminal, transitions = await wait_all_terminal(manager, task_ids, ctx.timeout_s)
    timeline["all_terminal_at"] = _now() if all_terminal else None
    if not all_terminal:
        reasons.append(f"not all tasks reached a terminal state within {ctx.timeout_s:.0f}s")

    codes = await stop_workers(handles, ctx.run_dir)
    timeline["workers_stopped_at"] = _now()

    workers = build_worker_summaries(handles, codes)
    executions = aggregate_per_task(workers, "executions")
    terminal_writes = aggregate_per_task(workers, "terminal_writes")
    task_records = []
    for task_id, fixture in submitted.items():
        task = await manager.get_task(task_id)
        task_records.append(
            task_record(
                task_id, fixture, task, executions.get(task_id, 0), terminal_writes.get(task_id, 0)
            )
        )

    audit_counts = await audit_action_counts(ctx.audit_db)
    findings = await evidence_finding_count(ctx.evidence_db)
    cas_probe = []
    for record in task_records:
        if record["final_status"] in TERMINAL_VALUES:
            defended, outcome = await cas_terminal_probe(store, record["task_id"])
            cas_probe.append(
                {"task_id": record["task_id"], "defended": defended, "outcome": outcome}
            )

    counts = aggregate_counts(workers, task_records, audit_counts, findings, transitions)
    counts["cas_probe_attempts"] = len(cas_probe)
    counts["cas_probe_defended"] = sum(1 for probe in cas_probe if probe["defended"])

    fabricated = [
        record
        for record in task_records
        if record["final_status"] == "completed" and not record["output_head"]
    ]
    mismatches = [
        record
        for record in task_records
        if record["final_status"] == "completed" and record["output_match"] is False
    ]
    duplicate_writes = {
        task_id: count
        for task_id, count in counts["terminal_writes_per_task"].items()
        if count != 1
    }
    duplicate_executions = {
        task_id: count for task_id, count in counts["executions_per_task"].items() if count != 1
    }
    undefended = [probe for probe in cas_probe if not probe["defended"]]

    if fabricated:
        reasons.append(f"completed tasks with empty output (forced pass): {len(fabricated)}")
    if mismatches:
        reasons.append(f"completed tasks whose output lacks the expected marker: {len(mismatches)}")
    if duplicate_writes:
        reasons.append(f"tasks with != 1 terminal CAS write: {duplicate_writes}")
    if duplicate_executions:
        reasons.append(f"tasks executed more than once: {duplicate_executions}")
    if undefended:
        reasons.append(f"terminal CAS probe not defended: {undefended}")

    await store.close()
    verdict = "pass" if not reasons else "fail"
    return {
        "verdict": verdict,
        "reasons": reasons or ["all tasks terminal, single execution per task, outputs verified"],
        "timeline": timeline,
        "counts": counts,
        "tasks": task_records,
        "cas_probe": cas_probe,
        "workers": [_worker_evidence(worker) for worker in workers],
        "audit_by_action": audit_counts,
        "run_dir": str(ctx.run_dir),
    }


def _worker_evidence(worker: WorkerStats) -> dict:
    return {
        "role": worker.role,
        "pid": worker.pid,
        "exit_code": worker.exit_code,
        "backoffs": worker.backoffs,
        "backoff_statuses": worker.backoff_statuses,
        "planner_fallbacks": worker.planner_fallbacks,
        "executor_retries": worker.executor_retries,
        "cas_yields": worker.cas_yields,
        "requeued": worker.requeued,
        "anomalies": worker.anomalies,
        "log_path": worker.log_path,
    }


# --------------------------------------------------------------------------- #
# Leg 2: restart
# --------------------------------------------------------------------------- #


async def run_restart_leg(ctx: LegContext, fixtures: list[dict[str, str]]) -> dict:
    from agency.kernel.task_store import TaskStore
    from agency.kernel.tasks import TaskManager

    timeline: dict[str, object] = {}
    reasons: list[str] = []
    store = TaskStore(ctx.tasks_db)
    await store.initialize()
    manager = TaskManager(store)
    timeline["submitted_at"] = _now()
    submitted = await submit_tasks(manager, fixtures)
    task_ids = list(submitted)

    handles = [
        spawn_worker(
            role,
            ctx.run_dir,
            lease_seconds=ctx.lease_seconds,
            concurrency=ctx.concurrency,
            recover=False,
        )
        for role in ("w1", "w2")
    ]
    ready = await wait_ready(handles, ctx.run_dir)
    timeline["workers_ready_at"] = _now() if ready else None
    if not ready:
        reasons.append("workers failed to become ready before the kill")

    # Wait until the designated victim owns at least one RUNNING claim.
    victim, survivor = handles[0], handles[1]
    victim_owned: list[dict] = []
    deadline = time.monotonic() + 90.0
    while time.monotonic() < deadline:
        rows = read_task_rows(ctx.tasks_db)
        victim_owned = rows_owned_by(rows, _owner_pid(victim))
        if victim_owned:
            break
        if survivor.proc.poll() is None and rows_owned_by(rows, _owner_pid(survivor)):
            # Victim never claimed (provider raced ahead); swap roles honestly.
            victim, survivor = survivor, victim
            victim_owned = rows_owned_by(rows, _owner_pid(victim))
            if victim_owned:
                break
        await asyncio.sleep(1.0)
    if not victim_owned:
        reasons.append("no RUNNING claim owned by a killable worker before the deadline")

    kill_ok = bool(victim_owned)
    if kill_ok:
        # Short grace: let at least one subtask audit row land so the
        # append-only check has content, while the victim's claim stays
        # mid-flight. If the claim completes first, swap to a worker that
        # still owns one; if none does, kill anyway and record it honestly.
        grace_deadline = time.monotonic() + 15.0
        while time.monotonic() < grace_deadline:
            has_audit = bool(await audit_digests(ctx.audit_db))
            rows = read_task_rows(ctx.tasks_db)
            if rows_owned_by(rows, _owner_pid(victim)):
                if has_audit:
                    break
            elif rows_owned_by(rows, _owner_pid(survivor)):
                victim, survivor = survivor, victim
                continue
            else:
                break  # nothing mid-flight left to orphan
            await asyncio.sleep(1.0)
        victim_owned = rows_owned_by(read_task_rows(ctx.tasks_db), _owner_pid(victim))

    # Pre-kill snapshot: task rows, audit digests, terminal output digests.
    pre_kill_rows = read_task_rows(ctx.tasks_db)
    pre_kill_audit = await audit_digests(ctx.audit_db)
    pre_kill_terminal: dict[str, dict] = {}
    for task_id in task_ids:
        task = await manager.get_task(task_id)
        if task is not None and task.status.value in TERMINAL_VALUES:
            summary = (
                str(task.output.get("summary", "") or "") if isinstance(task.output, dict) else ""
            )
            pre_kill_terminal[task_id] = {
                "status": task.status.value,
                "output_digest": _digest(summary),
            }
    timeline["pre_kill_snapshot"] = {
        "tasks": [
            {
                "task_id": row["task_id"],
                "status": row["status"],
                "owner_pid": (row.get("lease_owner") or "").split(":")[1]
                if (row.get("lease_owner") or "").count(":") >= 1
                else None,
                "lease_expires_at": row.get("lease_expires_at"),
            }
            for row in pre_kill_rows
        ],
        "audit_entries": len(pre_kill_audit),
        "terminal_tasks": sorted(pre_kill_terminal),
    }

    orphans: list[dict] = []
    if kill_ok:
        await asyncio.to_thread(kill_tree, victim.proc)
        reported_pid = _owner_pid(victim)
        # uv's venv python.exe trampoline: /T should take the interpreter child
        # too; if the reported (lease-owning) pid somehow survives, finish it.
        if pid_alive(reported_pid):
            await asyncio.to_thread(_force_kill_pid, reported_pid)
        # Post-kill read is authoritative: whatever the victim still holds
        # RUNNING now is exactly what recovery must find after expiry.
        orphans = rows_owned_by(read_task_rows(ctx.tasks_db), _owner_pid(victim))
        timeline["kill"] = {
            "at": _now(),
            "worker": victim.role,
            "reported_pid": reported_pid,
            "reported_pid_dead": pid_alive(reported_pid) is False,
            "trampoline_pid": victim.proc.pid,
            "running_owned_by_victim": [row["task_id"] for row in victim_owned],
            "orphaned_running_claims": [row["task_id"] for row in orphans],
        }
        if not orphans:
            reasons.append(
                "kill orphaned no RUNNING claim (claims completed first); "
                "restart recovery semantics were not exercised"
            )
    else:
        timeline["kill"] = {
            "at": _now(),
            "worker": None,
            "reported_pid": None,
            "reported_pid_dead": None,
            "trampoline_pid": None,
            "running_owned_by_victim": [],
            "orphaned_running_claims": [],
        }

    # Lease discipline probe: an expired-only recovery right after the kill
    # must requeue exactly the claims whose lease already lapsed — the
    # victim's fresh leases (and the survivor's live ones) stay untouched.
    now = datetime.now(UTC)
    rows_now = read_task_rows(ctx.tasks_db)
    expected_expired = sum(
        1 for row in rows_now if row["status"] == "running" and _lease_expired(row, now)
    )
    probe_requeued = await store.requeue_running(
        note="soak probe (expired-only, immediately after kill)", expired_only=True
    )
    timeline["probe_after_kill"] = {
        "at": _now(),
        "requeued": probe_requeued,
        "running_rows": sum(1 for row in rows_now if row["status"] == "running"),
        "expired_lease_rows": expected_expired,
        "leased_claims_left_alone": probe_requeued == expected_expired,
    }
    if probe_requeued != expected_expired:
        reasons.append(
            f"post-kill expired-only probe requeued {probe_requeued} rows but only "
            f"{expected_expired} had lapsed leases (fresh claims were touched)"
        )

    # Wait for the victim's leases to lapse, then start the recovery process.
    expiry_deadline = time.monotonic() + ctx.lease_seconds + 90.0
    while time.monotonic() < expiry_deadline:
        now = datetime.now(UTC)
        rows = read_task_rows(ctx.tasks_db)
        stale = [
            row for row in rows_owned_by(rows, _owner_pid(victim)) if not _lease_expired(row, now)
        ]
        if not stale:
            break
        await asyncio.sleep(2.0)
    timeline["victim_leases_expired_at"] = _now()

    recovery = spawn_worker(
        "w3",
        ctx.run_dir,
        lease_seconds=ctx.lease_seconds,
        concurrency=ctx.concurrency,
        recover=True,
    )
    timeline["recovery_worker_spawned_at"] = _now()
    recovery_ready = await wait_ready([recovery], ctx.run_dir)
    timeline["recovery_worker_ready_at"] = _now() if recovery_ready else None
    if not recovery_ready:
        reasons.append("recovery worker failed to become ready")

    all_terminal, transitions = await wait_all_terminal(manager, task_ids, ctx.timeout_s)
    timeline["all_terminal_at"] = _now() if all_terminal else None
    if not all_terminal:
        reasons.append(f"not all tasks reached a terminal state within {ctx.timeout_s:.0f}s")

    live_handles = [handle for handle in handles if handle.proc.poll() is None] + [recovery]
    codes = await stop_workers(live_handles, ctx.run_dir)
    timeline["workers_stopped_at"] = _now()

    # The victim's log is summarized separately only when it was actually
    # killed; otherwise the live-handle pass already parsed it once.
    workers = build_worker_summaries(live_handles, codes)
    if kill_ok:
        victim_log_stats = summarize_worker(
            victim.role, victim.log_path, WorkerStats(role=victim.role)
        )
        victim_log_stats.pid = _owner_pid(victim)
        victim_log_stats.exit_code = victim.proc.returncode
        workers.append(victim_log_stats)

    executions = aggregate_per_task(workers, "executions")
    terminal_writes = aggregate_per_task(workers, "terminal_writes")
    orphan_ids = {row["task_id"] for row in orphans}
    task_records = []
    for task_id, fixture in submitted.items():
        task = await manager.get_task(task_id)
        record = task_record(
            task_id, fixture, task, executions.get(task_id, 0), terminal_writes.get(task_id, 0)
        )
        record["owned_by_killed_worker"] = task_id in {row["task_id"] for row in victim_owned}
        record["orphaned_by_kill"] = task_id in orphan_ids
        task_records.append(record)

    audit_counts = await audit_action_counts(ctx.audit_db)
    findings = await evidence_finding_count(ctx.evidence_db)
    post_kill_audit = await audit_digests(ctx.audit_db)
    missing_audit = [
        entry_id
        for entry_id in pre_kill_audit
        if post_kill_audit.get(entry_id) != pre_kill_audit[entry_id]
    ]
    audit_intact = not missing_audit

    cas_probe = []
    for record in task_records:
        if record["final_status"] in TERMINAL_VALUES:
            defended, outcome = await cas_terminal_probe(store, record["task_id"])
            cas_probe.append(
                {"task_id": record["task_id"], "defended": defended, "outcome": outcome}
            )

    counts = aggregate_counts(workers, task_records, audit_counts, findings, transitions)
    counts["cas_probe_attempts"] = len(cas_probe)
    counts["cas_probe_defended"] = sum(1 for probe in cas_probe if probe["defended"])
    counts["audit_append_only_intact"] = audit_intact
    counts["audit_entries_before_kill"] = len(pre_kill_audit)
    counts["audit_entries_after"] = len(post_kill_audit)
    recovered = [
        record
        for record in task_records
        if record["orphaned_by_kill"] and record["final_status"] in TERMINAL_VALUES
    ]
    counts["recovered_tasks_terminal"] = len(recovered)
    counts["orphaned_running_claims"] = len(orphans)
    recovery_requeued = sum(worker.requeued for worker in workers if worker.role == "w3")
    counts["recovery_requeued"] = recovery_requeued
    counts["claims_still_leased_at_recovery"] = (
        timeline["probe_after_kill"]["running_rows"]
        - timeline["probe_after_kill"]["expired_lease_rows"]
        if kill_ok
        else 0
    )
    notes: list[str] = []
    if kill_ok and recovery_requeued < len(orphans):
        reasons.append(
            f"recovery requeued {recovery_requeued} but the kill orphaned "
            f"{len(orphans)} RUNNING claim(s)"
        )
    elif kill_ok and recovery_requeued > len(orphans):
        notes.append(
            f"recovery requeued {recovery_requeued} vs {len(orphans)} orphaned claims: a "
            "survivor's lease lapsed during the recovery window (documented "
            "hung-but-alive limit); the extra claim was re-executed to terminal"
        )

    # No task lost, no terminal record overwritten.
    missing_tasks = [
        record["task_id"] for record in task_records if record["final_status"] == "missing"
    ]
    if missing_tasks:
        reasons.append(f"tasks missing from the store after recovery: {missing_tasks}")
    overwritten = []
    for record in task_records:
        before = pre_kill_terminal.get(record["task_id"])
        if before is None:
            continue
        if (
            record["final_status"] != before["status"]
            or record["output_digest"] != before["output_digest"]
        ):
            overwritten.append(record["task_id"])
    if overwritten:
        reasons.append(f"pre-kill terminal records were overwritten: {overwritten}")
    if not audit_intact:
        reasons.append(f"pre-kill audit entries missing or altered: {len(missing_audit)}")

    fabricated = [
        record
        for record in task_records
        if record["final_status"] == "completed" and not record["output_head"]
    ]
    mismatches = [
        record
        for record in task_records
        if record["final_status"] == "completed" and record["output_match"] is False
    ]
    if fabricated:
        reasons.append(f"completed tasks with empty output (forced pass): {len(fabricated)}")
    if mismatches:
        reasons.append(f"completed tasks whose output lacks the expected marker: {len(mismatches)}")
    duplicate_writes = {
        task_id: count
        for task_id, count in counts["terminal_writes_per_task"].items()
        if count != 1
    }
    if duplicate_writes:
        reasons.append(f"tasks with != 1 terminal CAS write: {duplicate_writes}")
    undefended = [probe for probe in cas_probe if not probe["defended"]]
    if undefended:
        reasons.append(f"terminal CAS probe not defended: {undefended}")

    await store.close()
    verdict = "pass" if not reasons else "fail"
    return {
        "verdict": verdict,
        "reasons": reasons
        or [
            (
                "all tasks terminal after kill+recovery; leased claims untouched until expiry; "
                "no terminal record overwritten; audit append-only intact"
            )
        ],
        "notes": notes,
        "timeline": timeline,
        "counts": counts,
        "tasks": task_records,
        "cas_probe": cas_probe,
        "workers": [_worker_evidence(worker) for worker in workers],
        "audit_by_action": audit_counts,
        "run_dir": str(ctx.run_dir),
    }


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def secrets_scan(evidence_text: str, env_path: Path) -> list[str]:
    """Return names of .env secret values that leaked into the evidence text."""
    leaked: list[str] = []
    if not env_path.exists():
        return leaked
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if not value or len(value) < 16:
            continue
        if (
            "KEY" in key or "TOKEN" in key or "SECRET" in key or "PASSWORD" in key
        ) and value in evidence_text:
            leaked.append(key)
    return leaked


def resolve_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        return out.stdout.strip() or COMMIT_PLACEHOLDER
    except (OSError, subprocess.SubprocessError):
        return COMMIT_PLACEHOLDER


def provider_record() -> dict:
    provider = os.environ.get("AGENCY_LLM_PROVIDER", "")
    model = os.environ.get("AGENCY_LLM_MODEL", "")
    base_url = os.environ.get("AGENCY_LLM_BASE_URL", "")
    return {
        "provider": provider,
        "model": model,
        "base_url_host": base_url.split("//")[-1].split("/")[0] if base_url else "",
        "key_present": bool(os.environ.get("AGENCY_LLM_API_KEY")),
    }


async def run_leg(leg: str, args: argparse.Namespace, run_root: Path) -> dict:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    run_dir = run_root / f"{leg}-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    ctx = LegContext(
        name=leg,
        run_dir=run_dir,
        tasks_db=str(run_dir / "tasks.db"),
        audit_db=str(run_dir / "audit.db"),
        evidence_db=str(run_dir / "evidence.db"),
        lease_seconds=args.lease_seconds,
        concurrency=args.concurrency,
        timeout_s=args.timeout,
    )
    if leg == "concurrency":
        fixtures = build_fixtures(args.tasks)
        return await run_concurrency_leg(ctx, fixtures)
    fixtures = build_fixtures(args.restart_tasks)
    return await run_restart_leg(ctx, fixtures)


async def async_main(args: argparse.Namespace) -> int:
    load_dotenv(REPO_ROOT / ".env")
    provider = provider_record()
    if provider["provider"] == "echo":
        print("refusing to soak against the echo provider; configure a live .env", flush=True)
        return 2

    evidence_path = Path(args.evidence)
    evidence: dict = {
        "generated_at": _now(),
        "commit": resolve_commit(),
        "provider": provider,
        "config": {
            "tasks_concurrency_leg": args.tasks,
            "tasks_restart_leg": args.restart_tasks,
            "concurrency_per_worker": args.concurrency,
            "lease_seconds": args.lease_seconds,
            "leg_timeout_s": args.timeout,
        },
        "legs": {},
    }
    _write_evidence(evidence, evidence_path)

    exit_code = 0
    for leg in args.legs:
        print(f"=== soak leg: {leg} ===", flush=True)
        try:
            result = await run_leg(leg, args, Path(args.run_root))
        except Exception as exc:  # noqa: BLE001 - the evidence must record harness faults too
            result = {
                "verdict": "fail",
                "reasons": [f"harness exception: {type(exc).__name__}: {exc}"],
            }
        evidence["legs"][leg] = result
        _write_evidence(evidence, evidence_path)
        print(
            f"=== leg {leg}: {result['verdict']} ({'; '.join(result['reasons'][:3])})",
            flush=True,
        )
        if result["verdict"] != "pass":
            exit_code = 1
    print(f"evidence: {evidence_path}", flush=True)
    return exit_code


def _write_evidence(evidence: dict, evidence_path: Path) -> None:
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(evidence, indent=2, default=str)
    leaked = secrets_scan(text, REPO_ROOT / ".env")
    if leaked:
        raise SystemExit(f"evidence would leak secret values for: {leaked}; refusing to write")
    evidence_path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=10, help="concurrency-leg task count")
    parser.add_argument("--restart-tasks", type=int, default=6, help="restart-leg task count")
    parser.add_argument("--concurrency", type=int, default=2, help="dispatcher slots per worker")
    parser.add_argument("--lease-seconds", type=int, default=45, help="claim lease TTL")
    parser.add_argument(
        "--timeout", type=float, default=2400.0, help="per-leg terminal wait, seconds"
    )
    parser.add_argument("--evidence", default=str(DEFAULT_EVIDENCE_PATH), help="evidence JSON path")
    parser.add_argument("--leg", action="append", choices=["concurrency", "restart"], dest="legs")
    parser.add_argument("--run-root", default=os.path.join(tempfile.gettempdir(), "agency-soak"))
    parser.add_argument("--worker", help="internal: run one dispatcher process (child mode)")
    parser.add_argument("--run-dir", help="internal: shared leg directory (child mode)")
    args = parser.parse_args(argv)
    if args.legs is None:
        args.legs = ["concurrency", "restart"]

    os.chdir(REPO_ROOT)
    if args.worker:
        if not args.run_dir:
            parser.error("--worker requires --run-dir")
        return asyncio.run(worker_main(args.worker, Path(args.run_dir)))
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
