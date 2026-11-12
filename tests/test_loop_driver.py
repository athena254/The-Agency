"""Tests for the deterministic autonomous loop driver (Phase 1 skeleton)."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from agency.autonomy.loop_driver import (
    NO_PROGRESS_LIMIT,
    SAME_ERROR_LIMIT,
    GateResult,
    LoopConfig,
    LoopDriver,
    WorkerResult,
)
from agency.autonomy.loop_state import LoopBudgets, LoopItem, LoopStore
from agency.autonomy.proof import verify_chain


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "seed.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


class ScriptedWorker:
    """Writes a scripted value to out.txt per item; records feedback."""

    name = "scripted"

    def __init__(self, scripts, *, stop_after: int | None = None):
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.per_item = {k: 0 for k in scripts}
        self.stop_after = stop_after
        self.driver: LoopDriver | None = None
        self.calls: list[dict] = []

    def run(self, item, worktree, feedback=""):
        self.calls.append({"item": item.id, "feedback": feedback})
        if (
            self.stop_after is not None
            and len(self.calls) >= self.stop_after
            and self.driver is not None
        ):
            self.driver.request_stop()
        n = self.per_item.get(item.id, 0)
        self.per_item[item.id] = n + 1
        script = self.scripts.get(item.id, ["ok"])
        value = script[min(n, len(script) - 1)]
        (Path(worktree) / "out.txt").write_text(value, encoding="utf-8")
        return WorkerResult(gate="PASS", changed_paths=("out.txt",))


class NoProgressWorker:
    name = "no_progress"

    def __init__(self) -> None:
        self.calls = 0

    def run(self, item, worktree, feedback=""):
        self.calls += 1
        return WorkerResult(gate="PASS", changed_paths=())


class ExplodingWorker:
    name = "exploding"

    def run(self, item, worktree, feedback=""):
        raise RuntimeError("worker exploded")


class FailingWorker:
    """Writes a passing change but reports a failing worker gate."""

    name = "failing"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, item, worktree, feedback=""):
        self.calls.append({"item": item.id, "feedback": feedback})
        (Path(worktree) / "out.txt").write_text("ok", encoding="utf-8")
        return WorkerResult(gate="FAIL", changed_paths=("out.txt",), detail="worker gate failed")


class ContentGate:
    """PASS when out.txt == 'ok'; FAIL otherwise with a stable signature."""

    def __init__(self, pass_value="ok", signature="content_not_ok"):
        self.pass_value = pass_value
        self.signature = signature
        self.calls: list[dict] = []

    def run(self, worktree, item, changed_paths):
        self.calls.append({"item": item.id, "changed": list(changed_paths)})
        path = Path(worktree) / "out.txt"
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        if content == self.pass_value:
            return GateResult(gate="PASS")
        return GateResult(
            gate="FAIL",
            detail=self.signature,
            feedback=f"content was {content!r}; expected {self.pass_value!r}",
            error_signature=self.signature,
        )


def _config(repo: Path, **over) -> LoopConfig:
    args = {
        "session_id": "test-session",
        "goal": "demo goal",
        "repo": str(repo),
        "branch": "agency-loop-test",
    }
    args.update(over)
    return LoopConfig(**args)


def _item(item_id: str, **over) -> LoopItem:
    args = {"id": item_id, "title": item_id}
    args.update(over)
    return LoopItem(**args)


def test_loop_completes_all_items(tmp_path):
    repo = _git_repo(tmp_path)
    store = LoopStore(":memory:")
    worker = ScriptedWorker({"a": ["ok"], "b": ["ok"]})
    driver = LoopDriver(_config(repo), store, worker, ContentGate(), [_item("a"), _item("b")])
    result = driver.run()
    assert result.status == "COMPLETED"
    assert result.items_done == 2
    assert result.items_blocked == 0
    assert result.items_pending == 0
    assert result.iterations == 2
    assert verify_chain(driver.state.proof_events)
    store.close()


def test_loop_retries_then_succeeds_with_feedback(tmp_path):
    repo = _git_repo(tmp_path)
    store = LoopStore(":memory:")
    worker = ScriptedWorker({"a": ["bad", "bad", "ok"], "b": ["ok"]})
    gate = ContentGate()
    driver = LoopDriver(_config(repo), store, worker, gate, [_item("a"), _item("b")])
    result = driver.run()
    assert result.status == "COMPLETED"
    assert result.items_done == 2
    assert result.iterations == 4
    # the third attempt on item "a" received the failure feedback
    third_call = [c for c in worker.calls if c["item"] == "a"][2]
    assert "content was 'bad'" in third_call["feedback"]
    store.close()


def test_no_progress_breaker_blocks_item(tmp_path):
    repo = _git_repo(tmp_path)
    store = LoopStore(":memory:")
    worker = NoProgressWorker()
    driver = LoopDriver(_config(repo), store, worker, ContentGate(), [_item("a")])
    result = driver.run()
    assert result.items_blocked == 1
    assert result.items_done == 0
    assert worker.calls == NO_PROGRESS_LIMIT
    assert driver.state.queue[0].detail == "no_progress"
    store.close()


def test_same_error_breaker_blocks_item(tmp_path):
    repo = _git_repo(tmp_path)
    store = LoopStore(":memory:")
    worker = ScriptedWorker({"a": ["bad"]})
    driver = LoopDriver(
        _config(repo), store, worker, ContentGate(signature="stable_sig"), [_item("a")]
    )
    result = driver.run()
    assert result.items_blocked == 1
    assert driver.state.queue[0].detail == "same_error"
    assert result.iterations == SAME_ERROR_LIMIT
    store.close()


def test_budget_max_iterations_stops(tmp_path):
    repo = _git_repo(tmp_path)
    store = LoopStore(":memory:")
    worker = ScriptedWorker({"a": ["bad"], "b": ["ok"]})
    driver = LoopDriver(
        _config(repo, budgets=LoopBudgets(max_iterations=2)),
        store,
        worker,
        ContentGate(),
        [_item("a"), _item("b")],
    )
    result = driver.run()
    assert result.status == "PAUSED"
    assert result.iterations == 2
    assert result.items_done == 0
    assert result.items_pending == 2
    store.close()


def test_user_stop_halts_cleanly_and_persists(tmp_path):
    repo = _git_repo(tmp_path)
    store = LoopStore(":memory:")
    worker = ScriptedWorker({"a": ["bad"], "b": ["ok"]}, stop_after=1)
    driver = LoopDriver(_config(repo), store, worker, ContentGate(), [_item("a"), _item("b")])
    worker.driver = driver
    result = driver.run()
    assert result.status == "PAUSED"
    assert result.items_pending == 2
    assert result.iterations == 1
    loaded = store.load("test-session")
    assert loaded is not None and loaded.status == "PAUSED"
    store.close()


def test_state_resumes_across_restart(tmp_path):
    repo = _git_repo(tmp_path)
    store = LoopStore(":memory:")
    items = [_item("a")]

    worker1 = ScriptedWorker({"a": ["bad"]}, stop_after=1)
    driver1 = LoopDriver(_config(repo), store, worker1, ContentGate(), items)
    worker1.driver = driver1
    first = driver1.run()
    assert first.status == "PAUSED"

    worker2 = ScriptedWorker({"a": ["ok"]})
    driver2 = LoopDriver(_config(repo), store, worker2, ContentGate(), items)
    second = driver2.run()
    assert second.status == "COMPLETED"
    assert second.items_done == 1
    assert second.iterations == 2
    assert verify_chain(driver2.state.proof_events)
    store.close()


def test_dependencies_blocked_item(tmp_path):
    repo = _git_repo(tmp_path)
    store = LoopStore(":memory:")
    items = [_item("a"), _item("b", deps=("a",))]
    worker = ScriptedWorker({"a": ["bad"], "b": ["ok"]})
    driver = LoopDriver(_config(repo), store, worker, ContentGate(), items)
    result = driver.run()
    assert result.items_blocked == 2
    assert result.items_done == 0
    by_id = {i.id: i for i in driver.state.queue}
    assert by_id["a"].detail == "same_error"
    assert by_id["b"].detail == "dependency_blocked"
    store.close()


def test_worktree_isolation_and_commit_lands_on_branch(tmp_path):
    repo = _git_repo(tmp_path)
    store = LoopStore(":memory:")
    worker = ScriptedWorker({"a": ["ok"]})
    driver = LoopDriver(_config(repo), store, worker, ContentGate(), [_item("a")])
    driver.run()
    assert not (repo / "out.txt").exists()
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert status.stdout.strip() == ""
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline", "agency-loop-test"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "agency-loop" in log.stdout
    store.close()


def test_no_budgets_means_no_cap(tmp_path):
    repo = _git_repo(tmp_path)
    store = LoopStore(":memory:")
    items = [_item(f"item-{i}") for i in range(12)]
    worker = ScriptedWorker({i.id: ["bad", "ok"] for i in items})
    driver = LoopDriver(_config(repo), store, worker, ContentGate(), items)
    result = driver.run()
    assert result.status == "COMPLETED"
    assert result.items_done == 12
    assert result.iterations == 24
    store.close()


def test_worker_exception_marks_failed_and_stays_failed_after_finalize(tmp_path):
    repo = _git_repo(tmp_path)
    store = LoopStore(":memory:")
    driver = LoopDriver(_config(repo), store, ExplodingWorker(), ContentGate(), [_item("a")])

    with pytest.raises(RuntimeError, match="worker exploded"):
        driver.run()

    assert driver.state is not None
    assert driver.state.status == "FAILED"
    assert driver._result().status == "FAILED"

    driver._finalize(time.monotonic())
    assert driver.state is not None
    assert driver.state.status == "FAILED"
    store.close()


def test_worker_gate_failure_blocks_item_even_when_harness_gate_passes(tmp_path):
    repo = _git_repo(tmp_path)
    store = LoopStore(":memory:")
    worker = FailingWorker()
    driver = LoopDriver(
        _config(repo, max_attempts_per_item=3),
        store,
        worker,
        ContentGate(),
        [_item("a")],
    )

    result = driver.run()

    assert result.items_done == 0
    assert len(worker.calls) == 3
    assert driver.state.queue[0].status == "blocked"
    assert driver.state.queue[0].detail == "max_attempts"
    assert "worker gate failed" in worker.calls[1]["feedback"]
    store.close()
