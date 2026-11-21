"""Integration: ABCI host + EngineAdapter + PeerLog durable results."""

from __future__ import annotations

import socket

from typer.testing import CliRunner

from agency.cli.main import app
from agency.consensus.abci.host import ABCIHost
from tests.test_consensus_app_state import ballot, proposal
from tests.test_consensus_grants import BFT_TIME_MS, consumers
from tests.test_consensus_grants import setup4 as grants_setup4


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _recv(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _read_varint(sock):
    value = 0
    shift = 0
    while True:
        first = _recv(sock, 1)
        if not first:
            return None
        byte = first[0]
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value
        shift += 7


def _read_frame(sock):
    length = _read_varint(sock)
    if length is None:
        return None
    if length == 0:
        return b""
    return _recv(sock, length)


def test_host_wiring_durable_result(tmp_path):
    ids, membership, ledger = grants_setup4()
    from agency.consensus.app import AgencyApplication

    app = AgencyApplication(membership)
    consumer_ids = list(consumers())
    from agency.peers.log import PeerLog

    log = PeerLog(
        tmp_path / "peer.log",
        peer_id=ids[0].peer_id,
        genesis={
            "epoch": membership.epoch,
            "membership_root": membership.root,
            "policy_version": membership.policy_version,
        },
        expected_genesis_root=membership.root,
        check_same_thread=False,
    )
    log.open()
    from agency.consensus.actions import ActionRegistry, ActionSpec
    from agency.consensus.block_adapter import BlockEngineAdapter

    registry = ActionRegistry()
    adapter = BlockEngineAdapter(
        app=app,
        ledger=ledger,
        issuer=ids[0],
        membership=membership,
        logs={ids[0].peer_id: log},
        registry=registry,
        affected_consumers=consumer_ids,
        bft_time_ms=BFT_TIME_MS,
    )

    host = ABCIHost(app, host="127.0.0.1", port=0)

    errors = []

    def on_block(result, ctx):
        try:
            pid = proposal("a")
            spec = ActionSpec(
                proposal_id=pid,
                task_id="task-1",
                name="calculator",
                args={"op": "add", "a": 2, "b": 3},
                affected_consumers=tuple(consumer_ids),
            )
            inputs = [
                __import__(
                    "agency.consensus.consumer_input", fromlist=["ConsumerInput"]
                ).ConsumerInput(
                    consumer_id=c,
                    proposal_id=pid,
                    attestation_digest="0" * 64,
                    gateway_peer_id=ids[0].peer_id,
                )
                for c in consumer_ids
            ]
            adapter.process_result(result, ctx, {pid: spec}, inputs)
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    host.on_block = on_block
    host.start()
    try:
        pid = proposal("a")
        txs = [ballot(ids[i], membership, pid, "approve", i + 1) for i in range(3)]
        # build finalize request body: field 20 with txs as repeated field 1
        from agency.consensus.abci.wire import encode_message_field

        body = b""
        for t in txs:
            body += encode_message_field(1, t)
        req = encode_message_field(20, body)
        s = socket.create_connection(("127.0.0.1", host.port), timeout=5)
        s.sendall(_varint(len(req)) + req)
        resp = _read_frame(s)
        s.close()
        assert resp is not None
        # commit
        req2 = encode_message_field(11, b"")
        s = socket.create_connection(("127.0.0.1", host.port), timeout=5)
        s.sendall(_varint(len(req2)) + req2)
        _read_frame(s)
        s.close()
    finally:
        host.stop()

    assert errors == [], errors
    kinds = [r.kind for r in log.records()]
    assert "executed_grant" in kinds
    assert log.verify().ok is True
    assert len(adapter.results_for(proposal("a"))) >= 1
    log.close()


def test_cli_abci_host_missing_genesis():
    runner = CliRunner()
    result = runner.invoke(
        app, ["consensus-abci-host", "--genesis", "nope.json", "--peer-id", "peer_x"]
    )
    assert result.exit_code != 0
