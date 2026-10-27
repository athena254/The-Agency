"""Tests for minimal durable local recovery v1 (SPEC_LOCAL_RECOVERY_V1)."""

from __future__ import annotations

import os
import runpy
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
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


OUTPUT_TAIL_BYTES = 4096


@dataclass
class _Child:
    process: subprocess.Popen[bytes]
    stdout: Path
    stderr: Path

    def diagnostics(self) -> str:
        # Read only bounded tails; regular files cannot backpressure the child.
        parts = [f"pid {self.process.pid}, exit code {self.process.poll()}"]
        for label, path in (("stdout", self.stdout), ("stderr", self.stderr)):
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - OUTPUT_TAIL_BYTES))
                tail = handle.read(OUTPUT_TAIL_BYTES).decode("utf-8", errors="replace")
            parts.append(f"{label} (last {OUTPUT_TAIL_BYTES} bytes): {tail}")
        return "\n".join(parts)


def _check_child(child: _Child, waiting: str) -> None:
    if child.process.poll() is not None:
        raise RuntimeError(f"child exited while waiting for {waiting}\n{child.diagnostics()}")


def _wait_for_path(path: Path, child: _Child, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        _check_child(child, str(path))
        if path.exists():
            return
        if time.monotonic() > deadline:
            raise TimeoutError(f"timed out waiting for {path}\n{child.diagnostics()}")
        time.sleep(0.05)


def _wait_for_marker(path: Path, token: str, child: _Child, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        _check_child(child, f"{token!r} in {path}")
        if path.exists() and token in path.read_text():
            return
        if time.monotonic() > deadline:
            raise TimeoutError(f"timed out waiting for {token!r} in {path}\n{child.diagnostics()}")
        time.sleep(0.05)


@contextmanager
def _cleanup_after(cleanup: Callable[[], object]) -> Iterator[None]:
    """Do not let a secondary cleanup failure replace the useful test failure."""
    try:
        yield
    except BaseException as primary:
        try:
            cleanup()
        except BaseException as secondary:  # noqa: BLE001 - preserve the primary failure
            primary.add_note(f"cleanup failed: {secondary!r}")
        raise
    else:
        cleanup()


def _reap_child(process: subprocess.Popen[bytes]) -> None:
    try:
        if process.poll() is None:
            process.kill()
    finally:
        # Even a failing kill must not skip reaping a child that has exited.
        process.wait(timeout=20)


@contextmanager
def _child_process(script: Path, *args: str) -> Iterator[_Child]:
    stdout_path = script.with_suffix(".stdout")
    stderr_path = script.with_suffix(".stderr")
    files = ExitStack()
    with _cleanup_after(files.close):
        stdout = files.enter_context(stdout_path.open("wb"))
        stderr = files.enter_context(stderr_path.open("wb"))
        executable = sys.executable
        env = _child_env()
        if os.name == "nt" and sys.prefix != sys.base_prefix:
            # The Windows venv executable is a launcher, not the interpreter.
            # Bypass it so kill()/wait() own the actual lock holder, retaining
            # the same venv configuration through CPython's launcher variable.
            executable = sys._base_executable
            env["__PYVENV_LAUNCHER__"] = sys.executable
        process = subprocess.Popen(
            [executable, str(script), *args],
            env=env,
            stdout=stdout,
            stderr=stderr,
        )
        with _cleanup_after(lambda: _reap_child(process)):
            yield _Child(process, stdout_path, stderr_path)


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    # Retain the qualification harness's inherited sitecustomize/network guard.
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(SRC_DIR), env.get("PYTHONPATH", "")) if part
    )
    env["AGENCY_LLM_PROVIDER"] = "echo"
    env["AGENCY_LLM_MODEL"] = "echo"
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

CRASH_SCRIPT = """import sys, time, traceback
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
    def diagnostic_callback(inputs, prev):
        try:
            return op_safe(inputs, prev)
        except Exception:
            traceback.print_exc()
            raise
    executor = WorkflowExecutor(
        registry, {"op_safe": diagnostic_callback}, authorizer=lambda a, p: True,
        replay_safe=frozenset({"op_safe"}), operation_versions={"op_safe": "1"},
    )
    run = executor.run_recoverable("wf", "1.0.0", {"x": 1}, "alice")
    if run.status != "COMPLETED":
        raise RuntimeError(f"recoverable child failed: {run.step_results!r}")
except BaseException as primary:
    try:
        registry.close()
    except BaseException as secondary:
        primary.add_note(f"registry cleanup failed: {secondary!r}")
    raise
else:
    registry.close()
"""


@pytest.mark.parametrize("failure", ["executor", "callback"])
def test_crash_script_propagates_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    script = CRASH_SCRIPT % str(SRC_DIR)
    if failure == "executor":

        def fail(*args, **kwargs):
            raise RuntimeError("synthetic executor failure")

        monkeypatch.setattr(WorkflowExecutor, "run_recoverable", fail)
    else:
        script = script.replace(
            'if "s0" not in prev:',
            'raise RuntimeError("synthetic callback failure")\n        if "s0" not in prev:',
        )
    monkeypatch.setattr(
        sys, "argv", ["crasher.py", str(tmp_path / "rec.db"), str(tmp_path / "marker")]
    )
    script_path = tmp_path / "crasher.py"
    script_path.write_text(script)
    expected = "synthetic executor failure" if failure == "executor" else "recoverable child failed"
    with pytest.raises(RuntimeError, match=expected):
        runpy.run_path(str(script_path), run_name="__main__")
    if failure == "callback":
        assert "synthetic callback failure" in capsys.readouterr().err


def test_child_handle_targets_interpreter(tmp_path: Path) -> None:
    # kill()/wait() must target the script, not a Windows venv launcher whose
    # interpreter descendant can retain the lock after the launcher is reaped.
    script = tmp_path / "identity.py"
    script.write_text(
        "import os, sys\n"
        "from pathlib import Path\n"
        "from agency.workflows.recovery import RecoverableLock\n"
        "print(os.getpid(), flush=True)\n"
        "print(Path(sys.prefix).resolve(), flush=True)\n"
        "print(RecoverableLock.__module__, flush=True)\n"
    )
    with _child_process(script) as child:
        assert child.process.wait(timeout=20) == 0, child.diagnostics()
        pid, prefix, module = child.stdout.read_text().splitlines()
        assert int(pid) == child.process.pid, child.diagnostics()
        assert Path(prefix) == Path(sys.prefix).resolve()
        assert module == "agency.workflows.recovery"


@pytest.mark.parametrize("wait_kind", ["path", "marker"])
def test_child_timeout_reaps_and_preserves_primary_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wait_kind: str
) -> None:
    script = tmp_path / "sleeper.py"
    script.write_text("import time; time.sleep(60)")
    child = None
    waited = []
    try:
        with (
            pytest.raises(TimeoutError, match="timed out waiting") as caught,
            _child_process(script) as child,
        ):
            original_kill, original_wait = child.process.kill, child.process.wait

            def kill_then_fail():
                original_kill()
                raise RuntimeError("synthetic cleanup failure")

            def record_wait(*args, **kwargs):
                waited.append(True)
                return original_wait(*args, **kwargs)

            monkeypatch.setattr(child.process, "kill", kill_then_fail)
            monkeypatch.setattr(child.process, "wait", record_wait)
            absent = tmp_path / "absent"
            if wait_kind == "path":
                _wait_for_path(absent, child, timeout=0)
            else:
                _wait_for_marker(absent, "missing-token", child, timeout=0)
        assert waited
        assert child.process.returncode is not None
        assert "stdout" in str(caught.value) and "stderr" in str(caught.value)
        assert any("synthetic cleanup failure" in note for note in caught.value.__notes__)
    finally:
        # Keep the regression itself leak-free even against the broken helper.
        if child is not None:
            if child.process.poll() is None:
                original_kill()
            original_wait(timeout=20)


def test_child_env_preserves_offline_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "synthetic-offline-guard")
    monkeypatch.setenv("AGENCY_LLM_PROVIDER", "not-echo")
    monkeypatch.setenv("AGENCY_LLM_MODEL", "not-echo")
    env = _child_env()
    assert env["PYTHONPATH"].split(os.pathsep) == [str(SRC_DIR), "synthetic-offline-guard"]
    assert env["AGENCY_LLM_PROVIDER"] == env["AGENCY_LLM_MODEL"] == "echo"


@pytest.mark.parametrize("scenario", ["holder", "crasher"])
def test_child_early_exit_reports_bounded_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    # Exercise the actual launch/wait/cleanup wiring with a deterministically dead child.
    script = """import sys
sys.path.insert(0, %r)
print("synthetic stdout", flush=True)
print("x" * 20000 + "synthetic stderr", file=sys.stderr, flush=True)
sys.exit(23)
"""
    script_name, wait_name, exercise = {
        "holder": (
            "HOLDER_SCRIPT",
            "_wait_for_path",
            test_lock_denial_while_holder_alive_and_release_after_kill,
        ),
        "crasher": (
            "CRASH_SCRIPT",
            "_wait_for_marker",
            test_child_killed_mid_callback_reconcile_resume,
        ),
    }[scenario]
    monkeypatch.setitem(globals(), script_name, script)
    original_popen = subprocess.Popen

    def exited_child(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        child.wait(timeout=20)
        return child

    monkeypatch.setattr(subprocess, "Popen", exited_child)
    original_wait = globals()[wait_name]

    def no_deadline_wait(*args, **kwargs):
        return original_wait(*args, **kwargs, timeout=0)

    monkeypatch.setitem(globals(), wait_name, no_deadline_wait)
    with pytest.raises(RuntimeError, match="exit code 23") as caught:
        exercise(tmp_path)
    message = str(caught.value)
    assert "synthetic stdout" in message
    assert "synthetic stderr" in message
    assert "stdout" in message and "stderr" in message
    assert len(message) < 10000


def test_lock_denial_while_holder_alive_and_release_after_kill(tmp_path: Path) -> None:
    db_path = str(tmp_path / "rec.db")
    registry = WorkflowRegistry(db_path)
    with _cleanup_after(registry.close):
        registry.register(make_definition("op_safe"))
        script = tmp_path / "holder.py"
        script.write_text(HOLDER_SCRIPT % str(SRC_DIR))
        ready = tmp_path / "holder.ready"
        with _child_process(script, db_path, str(ready)) as child:
            _wait_for_path(ready, child)
            parent_lock = RecoverableLock(db_path)
            assert parent_lock.acquire() is False
            executor = make_executor(registry, {"op_safe": lambda i, p: {"n": 1}})
            with pytest.raises(RuntimeError, match="lock held"):
                executor.run_recoverable("wf", "1.0.0", {"x": 1}, "alice")
            with pytest.raises(RuntimeError, match="lock held"):
                executor.reconcile()
            with pytest.raises(RuntimeError, match="lock held"):
                executor.resume("missing", {"x": 1}, "alice")
        free_lock = RecoverableLock(db_path)
        assert free_lock.acquire() is True
        free_lock.release()
        executor = make_executor(registry, {"op_safe": lambda i, p: {"n": 1}})
        assert executor.reconcile() == []


def test_child_killed_mid_callback_reconcile_resume(tmp_path: Path) -> None:
    db_path = str(tmp_path / "rec.db")
    registry = WorkflowRegistry(db_path)
    with _cleanup_after(registry.close):
        registry.register(make_definition("op_safe", "op_safe"))
        script = tmp_path / "crasher.py"
        script.write_text(CRASH_SCRIPT % str(SRC_DIR))
        marker = tmp_path / "calls.marker"
        with _child_process(script, db_path, str(marker)) as child:
            _wait_for_marker(marker, "s1-start", child)
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
