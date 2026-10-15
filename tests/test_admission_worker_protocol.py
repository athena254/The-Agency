"""Checked admission-worker gates against real, scratch-only durable audit."""

import asyncio
import queue
import time

import pytest

from agency.kernel.audit import BetaAuditLog
from tests.worker_support.admission import admission_worker


def _beta_race_worker(path, start, release, barrier, messages):
    admission_worker(path, Gate(True), start, release, barrier, messages, time.monotonic() + 60)


class Gate:
    def __init__(self, value):
        self.value = value
        self.waits = []

    def wait(self, timeout):
        self.waits.append(timeout)
        return self.value


async def audit_state(path):
    log = BetaAuditLog(path)
    await log.initialize()
    try:
        return await log.query(), await log.has_pending_calls()
    finally:
        await log.close()


def test_expired_start_never_dispatches_or_writes(tmp_path):
    path = str(tmp_path / "expired-start.db")
    messages = queue.Queue()
    start, release, barrier = Gate(False), Gate(True), Gate(True)
    original = BetaAuditLog.has_pending_calls
    try:
        with pytest.raises(AssertionError, match="start gate timed out"):
            _beta_race_worker(path, start, release, barrier, messages)
    finally:
        BetaAuditLog.has_pending_calls = original
    assert start.waits == [10]
    assert release.waits == []
    assert asyncio.run(audit_state(path)) == ([], False)


def test_expired_release_never_reports_success(tmp_path):
    path = str(tmp_path / "expired-release.db")
    messages = queue.Queue()
    start, release, barrier = Gate(True), Gate(False), Gate(True)
    original = BetaAuditLog.has_pending_calls
    try:
        with pytest.raises(AssertionError, match="release gate timed out"):
            _beta_race_worker(path, start, release, barrier, messages)
    finally:
        BetaAuditLog.has_pending_calls = original
    assert release.waits == [10]
    rows, pending = asyncio.run(audit_state(path))
    assert {row.result for row in rows} == {"pending", "error"}
    assert len(rows) == 2 and pending is False
    observed = list(messages.queue)
    result = next(m for m in observed if m["event"] == "result")
    assert result["ok"] is False and result["error"] == "beta tool failed"
    assert result["output"] is None
    assert observed[-1]["event"] == "worker_error"
    assert observed[-1]["phase"] == "release"


@pytest.mark.parametrize("gate_value", [1, None])
def test_start_requires_literal_true(tmp_path, gate_value):
    path = str(tmp_path / "literal-start.db")
    messages = queue.Queue()
    with pytest.raises(AssertionError, match="start gate timed out"):
        _beta_race_worker(path, Gate(gate_value), Gate(True), Gate(True), messages)
    assert asyncio.run(audit_state(path)) == ([], False)


def test_arm_expiry_never_readies_or_dispatches(tmp_path):
    path = str(tmp_path / "expired-arm.db")
    messages = queue.Queue()
    start = Gate(True)
    with pytest.raises(AssertionError, match="arm gate timed out"):
        admission_worker(
            path, Gate(False), start, Gate(True), Gate(True), messages, time.monotonic() + 60
        )
    assert start.waits == []
    assert [m["event"] for m in list(messages.queue)] == [
        "bootstrap_started",
        "bootstrap_complete",
        "worker_error",
    ]
    assert asyncio.run(audit_state(path)) == ([], False)


def test_worker_restores_mutant_class_patches(tmp_path):
    original_pending, original_claim = BetaAuditLog.has_pending_calls, BetaAuditLog.claim_intent
    with pytest.raises(AssertionError, match="start gate timed out"):
        admission_worker(
            str(tmp_path / "restore.db"),
            Gate(True),
            Gate(False),
            Gate(True),
            Gate(True),
            queue.Queue(),
            time.monotonic() + 60,
            mutant=True,
        )
    assert BetaAuditLog.has_pending_calls is original_pending
    assert BetaAuditLog.claim_intent is original_claim


def test_spawn_support_has_only_stdlib_top_level_imports():
    import ast
    import sys
    from pathlib import Path

    from tests.worker_support import admission

    assert (
        Path(admission.__file__).resolve() == Path(__file__).parent / "worker_support/admission.py"
    )
    module = ast.parse(Path(admission.__file__).read_text(encoding="utf8"))
    for node in module.body:
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] in sys.stdlib_module_names for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module.split(".")[0] in sys.stdlib_module_names
    assert admission_worker.__module__ == "tests.worker_support.admission"
    assert "test_beta_tool_boundary" not in Path(admission.__file__).read_text(encoding="utf8")


def test_parent_rejects_real_split_claim_two_ran_mutant(tmp_path):
    from tests.worker_support.admission import AdmissionProtocolError, run_admission_race

    path = str(tmp_path / "unsafe-mutant.db")
    with pytest.raises(AdmissionProtocolError, match="two tools ran before release") as caught:
        run_admission_race(path, mutant=True)
    observed = caught.value.observations
    ran = [m for m in observed if m["event"] == "ran"]
    assert len(ran) == 2 and len({m["pid"] for m in ran}) == 2
    assert not any(m["event"] == "result" for m in observed)
    rows, pending = asyncio.run(audit_state(path))
    assert len(rows) == 2 and {row.result for row in rows} == {"pending"}
    assert len({row.evidence["call_id"] for row in rows}) == 2
    assert pending is True
    print(f"unsafe split claim rejected: two held calls, pids={[m['pid'] for m in ran]}")


class FailingStartContext:
    """Delegate real spawn, then fail after second child acquired a PID."""

    def __init__(self):
        import multiprocessing

        self.real = multiprocessing.get_context("spawn")
        self.workers = []
        self.events = []
        self.closed = []

    def Event(self):
        event = self.real.Event()
        self.events.append(event)
        return event

    def Barrier(self, count):
        return self.real.Barrier(count)

    def Queue(self):
        return self.real.Queue()

    def Process(self, **kwargs):
        worker = self.real.Process(**kwargs)
        original_start, original_close = worker.start, worker.close
        index = len(self.workers)
        self.workers.append(worker)

        def start():
            original_start()
            if index == 1:
                raise RuntimeError("synthetic failure after process start")

        def close():
            assert not worker.is_alive()
            assert worker.exitcode is not None
            self.closed.append(index)
            original_close()

        class OwnedProcess:
            # Keep callbacks OFF the real Process: Windows pickles its dict.
            def __getattr__(self, name):
                return getattr(worker, name)

            def start(self):
                start()

            def close(self):
                close()

        return OwnedProcess()


def test_partially_failed_start_reaps_every_owned_child(tmp_path):
    from tests.worker_support.admission import run_admission_race

    context = FailingStartContext()
    with pytest.raises(RuntimeError, match="synthetic failure after process start"):
        run_admission_race(str(tmp_path / "partial-start.db"), context=context)
    assert context.closed == [0, 1]
    assert not any(event.is_set() for event in context.events)
    assert not (tmp_path / "partial-start.db").exists()


@pytest.mark.parametrize(
    "fault, expected",
    [
        ("worker_error", r"worker pid=101 phase=bootstrap error=RuntimeError"),
        ("early_ready", "bootstrap unexpected event=ready"),
        ("wrong_pid", "bootstrap unknown worker PID"),
        ("late_completion", "bootstrap absolute deadline expired"),
    ],
)
def test_parent_fails_fast_on_invalid_bootstrap(tmp_path, monkeypatch, fault, expected):
    from types import SimpleNamespace

    from tests.worker_support import admission

    clock = [0.0]
    monkeypatch.setattr(admission, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    events, workers = [], []
    messages = queue.Queue()
    messages.close = lambda: None
    messages.join_thread = lambda: None

    def event():
        value = SimpleNamespace(set=lambda: events.append("set"))
        return value

    def process(**kwargs):
        worker = SimpleNamespace(pid=None, exitcode=None, live=False, closed=False)
        index = len(workers)
        workers.append(worker)

        def start():
            worker.pid, worker.live = 101 + index, True
            if index == 0:
                event_name = "ready" if fault == "early_ready" else "worker_error"
                messages.put(
                    {
                        "event": event_name,
                        "pid": 999 if fault == "wrong_pid" else 101,
                        "phase": "bootstrap",
                        "error_type": "RuntimeError",
                    }
                )
                if fault == "late_completion":
                    original_get = messages.get

                    def late_get(**kwargs):
                        item = original_get(**kwargs)
                        clock[0] = 61.0  # One aggregate budget, never 60s per message.
                        return item

                    messages.get = late_get

        def terminate():
            worker.live, worker.exitcode = False, -15

        def close():
            assert worker.live is False and worker.exitcode == -15
            worker.closed = True

        worker.start, worker.terminate, worker.kill = start, terminate, terminate
        worker.join, worker.is_alive, worker.close = (
            lambda timeout: None,
            lambda: worker.live,
            close,
        )
        return worker

    context = SimpleNamespace(
        Event=event, Barrier=lambda count: None, Queue=lambda: messages, Process=process
    )
    with pytest.raises(admission.AdmissionProtocolError, match=expected):
        admission.run_admission_race(str(tmp_path / "invalid-bootstrap.db"), context=context)
    assert len(workers) == 2 and all(w.closed for w in workers)
    assert events == []
    assert not (tmp_path / "invalid-bootstrap.db").exists()
