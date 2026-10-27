import subprocess
import sys
from pathlib import Path

from agency.forge.external_agent import run_external_agent


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README.md").write_text("hi\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)
    return repo


def test_external_agent_applies_and_gates_pass(tmp_path):
    repo = _git_repo(tmp_path)
    agent = tmp_path / "agent.py"
    agent.write_text('from pathlib import Path\nPath("README.md").write_text("hi\\nchanged\\n")\n')
    report = run_external_agent(
        str(repo),
        [sys.executable, str(agent)],
        "change readme",
        cleanup=True,
    )
    assert report.gate == "PASS"
    assert report.changed_paths == ("README.md",)
    assert report.exit_code == 0
    assert report.inspection.gate == "PASS"
    assert report.security.gate == "PASS"


def test_external_agent_failure_on_nonzero_exit(tmp_path):
    repo = _git_repo(tmp_path)
    agent = tmp_path / "agent.py"
    agent.write_text("import sys\nsys.exit(1)\n")
    report = run_external_agent(
        str(repo),
        [sys.executable, str(agent)],
        "fail",
        cleanup=True,
    )
    assert report.gate == "FAIL"
    assert report.exit_code == 1


def test_external_agent_no_changes_fails(tmp_path):
    repo = _git_repo(tmp_path)
    agent = tmp_path / "agent.py"
    agent.write_text("print('noop')\n")
    report = run_external_agent(
        str(repo),
        [sys.executable, str(agent)],
        "noop",
        cleanup=True,
    )
    assert report.gate == "FAIL"
    assert report.changed_paths == ()
