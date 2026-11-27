"""Tests for the efficiency modules added to the factory."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from agency.forge.concurrency import run_queue
from agency.forge.env_config import load_env_file
from agency.forge.integration import propose_integration
from agency.forge.observability import make_trace
from agency.forge.planning import check_plan, path_allowed
from agency.forge.project_adapters import prepare_dependencies
from agency.forge.routing import route_job
from agency.forge.runtime_verify import verify_runtime


def test_routing_matches_job_risk_and_size() -> None:
    assert route_job("Fix readme typo").job_class == "trivial"
    assert route_job("Add a footer component").job_class == "standard"
    risky = route_job("Harden auth permissions")
    assert risky.job_class == "risky"
    assert risky.require_quorum
    assert risky.tool_budget > route_job("Add a footer").tool_budget


def test_env_loader_sets_keys_without_values(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FORGE_TEST_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("FORGE_TEST_KEY=secret-value\n# comment\n", encoding="utf-8")
    keys = load_env_file(env)
    assert keys == ("FORGE_TEST_KEY",)
    assert os.environ["FORGE_TEST_KEY"] == "secret-value"


def test_plan_allowlist_is_enforced(tmp_path: Path) -> None:
    (tmp_path / "PLAN.md").write_text(
        "Goal: x\nApproach: y\nFiles: `app.py`, `tests/`\nTests: pytest\nRisks: none\n",
        encoding="utf-8",
    )
    report = check_plan(tmp_path)
    assert report.gate == "PASS"
    assert "app.py" in report.allowed_files
    assert path_allowed("tests/test_app.py", report.allowed_files)
    assert not path_allowed("secrets.py", report.allowed_files)


def test_job_trace_records_phases() -> None:
    trace = make_trace("job-1", {"agent": 12.3456, "tests": 2.0})
    assert trace.total_s == 14.346
    assert [p.name for p in trace.phases] == ["agent", "tests"]


def test_concurrency_runs_queued_jobs_with_limited_slots() -> None:
    class FakeOps:
        def __init__(self, state: dict[str, int]) -> None:
            self.state = state

        def dispatch_next(self) -> str | None:
            if self.state["left"] <= 0:
                return None
            self.state["left"] -= 1
            return f"job-{self.state['left']}"

        def close(self) -> None:
            return None

    state = {"left": 3}
    jobs = run_queue(lambda: FakeOps(state), limit=2, max_jobs=10)
    assert sorted(jobs) == ["job-0", "job-1", "job-2"]


def test_runtime_verify_polls_real_endpoint(tmp_path: Path) -> None:
    port = 8766
    server = subprocess.Popen(
        [
            "C:/Users/PC/The-Agency/.venv/Scripts/python.exe",
            "-m",
            "http.server",
            str(port),
            "--bind",
            "127.0.0.1",
            "--directory",
            str(tmp_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        report = verify_runtime(
            tmp_path,
            ["C:/Users/PC/The-Agency/.venv/Scripts/python.exe", "-m", "http.server", "9999"],
            [f"http://127.0.0.1:{port}/"],
            timeout_s=10,
        )
        assert report.gate == "PASS"
        assert report.statuses[0][1] == 200
    finally:
        server.terminate()
        server.wait(timeout=5)


def test_integration_combines_disjoint_work(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    for key, value in (("user.email", "p@local"), ("user.name", "p")):
        subprocess.run(["git", "-C", str(repo), "config", key, value], check=True)
    base = repo / "base.txt"
    base.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    subprocess.run(["git", "-C", str(repo), "branch", "integration"], check=True)
    for branch, filename in (("a", "a.txt"), ("b", "b.txt")):
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "integration"], check=True)
        subprocess.run(["git", "-C", str(repo), "checkout", "-qb", branch], check=True)
        (repo / filename).write_text(branch, encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", branch], check=True)
    work = tmp_path / "combined"
    work.mkdir()
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    report = propose_integration(repo, ["a", "b"], worktree=work, output_branch="integration")
    assert report.gate == "PASS"
    assert set(report.combined) == {"a.txt", "b.txt"}
    assert (work / "a.txt").read_text(encoding="utf-8") == "a"


def test_project_adapter_note_is_bounded(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    note = prepare_dependencies(tmp_path, work, "python")
    assert isinstance(note, str) and len(note) < 200
