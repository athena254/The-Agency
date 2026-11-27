"""Parent wiring tests: the four child branches work as one governed loop."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from agency.cli.main import app
from agency.forge.apprentice_policy import enforce_apprentice_policy
from agency.forge.external_agent import run_external_agent
from agency.forge.orchestration import BlacksmithOps


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "p@local"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "p"], check=True, capture_output=True
    )
    (repo / "README.md").write_text("wiring\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "init"], check=True, capture_output=True
    )
    return repo


def _worker(tmp_path: Path, *, valid_plan: bool) -> Path:
    worker = tmp_path / "worker.py"
    files = "`app.py`" if valid_plan else "none"
    worker.write_text(
        "import pathlib, subprocess\n"
        f"lines = ['Goal: x', 'Approach: y', 'Files: {files}', 'Tests: pytest -q', 'Risks: low']\n"
        "pathlib.Path('PLAN.md').write_text('\\n'.join(lines))\n"
        "pathlib.Path('app.py').write_text('x = 1')\n"
        "subprocess.run(['git', 'add', '.'])\n"
        "subprocess.run(['git', 'commit', '-qm', 'worker'])\n",
        encoding="utf-8",
    )
    return worker


def test_strict_plan_is_enforced_for_code_jobs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    report = run_external_agent(
        repo,
        [sys.executable, str(_worker(tmp_path, valid_plan=False))],
        "build a feature",
        worktree_root=tmp_path / "wt1",
        plan_required=True,
        route="standard",
        role="striker",
        run_tests_flag=False,
        run_review_flag=False,
    )
    assert report.plan_gate == "FAIL"
    assert "no_file_allowlist" in report.plan_findings
    assert report.gate == "FAIL"


def test_apprentice_cannot_take_risky_work(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    policy = enforce_apprentice_policy("apprentice", "risky", 60)
    assert policy.allowed is False
    report = run_external_agent(
        repo,
        [sys.executable, str(_worker(tmp_path, valid_plan=True))],
        "harden auth permissions",
        worktree_root=tmp_path / "wt2",
        plan_required=True,
        route="risky",
        role="apprentice",
        tool_budget=60,
        run_tests_flag=False,
        run_review_flag=False,
    )
    assert report.policy_allowed is False
    assert report.effective_tool_budget <= 12
    assert report.gate == "FAIL"


def test_submit_carries_runtime_verification_fields(tmp_path: Path) -> None:
    db = tmp_path / "jobs.db"
    result = CliRunner().invoke(
        app,
        [
            "forge",
            "submit",
            "--title",
            "Harden auth",
            "--task",
            "harden the auth gate",
            "--repo",
            str(tmp_path),
            "--db",
            str(db),
            "--class",
            "risky",
            "--verify-command",
            "python server.py",
            "--verify-endpoint",
            "http://127.0.0.1:9/health",
        ],
    )
    assert result.exit_code == 0, result.output
    ops = BlacksmithOps(str(db))
    try:
        job = ops.status().recent[0]
        assert job.verify_command == "python server.py"
        assert job.verify_endpoints == ("http://127.0.0.1:9/health",)
    finally:
        ops.close()


def test_learn_captures_failure_without_dispatching(tmp_path: Path) -> None:
    db = tmp_path / "jobs.db"
    source = tmp_path / "ci.json"
    source.write_text(
        json.dumps([{"task_id": "t-1", "gate": "FAILED", "source": "ci", "detail": "one failed"}]),
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        app,
        ["forge", "learn", "--input", str(source)],
    )
    assert result.exit_code == 0, result.output
    assert "CANDIDATE" in result.output
    assert "keys:" in result.output
    ops = BlacksmithOps(str(db))
    try:
        assert ops.status().total == 0
    finally:
        ops.close()
