"""Enforcement test: no plan, no pass — even if the worker writes code."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agency.forge.external_agent import run_external_agent


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "p@local"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "p"], check=True)
    (repo / "README.md").write_text("plan probe\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "init"], check=True, capture_output=True
    )
    return repo


def test_missing_plan_fails_the_external_agent_gate(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    python = "C:/Users/PC/The-Agency/.venv/Scripts/python.exe"
    report = run_external_agent(
        repo,
        [python, "-c", "open('app.py','w').write('x = 1\\n')"],
        "build something",
        worktree_root=tmp_path / "worktrees",
        plan_required=True,
        run_tests_flag=False,
        run_review_flag=False,
    )
    assert report.gate == "FAIL"
    assert report.plan_gate == "FAIL"
    assert "plan_file_missing" in report.plan_findings
    assert "plan_gate_failed" in report.caveats
