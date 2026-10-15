"""Tests for minimal durable local recovery v1 (SPEC_LOCAL_RECOVERY_V1)."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agency.workflows.executor import WorkflowExecutor
from agency.workflows.recovery import (
    RecoverableLock,
    RecoverableRun,
    definition_digest,
    input_digest,
)
from agency.workflows.registry import (
    MAX_JSON_BYTES,
    WorkflowDefinition,
    WorkflowRegistry,
    WorkflowRun,
    WorkflowStep,
)

OPS_V = {"op_safe": "1", "op_other": "1", "block": "1"}
SRC_DIR = Path(__file__).resolve().parents[1] / "src"


def make_registry(tmp_path: Path, name: str = "rec.db") -> WorkflowRegistry:
    return WorkflowRegistry(str(tmp_path / name))


def make_definition(*ops: str) -> WorkflowDefinition:
    steps = tuple(WorkflowStep(f"s{i}", op) for i, op in enumerate(ops))
    return WorkflowDefinition(workflow_id="wf", version="1.0.0", steps=steps)


def make_executor(
    registry: WorkflowRegistry,
    operations: dict,
    replay=frozenset({"op_safe"}),
    versions: dict | None = None,
    authorizer=None,
) -> WorkflowExecutor:
    auth = (lambda _a, _p: True) if authorizer is None else authorizer
    return WorkflowExecutor(
        registry,
        operations,
        authorizer=auth,
        replay_safe=frozenset(replay),
        operation_versions=dict(versions) if versions is not None else dict(OPS_V),
    )


def _wait_for_path(path: Path, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() > deadline:
            raise TimeoutError(f"timed out waiting for {path}")
        time.sleep(0.05)


def _wait_for_marker(path: Path, token: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if path.exists() and token in path.read_text():
            return
        if time.monotonic() > deadline:
            raise TimeoutError(f"timed out waiting for {token!r} in {path}")
        time.sleep(0.05)


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_DIR)
    env.setdefault("AGENCY_LLM_PROVIDER", "echo")
    env.setdefault("AGENCY_LLM_MODEL", "echo")
    return env


def _seed_running(
    registry: WorkflowRegistry,
    definition: WorkflowDefinition,
    inputs: dict,
    actor: str,
    step_results: dict,
) -> RecoverableRun:
    seed = RecoverableRun(
        run_id="seed-run-1",
        workflow_id=definition.workflow_id,
        version=definition.version,
        actor_id=actor,
        status="RUNNING",
        step_results={sid: dict(entry) for sid, entry in step_results.items()},
        input_digest=input_digest(inputs),
        definition_digest=definition_digest(definition),
        ops_binding={"op_safe": "1"},
        created_at="2026-01-01T00:00:00+00:00",
        updated_at=None,
    )
    return registry.record_recoverable_run(seed)


def test_durable_run_and_reopen(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    try:
        registry.register(make_definition("op_safe"))
        executor = make_executor(registry, {"op_safe": lambda i, p: {"echo": i.get("x")}})
        run = executor.run_recoverable("wf", "1.0.0", {"x": 1}, "alice")
        assert run.status == "COMPLETED"
        assert run.step_results["s0"]["output"] == {"echo": 1}
        run_id = run.run_id
    finally:
        registry.close()
    reopened = WorkflowRegistry(str(tmp_path / "rec.db"))
    try:
        kept = reopened.get_recoverable_run(run_id)
        assert kept.status == "COMPLETED"
        assert kept.step_results["s0"]["output"] == {"echo": 1}
    finally:
        reopened.close()


def test_resume_completed_wrong_actor_denied(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    try:
        registry.register(make_definition("op_safe"))
        executor = make_executor(registry, {"op_safe": lambda i, p: {"n": 1}})
        run = executor.run_recoverable("wf", "1.0.0", {"x": 1}, "alice")
        assert run.status == "COMPLETED"
        with pytest.raises(ValueError, match="actor"):
            executor.resume(run.run_id, {"x": 1}, "mallory")
    finally:
        registry.close()


def test_resume_completed_wrong_inputs_denied(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    try:
        registry.register(make_definition("op_safe"))
        executor = make_executor(registry, {"op_safe": lambda i, p: {"n": 1}})
        run = executor.run_recoverable("wf", "1.0.0", {"x": 1}, "alice")
        assert run.status == "COMPLETED"
        with pytest.raises(ValueError, match="digest"):
            executor.resume(run.run_id, {"x": 2}, "alice")
    finally:
        registry.close()


def test_resume_completed_matching_inputs_returns_completed(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    try:
        registry.register(make_definition("op_safe"))
        executor = make_executor(registry, {"op_safe": lambda i, p: {"n": 1}})
        run = executor.run_recoverable("wf", "1.0.0", {"x": 1}, "alice")
        again = executor.resume(run.run_id, {"x": 1}, "alice")
        assert again.status == "COMPLETED"
        assert again.run_id == run.run_id
        assert again.step_results == run.step_results
    finally:
        registry.close()


def _indeterminate_run(tmp_path: Path, name: str) -> tuple[WorkflowRegistry, str]:
    registry = make_registry(tmp_path, name)
    registry.register(make_definition("op_safe", "op_safe"))
    calls: list[list[str]] = []

    def op_safe(inputs: dict, prev: dict) -> object:
        calls.append(sorted(prev.keys()))
        return {"n": len(calls)}

    executor = make_executor(registry, {"op_safe": op_safe})
    seed = _seed_running(
        registry,
        registry.get("wf", "1.0.0"),
        {"x": 1},
        "alice",
        {"s0": {"status": "ok", "output": {"n": 1}, "error": None}},
    )
    reconciled = executor.reconcile()
    assert [r.run_id for r in reconciled] == [seed.run_id]
    assert executor.inspect_recoverable(seed.run_id).status == "INDETERMINATE"
    assert calls == []
    return registry, seed.run_id


def test_resume_indeterminate_wrong_inputs_denied(tmp_path: Path) -> None:
    registry, run_id = _indeterminate_run(tmp_path, "rec.db")
    try:
        executor = make_executor(registry, {"op_safe": lambda i, p: {"n": 9}})
        with pytest.raises(ValueError, match="digest"):
            executor.resume(run_id, {"x": 999}, "alice")
    finally:
        registry.close()


def test_resume_indeterminate_wrong_actor_denied(tmp_path: Path) -> None:
    registry, run_id = _indeterminate_run(tmp_path, "rec.db")
    try:
        executor = make_executor(registry, {"op_safe": lambda i, p: {"n": 9}})
        with pytest.raises(ValueError, match="actor"):
            executor.resume(run_id, {"x": 1}, "mallory")
    finally:
        registry.close()


def test_resume_indeterminate_op_version_change_denied(tmp_path: Path) -> None:
    registry, run_id = _indeterminate_run(tmp_path, "rec.db")
    try:
        executor = make_executor(
            registry,
            {"op_safe": lambda i, p: {"n": 9}},
            versions={"op_safe": "2", "op_other": "1", "block": "1"},
        )
        with pytest.raises(ValueError, match="operation versions"):
            executor.resume(run_id, {"x": 1}, "alice")
    finally:
        registry.close()


def test_resume_indeterminate_definition_change_denied(tmp_path: Path) -> None:
    registry, run_id = _indeterminate_run(tmp_path, "rec.db")
    try:
        conn = sqlite3.connect(str(tmp_path / "rec.db"))
        try:
            row = conn.execute(
                "SELECT definition_json FROM workflow_definitions "
                "WHERE workflow_id = ? AND version = ?",
                ("wf", "1.0.0"),
            ).fetchone()
            import json as _json

            payload = _json.loads(row[0])
            payload["steps"].append(
                {
                    "step_id": "evil",
                    "operation": "op_safe",
                    "depends_on": [],
                    "permissions": [],
                }
            )
            conn.execute(
                "UPDATE workflow_definitions SET definition_json = ? "
                "WHERE workflow_id = ? AND version = ?",
                (_json.dumps(payload, sort_keys=True), "wf", "1.0.0"),
            )
            conn.commit()
        finally:
            conn.close()
        executor = make_executor(registry, {"op_safe": lambda i, p: {"n": 9}})
        with pytest.raises(ValueError, match="definition"):
            executor.resume(run_id, {"x": 1}, "alice")
    finally:
        registry.close()


def test_resume_failed_and_unknown_runs_refused(tmp_path: Path) -> None:
    registry, run_id = _indeterminate_run(tmp_path, "rec.db")
    try:
        executor = make_executor(registry, {"op_safe": lambda i, p: {"n": 9}})
        with pytest.raises(KeyError):
            executor.resume("missing-run", {"x": 1}, "alice")
        stored = registry.get_recoverable_run(run_id)
        failed = RecoverableRun(
            run_id=stored.run_id,
            workflow_id=stored.workflow_id,
            version=stored.version,
            actor_id=stored.actor_id,
            status="FAILED",
            step_results={sid: dict(e) for sid, e in stored.step_results.items()},
            input_digest=stored.input_digest,
            definition_digest=stored.definition_digest,
            ops_binding=dict(stored.ops_binding),
            created_at=stored.created_at,
            updated_at=None,
        )
        registry.record_recoverable_run(failed)
        with pytest.raises(RuntimeError, match="reconcile before resume"):
            executor.resume(run_id, {"x": 1}, "alice")
    finally:
        registry.close()


def test_unsafe_tail_never_called_and_stays_indeterminate(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    try:
        registry.register(make_definition("op_safe", "op_other"))
        unsafe_calls: list[dict] = []

        def op_other(inputs: dict, prev: dict) -> object:
            unsafe_calls.append(dict(prev))
            return {"n": 1}

        executor = make_executor(
            registry,
            {"op_safe": lambda i, p: {"n": 1}, "op_other": op_other},
            replay=frozenset({"op_safe"}),
        )
        run = executor.run_recoverable("wf", "1.0.0", {"x": 1}, "alice")
        assert run.status == "INDETERMINATE"
        assert run.step_results["s0"]["status"] == "ok"
        assert run.step_results["s1"]["status"] == "refused"
        assert run.step_results["s1"]["error"] == "replay refused: not replay-safe"
        assert unsafe_calls == []
        resumed = executor.resume(run.run_id, {"x": 1}, "alice")
        assert resumed.status == "INDETERMINATE"
        assert unsafe_calls == []
    finally:
        registry.close()


def _recording_op(calls: list[dict]) -> object:
    def op_safe(inputs: dict, prev: dict) -> object:
        calls.append(dict(prev))
        return {"n": 1}

    return op_safe


def test_strict_authorizer_truthy_non_true_denied(tmp_path: Path) -> None:
    for variant, auth in [
        ("truthy-int", lambda _a, _p: 1),  # type: ignore[return-value]
        ("truthy-str", lambda _a, _p: "yes"),  # type: ignore[return-value]
        ("raising", lambda _a, _p: (_ for _ in ()).throw(RuntimeError("boom"))),
        ("none", None),
    ]:
        registry = make_registry(tmp_path, f"strict-{variant}.db")
        try:
            registry.register(
                WorkflowDefinition(
                    workflow_id="wf",
                    version="1.0.0",
                    steps=(WorkflowStep("s0", "op_safe", permissions=("perm.a",)),),
                )
            )
            op_calls: list[dict] = []
            op_safe = _recording_op(op_calls)

            executor = make_executor(
                registry, {"op_safe": op_safe}, authorizer=auth, versions=dict(OPS_V)
            )
            if variant == "none":
                executor = WorkflowExecutor(
                    registry,
                    {"op_safe": op_safe},
                    authorizer=None,
                    replay_safe=frozenset({"op_safe"}),
                    operation_versions=dict(OPS_V),
                )
            run = executor.run_recoverable("wf", "1.0.0", {"x": 1}, "alice")
            assert run.status == "FAILED", variant
            assert run.step_results["s0"]["error"] == "permission denied: perm.a", variant
            assert op_calls == [], variant
        finally:
            registry.close()


def test_run_persistence_failure_propagates_no_fake_success(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    try:
        registry.register(make_definition("op_safe"))
        executor = make_executor(registry, {"op_safe": lambda i, p: {"n": 1}})
        original = registry.record_recoverable_run

        def fail_completed(run: RecoverableRun) -> RecoverableRun:
            if run.status == "COMPLETED":
                raise RuntimeError("final persist boom")
            return original(run)

        registry.record_recoverable_run = fail_completed  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="final persist boom"):
            executor.run_recoverable("wf", "1.0.0", {"x": 1}, "alice")
        rows = registry.list_recoverable_runs()
        assert rows != []
        assert all(r.status != "COMPLETED" for r in rows)
    finally:
        registry.close()


def test_run_intent_persistence_failure_propagates(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    try:
        registry.register(make_definition("op_safe"))
        executor = make_executor(registry, {"op_safe": lambda i, p: {"n": 1}})

        def boom(run: RecoverableRun) -> RecoverableRun:
            raise RuntimeError("intent persist boom")

        registry.record_recoverable_run = boom  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="intent persist boom"):
            executor.run_recoverable("wf", "1.0.0", {"x": 1}, "alice")
    finally:
        registry.close()


def test_resume_persistence_failure_propagates(tmp_path: Path) -> None:
    registry, run_id = _indeterminate_run(tmp_path, "rec.db")
    try:
        executor = make_executor(registry, {"op_safe": lambda i, p: {"n": 2}})

        def boom(run: RecoverableRun) -> RecoverableRun:
            raise RuntimeError("resume persist boom")

        registry.record_recoverable_run = boom  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="resume persist boom"):
            executor.resume(run_id, {"x": 1}, "alice")
    finally:
        registry.close()


def test_memory_backend_refused(tmp_path: Path) -> None:
    registry = WorkflowRegistry(":memory:")
    try:
        executor = make_executor(registry, {"op_safe": lambda i, p: {"n": 1}})
        registry.register(make_definition("op_safe"))
        with pytest.raises(ValueError, match=":memory:"):
            executor.run_recoverable("wf", "1.0.0", {"x": 1}, "alice")
        with pytest.raises(ValueError, match=":memory:"):
            executor.resume("r", {"x": 1}, "alice")
        with pytest.raises(ValueError, match=":memory:"):
            executor.reconcile()
        with pytest.raises(ValueError, match=":memory:"):
            executor.inspect_recoverable("r")
    finally:
        registry.close()
    with pytest.raises(ValueError, match=":memory:"):
        RecoverableLock(":memory:")


def test_legacy_rows_never_reconciled_or_resumed(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    try:
        registry.register(make_definition("op_safe"))
        legacy = WorkflowRun(
            run_id="legacy-1",
            workflow_id="wf",
            version="1.0.0",
            actor_id="alice",
            status="RUNNING",
            step_results={},
            created_at="2026-01-01T00:00:00+00:00",
            completed_at=None,
        )
        registry.record_run(legacy)
        executor = make_executor(registry, {"op_safe": lambda i, p: {"n": 1}})
        assert executor.reconcile() == []
        with pytest.raises(KeyError):
            executor.inspect_recoverable("legacy-1")
        with pytest.raises(KeyError):
            registry.get_recoverable_run("legacy-1")
        with pytest.raises(KeyError):
            executor.resume("legacy-1", {"x": 1}, "alice")
        assert registry.get_run("legacy-1").status == "RUNNING"
    finally:
        registry.close()


def test_reconcile_resume_keeps_acknowledged_prefix(tmp_path: Path) -> None:
    registry, run_id = _indeterminate_run(tmp_path, "rec.db")
    try:
        calls: list[list[str]] = []

        def op_safe(inputs: dict, prev: dict) -> object:
            calls.append(sorted(prev.keys()))
            assert "s0" in prev
            assert prev["s0"] == {"n": 1}
            return {"n": 2}

        executor = make_executor(registry, {"op_safe": op_safe})
        resumed = executor.resume(run_id, {"x": 1}, "alice")
        assert resumed.status == "COMPLETED"
        assert resumed.step_results["s0"] == {"status": "ok", "output": {"n": 1}, "error": None}
        assert resumed.step_results["s1"]["status"] == "ok"
        assert resumed.step_results["s1"]["output"] == {"n": 2}
        assert calls == [["s0"]]
    finally:
        registry.close()


def test_json_bounds_enforced(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    try:
        registry.register(make_definition("op_safe"))
        executor = make_executor(registry, {"op_safe": lambda i, p: {"n": 1}})
        big = "A" * (MAX_JSON_BYTES + 1)
        with pytest.raises(ValueError, match="exceed"):
            executor.run_recoverable("wf", "1.0.0", {"x": big}, "alice")
        with pytest.raises(ValueError, match="JSON"):
            executor.run_recoverable("wf", "1.0.0", {"x": float("nan")}, "alice")

        def oversize(inputs: dict, prev: dict) -> object:
            return {"big": "B" * MAX_JSON_BYTES}

        executor_big = make_executor(registry, {"op_safe": oversize})
        run = executor_big.run_recoverable("wf", "1.0.0", {"x": 1}, "alice")
        assert run.status == "FAILED"
        assert "exceeds" in str(run.step_results["s0"]["error"])

        def non_json(inputs: dict, prev: dict) -> object:
            return object()

        executor_nj = make_executor(registry, {"op_safe": non_json})
        run_nj = executor_nj.run_recoverable("wf", "1.0.0", {"x": 1}, "alice")
        assert run_nj.status == "FAILED"
        assert "non-JSON-serializable" in str(run_nj.step_results["s0"]["error"])
    finally:
        registry.close()


def test_indeterminate_survives_reopen(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    try:
        registry.register(make_definition("op_safe", "op_other"))
        executor = make_executor(
            registry,
            {"op_safe": lambda i, p: {"n": 1}, "op_other": lambda i, p: {"n": 2}},
            replay=frozenset({"op_safe"}),
        )
        run = executor.run_recoverable("wf", "1.0.0", {"x": 1}, "alice")
        assert run.status == "INDETERMINATE"
        run_id = run.run_id
    finally:
        registry.close()
    reopened = WorkflowRegistry(str(tmp_path / "rec.db"))
    try:
        kept = reopened.get_recoverable_run(run_id)
        assert kept.status == "INDETERMINATE"
        assert kept.step_results["s0"]["status"] == "ok"
        assert kept.step_results["s1"]["status"] == "refused"
    finally:
        reopened.close()


HOLDER_SCRIPT = """import sys, time
sys.path.insert(0, %r)
from agency.workflows.recovery import RecoverableLock
db, ready = sys.argv[1], sys.argv[2]
lock = RecoverableLock(db)
if not lock.acquire():
    sys.exit(2)
with open(ready, "w") as handle:
    handle.write("ready")
try:
    time.sleep(60)
finally:
    lock.release()
"""

CRASH_SCRIPT = """import sys, time
sys.path.insert(0, %r)
from agency.workflows.executor import WorkflowExecutor
from agency.workflows.registry import WorkflowDefinition, WorkflowRegistry, WorkflowStep
db, marker = sys.argv[1], sys.argv[2]
registry = WorkflowRegistry(db)
try:
    steps = (WorkflowStep("s0", "op_safe"), WorkflowStep("s1", "op_safe"))
    try:
        registry.register(WorkflowDefinition("wf", "1.0.0", steps))
    except ValueError:
        pass
    def op_safe(inputs, prev):
        if "s0" not in prev:
            with open(marker, "a") as handle:
                handle.write("s0\\n")
            return {"n": 1}
        with open(marker, "a") as handle:
            handle.write("s1-start\\n")
        for _ in range(600):
            time.sleep(0.1)
        return {"n": 2}
    executor = WorkflowExecutor(
        registry, {"op_safe": op_safe}, authorizer=lambda a, p: True,
        replay_safe=frozenset({"op_safe"}), operation_versions={"op_safe": "1"},
    )
    try:
        executor.run_recoverable("wf", "1.0.0", {"x": 1}, "alice")
    except Exception:
        pass
finally:
    registry.close()
"""


def test_lock_denial_while_holder_alive_and_release_after_kill(tmp_path: Path) -> None:
    db_path = str(tmp_path / "rec.db")
    registry = WorkflowRegistry(db_path)
    child: subprocess.Popen[bytes] | None = None
    try:
        registry.register(make_definition("op_safe"))
        script = tmp_path / "holder.py"
        script.write_text(HOLDER_SCRIPT % str(SRC_DIR))
        ready = tmp_path / "holder.ready"
        child = subprocess.Popen(
            [sys.executable, str(script), db_path, str(ready)],
            env=_child_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_for_path(ready)
            parent_lock = RecoverableLock(db_path)
            assert parent_lock.acquire() is False
            executor = make_executor(registry, {"op_safe": lambda i, p: {"n": 1}})
            with pytest.raises(RuntimeError, match="lock held"):
                executor.run_recoverable("wf", "1.0.0", {"x": 1}, "alice")
            with pytest.raises(RuntimeError, match="lock held"):
                executor.reconcile()
            with pytest.raises(RuntimeError, match="lock held"):
                executor.resume("missing", {"x": 1}, "alice")
        finally:
            assert child is not None
            child.kill()
            child.wait(timeout=20)
            child = None
        free_lock = RecoverableLock(db_path)
        assert free_lock.acquire() is True
        free_lock.release()
        executor = make_executor(registry, {"op_safe": lambda i, p: {"n": 1}})
        assert executor.reconcile() == []
    finally:
        if child is not None:
            child.kill()
            child.wait(timeout=20)
        registry.close()


def test_child_killed_mid_callback_reconcile_resume(tmp_path: Path) -> None:
    db_path = str(tmp_path / "rec.db")
    registry = WorkflowRegistry(db_path)
    child: subprocess.Popen[bytes] | None = None
    try:
        registry.register(make_definition("op_safe", "op_safe"))
        script = tmp_path / "crasher.py"
        script.write_text(CRASH_SCRIPT % str(SRC_DIR))
        marker = tmp_path / "calls.marker"
        child = subprocess.Popen(
            [sys.executable, str(script), db_path, str(marker)],
            env=_child_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_for_marker(marker, "s1-start")
        finally:
            assert child is not None
            child.kill()
            child.wait(timeout=20)
            child = None
        text = marker.read_text()
        assert "s0" in text
        assert "s1-start" in text
        executor = make_executor(registry, {"op_safe": lambda i, p: {"n": 1}})
        crashed = registry.list_recoverable_runs("RUNNING")
        assert len(crashed) == 1
        reconciled = executor.reconcile()
        assert len(reconciled) == 1
        assert reconciled[0].status == "INDETERMINATE"
        assert reconciled[0].step_results["s0"]["status"] == "ok"
        assert reconciled[0].step_results["s0"]["output"] == {"n": 1}
        calls: list[list[str]] = []

        def op_safe(inputs: dict, prev: dict) -> object:
            calls.append(sorted(prev.keys()))
            assert prev.get("s0") == {"n": 1}
            return {"n": 2}

        resume_executor = make_executor(registry, {"op_safe": op_safe})
        resumed = resume_executor.resume(reconciled[0].run_id, {"x": 1}, "alice")
        assert resumed.status == "COMPLETED"
        assert resumed.step_results["s0"]["output"] == {"n": 1}
        assert resumed.step_results["s1"]["output"] == {"n": 2}
        assert calls == [["s0"]]
    finally:
        if child is not None:
            child.kill()
            child.wait(timeout=20)
        registry.close()
