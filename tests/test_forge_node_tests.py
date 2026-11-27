"""Node projects must get a real test gate, not a silent SKIP on Windows."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agency.forge.test_runner import build_test_command, run_tests


def test_node_command_uses_resolved_npm_path() -> None:
    command = build_test_command("node", [])
    assert command is not None
    assert Path(command[0]).is_file()
    assert command[1:] == ["test"]


def test_node_project_runs_its_test_script(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "p@local"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "p"], check=True, capture_output=True
    )
    (repo / "package.json").write_text(
        json.dumps(
            {
                "name": "node-probe",
                "type": "module",
                "scripts": {"test": 'node -e "process.exit(0)"'},
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "package.json"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "init"], check=True, capture_output=True
    )

    report = run_tests(repo, [], timeout_s=60)
    assert report.gate == "PASS", report.stderr
    assert report.language == "node"
    assert report.returncode == 0
