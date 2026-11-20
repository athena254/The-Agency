"""Hermetic single-host Wave 1 acceptance: decision -> grant -> actuator -> durable result.

No clock, network, or engine. Composes the consensus app, grants, actuator,
consumer-input coverage, and a PeerLog durable store.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from agency.consensus.actions import ActionOutcome, ActionRegistry, ActionSpec
from agency.consensus.app import AgencyApplication
from agency.consensus.block_adapter import BlockEngineAdapter
from agency.consensus.consumer_input import ConsumerInput
from agency.peers.log import PeerLog
from tests.test_consensus_app_state import ballot, context, proposal
from tests.test_consensus_grants import BFT_TIME_MS, consumers
from tests.test_consensus_grants import setup4 as grants_setup4


def _make(tmp_path: Path):
    ids, membership, ledger = grants_setup4()
    app = AgencyApplication(membership)
    consumer_ids = list(consumers())
    log = PeerLog(
        tmp_path / "peer.log",
        peer_id=ids[0].peer_id,
        genesis={
            "epoch": membership.epoch,
            "membership_root": membership.root,
            "policy_version": membership.policy_version,
        },
        expected_genesis_root=membership.root,
    )
    log.open()
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
    return ids, membership, ledger, app, log, adapter, registry, consumer_ids


def _ballots(ids, membership, pid):
    return [ballot(ids[i], membership, pid, "approve", i + 1) for i in range(3)]


def test_wave1_positive_end_to_end(tmp_path: Path) -> None:
    ids, membership, _ledger, _app, log, adapter, _registry, consumer_ids = _make(tmp_path)
    pid = proposal("a")
    txs = _ballots(ids, membership, pid)
    ctx = context(membership, height=1, bft_time_ms=BFT_TIME_MS)

    spec = ActionSpec(
        proposal_id=pid,
        task_id="task-1",
        name="calculator",
        args={"op": "add", "a": 2, "b": 3},
        affected_consumers=tuple(consumer_ids),
    )
    inputs = [
        ConsumerInput(
            consumer_id=c,
            proposal_id=pid,
            attestation_digest="0" * 64,
            gateway_peer_id=ids[0].peer_id,
        )
        for c in consumer_ids
    ]

    reports = adapter.process_block(txs, ctx, {pid: spec}, inputs)
    assert len(reports) == 1
    r = reports[0]
    assert r.decision_outcome == "passed"
    assert r.grant is not None
    assert r.actuation is not None and r.actuation.ok is True
    assert r.actuation.value == 5
    assert r.consumer_inputs_missing == set()
    assert r.action_outcome == ActionOutcome(True, 5)

    kinds = [rec.kind for rec in log.records()]
    assert "intent" in kinds
    assert "outcome" in kinds
    assert "executed_grant" in kinds
    assert kinds.index("intent") < kinds.index("outcome")
    assert log.verify().ok is True

    res = adapter.results_for(pid)
    assert len(res) >= 1


def test_wave1_echo_action(tmp_path: Path) -> None:
    ids, membership, _ledger, _app, _log, adapter, _registry, consumer_ids = _make(tmp_path)
    pid = proposal("e")
    txs = _ballots(ids, membership, pid)
    ctx = context(membership, height=1, bft_time_ms=BFT_TIME_MS)

    spec = ActionSpec(
        proposal_id=pid,
        task_id="task-e",
        name="echo",
        args={"text": "hi"},
        affected_consumers=tuple(consumer_ids),
    )
    inputs = [
        ConsumerInput(
            consumer_id=c,
            proposal_id=pid,
            attestation_digest="0" * 64,
            gateway_peer_id=ids[0].peer_id,
        )
        for c in consumer_ids
    ]

    reports = adapter.process_block(txs, ctx, {pid: spec}, inputs)
    r = reports[0]
    assert r.actuation is not None and r.actuation.ok is True
    assert r.actuation.value == "hi"


def test_wave1_negative_missing_consumer_input(tmp_path: Path) -> None:
    ids, membership, _ledger, _app, _log, adapter, _registry, consumer_ids = _make(tmp_path)
    pid = proposal("b")
    txs = _ballots(ids, membership, pid)
    ctx = context(membership, height=1, bft_time_ms=BFT_TIME_MS)

    spec = ActionSpec(
        proposal_id=pid,
        task_id="task-2",
        name="echo",
        args={"text": "hi"},
        affected_consumers=tuple(consumer_ids),
    )
    reports = adapter.process_block(txs, ctx, {pid: spec}, [])
    r = reports[0]
    assert r.decision_outcome == "passed"
    assert r.actuation is None
    assert r.consumer_inputs_missing == set(consumer_ids)
    assert "executed_grant" not in [rec.kind for rec in _log.records()]


def test_wave1_negative_tampered_grant(tmp_path: Path) -> None:
    ids, membership, ledger, _app, log, _adapter, _registry, consumer_ids = _make(tmp_path)
    from agency.consensus.actuator import Actuator
    from agency.consensus.grants import issue_grant

    grant = issue_grant(
        ids[0],
        membership=membership,
        proposal_id=proposal("f"),
        task_id="task-z",
        issued_at_ms=1000,
        expires_at_ms=BFT_TIME_MS + 1000,
        affected_consumers=consumer_ids,
    )
    tampered = replace(grant, signature=b"\x00" * 64)
    actuator = Actuator(
        ledger=ledger,
        bft_time_ms=BFT_TIME_MS,
        log=log,
        membership_epoch=membership.epoch,
        membership_root=membership.root,
    )
    called = {"n": 0}

    def exec_fn():
        called["n"] += 1
        return "done"

    result = actuator.run(tampered, exec_fn)
    assert result.ok is False
    assert called["n"] == 0


def test_wave1_negative_stale_grant(tmp_path: Path) -> None:
    ids, membership, ledger, _app, log, _adapter, _registry, consumer_ids = _make(tmp_path)
    from agency.consensus.actuator import Actuator

    grant = ledger.issue(
        ids[0],
        proposal_id=proposal("e"),
        task_id="task-s",
        issued_at_ms=1000,
        expires_at_ms=BFT_TIME_MS + 1000,
        affected_consumers=consumer_ids,
    )
    actuator = Actuator(
        ledger=ledger,
        bft_time_ms=BFT_TIME_MS + 100000,
        log=log,
        membership_epoch=membership.epoch,
        membership_root=membership.root,
    )
    called = {"n": 0}

    def exec_fn():
        called["n"] += 1
        return "done"

    result = actuator.run(grant, exec_fn)
    assert result.ok is False
    assert called["n"] == 0


def test_wave1_restart_idempotency(tmp_path: Path) -> None:
    ids, membership, _ledger, _app, log, adapter, _registry, consumer_ids = _make(tmp_path)
    pid = proposal("d")
    txs = _ballots(ids, membership, pid)
    ctx = context(membership, height=1, bft_time_ms=BFT_TIME_MS)
    spec = ActionSpec(
        proposal_id=pid,
        task_id="task-4",
        name="echo",
        args={"text": "hi"},
        affected_consumers=tuple(consumer_ids),
    )
    inputs = [
        ConsumerInput(
            consumer_id=c,
            proposal_id=pid,
            attestation_digest="0" * 64,
            gateway_peer_id=ids[0].peer_id,
        )
        for c in consumer_ids
    ]
    adapter.process_block(txs, ctx, {pid: spec}, inputs)
    adapter.process_block(
        txs, context(membership, height=2, bft_time_ms=BFT_TIME_MS), {pid: spec}, inputs
    )
    after_executed = [r.kind for r in log.records()].count("executed_grant")
    assert after_executed == 1
