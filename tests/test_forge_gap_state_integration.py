"""Focused tests for Forge gaps 6 (lease/heartbeat/trace) and 7 (integration gate)."""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from agency.cli.main import app
from agency.forge.evidence import EvidenceStore
from agency.forge.external_agent import ExternalAgentReport
from agency.forge.inspection import InspectionReport
from agency.forge.integration import propose_integration
from agency.forge.merge_queue import MergeQueueStore
from agency.forge.observability import PhaseTiming
from agency.forge.orchestration import (
    BlacksmithOps,
    JobState,
    heartbeat_age_s,
    lease_state,
    trace_from_report,
)
from agency.forge.security_scan import SecurityReport
from tests._fake_paths import FAKE_REPO


class ReportedAgentReport(ExternalAgentReport):
    """A worker that really reports duration/cost (rare, but honoured)."""

    agent_duration_s: float | None = None
    estimated_cost_usd: float | None = None


def _base_report(
    gate: str = "PASS", *, phases: tuple[PhaseTiming, ...] = ()
) -> ExternalAgentReport:
    now = datetime.now(UTC)
    root = "D:/fake/worktree"
    return ExternalAgentReport(
        report_id=str(uuid4()),
        created_at=now,
        worktree_path=root,
        branch="blacksmith-test",
        task="t",
        exit_code=0 if gate == "PASS" else 1,
        changed_paths=("src/x.py",),
        phases=phases,
        gate=gate,  # type: ignore[arg-type]
        inspection=InspectionReport(report_id=str(uuid4()), created_at=now, root=root, gate="PASS"),
        security=SecurityReport(
            report_id=str(uuid4()),
            created_at=now,
            root=root,
            gate="PASS",
            scanned_files=1,
            skipped_files=0,
        ),
    )


def _ops(tmp_path: Path, runner: object, **over: object) -> tuple[BlacksmithOps, list[str]]:
    events: list[str] = []
    ops = BlacksmithOps(
        str(tmp_path / "jobs.db"),
        runner=runner,  # type: ignore[arg-type]
        evidence=EvidenceStore(str(tmp_path / "evidence.db")),
        merge_queue=MergeQueueStore(str(tmp_path / "mq.db")),
        audit_sink=lambda event, _payload: events.append(event),
        **over,  # type: ignore[arg-type]
    )
    return ops, events


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _make_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    for key, value in (("user.email", "p@local"), ("user.name", "p")):
        _git(repo, "config", key, value)
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "branch", "integration")
    for branch, filename in (("a", "a.txt"), ("b", "b.txt")):
        _git(repo, "checkout", "-q", "integration")
        _git(repo, "checkout", "-qb", branch)
        (repo / filename).write_text(branch, encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", branch)
    work = tmp_path / "combined"
    work.mkdir()
    _git(work, "init", "-q")
    _git(work, "config", "user.email", "p@local")
    _git(work, "config", "user.name", "p")
    return repo, work


# ---------------------------------------------------------------- gap 6: lease
def test_live_heartbeat_keeps_dispatched_job_out_of_uncertain(tmp_path: Path) -> None:
    release = threading.Event()
    started = threading.Event()

    def runner(*_a: object, **_k: object) -> ExternalAgentReport:
        started.set()
        assert release.wait(5)
        return _base_report()

    ops, _events = _ops(tmp_path, runner, heartbeat_interval_s=0.05, lease_ttl_s=5.0)
    probe = BlacksmithOps(str(tmp_path / "jobs.db"), lease_ttl_s=5.0)
    worker: threading.Thread | None = None
    try:
        job = ops.submit("t", "task", FAKE_REPO)
        worker = threading.Thread(target=ops.dispatch_next, daemon=True)
        worker.start()
        assert started.wait(3)
        first = probe.get(job.job_id).heartbeat_at
        assert first  # lease taken at dispatch
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if probe.get(job.job_id).heartbeat_at != first:
                break
            time.sleep(0.02)
        live = probe.get(job.job_id)
        assert live.heartbeat_at != first  # refreshed by the bounded helper
        assert live.state is JobState.DISPATCHED
        assert lease_state(live, ttl_s=5.0) == "LIVE"
        # A fresh process reopening the DB must not call it UNCERTAIN.
        reader = BlacksmithOps(str(tmp_path / "jobs.db"), lease_ttl_s=5.0)
        try:
            assert reader.get(job.job_id).state is JobState.DISPATCHED
        finally:
            reader.close()
    finally:
        release.set()
        if worker is not None:
            worker.join(timeout=5)
        probe.close()
        ops.close()


def test_stale_lease_is_reaped_as_uncertain(tmp_path: Path) -> None:
    ops, _events = _ops(tmp_path, _base_report, lease_ttl_s=2.0)
    try:
        job = ops.submit("t", "task", FAKE_REPO)
        stale = job.model_copy(
            update={
                "state": JobState.DISPATCHED,
                "heartbeat_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            }
        )
        ops._conn.execute(
            "UPDATE forge_jobs SET state = ?, document = ? WHERE job_id = ?",
            (JobState.DISPATCHED.value, stale.model_dump_json(), job.job_id),
        )
        ops._conn.commit()
    finally:
        ops.close()
    reopened, events = _ops(tmp_path, _base_report, lease_ttl_s=2.0)
    try:
        recovered = reopened.get(job.job_id)
        assert recovered.state is JobState.UNCERTAIN
        assert lease_state(recovered, ttl_s=2.0) == "IDLE"
        assert "job_uncertain" in events
        assert reopened.dispatch_next() is None  # never auto-resumed
    finally:
        reopened.close()


def test_heartbeat_is_bounded_and_scoped_to_dispatched(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        BlacksmithOps(str(tmp_path / "bad.db"), heartbeat_interval_s=31.0)
    ops, events = _ops(tmp_path, _base_report)
    try:
        with pytest.raises(ValueError):
            ops.heartbeat("  ")
        assert ops.heartbeat("unknown-job") is None
        job = ops.submit("t", "task", FAKE_REPO)
        assert ops.heartbeat(job.job_id) is None  # QUEUED is not leased
        dispatched = job.model_copy(update={"state": JobState.DISPATCHED})
        ops._conn.execute(
            "UPDATE forge_jobs SET state = ?, document = ? WHERE job_id = ?",
            (JobState.DISPATCHED.value, dispatched.model_dump_json(), job.job_id),
        )
        ops._conn.commit()
        before = len(events)
        updated = ops.heartbeat(job.job_id, owner="x" * 200)
        assert updated is not None
        assert updated.lease_owner == "x" * 64  # bounded label
        assert heartbeat_age_s(updated.heartbeat_at) is not None
        assert len(events) == before  # a beat is not a state transition
        assert heartbeat_age_s("not-a-timestamp") is None
    finally:
        ops.close()


# -------------------------------------------------------------- gap 6: traces
def test_traces_carry_duration_and_cost_only_when_reported(tmp_path: Path) -> None:
    bare = trace_from_report(
        SimpleNamespace(phases=(PhaseTiming(name="tests", duration_s=1.0),)), job_id="j"
    )
    assert bare.agent_duration_s is None and bare.estimated_cost_usd is None
    assert "agent_duration_not_reported" in bare.caveats
    assert "estimated_cost_not_reported" in bare.caveats

    phased = trace_from_report(
        SimpleNamespace(phases=(PhaseTiming(name="agent", duration_s=2.5),)), job_id="j"
    )
    assert phased.agent_duration_s == 2.5 and phased.estimated_cost_usd is None

    reported = ReportedAgentReport(
        **_base_report().model_dump(), agent_duration_s=3.5, estimated_cost_usd=0.0042
    )
    ops, _events = _ops(tmp_path / "a", lambda *_a, **_k: reported)
    try:
        ops.submit("t", "task", FAKE_REPO)
        done = ops.dispatch_next()
        assert done is not None and done.trace is not None
        assert done.trace.agent_duration_s == 3.5
        assert done.trace.estimated_cost_usd == 0.0042
    finally:
        ops.close()

    # A normal report has neither field: Forge records no invented numbers.
    ops2, _events2 = _ops(tmp_path / "b", lambda *_a, **_k: _base_report())
    try:
        ops2.submit("t", "task", FAKE_REPO)
        done2 = ops2.dispatch_next()
        assert done2 is not None and done2.trace is not None
        assert done2.trace.agent_duration_s is None
        assert done2.trace.estimated_cost_usd is None
    finally:
        ops2.close()


# ------------------------------------------------------- gap 7: integration gate
def test_integration_runs_suite_and_marks_ready(tmp_path: Path) -> None:
    repo, work = _make_repo(tmp_path)
    command = (
        sys.executable,
        "-c",
        (
            "import pathlib; assert pathlib.Path('a.txt').read_text() == 'a'; "
            "assert pathlib.Path('b.txt').read_text() == 'b'"
        ),
    )
    report = propose_integration(
        repo,
        ["a", "b"],
        worktree=work,
        output_branch="integration",
        test_command=command,
        test_timeout_s=10,
    )
    assert report.gate == "PASS"
    assert report.test_gate == "PASS"
    assert report.test_returncode == 0
    assert report.ready is True
    assert set(report.combined) == {"a.txt", "b.txt"}
    assert _git(work, "rev-list", "--count", "HEAD") == "1"
    assert _git(work, "remote") == ""  # nothing pushed, no remote added


def test_integration_failure_blocks_readiness(tmp_path: Path) -> None:
    repo, work = _make_repo(tmp_path)
    report = propose_integration(
        repo,
        ["a", "b"],
        worktree=work,
        output_branch="integration",
        test_command=(sys.executable, "-c", "raise SystemExit(7)"),
        test_timeout_s=10,
    )
    assert report.gate == "PASS"  # combination itself succeeded
    assert report.test_gate == "FAIL"
    assert report.test_returncode == 7
    assert report.ready is False
    assert _git(work, "remote") == ""


def test_integration_timeout_is_bounded(tmp_path: Path) -> None:
    repo, work = _make_repo(tmp_path)
    start = time.monotonic()
    report = propose_integration(
        repo,
        ["a"],
        worktree=work,
        output_branch="integration",
        test_command=(sys.executable, "-c", "import time; time.sleep(5)"),
        test_timeout_s=1,
    )
    assert time.monotonic() - start < 4
    assert report.test_gate == "FAIL"
    assert report.test_timed_out is True
    assert report.ready is False


def test_overlap_skips_suite_and_blocks_readiness(tmp_path: Path) -> None:
    repo, work = _make_repo(tmp_path)
    _git(repo, "checkout", "-q", "integration")
    _git(repo, "checkout", "-qb", "c")
    (repo / "a.txt").write_text("conflict", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "c")
    report = propose_integration(
        repo,
        ["a", "c"],
        worktree=work,
        output_branch="integration",
        test_command=(sys.executable, "-c", "print('x')"),
    )
    assert report.gate == "FAIL"
    assert report.test_gate == "NOT_RUN"
    assert report.ready is False


# --------------------------------------------------------------------- CLI wiring
def test_cli_exposes_lease_and_test_gate_flags(tmp_path: Path) -> None:
    runner = CliRunner()
    for args, flag in (
        (["forge", "run", "--help"], "--heartbeat-interval"),
        (["forge", "integrate", "--help"], "--test"),
        (["forge", "integrate", "--help"], "--test-timeout"),
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
        assert flag in re.sub(r"\x1b\[[0-9;]*m", "", result.output)

    db = tmp_path / "watch.db"
    ops = BlacksmithOps(str(db))
    try:
        job = ops.submit("watched", "task", FAKE_REPO)
        live = job.model_copy(
            update={
                "state": JobState.DISPATCHED,
                "heartbeat_at": datetime.now(UTC).isoformat(),
                "lease_owner": "w-test",
            }
        )
        ops._conn.execute(
            "UPDATE forge_jobs SET state = ?, document = ? WHERE job_id = ?",
            (JobState.DISPATCHED.value, live.model_dump_json(), job.job_id),
        )
        ops._conn.commit()
    finally:
        ops.close()
    result = runner.invoke(app, ["forge", "jobs", "--db", str(db), "--watch", "--ticks", "1"])
    assert result.exit_code == 0, result.output
    assert "hb=" in result.output
    assert "LIVE:" in result.output


def test_cli_integration_prints_test_gate_and_readiness(tmp_path: Path) -> None:
    repo, work = _make_repo(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "forge",
            "integrate",
            "--repo",
            str(repo),
            "--branch",
            "a",
            "--branch",
            "b",
            "--worktree",
            str(work),
            "--output-branch",
            "integration",
            "--test",
            f'{sys.executable} -c "print(1)"',
            "--test-timeout",
            "10",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "test gate: PASS" in result.output
    assert "ready: yes" in result.output
