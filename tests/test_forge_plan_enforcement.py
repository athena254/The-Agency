"""Enforcement test: no plan, no pass — even if the worker writes code."""

from __future__ import annotations

import subprocess
import sys
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
    python = sys.executable
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


def test_committed_worker_changes_are_seen_by_the_gate(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    python = sys.executable
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import pathlib, subprocess\n"
        "lines = ['Goal: x', 'Approach: y', 'Files: `app.py`, `PLAN.md`', 'Tests: pytest -q', 'Risks: low']\n"
        "pathlib.Path('PLAN.md').write_text('\\n'.join(lines))\n"
        "pathlib.Path('app.py').write_text('x = 1')\n"
        "subprocess.run(['git', 'add', '.'])\n"
        "subprocess.run(['git', 'commit', '-qm', 'worker'])\n",
        encoding="utf-8",
    )
    report = run_external_agent(
        repo,
        [python, str(worker)],
        "build with a plan",
        worktree_root=tmp_path / "worktrees2",
        plan_required=True,
        run_tests_flag=False,
        run_review_flag=False,
    )
    assert report.plan_gate == "PASS"
    assert "app.py" in report.changed_paths
    assert "PLAN.md" in report.changed_paths
    assert report.gate == "PASS"


def test_files_outside_plan_fail_the_gate(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    python = sys.executable
    worker = tmp_path / "worker_outside.py"
    worker.write_text(
        "import pathlib, subprocess\n"
        "lines = ['Goal: x', 'Approach: y', 'Files: `app.py`', 'Tests: pytest', 'Risks: none']\n"
        "pathlib.Path('PLAN.md').write_text('\\n'.join(lines))\n"
        "pathlib.Path('app.py').write_text('x = 1')\n"
        "pathlib.Path('evil.py').write_text('y = 2')\n"
        "subprocess.run(['git', 'add', '.'])\n"
        "subprocess.run(['git', 'commit', '-qm', 'worker'])\n",
        encoding="utf-8",
    )
    report = run_external_agent(
        repo,
        [python, str(worker)],
        "stay inside plan",
        worktree_root=tmp_path / "worktrees3",
        plan_required=True,
        run_tests_flag=False,
        run_review_flag=False,
    )
    assert report.gate == "FAIL"
    assert "files_outside_plan" in report.caveats
