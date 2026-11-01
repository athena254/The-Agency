"""Tests for the protected-paths guardrail on AgencyCodingAgent.run_task."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from agency.agents.coding import AgencyCodingAgent


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "m.py").write_text("x = 1\n", encoding="utf-8")
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def test_run_task_protected_path_raises(tmp_path: Path):
    repo = _git_repo(tmp_path)
    agent = AgencyCodingAgent(workspace=repo)
    with pytest.raises(ValueError, match="protected"):
        agent.run_task("feat/protected", [{"path": ".env", "old": "SECRET=1", "new": "SECRET=2"}])


def test_run_task_protected_nested_path_raises(tmp_path: Path):
    repo = _git_repo(tmp_path)
    agent = AgencyCodingAgent(workspace=repo)
    with pytest.raises(ValueError):
        agent.run_task(
            "feat/protected",
            [{"path": "secrets/api.key", "old": "a", "new": "b"}],
        )


def test_run_task_protected_among_safe_edits_raises(tmp_path: Path):
    repo = _git_repo(tmp_path)
    agent = AgencyCodingAgent(workspace=repo)
    with pytest.raises(ValueError):
        agent.run_task(
            "feat/mixed",
            [
                {"path": "m.py", "old": "x = 1", "new": "x = 2"},
                {"path": "infra/deploy.yml", "old": "a", "new": "b"},
            ],
        )


def test_run_task_protected_does_not_create_worktree(tmp_path: Path):
    repo = _git_repo(tmp_path)
    agent = AgencyCodingAgent(workspace=repo)
    before = subprocess.run(
        ["git", "worktree", "list", "--porcelain"], cwd=repo, capture_output=True, text=True
    ).stdout
    with pytest.raises(ValueError):
        agent.run_task("feat/protected", [{"path": "migrations/0001.py", "old": "a", "new": "b"}])
    after = subprocess.run(
        ["git", "worktree", "list", "--porcelain"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert before == after


def test_run_task_safe_path_still_works(tmp_path: Path):
    repo = _git_repo(tmp_path)
    agent = AgencyCodingAgent(workspace=repo)
    report = agent.run_task("feat/ok", [{"path": "m.py", "old": "x = 1", "new": "x = 2"}])
    assert report.gate == "PASS"
    assert report.edits_applied == 1


def test_arun_task_protected_raises(tmp_path: Path):
    repo = _git_repo(tmp_path)
    agent = AgencyCodingAgent(workspace=repo)
    with pytest.raises(ValueError):
        asyncio.run(
            agent.arun_task("feat/async-protected", [{"path": ".env", "old": "SECRET=1", "new": "SECRET=2"}])
        )


def test_run_with_skill_protected_raises(tmp_path: Path):
    repo = _git_repo(tmp_path)
    skill = tmp_path / "SKILL.md"
    skill.write_text("# skill\n", encoding="utf-8")
    agent = AgencyCodingAgent(workspace=repo)
    with pytest.raises(ValueError):
        agent.run_with_skill(
            skill, "feat/skill-protected", [{"path": ".env", "old": "SECRET=1", "new": "SECRET=2"}]
        )
