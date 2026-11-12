"""End-to-end integration: queue -> worker -> gates -> commit -> proof."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from agency.autonomy.loop_driver import GateResult, LoopConfig, LoopDriver, WorkerResult
from agency.autonomy.loop_gates import AcceptanceVerifier, ForgeGateRunner
from agency.autonomy.loop_queue import parse_goal_plan
from agency.autonomy.loop_state import LoopStore
from agency.autonomy.loop_workers import ExternalWorker
from agency.autonomy.proof import verify_chain

FIXER = """
import pathlib
root = pathlib.Path(".")
src = root / "calc.py"
text = src.read_text(encoding="utf-8")
src.write_text(text.replace("return a - b", "return a + b"), encoding="utf-8")
"""


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    (repo / "GOAL.md").write_text("# Goal\n\nFix the calculator so add() sums.\n", encoding="utf-8")
    (repo / "fix_plan.md").write_text(
        "# Fix plan\n\n- [ ] Fix add() in calc.py\n  - test: test_calc.py\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def test_full_loop_end_to_end(tmp_path):
    repo = _git_repo(tmp_path)
    items = parse_goal_plan(repo / "GOAL.md", repo / "fix_plan.md")
    assert len(items) == 1
    assert items[0].acceptance == ("test_calc.py",)

    store = LoopStore(":memory:")
    worker = ExternalWorker([sys.executable, "-c", FIXER], timeout_s=60)
    gate = ForgeGateRunner(
        run_tests=True,
        run_review=False,
        run_holdout=False,
        verifier=AcceptanceVerifier(test_timeout_s=60),
    )
    config = LoopConfig(
        session_id="e2e",
        goal="fix calculator",
        repo=str(repo),
        branch="agency-loop-e2e",
        worker=worker.name,
    )
    driver = LoopDriver(config, store, worker, gate, items)
    result = driver.run()

    assert result.status == "COMPLETED"
    assert result.items_done == 1
    assert result.iterations == 1
    assert verify_chain(driver.state.proof_events)

    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline", "agency-loop-e2e"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "agency-loop" in log.stdout

    committed = subprocess.run(
        ["git", "-C", str(repo), "show", "agency-loop-e2e:calc.py"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "return a + b" in committed.stdout

    assert "return a - b" in (repo / "calc.py").read_text(encoding="utf-8")
    store.close()


def test_cli_loop_run_loads_local_goal_without_repo(monkeypatch, tmp_path):
    """The CLI loads a local GOAL.md/fix_plan.md pair without forcing the GitHub source."""
    import importlib

    from typer.testing import CliRunner

    from agency.cli.main import app

    cli_main = importlib.import_module("agency.cli.main")

    repo = _git_repo(tmp_path)
    captured: dict[str, object] = {}

    def fake_load_queue(source, **kwargs):
        captured["source"] = source
        captured["kwargs"] = kwargs
        return parse_goal_plan(repo / "GOAL.md", repo / "fix_plan.md")

    class FakeWorker:
        name = "fake"

        def run(self, item, worktree, feedback=""):
            (Path(worktree) / "out.txt").write_text("ok", encoding="utf-8")
            return WorkerResult(gate="PASS", changed_paths=("out.txt",))

    class FakeGate:
        def run(self, worktree, item, changed_paths):
            return GateResult(gate="PASS")

    monkeypatch.setattr(cli_main, "load_queue", fake_load_queue)
    monkeypatch.setattr(cli_main, "_make_worker", lambda *args, **kwargs: FakeWorker())
    monkeypatch.setattr(cli_main, "ForgeGateRunner", lambda **kwargs: FakeGate())

    db = tmp_path / "loop.db"
    result = CliRunner().invoke(app, ["loop", "run", str(repo), "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert captured["source"] == str(repo)
    assert captured["kwargs"] == {}

    store = LoopStore(str(db))
    try:
        state = store.load("agency-loop")
        assert state is not None
        assert state.status == "COMPLETED"
        assert state.queue[0].status == "done"
    finally:
        store.close()
