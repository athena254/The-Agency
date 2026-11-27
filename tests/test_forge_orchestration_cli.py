"""CLI contract tests for Blacksmith submit/dispatch orchestration."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from agency.cli.main import app


def test_submit_creates_queued_job_with_role(tmp_path: Path) -> None:
    db = tmp_path / "jobs.db"
    result = CliRunner().invoke(
        app,
        [
            "forge",
            "submit",
            "--title",
            "Build page",
            "--task",
            "Build a landing page",
            "--repo",
            str(tmp_path),
            "--db",
            str(db),
            "--role",
            "apprentice",
            "--acceptance",
            "health test,build test",
            "--test-paths",
            "tests/test_page.test.ts",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "QUEUED" in result.output
    assert "apprentice" in result.output

    from agency.forge.orchestration import BlacksmithOps

    ops = BlacksmithOps(str(db))
    try:
        jobs = ops.status().recent
        assert len(jobs) == 1
        assert jobs[0].role == "apprentice"
        assert jobs[0].acceptance == ("health test", "build test")
    finally:
        ops.close()


def test_dispatch_command_is_registered() -> None:
    result = CliRunner().invoke(app, ["forge", "dispatch", "--help"])
    assert result.exit_code == 0
    assert "--command" in result.output
