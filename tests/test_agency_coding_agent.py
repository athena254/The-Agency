"""Tests for the Agency coding agent operator."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from agency.agents.coding import AgencyCodingAgent, read_skill
from agency.forge import CodingAgentStore


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


def test_agency_coding_agent_runs_task(tmp_path: Path):
    repo = _git_repo(tmp_path)
    agent = AgencyCodingAgent(workspace=repo)
    report = agent.run_task("feat/ok", [{"path": "m.py", "old": "x = 1", "new": "x = 2"}])
    assert report.gate == "PASS"
    assert report.edits_applied == 1


def test_agency_coding_agent_persists_report(tmp_path: Path):
    repo = _git_repo(tmp_path)
    db = tmp_path / "ca.db"
    agent = AgencyCodingAgent(workspace=repo, store_path=db)
    report = agent.run_task("feat/store", [{"path": "m.py", "old": "x = 1", "new": "x = 2"}])
    store = CodingAgentStore(db)
    try:
        loaded = store.load(report.report_id)
    finally:
        store.close()
    assert loaded is not None
    assert loaded.edits_applied == report.edits_applied


def test_agency_coding_agent_async(tmp_path: Path):
    repo = _git_repo(tmp_path)
    agent = AgencyCodingAgent(workspace=repo)
    report = asyncio.run(
        agent.arun_task("feat/async", [{"path": "m.py", "old": "x = 1", "new": "x = 2"}])
    )
    assert report.gate == "PASS"


def test_agency_coding_agent_run_with_skill(tmp_path: Path):
    repo = _git_repo(tmp_path)
    skill = tmp_path / "SKILL.md"
    skill.write_text("# skill\n", encoding="utf-8")
    agent = AgencyCodingAgent(workspace=repo)
    report = agent.run_with_skill(
        skill, "feat/skill", [{"path": "m.py", "old": "x = 1", "new": "x = 2"}]
    )
    assert report.gate == "PASS"


def test_read_skill_missing_raises(tmp_path: Path):
    import pytest

    with pytest.raises(FileNotFoundError):
        read_skill(tmp_path / "nope.md")


def test_agency_coding_agent_default_workspace_is_agency():
    agent = AgencyCodingAgent()
    assert agent.workspace.name == "The-Agency"
    assert (agent.workspace / "src" / "agency").is_dir()
