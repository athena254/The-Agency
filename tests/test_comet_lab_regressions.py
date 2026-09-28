"""Regression tests for agency.lab.comet_smoke (free lattice engine)."""

from __future__ import annotations

import base64
import io
import itertools
import json
import socket
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agency.lab import comet_smoke as lab

GOOD_HASH = "AB" * 32
CANON_PAYLOAD = "0x" + GOOD_HASH


@dataclass
class FakeClock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now


def make_hooks(clock=None, **overrides):
    clock = clock or FakeClock()
    hooks = lab.LabHooks(
        popen=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no popen")),
        run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no run")),
        make_rpc=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no rpc")),
        allocate_ports=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no ports")),
        sleep=lambda seconds: setattr(clock, "now", clock.now + seconds),
        monotonic=clock,
        now_utc=lambda: datetime(2026, 9, 28, tzinfo=UTC),
    )
    for key, value in overrides.items():
        assert hasattr(hooks, key), key
        setattr(hooks, key, value)
    return hooks


def make_inputs(tmp_path, whole_timeout=1.0):
    return lab.Inputs(binary=Path("/fake"), output=tmp_path, whole_timeout=whole_timeout)


def spec(index):
    return lab.NodeSpec(
        index=index,
        node_id=format(0x11 * (index + 1), "02x") * 20,  # 40 hex chars
        home=Path(f"/fake/n{index}"),
        p2p_port=30300 + index,
        rpc_port=30400 + index,
    )


_SOCK_PORTS = itertools.count(50000)


@dataclass
class FakeSock:
    fail_bind: bool = False
    closed: bool = False
    port: int = field(default_factory=lambda: next(_SOCK_PORTS))

    def bind(self, address):
        if self.fail_bind:
            raise OSError("address already in use")

    def listen(self, backlog=1):
        pass

    def getsockname(self):
        return ("127.0.0.1", self.port)

    def close(self):
        self.closed = True


def test_version_supported_accepts_exact_line():
    assert lab.version_supported("0.38.26\n") is True


def test_version_supported_rejects_two_versions():
    assert lab.version_supported("0.38.25 0.38.26") is False


def test_allocate_ports_does_not_retry_forever(monkeypatch):
    created = []

    def fake_socket(*args, **kwargs):
        assert len(created) < 40, "allocate_ports retried infinitely on OSError"
        sock = FakeSock(fail_bind=True)
        created.append(sock)
        return sock

    monkeypatch.setattr(socket, "socket", fake_socket, raising=True)
    with pytest.raises((lab.LabError, OSError)):
        lab.allocate_ports(4)
    assert created, "allocate_ports never opened a socket"
    assert all(sock.closed for sock in created), "opened sockets left unclosed"


def test_allocate_ports_closes_partial_successes(monkeypatch):
    created = []
    state = {"n": 0}

    def fake_socket(*args, **kwargs):
        assert len(created) < 40, "unbounded socket creation"
        state["n"] += 1
        sock = FakeSock(fail_bind=state["n"] > 2)
        created.append(sock)
        return sock

    monkeypatch.setattr(socket, "socket", fake_socket, raising=True)
    with pytest.raises((lab.LabError, OSError)):
        lab.allocate_ports(5)
    assert len(created) <= 40, "unbounded socket creation"
    assert all(sock.closed for sock in created), "partial successes not closed"


def test_poll_until_deadline_checked_after_attempt():
    clock = FakeClock()
    hooks = make_hooks(clock)

    def attempt():
        clock.now = 2.0
        return True

    deadline = lab.Deadline(1.0, clock=clock)
    with pytest.raises(lab.LabTimeout):
        lab.poll_until(hooks, deadline, 1.0, "stage", attempt)


def test_poll_until_stage_deadline_checked_after_attempt():
    clock = FakeClock()
    hooks = make_hooks(clock)

    def attempt():
        clock.now = 2.0
        return True

    deadline = lab.Deadline(10.0, clock=clock)
    with pytest.raises(lab.LabTimeout):
        lab.poll_until(hooks, deadline, 1.0, "stage", attempt)


def test_poll_until_permanent_rpc_error_propagates():
    clock = FakeClock()
    hooks = make_hooks(clock)
    calls = {"n": 0}

    def attempt():
        calls["n"] += 1
        raise lab.RpcError("Invalid params", -32602)

    deadline = lab.Deadline(10.0, clock=clock)
    with pytest.raises(lab.RpcError) as excinfo:
        lab.poll_until(hooks, deadline, 1.0, "stage", attempt)
    assert excinfo.value.code == -32602
    assert calls["n"] <= 3


def _valid_report():
    phases = [
        {"name": name, "status": "passed", "detail": "ok", "duration_s": 0}
        for name in lab.PHASE_SEQUENCE
    ]
    return {
        "schema": lab.SCHEMA,
        "label": lab.LABEL,
        "engine_only": True,
        "not_proved": list(lab.NOT_PROVED),
        "status": "PASS",
        "failures": [],
        "cleanup": {"errors": [], "confirmed_stopped": True},
        "phases": phases,
    }


def test_validate_report_accepts_valid_report():
    assert lab.validate_report(_valid_report()) is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda report: report.__setitem__("phases", []),
        lambda report: report["phases"].pop(),
        lambda report: report["phases"].append(dict(report["phases"][0])),
        lambda report: report.__setitem__("phases", list(reversed(report["phases"]))),
        lambda report: report["phases"].__setitem__(0, "version"),
    ],
)
def test_validate_report_rejects_malformed_phases(mutate):
    report = _valid_report()
    mutate(report)
    with pytest.raises(lab.ReportError):
        lab.validate_report(report)


def test_validate_report_cleanup_errors_block_pass():
    report = _valid_report()
    report["cleanup"]["errors"] = ["child leaked"]
    with pytest.raises(lab.ReportError):
        lab.validate_report(report)


class FakeProc:
    def __init__(self, fail_terminate=False, unkillable=False):
        self.fail_terminate = fail_terminate
        self.unkillable = unkillable
        self.calls = []
        self._dead = False
        self._returncode = None

    def poll(self):
        return self._returncode if self._dead else None

    def terminate(self):
        self.calls.append("terminate")
        if self.unkillable:
            raise OSError("cannot terminate")
        if self.fail_terminate:
            self.fail_terminate = False
            raise OSError("terminate failed")
        self._dead = True
        self._returncode = -15

    def kill(self):
        self.calls.append("kill")
        if self.unkillable:
            raise OSError("cannot kill")
        self._dead = True
        self._returncode = -9

    def wait(self, timeout=None):
        self.calls.append("wait")
        if self.unkillable:
            raise OSError("cannot wait")
        if not self._dead:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
        return self._returncode


class FakeStream(io.BytesIO):
    def __init__(self):
        super().__init__()
        self.closed_flag = False

    def close(self):
        self.closed_flag = True
        super().close()


def register_node(runner, index, proc):
    node = lab.NodeProcess(
        spec=spec(index), proc=proc, handle=FakeStream(), log_name=f"n{index}.log"
    )
    runner.nodes.append(node.spec)
    runner.processes[index] = node
    return node


def test_keyboard_interrupt_in_phase_stops_child_and_reports_fail(tmp_path, monkeypatch):
    proc = FakeProc()
    node_holder = {}

    for name in lab.PHASE_SEQUENCE:
        monkeypatch.setattr(lab.LabRunner, f"phase_{name}", lambda self: None)

    def phase_start4(self):
        node_holder["node"] = register_node(self, 0, proc)
        raise KeyboardInterrupt()

    monkeypatch.setattr(lab.LabRunner, "phase_start4", phase_start4)
    monkeypatch.setattr(lab.LabRunner, "phase_tx4", lambda self: None)

    report = lab.run_lab(make_inputs(tmp_path), make_hooks())
    assert report["status"] == "FAIL"
    statuses = {phase["name"]: phase["status"] for phase in report["phases"]}
    assert statuses.get("tx4") == "skipped"
    written = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert written["status"] == "FAIL"
    assert node_holder["node"].handle.closed_flag is True
    assert "terminate" in proc.calls and proc.poll() is not None


def test_stop_node_escalates_to_kill_and_closes_log():
    proc = FakeProc(fail_terminate=True)
    runner = lab.LabRunner(make_inputs(Path("/fake")), make_hooks())
    node = register_node(runner, 0, proc)
    lab.stop_node(node)
    assert "kill" in proc.calls
    assert node.handle.closed_flag is True
    assert node.running() is False


def test_stop_all_survives_unkillable_child():
    stuck = FakeProc(unkillable=True)
    healthy = FakeProc()
    runner = lab.LabRunner(make_inputs(Path("/fake")), make_hooks())
    stuck_node = register_node(runner, 0, stuck)
    healthy_node = register_node(runner, 1, healthy)
    cleanup = lab.stop_all(list(runner.processes.values()))
    assert cleanup["errors"], "stuck child should be reported as a cleanup error"
    assert stuck_node.handle.closed_flag is True
    assert healthy_node.handle.closed_flag is True
    assert healthy_node.running() is False


def test_run_lab_fails_when_child_stuck(tmp_path, monkeypatch):
    stuck = FakeProc(unkillable=True)

    for name in lab.PHASE_SEQUENCE:
        monkeypatch.setattr(lab.LabRunner, f"phase_{name}", lambda self: None)

    def phase_start4(self):
        register_node(self, 0, stuck)

    monkeypatch.setattr(lab.LabRunner, "phase_start4", phase_start4)

    report = lab.run_lab(make_inputs(tmp_path), make_hooks())
    assert report["status"] == "FAIL"
    assert report["cleanup"]["errors"]
    assert report["cleanup"]["confirmed_stopped"] is False


class FakeResponse:
    def __init__(self, body, code=200):
        self._body = body
        self.code = code
        self.status = code

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def rpc_env(monkeypatch):
    calls = []

    def make_opener(body, code=200):
        def fake_urlopen(request, *args, **kwargs):
            calls.append(request.full_url if hasattr(request, "full_url") else request)
            return FakeResponse(body, code)

        monkeypatch.setattr(lab.urllib.request, "urlopen", fake_urlopen, raising=True)

    return FakeClock(), lab.Deadline(30.0, clock=FakeClock()), calls, make_opener


def _rpc(deadline, base_url="http://127.0.0.1:30400"):
    return lab.RpcClient(base_url, deadline)


def test_rpc_call_tx_error_json_not_found(rpc_env):
    _clock, deadline, _calls, make_opener = rpc_env
    body = json.dumps(
        {
            "error": {
                "code": -32603,
                "message": "Internal error",
                "data": "tx (" + GOOD_HASH + ") not found",
            }
        }
    ).encode("utf-8")
    make_opener(body)
    rpc = _rpc(deadline)
    with pytest.raises(lab.RpcError) as excinfo:
        rpc.tx(GOOD_HASH)
    assert excinfo.value.code == -32603
    assert excinfo.value.not_found is True


def test_rpc_call_tx_prefixed_hash_rejected(monkeypatch):
    rpc = _rpc(lab.Deadline(30.0, clock=FakeClock()))
    monkeypatch.setattr(
        rpc,
        "result",
        lambda *a, **k: pytest.fail("prefixed hash must not reach the node"),
        raising=True,
    )
    with pytest.raises((lab.LabError, ValueError)):
        rpc.tx(CANON_PAYLOAD)


def test_rpc_call_tx_invalid_params_is_not_not_found(rpc_env):
    _clock, deadline, _calls, make_opener = rpc_env
    body = json.dumps({"error": {"code": -32602, "message": "Invalid params"}}).encode()
    make_opener(body)
    rpc = _rpc(deadline)
    with pytest.raises(lab.RpcError) as excinfo:
        rpc.tx(GOOD_HASH)
    assert excinfo.value.not_found is False


def test_rpc_call_tx_method_not_found_is_not_not_found(rpc_env):
    _clock, deadline, _calls, make_opener = rpc_env
    body = json.dumps({"error": {"code": -32601, "message": "Method not found"}}).encode()
    make_opener(body)
    rpc = _rpc(deadline)
    with pytest.raises(lab.RpcError) as excinfo:
        rpc.tx(GOOD_HASH)
    assert excinfo.value.not_found is False


def test_rpc_call_tx_sends_canonical_prefixed_params(monkeypatch):
    rpc = _rpc(lab.Deadline(30.0, clock=FakeClock()))
    seen = {}

    def fake_result(path, params=None):
        seen["path"] = path
        seen["params"] = params
        return {"ok": True}

    monkeypatch.setattr(rpc, "result", fake_result, raising=True)
    rpc.tx(GOOD_HASH)
    assert seen["path"] == "/tx"
    params = seen["params"] or {}
    sent = params.get("hash") if isinstance(params, dict) else params[0]
    assert sent == CANON_PAYLOAD


def test_rpc_call_tx_malformed_hash_rejected(monkeypatch):
    rpc = _rpc(lab.Deadline(30.0, clock=FakeClock()))
    monkeypatch.setattr(
        rpc,
        "result",
        lambda *a, **k: pytest.fail("malformed hash must not reach the node"),
        raising=True,
    )
    with pytest.raises((lab.LabError, ValueError)):
        rpc.tx("AB")


def test_phase_restore_requires_fresh_committed_tx(monkeypatch):
    runner = lab.LabRunner(make_inputs(Path("/fake")), make_hooks(FakeClock()))
    runner.nodes = [spec(index) for index in range(4)]

    old_heights = {"node0": 5, "node1": 5, "node2": 4, "node3": 4}
    runner.heights = dict(old_heights)
    runner.quorum_loss = {"observed_heights": {"node0": 5, "node1": 5}}
    runner.tx3_height = 3
    runner.app_hashes = ["old-app-hash" for _ in range(4)]
    runner.txs = {0: {"tx_hash": "deadbeef", "height": 2}}
    runner.late_commit_seen = lambda: False

    class FakeRpc:
        def __init__(self, *args, **kwargs):
            self.calls = []

        def result(self, path, params=None):
            self.calls.append((path, params))
            return {"height": 5, "app_hash": "old-app-hash"}

        def call(self, path, params=None):
            self.calls.append((path, params))
            return {"result": {"height": 5}}

        def app_hash(self, height):
            return "old-app-hash"

        def query_value(self, key):
            return base64.b64encode(b"old").decode()

        def tx(self, tx_hash):
            raise lab.LabTimeout("fresh tx never committed")

        def broadcast(self, payload=None):
            return {"code": 0, "hash": GOOD_HASH}

    attempted = {"n": 0}

    def fake_find_committed_height(*args, **kwargs):
        attempted["n"] += 1
        raise lab.LabTimeout("fresh tx never committed")

    monkeypatch.setattr(lab, "start_node", lambda *a, **k: None, raising=True)
    monkeypatch.setattr(lab, "probe_tx_hash", lambda *a, **k: (GOOD_HASH, 0), raising=True)
    monkeypatch.setattr(lab, "find_committed_height", fake_find_committed_height, raising=True)
    monkeypatch.setattr(runner, "rpc", lambda index=0: FakeRpc(), raising=True)
    monkeypatch.setattr(runner, "wait_heights", lambda *a, **k: dict(old_heights), raising=True)

    with pytest.raises(lab.LabError):
        runner.phase_restore()
    assert attempted["n"] >= 1, "phase_restore must look for a fresh committed tx"
