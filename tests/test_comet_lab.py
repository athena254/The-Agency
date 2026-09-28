"""Tests for the engine-only CometBFT lab harness.

These tests never launch ``cometbft``: they cover argument validation, output-dir
refusal, TOML rewriting, deadline/cleanup behaviour and report validation only.
"""

from __future__ import annotations

import io
import json
import time
import tomllib
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

import pytest

from agency.lab import comet_smoke as lab

CONFIG_TEMPLATE = """# This is a TOML config file.
# For more information, see https://github.com/cometbft/cometbft

proxy_app = "tcp://127.0.0.1:26658"

[rpc]
# TCP or UNIX socket address for the RPC API to listen on.
laddr = "tcp://127.0.0.1:26657"

unsafe = false

pprof_laddr = "localhost:6060"

[p2p]
# Address to listen for incoming connections
laddr = "tcp://0.0.0.0:26656"

# Comma separated list of seed nodes to connect to
seeds = ""

# Comma separated list of nodes to keep persistent connections to
persistent_peers = ""

addr_book_strict = false

pex = true

allow_duplicate_ip = false

[instrumentation]
# When true, Prometheus metrics are served under /metrics on
prometheus = true
"""


def _fake_binary(tmp_path: Path) -> Path:
    binary = tmp_path / "cometbft.exe"
    binary.write_text("placeholder\n", encoding="utf-8")
    return binary


def _write_config(path: Path, *, duplicate_pex: bool = False) -> None:
    text = CONFIG_TEMPLATE
    if duplicate_pex:
        text = text.replace("pex = true", "pex = true\npex = true")
    path.write_text(text, encoding="utf-8")


def _ids() -> list[str]:
    return [str(index) * 4 + "ab" * 18 for index in range(4)]


def _node_args() -> tuple[list[str], list[int], list[int]]:
    ids = _ids()
    return ids, [26001, 26002, 26003, 26004], [27001, 27002, 27003, 27004]


class FakeProc:
    """Minimal ``subprocess.Popen`` stand-in for cleanup tests."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.waits = 0

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.waits += 1
        return self.returncode if self.returncode is not None else 0


def _fake_popen_factory(started: list[FakeProc]) -> Any:
    def factory(argv: Sequence[str], handle: IO[bytes]) -> FakeProc:
        assert handle is not None and not handle.closed
        proc = FakeProc()
        started.append(proc)
        return proc

    return factory


def test_validate_binary_arg_rejects_relative_and_missing(tmp_path: Path) -> None:
    with pytest.raises(lab.UsageError):
        lab.validate_binary_arg("tools/cometbft.exe")
    with pytest.raises(lab.UsageError):
        lab.validate_binary_arg(str(tmp_path / "absent.exe"))


def test_validate_binary_arg_rejects_directory_and_accepts_file(tmp_path: Path) -> None:
    with pytest.raises(lab.UsageError):
        lab.validate_binary_arg(str(tmp_path))
    binary = _fake_binary(tmp_path)
    assert lab.validate_binary_arg(str(binary)) == binary


def test_prepare_output_dir_refuses_nonempty_and_preserves(tmp_path: Path) -> None:
    out = tmp_path / "lab"
    out.mkdir()
    sentinel = out / "report.json"
    sentinel.write_text("{}", encoding="utf-8")
    with pytest.raises(lab.UsageError):
        lab.prepare_output_dir(str(out))
    assert sentinel.read_text(encoding="utf-8") == "{}"


def test_prepare_output_dir_creates_missing_parents_and_reuses_empty(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "lab"
    assert lab.prepare_output_dir(str(out)) == out
    assert out.is_dir() and not any(out.iterdir())
    assert lab.prepare_output_dir(str(out)) == out


def test_validate_inputs_rejects_non_positive_timeout(tmp_path: Path) -> None:
    binary = _fake_binary(tmp_path)
    out = tmp_path / "lab"
    for bad in (0.0, -1.0):
        with pytest.raises(lab.UsageError):
            lab.validate_inputs(str(binary), str(out), bad)


def test_validate_inputs_accepts_defaults(tmp_path: Path) -> None:
    binary = _fake_binary(tmp_path)
    inputs = lab.validate_inputs(str(binary), str(tmp_path / "lab"), lab.DEFAULT_WHOLE_TIMEOUT_S)
    assert inputs.binary == binary
    assert inputs.whole_timeout == lab.DEFAULT_WHOLE_TIMEOUT_S


def test_parse_args_requires_binary_and_output() -> None:
    with pytest.raises(SystemExit) as excinfo:
        lab.parse_args([])
    assert excinfo.value.code == 2


def test_parse_args_defaults_whole_timeout(tmp_path: Path) -> None:
    binary = _fake_binary(tmp_path)
    args = lab.parse_args(["--binary", str(binary), "--output", str(tmp_path / "lab")])
    assert args.whole_timeout == lab.DEFAULT_WHOLE_TIMEOUT_S


def test_version_gate_accepts_only_the_pinned_version() -> None:
    assert lab.version_supported("0.38.26\n") is True
    assert lab.EXPECTED_VERSION == "0.38.26"
    for bad in ("0.38.25\n", "", "cometbft version output unavailable", "0.38.260\n", "10.38.26\n"):
        assert lab.version_supported(bad) is False, bad


def test_validate_whole_timeout_rejects_non_finite_values() -> None:
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(lab.UsageError):
            lab.validate_whole_timeout(bad)
    assert lab.validate_whole_timeout(420.0) == 420.0


def test_validate_inputs_rejects_existing_nonempty_output(tmp_path: Path) -> None:
    binary = _fake_binary(tmp_path)
    out = tmp_path / "lab"
    out.mkdir()
    (out / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(lab.UsageError):
        lab.validate_inputs(str(binary), str(out), 60.0)
    assert (out / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_edit_toml_targets_one_section_and_leaves_other_lines_identical() -> None:
    text = CONFIG_TEMPLATE.replace(
        "[instrumentation]", "[statesync]\npex = true\n\n[instrumentation]"
    )
    unrelated = [line for line in text.splitlines() if line.strip() != "pex = true"]
    updated = lab.edit_toml_text(text, [lab.TomlEdit("p2p", "pex", False)])
    assert "pex = true\n\n[instrumentation]" in updated  # same key, other section untouched
    assert updated.count("pex = true") == 1
    assert updated.count("pex = false") == 1
    for line in unrelated:
        assert line in updated
    parsed = tomllib.loads(updated)
    assert parsed["p2p"]["pex"] is False
    assert parsed["statesync"]["pex"] is True
    assert len(updated.splitlines()) == len(text.splitlines())


def test_edit_toml_zero_matches_is_fatal() -> None:
    with pytest.raises(lab.LabError):
        lab.edit_toml_text(CONFIG_TEMPLATE, [lab.TomlEdit("p2p", "does_not_exist", False)])


def test_edit_toml_duplicate_matches_is_fatal() -> None:
    text = CONFIG_TEMPLATE.replace("pex = true", "pex = true\npex = true")
    with pytest.raises(lab.LabError):
        lab.edit_toml_text(text, [lab.TomlEdit("p2p", "pex", False)])


def test_edit_toml_ignores_commented_template_lines() -> None:
    text = CONFIG_TEMPLATE.replace("pex = true", "# pex = true")
    with pytest.raises(lab.LabError):
        lab.edit_toml_text(text, [lab.TomlEdit("p2p", "pex", False)])


def test_rewrite_and_verify_config_round_trip(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    _write_config(config)
    edits = lab.config_edits(rpc_port=27001, p2p_port=26001, persistent_peers="ID@127.0.0.1:26002")
    lab.rewrite_config(config, edits)
    lab.verify_config(config, edits)
    parsed = lab.load_toml(config)
    assert parsed["rpc"]["laddr"] == "tcp://127.0.0.1:27001"
    assert parsed["p2p"]["laddr"] == "tcp://127.0.0.1:26001"
    assert parsed["p2p"]["pex"] is False
    assert parsed["p2p"]["seeds"] == ""
    assert parsed["p2p"]["allow_duplicate_ip"] is True
    assert parsed["instrumentation"]["prometheus"] is False
    assert parsed["rpc"]["pprof_laddr"] == ""


def test_verify_config_detects_an_unapplied_edit(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    _write_config(config)
    edits = lab.config_edits(rpc_port=27001, p2p_port=26001, persistent_peers="")
    lab.rewrite_config(config, edits)
    lab.verify_config(config, edits)
    with pytest.raises(lab.LabError):
        lab.verify_config(config, [lab.TomlEdit("p2p", "pex", True)])


def test_persistent_peers_excludes_self_and_uses_own_p2p_ports() -> None:
    ids, p2p_ports, _ = _node_args()
    nodes = [
        lab.NodeSpec(
            index=i,
            node_id=ids[i],
            home=Path(f"/tmp/node{i}"),
            p2p_port=p2p_ports[i],
            rpc_port=27000 + i,
        )
        for i in range(4)
    ]
    for index in range(4):
        peers = lab.build_persistent_peers(nodes, index)
        entries = peers.split(",")
        assert len(entries) == 3
        assert ids[index] not in peers
        for other in range(4):
            if other == index:
                continue
            assert f"{ids[other]}@127.0.0.1:{p2p_ports[other]}" in entries


def test_allocate_ports_returns_eight_distinct_loopback_ports() -> None:
    ports = lab.allocate_ports(8)
    assert len(ports) == 8
    assert len(set(ports)) == 8
    assert all(1024 <= port <= 65535 for port in ports)


def test_node_argv_is_loopback_only_with_kvstore_proxy() -> None:
    ids, p2p_ports, rpc_ports = _node_args()
    node = lab.NodeSpec(
        index=0,
        node_id=ids[0],
        home=Path("/tmp/node0"),
        p2p_port=p2p_ports[0],
        rpc_port=rpc_ports[0],
    )
    argv = lab.node_argv(Path("/opt/cometbft.exe"), node, "PEER")
    assert argv[1] == "start"
    assert argv[0].endswith("cometbft.exe")
    assert argv[argv.index("--proxy_app") + 1] == "persistent_kvstore"
    assert "--p2p.pex=false" in argv and "--rpc.unsafe=false" in argv
    assert argv[argv.index("--rpc.laddr") + 1] == f"tcp://127.0.0.1:{rpc_ports[0]}"
    assert argv[argv.index("--p2p.laddr") + 1] == f"tcp://127.0.0.1:{p2p_ports[0]}"
    assert "--rpc.unsafe=true" not in argv


def test_deadline_remaining_clamps_and_bound_expires() -> None:
    deadline = lab.Deadline(0.2)
    assert 0.0 < deadline.remaining() <= 0.2
    assert 0.0 < deadline.bound(60.0) <= 0.2
    time.sleep(0.25)
    assert deadline.remaining() == 0.0
    assert deadline.expired() is True
    with pytest.raises(lab.LabTimeout):
        deadline.bound(1.0)


def _report(status: str, failures: list[str], phases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": lab.SCHEMA,
        "label": lab.LABEL,
        "not_proved": list(lab.NOT_PROVED),
        "status": status,
        "failures": failures,
        "phases": phases,
        "engine_only": True,
    }


def _passed(name: str) -> dict[str, Any]:
    return {"name": name, "status": "passed", "detail": "ok", "duration_s": 0.1}


def test_report_validation_accepts_clean_pass_and_json_round_trips() -> None:
    report = _report("PASS", [], [_passed(name) for name in lab.PHASE_SEQUENCE])
    report["cleanup"] = {"errors": [], "confirmed_stopped": True}
    lab.validate_report(report)
    lab.assert_sanitized(json.dumps(report))
    assert json.loads(json.dumps(report)) == report
    assert report["label"] == "engine-only"
    assert report["not_proved"] == list(lab.NOT_PROVED)
    assert set(report["not_proved"]) >= {"byzantine-fault-tolerance", "network-partition"}


def test_report_validation_rejects_contradictions() -> None:
    with pytest.raises(lab.ReportError):
        lab.validate_report(_report("PASS", ["tx4: boom"], [_passed("version")]))
    with pytest.raises(lab.ReportError):
        lab.validate_report(
            _report(
                "PASS",
                [],
                [
                    _passed("version"),
                    {"name": "tx4", "status": "failed", "detail": "x", "duration_s": 0.0},
                ],
            )
        )
    with pytest.raises(lab.ReportError):
        lab.validate_report(
            _report(
                "PASS",
                [],
                [
                    _passed("version"),
                    {"name": "tx4", "status": "skipped", "detail": "x", "duration_s": 0.0},
                ],
            )
        )
    with pytest.raises(lab.ReportError):
        lab.validate_report(_report("FAIL", [], [_passed("version")]))
    with pytest.raises(lab.ReportError):
        lab.validate_report(_report("maybe", [], []))
    lab.validate_report(
        _report(
            "FAIL",
            ["tx4: boom"],
            [
                {
                    "name": name,
                    "status": "failed" if name == "tx4" else "skipped",
                    "detail": "boom",
                    "duration_s": 0.0,
                }
                for name in lab.PHASE_SEQUENCE
            ],
        )
    )


def test_assert_sanitized_rejects_key_and_private_key_markers() -> None:
    for marker in ("priv_validator_key.json", "node_key.json", "-----BEGIN PRIVATE KEY-----"):
        with pytest.raises(lab.ReportError):
            lab.assert_sanitized(f"report leaked {marker}")


class StuckProc(FakeProc):
    """Child that ignores the first terminate and needs a kill."""

    def __init__(self) -> None:
        super().__init__()
        self.wait_calls = 0

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        if self.wait_calls == 1:
            raise OSError("still alive")
        return super().wait(timeout)


def test_stop_node_and_stop_all_always_close_log_handles(tmp_path: Path) -> None:
    nodes = [
        lab.NodeSpec(i, f"{i}" * 40 + "ab" * 18, tmp_path, 26000 + i, 27000 + i) for i in range(2)
    ]
    simple = lab.NodeProcess(
        spec=nodes[0], proc=FakeProc(), handle=io.BytesIO(), log_name="node0.log"
    )
    stuck = lab.NodeProcess(
        spec=nodes[1], proc=StuckProc(), handle=io.BytesIO(), log_name="node1.log"
    )
    cleanup = lab.stop_all([simple, stuck])
    assert simple.proc.terminated and simple.handle.closed
    assert stuck.proc.killed and stuck.handle.closed
    assert cleanup == {
        "terminated": ["node0", "node1"],
        "exit_codes": {"node0": 0, "node1": -9},
        "log_files": ["node0.log", "node1.log"],
        "errors": [],
        "confirmed_stopped": True,
    }


class FakeRpc:
    """Canned node RPC: peers are healthy but the transaction never commits."""

    def __init__(self, state: FakeLab) -> None:
        self._state = state

    def latest_height(self) -> int:
        self._state.rpc_calls.append("status")
        return 1

    def broadcast_tx_sync(self, payload: str) -> dict[str, Any]:
        self._state.rpc_calls.append("broadcast_tx_sync")
        return {"code": 0, "hash": "AB" * 32, "log": ""}

    def tx(self, tx_hash: str) -> dict[str, Any]:
        self._state.rpc_calls.append("tx")
        raise lab.RpcError(
            "RPC /tx error -32603: Internal error", -32603, "tx (" + "AB" * 32 + ") not found"
        )

    def query_value(self, key: str) -> str:
        self._state.rpc_calls.append("abci_query")
        return "djQ="

    def app_hash(self, height: int) -> str:
        self._state.rpc_calls.append("commit")
        return "ABCD"


class FakeLab:
    """Subprocess-free hooks that model just enough of the lab for failure tests."""

    def __init__(self, output: Path) -> None:
        self.output = output
        self.now = 0.0
        self.processes: list[FakeProc] = []
        self.rpc_calls: list[str] = []

    def hooks(self) -> lab.LabHooks:
        return lab.LabHooks(
            popen=self._popen,
            run=self._run,
            make_rpc=lambda url, deadline: FakeRpc(self),
            allocate_ports=lambda count: [
                26001,
                26002,
                26003,
                26004,
                27001,
                27002,
                27003,
                27004,
            ][:count],
            sleep=self._sleep,
            monotonic=lambda: self.now,
            now_utc=lambda: datetime(2026, 9, 28, tzinfo=UTC),
        )

    def _sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.05)

    def _popen(self, argv: Sequence[str], handle: IO[bytes]) -> FakeProc:
        assert not handle.closed
        proc = FakeProc()
        self.processes.append(proc)
        return proc

    def _run(self, argv: Sequence[str], timeout: float) -> lab.CommandResult:
        assert timeout > 0
        if argv[1] == "version":
            return lab.CommandResult(0, "0.38.26\n", "")
        if argv[1] == "testnet":
            for index in range(4):
                config_dir = self.output / f"node{index}" / "config"
                config_dir.mkdir(parents=True, exist_ok=True)
                _write_config(config_dir / "config.toml")
            return lab.CommandResult(0, "", "")
        if argv[1] == "show-node-id":
            name = Path(argv[argv.index("--home") + 1]).name
            return lab.CommandResult(0, name.replace("node", "") * 40 + "\n", "")
        raise AssertionError(f"unexpected command: {argv}")


def test_cleanup_on_injected_failure_terminates_children_and_reports_fail(tmp_path: Path) -> None:
    output = tmp_path / "lab"
    output.mkdir()
    fake = FakeLab(output)
    inputs = lab.Inputs(binary=_fake_binary(tmp_path), output=output, whole_timeout=300.0)

    report = lab.run_lab(inputs, hooks=fake.hooks())

    assert report["status"] == "FAIL"
    assert report["failures"] and report["failures"][0].startswith("tx4:")
    assert report["engine_only"] is True
    assert report["label"] == "engine-only"
    assert report["not_proved"] == list(lab.NOT_PROVED)
    assert report["binary"]["matches"] is True
    assert report["limits"]["whole_timeout_s"] == 300.0
    assert len(report["nodes"]) == 4
    statuses = {phase["name"]: phase["status"] for phase in report["phases"]}
    assert statuses["version"] == "passed"
    assert statuses["testnet"] == "passed"
    assert statuses["start4"] == "passed"
    assert statuses["tx4"] == "failed"
    for name in ("stop1_commit3", "restart_catchup", "quorum_loss", "restore"):
        assert statuses[name] == "skipped", name
    assert len(fake.processes) == 4
    assert all(proc.terminated for proc in fake.processes)
    assert report["cleanup"]["terminated"] == ["node0", "node1", "node2", "node3"]
    assert report["quorum_loss"]["submitted"] is False
    lab.assert_sanitized(json.dumps(report))
    written = json.loads((output / lab.REPORT_NAME).read_text(encoding="utf-8"))
    assert written["status"] == "FAIL"
    lab.validate_report(written)
