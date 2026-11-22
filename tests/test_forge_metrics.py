"""Tests for forge metrics: read-only operational picture (forge plan P4)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from typer.testing import CliRunner

from agency.cli.main import app
from agency.forge.external_agent import ExternalAgentReport
from agency.forge.inspection import InspectionReport
from agency.forge.metrics import compute_metrics, format_metrics
from agency.forge.orchestration import BlacksmithOps, JobState
from agency.forge.security_scan import SecurityReport


def _report(gate: str = "PASS") -> ExternalAgentReport:
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
        inspection=InspectionReport(report_id=str(uuid4()), created_at=now, root=root, gate="PASS"),
        security=SecurityReport(
            report_id=str(uuid4()),
            created_at=now,
            root=root,
            gate="PASS",
            scanned_files=1,
            skipped_files=0,
        ),
        gate=gate,  # type: ignore[arg-type]
    )


def test_compute_metrics_missing_db_is_honest_zeros(tmp_path) -> None:
    metrics = compute_metrics(tmp_path / "nope.db")
    assert metrics.total_jobs == 0
    assert metrics.first_pass_rate == 0.0
    assert metrics.avg_attempts == 0.0


def test_compute_metrics_first_pass_rework_and_verdict_mix(tmp_path) -> None:
    db = str(tmp_path / "jobs.db")
    ops = BlacksmithOps(db)
    try:
        ops.submit("good", "task", "D:/fake/repo")
        ops.submit("flaky", "task", "D:/fake/repo")
        dead = ops.submit("dead", "task", "D:/fake/repo")
    finally:
        ops.close()

    fail_calls = {2, 4, 5, 6}
    calls = {"n": 0}

    def runner(*a, **k):
        calls["n"] += 1
        return _report("FAIL") if calls["n"] in fail_calls else _report()

    ops = BlacksmithOps(db, runner=runner)
    try:
        ops.dispatch_next()  # good -> PASSED (first pass)
        ops.dispatch_next()  # flaky -> FAILED
        ops.dispatch_next()  # flaky retry -> PASSED (rework)
        for _ in range(3):
            ops.dispatch_next()  # dead: FAIL, FAIL, retries exhausted -> BLOCKED
        assert ops.get(dead.job_id).state is JobState.BLOCKED
    finally:
        ops.close()

    metrics = compute_metrics(db)
    assert metrics.total_jobs == 3
    assert metrics.passed == 2
    assert metrics.first_pass == 1
    assert metrics.rework == 1
    assert metrics.first_pass_rate == 0.5
    assert metrics.rework_rate == 0.5
    assert metrics.blocked == 1
    assert metrics.by_state.get("AWAITING_HUMAN") == 2
    assert metrics.verdict_mix.get("PASS") == 2
    assert metrics.verdict_mix.get("FAIL") == 1
    assert metrics.avg_attempts == 2.0


def test_format_metrics_renders_the_numbers(tmp_path) -> None:
    db = str(tmp_path / "jobs.db")
    ops = BlacksmithOps(db)
    try:
        ops.submit("solo", "task", "D:/fake/repo")
    finally:
        ops.close()
    text = format_metrics(compute_metrics(db))
    assert "Forge jobs: 1" in text
    assert "QUEUED: 1" in text


def test_forge_metrics_cli(tmp_path) -> None:
    db = str(tmp_path / "jobs.db")
    result = CliRunner().invoke(app, ["forge", "metrics", "--db", db, "--json"])
    assert result.exit_code == 0
    assert '"total_jobs": 0' in result.output.replace(" ", "").replace('":', '":') or "total_jobs" in result.output
