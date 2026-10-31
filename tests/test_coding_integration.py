"""Integration test wiring plan_edits together with AgencyCodingAgent."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agency.agents.coding import AgencyCodingAgent
from agency.agents.coding_planner import plan_edits
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


def test_coding_integration_plan_edits_and_agent(tmp_path: Path):
    repo = _git_repo(tmp_path)

    def fake_llm(prompt, context):
        return json.dumps([{"path": "m.py", "old": "x = 1", "new": "x = 2"}])

    edits = plan_edits(fake_llm, "change x to 2")
    assert edits == [{"path": "m.py", "old": "x = 1", "new": "x = 2"}]

    db = tmp_path / "ca.db"
    agent = AgencyCodingAgent(workspace=repo, store_path=db)
    report = agent.run_task("feat/integration", edits)

    assert report.gate == "PASS"
    assert report.edits_applied == 1

    store = CodingAgentStore(db)
    try:
        loaded = store.load(report.report_id)
    finally:
        store.close()
    assert loaded is not None
    assert loaded.gate == "PASS"
    assert loaded.edits_applied == report.edits_applied