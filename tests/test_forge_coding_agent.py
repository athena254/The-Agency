"""Tests for Forge Phase 3 bounded coding agent."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agency.forge import CodingAgentReport, CodingAgentStore, run_coding_agent


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "m.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def test_coding_agent_applies_edit(tmp_path: Path):
    repo = _git_repo(tmp_path)
    report = run_coding_agent(repo, "feat/ok", [{"path": "m.py", "old": "x = 1", "new": "x = 2"}])
    assert isinstance(report, CodingAgentReport)
    assert report.gate == "PASS"
    assert report.edits_applied == 1
    assert report.inspection.gate == "PASS"
    assert report.security.gate == "PASS"


def test_coding_agent_fails_on_non_single_occurrence(tmp_path: Path):
    repo = _git_repo(tmp_path)
    (repo / "m.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "dup", "--amend"], cwd=repo, check=True)
    report = run_coding_agent(repo, "feat/bad", [{"path": "m.py", "old": "x = 1", "new": "x = 2"}])
    assert report.gate == "FAIL"
    assert report.edits_applied == 0


def test_coding_agent_cleanup(tmp_path: Path):
    repo = _git_repo(tmp_path)
    report = run_coding_agent(
        repo, "feat/clean", [{"path": "m.py", "old": "x = 1", "new": "x = 2"}], cleanup=True
    )
    assert not Path(report.worktree_path).exists()


def test_coding_agent_store_roundtrip(tmp_path: Path):
    repo = _git_repo(tmp_path)
    report = run_coding_agent(
        repo, "feat/store", [{"path": "m.py", "old": "x = 1", "new": "x = 2"}]
    )
    db = tmp_path / "ca.db"
    store = CodingAgentStore(db)
    store.save(report)
    store.close()
    reopened = CodingAgentStore(db)
    try:
        loaded = reopened.load(report.report_id)
    finally:
        reopened.close()
    assert loaded is not None
    assert loaded.edits_applied == report.edits_applied


def test_coding_agent_max_edits_bound(tmp_path: Path):
    repo = _git_repo(tmp_path)
    edits = [{"path": "m.py", "old": "x = 1", "new": "x = 2"} for _ in range(33)]
    report = run_coding_agent(repo, "feat/many", edits)
    assert report.gate == "FAIL"
    assert report.edits_applied == 0
