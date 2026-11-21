"""CLI smoke tests for the forge ship command (Tier 6 salvage, 2dc83cb).

The salvage's proof-log ``forge status`` was superseded on this line by the
BlacksmithOps job-queue status; only ``forge ship`` is ported here.
"""

from __future__ import annotations

from typer.testing import CliRunner

from agency.cli.main import app


def test_forge_help_lists_ship():
    result = CliRunner().invoke(app, ["forge", "--help"])
    assert result.exit_code == 0
    assert "ship" in result.output
    assert "status" in result.output  # BlacksmithOps status remains listed


def test_forge_ship_requires_approval(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.delenv("AGENCY_YOLO", raising=False)
    result = CliRunner().invoke(
        app,
        ["forge", "ship", "--worktree-root", "x", "--target-branch", "main"],
    )
    assert result.exit_code != 0
    assert "approved" in result.output.lower()


def test_forge_ship_requires_passing_gates(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.delenv("AGENCY_YOLO", raising=False)
    result = CliRunner().invoke(
        app,
        [
            "forge",
            "ship",
            "--worktree-root",
            "x",
            "--target-branch",
            "main",
            "--approved",
            "--pipeline-gate",
            "FAIL",
            "--smoke-gate",
            "PASS",
        ],
    )
    assert result.exit_code != 0
    assert "gates" in result.output.lower()
