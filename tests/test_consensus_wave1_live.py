"""Gated live Wave 1 acceptance: AgentExecutor through the consensus actuator.

Skipped unless AGENCY_LIVE_WAVE1=1. Requires a configured provider; uses the
default LLMAdapter (real call when keys present, echo fallback otherwise).
Records a real outcome digest in a PeerLog. Does not claim production proof.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENCY_LIVE_WAVE1") != "1",
    reason="set AGENCY_LIVE_WAVE1=1 to run live acceptance",
)

from agency.agents.executor import AgentExecutor
from agency.consensus.actions import ActionRegistry, ActionSpec, make_agent_executor_adapter
from agency.consensus.app import AgencyApplication
from agency.consensus.block_adapter import BlockEngineAdapter
from agency.consensus.consumer_input import ConsumerInput
from agency.peers.log import PeerLog
from tests.test_consensus_app_state import ballot, context, proposal
from tests.test_consensus_grants import BFT_TIME_MS, consumers
from tests.test_consensus_grants import setup4 as grants_setup4


def test_wave1_live_agent_executor(tmp_path: Path) -> None:
    ids, membership, ledger = grants_setup4()
    app = AgencyApplication(membership)
    consumer_ids = list(consumers())
    log = PeerLog(
        tmp_path / "live.log",
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
    executor = AgentExecutor()
    registry.register("agent_task", make_agent_executor_adapter(executor))
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
    pid = proposal("a")
    txs = [ballot(ids[i], membership, pid, "approve", i + 1) for i in range(3)]
    ctx = context(membership, height=1, bft_time_ms=BFT_TIME_MS)
    spec = ActionSpec(
        proposal_id=pid,
        task_id="live-task-1",
        name="agent_task",
        args={"task": "Reply with the single word OK."},
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
    assert r.actuation is not None
    assert r.actuation.ok is True
    assert log.verify().ok is True
    assert len(log.records()) >= 3
