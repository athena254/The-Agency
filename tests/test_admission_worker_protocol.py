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

    def __init__(self, *, wait_for_bootstrap=False):
        import multiprocessing

        self.real = multiprocessing.get_context("spawn")
        self.workers = []
        self.events = []
        self.closed = []
        self.wait_for_bootstrap = wait_for_bootstrap

    def Event(self):
        event = self.real.Event()
        self.events.append(event)
        return event

    def Barrier(self, count):
        return self.real.Barrier(count)

    def Queue(self):
        self.messages = self.real.Queue()
        return self.messages

    def Process(self, **kwargs):
        worker = self.real.Process(**kwargs)
        original_start, original_close = worker.start, worker.close
        index = len(self.workers)
        self.workers.append(worker)

        def start():
            if index == 1 and self.wait_for_bootstrap:
                # Force the valid schedule where worker 0 initialized the real DB
                # before worker 1's partial start fails. No sleep or retry race.
                deadline = kwargs["args"][-1]
                while True:
                    message = self.messages.get(timeout=max(0, deadline - time.monotonic()))
                    assert message["pid"] == self.workers[0].pid
                    assert message["event"] in {"bootstrap_started", "bootstrap_complete"}
                    if message["event"] == "bootstrap_complete":
                        break
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


@pytest.mark.parametrize(
    "faults, primary",
    [
        (("terminate",), "start"),
        (("join",), "start"),
        (("kill",), "start"),
        (("close",), "start"),
        (("queue.close",), "start"),
        (("queue.join_thread",), "start"),
        (("terminate", "join", "kill", "close", "queue.close", "queue.join_thread"), "start"),
        (("terminate",), "protocol"),
        (("terminate",), "interrupt"),
        (("close",), None),
        (("queue.close",), None),
        (("queue.join_thread",), None),
    ],
)
def test_cleanup_continues_and_preserves_primary_failure(tmp_path, faults, primary):
    from pathlib import Path
    from types import SimpleNamespace

    from tests.worker_support import admission

    calls, gates, workers = [], [], []
    messages = queue.Queue()
    original = (
        KeyboardInterrupt("synthetic interrupt")
        if primary == "interrupt"
        else RuntimeError("synthetic start failure")
    )

    def operation(name, index=None):
        calls.append((index, name))
        if name in faults and index in (None, 0):
            raise OSError("synthetic cleanup failure")

    messages.close = lambda: operation("queue.close")
    messages.join_thread = lambda: operation("queue.join_thread")

    def event():
        gate = SimpleNamespace(set=lambda: gates.append("set"), is_set=lambda: False)
        return gate

    def process(**kwargs):
        index = len(workers)
        worker = SimpleNamespace(pid=None, live=False, exitcode=None)
        workers.append(worker)

        def start():
            worker.pid, worker.live = 101 + index, True
            if index == 1 and primary in {"start", "interrupt"}:
                raise original

        def terminate():
            operation("terminate", index)
            # Leave worker 0 alive to exercise kill and final join independently.
            if index != 0:
                worker.live, worker.exitcode = False, -15

        def kill():
            worker.live, worker.exitcode = False, -9
            operation("kill", index)

        def join(timeout):
            if primary is None:
                worker.live, worker.exitcode = False, 0
            operation("join", index)

        worker.start, worker.terminate, worker.kill = start, terminate, kill
        worker.join, worker.is_alive = join, lambda: worker.live
        worker.close = lambda: operation("close", index)
        return worker

    def emit(event_name, pid, phase, **fields):
        messages.put({"event": event_name, "pid": pid, "phase": phase, **fields})

    if primary == "protocol":
        emit("worker_error", 101, "bootstrap", error_type="RuntimeError")
    elif primary is None:
        import sys

        for pid in (101, 102):
            emit("bootstrap_started", pid, "bootstrap")
            emit(
                "bootstrap_complete",
                pid,
                "bootstrap",
                support=str(Path(admission.__file__).resolve()),
                audit=str(
                    Path(admission.__file__).resolve().parents[2] / "src/agency/kernel/audit.py"
                ),
                registry=str(
                    Path(admission.__file__).resolve().parents[2] / "src/agency/tools/registry.py"
                ),
                prefix=sys.prefix,
            )
        for pid in (101, 102):
            emit("ready", pid, "start")
        emit("ran", 101, "release")
        emit(
            "result",
            102,
            "claim",
            ok=False,
            error="beta audit unavailable",
            output=None,
            status="INDETERMINATE",
        )
        emit("result", 101, "release", ok=True, error=None, output="ok")

    context = SimpleNamespace(
        Event=event, Barrier=lambda count: None, Queue=lambda: messages, Process=process
    )
    expected_type = (
        type(original) if primary in {"start", "interrupt"} else admission.AdmissionProtocolError
    )
    with pytest.raises(expected_type) as caught:
        admission.run_admission_race(str(tmp_path / "cleanup.db"), context=context)
    if primary in {"start", "interrupt"}:
        assert caught.value is original
    elif primary == "protocol":
        assert "phase=bootstrap error=RuntimeError" in str(caught.value)
    diagnostics = " ".join(getattr(caught.value, "__notes__", [])) + str(caught.value)
    for fault in faults:
        assert fault in diagnostics
    assert (1, "terminate") in calls if primary is not None else True
    assert (1, "join") in calls and (1, "close") in calls
    assert (None, "queue.close") in calls and (None, "queue.join_thread") in calls
    assert all(not worker.live for worker in workers)
    if primary is not None:
        assert (0, "kill") in calls
        assert calls.count((0, "join")) == 2
        assert gates == []


@pytest.mark.parametrize("wait_for_bootstrap", [False, True])
def test_partially_failed_start_reaps_every_owned_child(tmp_path, wait_for_bootstrap):
    from tests.worker_support.admission import run_admission_race

    context = FailingStartContext(wait_for_bootstrap=wait_for_bootstrap)
    with pytest.raises(RuntimeError, match="synthetic failure after process start"):
        run_admission_race(str(tmp_path / "partial-start.db"), context=context)
    assert context.closed == [0, 1]
    assert not any(event.is_set() for event in context.events)
    path = tmp_path / "partial-start.db"
    if wait_for_bootstrap:
        assert path.exists()  # Prove the initialized-before-failure schedule ran.
    assert asyncio.run(audit_state(str(path))) == ([], False)


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
