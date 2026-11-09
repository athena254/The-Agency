"""Eligible-set quorum, no user override, and hash-chain tests."""

from __future__ import annotations

import pytest

from agency.lattice.audit_chain import (
    GENESIS_HASH,
    chain_event,
    compute_event_hash,
    verify_chain,
)
from agency.lattice.backends.sqlite import SQLiteLattice
from agency.lattice.governance import GovernanceEngine
from agency.lattice.models import ConsensusProposal, LatticeConfig
from agency.lattice.reputation import ReputationEngine
from tests.test_lattice.test_governance import FakeLatticeBackend


@pytest.fixture
def backend() -> FakeLatticeBackend:
    return FakeLatticeBackend()


@pytest.fixture
def governance(backend: FakeLatticeBackend) -> GovernanceEngine:
    return GovernanceEngine(backend, ReputationEngine(backend))  # type: ignore[arg-type]


async def test_eligible_quorum_backward_compat_empty(governance: GovernanceEngine) -> None:
    p = await governance.submit_proposal("butler", "spawn_agent", {}, quorum=0.66)
    prop = await governance.get_proposal_status(p)
    assert prop.eligible_voters == []
    await governance.cast_vote("butler", p, "approve")
    assert (await governance.get_proposal_status(p)).status == "passed"


async def test_eligible_quorum_needs_eligible_fraction(governance: GovernanceEngine) -> None:
    p = await governance.submit_proposal(
        "butler",
        "spawn_agent",
        {},
        quorum=0.5,
        eligible_voters=["butler", "agent-a", "agent-b", "agent-c"],
    )
    await governance.cast_vote("butler", p, "approve")
    # 1.0 / (1.0 + 0.5*3) = 0.4 < 0.5 -> still open
    assert (await governance.get_proposal_status(p)).status == "open"
    await governance.cast_vote("agent-a", p, "approve")
    # 1.5/2.5 = 0.6 >= 0.5 -> passed
    assert (await governance.get_proposal_status(p)).status == "passed"


async def test_abstain_excluded_from_numerator(governance: GovernanceEngine) -> None:
    p = await governance.submit_proposal(
        "butler",
        "policy_change",
        {},
        quorum=0.5,
        eligible_voters=["butler", "agent-a"],
    )
    await governance.cast_vote("butler", p, "abstain")
    await governance.cast_vote("agent-a", p, "approve")
    # approve 0.5 / eligible 1.5 = 0.33 < 0.5; abstain does not help
    assert (await governance.get_proposal_status(p)).status == "open"


def test_model_eligible_voters_roundtrip() -> None:
    prop = ConsensusProposal(
        proposal_id="p1",
        proposer_id="butler",
        proposal_type="spawn_agent",
        eligible_voters=["a", "b"],
    )
    restored = ConsensusProposal.from_dict(prop.to_dict())
    assert restored.eligible_voters == ["a", "b"]
    legacy = ConsensusProposal.from_dict(
        {
            "proposal_id": "p2",
            "proposer_id": "butler",
            "proposal_type": "spawn_agent",
            "expires_at": prop.expires_at.isoformat(),
        }
    )
    assert legacy.eligible_voters == []


def test_audit_chain_verify() -> None:
    events: list[dict] = []
    prev = GENESIS_HASH
    for i in range(3):
        chained = chain_event(
            prev,
            {
                "event_id": f"e{i}",
                "event_type": "vote_cast",
                "actor": "butler",
                "target_id": "p1",
                "payload": {"i": i},
                "prior_state": None,
                "timestamp": f"2026-01-01T00:00:0{i}+00:00",
            },
        )
        events.append(chained)
        prev = chained["event_hash"]
    assert verify_chain(events) is True
    tampered = [dict(e) for e in events]
    tampered[1]["payload"] = {"i": 99}
    assert verify_chain(tampered) is False
    assert compute_event_hash(GENESIS_HASH, events[0]) == events[0]["event_hash"]


async def test_sqlite_eligible_quorum_and_chain(tmp_path) -> None:
    db_path = str(tmp_path / "lattice.db")
    backend = SQLiteLattice(config=LatticeConfig(sqlite_path=db_path))
    await backend.initialize()
    try:
        pid = await backend.submit_proposal(
            "butler", "spawn_agent", {}, quorum=0.66, eligible_voters=["user", "a1", "a2"]
        )
        prop = await backend.get_proposal_status(pid)
        assert prop.eligible_voters == ["user", "a1", "a2"]
        await backend.cast_vote("user", pid, "approve")
        prop = await backend.get_proposal_status(pid)
        # user 1.0 / (1.0+0.5+0.5)=0.5 < 0.66 -> open (no override)
        assert prop.status == "open"
        await backend.cast_vote("a1", pid, "approve")
        assert (await backend.get_proposal_status(pid)).status == "passed"
        events = await backend.get_events(limit=100)
        chained = [e for e in reversed(events) if e.event_hash]
        assert chained, "expected hash-chained events"
        assert verify_chain(chained) is True
    finally:
        await backend.close()
