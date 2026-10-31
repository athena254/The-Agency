"""Tests for the end-to-end described coding task flow."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agency.agents.coding import AgencyCodingAgent
from agency.agents.coding_flow import run_described_task


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


def test_coding_flow_runs_described_task(tmp_path: Path):
    repo = _git_repo(tmp_path)

    def fake_llm(prompt, context):
        return json.dumps([{"path": "m.py", "old": "x = 1", "new": "x = 2"}])

    agent = AgencyCodingAgent(workspace=repo)
    edits, report = run_described_task(fake_llm, "change x to 2", agent, branch="feat/flow")

    assert edits == [{"path": "m.py", "old": "x = 1", "new": "x = 2"}]
    assert report.gate == "PASS"
    assert report.edits_applied == 1
