"""Durable result chain: fencing-backed execution + consumer-visible results.

Proves the Wave1 block pipeline wires the A4 fencing/grant-store and A5
assignment journal into a result that survives restart and is queryable by
proposal, grant digest and affected consumer.
"""

from __future__ import annotations

import json
from pathlib import Path

from agency.autonomy.assignment_journal import AssignmentJournal
from agency.consensus.actions import ActionRegistry
from agency.consensus.app import AgencyApplication
from agency.consensus.block_adapter import BlockEngineAdapter
from agency.consensus.consumer_input import ConsumerInput
from agency.consensus.fencing import FencingStateStore
from agency.consensus.grant_store import DurableGrantStore
from agency.peers.canonical import digest_hex, digest_json
from agency.peers.log import PeerLog
from tests.test_consensus_app_state import ballot, context, proposal
from tests.test_consensus_grants import BFT_TIME_MS, consumers
from tests.test_consensus_grants import setup4 as grants_setup4


def _open(tmp_path: Path):
    ids, membership, _ = grants_setup4()
    consumer_ids = list(consumers())
    app = AgencyApplication(membership)
    store = DurableGrantStore(membership=membership, db_path=tmp_path / "grants.db")
    fencing = FencingStateStore(db_path=tmp_path / "fencing.db")
    journal = AssignmentJournal(tmp_path / "journal.db")
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
        ledger=store,
        issuer=ids[0],
        membership=membership,
        logs={ids[0].peer_id: log},
        registry=registry,
        affected_consumers=consumer_ids,
        bft_time_ms=BFT_TIME_MS,
        fencing=fencing,
        journal=journal,
    )
    return ids, membership, consumer_ids, app, store, fencing, journal, log, registry, adapter


def _txs(ids, membership, pid, *, seq=0, ballot_seq=1):
    return [
        ballot(ids[i], membership, pid, "approve", i + 1 + seq, ballot_seq=ballot_seq)
        for i in range(3)
    ]


def _inputs(ids, pid, consumer_ids):
    return [
        ConsumerInput(
            consumer_id=c,
            proposal_id=pid,
            attestation_digest="0" * 64,
            gateway_peer_id=ids[0].peer_id,
        )
        for c in consumer_ids
    ]


def _spec(pid, name, task_id, consumer_ids, **args):
    from agency.consensus.actions import ActionSpec

    return ActionSpec(
        proposal_id=pid,
        task_id=task_id,
        name=name,
        args=args,
        affected_consumers=tuple(consumer_ids),
    )


def test_durable_result_is_receipt_backed_and_consumer_visible(tmp_path: Path) -> None:
    ids, membership, consumer_ids, _app, store, fencing, journal, log, _registry, adapter = _open(
        tmp_path
    )
    pid = proposal("a")
    spec = _spec(pid, "calculator", "task-1", consumer_ids, op="add", a=2, b=3)
    inputs = _inputs(ids, pid, consumer_ids)

    reports = adapter.process_block(
        _txs(ids, membership, pid),
        context(membership, height=1, bft_time_ms=BFT_TIME_MS),
        {pid: spec},
        inputs,
    )
    assert len(reports) == 1
    report = reports[0]
    assert report.actuation is not None and report.actuation.ok

    grant_digest = report.grant.digest
    receipt = fencing.receipt(grant_digest)
    assert receipt is not None
    assert receipt.outcome == json.dumps(5.0, sort_keys=True, separators=(",", ":"))
    assert receipt.fencing_token == 1

    assignment_id = "asg_" + digest_hex(pid.encode())[:32]
    record = journal.get(assignment_id)
    assert record.state == "COMPLETED"
    assert record.grant_digest == grant_digest
    assert record.result_digest == digest_json({"value": receipt.outcome})

    kinds = [rec.kind for rec in log.records()]
    assert "result" in kinds
    assert log.verify().ok

    by_proposal = adapter.results_for(pid)
    assert len(by_proposal) == 1
    assert by_proposal[0]["grant_digest"] == grant_digest
    assert adapter.result_for_grant(grant_digest) == receipt
    assert len(adapter.results_for_consumer(consumer_ids[0])) == 1
    assert adapter.results_for_consumer("human_" + "f" * 32) == []

    store.close()
    fencing.close()
    journal.close()
    log.close()


def test_durable_result_survives_reopen_and_rejects_replay(tmp_path: Path) -> None:
    ids, membership, consumer_ids, _app, store, fencing, journal, log, _registry, adapter = _open(
        tmp_path
    )
    calls = {"n": 0}

    def _spy(spec):
        calls["n"] += 1
        return "done"

    _registry.register("spy", _spy)
    pid = proposal("b")
    spec = _spec(pid, "spy", "task-r", consumer_ids)
    inputs = _inputs(ids, pid, consumer_ids)
    adapter.process_block(
        _txs(ids, membership, pid),
        context(membership, height=1, bft_time_ms=BFT_TIME_MS),
        {pid: spec},
        inputs,
    )
    assert calls["n"] == 1
    first_receipt = fencing.receipt(adapter.results_for(pid)[0]["grant_digest"])
    assert first_receipt is not None

    store.close()
    fencing.close()
    journal.close()
    log.close()

    store2 = DurableGrantStore(membership=membership, db_path=tmp_path / "grants.db")
    fencing2 = FencingStateStore(db_path=tmp_path / "fencing.db")
    journal2 = AssignmentJournal(tmp_path / "journal.db")
    log2 = PeerLog(
        tmp_path / "peer.log",
        peer_id=ids[0].peer_id,
        genesis={
            "epoch": membership.epoch,
            "membership_root": membership.root,
            "policy_version": membership.policy_version,
        },
        expected_genesis_root=membership.root,
    )
    log2.open()
    registry2 = ActionRegistry()
    registry2.register("spy", _spy)
    adapter2 = BlockEngineAdapter(
        app=AgencyApplication(membership),
        ledger=store2,
        issuer=ids[0],
        membership=membership,
        logs={ids[0].peer_id: log2},
        registry=registry2,
        affected_consumers=consumer_ids,
        bft_time_ms=BFT_TIME_MS,
        fencing=fencing2,
        journal=journal2,
    )

    assert len(adapter2.results_for(pid)) == 1
    adapter2.process_block(
        _txs(ids, membership, pid),
        context(membership, height=2, bft_time_ms=BFT_TIME_MS),
        {pid: spec},
        inputs,
    )
    assert calls["n"] == 1
    assert len(fencing2.receipts) == 1
    assert fencing2.receipt(first_receipt.grant_digest) == first_receipt
    assert len(adapter2.results_for(pid)) == 1

    store2.close()
    fencing2.close()
    journal2.close()
    log2.close()


def test_fencing_token_advances_for_successive_grants_on_one_resource(tmp_path: Path) -> None:
    ids, membership, consumer_ids, _app, store, fencing, journal, log, _registry, adapter = _open(
        tmp_path
    )
    pids = [proposal("c"), proposal("d")]
    specs = {pid: _spec(pid, "echo", "task-shared", consumer_ids, text=pid) for pid in pids}
    for height, pid in enumerate(pids, start=1):
        adapter.process_block(
            _txs(ids, membership, pid, seq=3 * (height - 1), ballot_seq=height),
            context(membership, height=height, bft_time_ms=BFT_TIME_MS),
            specs,
            _inputs(ids, pid, consumer_ids),
        )

    assert sorted(r.fencing_token for r in fencing.receipts) == [1, 2]
    assert len(adapter.results_for(pids[0])) == 1
    assert len(adapter.results_for(pids[1])) == 1

    store.close()
    fencing.close()
    journal.close()
    log.close()
