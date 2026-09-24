"""Tests for the Lattice governance and reputation modules (Brief 3)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest

from agency.lattice.governance import GovernanceEngine
from agency.lattice.models import (
    ConsensusProposal,
    EdgeType,
    NodeType,
    Reputation,
    utc_now,
)
from agency.lattice.reputation import ReputationEngine


class FakeLatticeBackend:
    """Minimal in-memory LatticeBackend: generic graph ops + event log."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self._counter = 0

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_{self._counter}"

    async def create_node(
        self,
        node_type: NodeType,
        properties: dict[str, Any],
        actor: str = "system",
    ) -> str:
        node_id = properties.get("proposal_id") or self._next_id("node")
        node_id = str(node_id)
        self.nodes[node_id] = {
            "id": node_id,
            "node_type": node_type,
            "properties": dict(properties),
        }
        self.events.append({"event_type": "node_created", "actor": actor, "target_id": node_id})
        return node_id

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        return self.nodes.get(node_id)

    async def update_node(
        self, node_id: str, properties: dict[str, Any], actor: str = "system"
    ) -> bool:
        self.events.append({"event_type": "node_updated", "actor": actor, "target_id": node_id})
        return True

    async def add_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: EdgeType,
        properties: dict[str, Any] | None = None,
        actor: str = "system",
    ) -> str:
        edge_id = self._next_id("edge")
        self.edges[edge_id] = {
            "id": edge_id,
            "source_id": source_id,
            "target_id": target_id,
            "edge_type": edge_type,
            "properties": dict(properties or {}),
        }
        # Event sourcing: every vote creates an event.
        self.events.append({"event_type": "edge_added", "actor": actor, "target_id": edge_id})
        return edge_id


@pytest.fixture
def backend() -> FakeLatticeBackend:
    return FakeLatticeBackend()


@pytest.fixture
def reputation(backend: FakeLatticeBackend) -> ReputationEngine:
    return ReputationEngine(backend)  # type: ignore[arg-type]


@pytest.fixture
def governance(backend: FakeLatticeBackend, reputation: ReputationEngine) -> GovernanceEngine:
    return GovernanceEngine(backend, reputation)  # type: ignore[arg-type]


# -- proposal submission and retrieval ---------------------------------- #


async def test_submit_and_get_proposal(governance: GovernanceEngine) -> None:
    proposal_id = await governance.submit_proposal("butler", "spawn_agent", {"role": "researcher"})
    assert proposal_id.startswith("prop_")
    proposal = await governance.get_proposal_status(proposal_id)
    assert isinstance(proposal, ConsensusProposal)
    assert proposal.proposer_id == "butler"
    assert proposal.proposal_type == "spawn_agent"
    assert proposal.status == "open"
    assert proposal.quorum_required == pytest.approx(0.66)
    assert proposal.votes == []


async def test_submit_rejects_bad_type_and_quorum(governance: GovernanceEngine) -> None:
    with pytest.raises(ValueError):
        await governance.submit_proposal("butler", "bogus_type", {})
    with pytest.raises(ValueError):
        await governance.submit_proposal("butler", "spawn_agent", {}, quorum=0.0)
    with pytest.raises(ValueError):
        await governance.submit_proposal("butler", "spawn_agent", {}, ttl_seconds=0)


async def test_get_unknown_proposal_raises_key_error(governance: GovernanceEngine) -> None:
    with pytest.raises(KeyError):
        await governance.get_proposal_status("prop_missing")


async def test_list_open_proposals(governance: GovernanceEngine) -> None:
    assert await governance.list_open_proposals() == []
    p1 = await governance.submit_proposal("butler", "spawn_agent", {})
    p2 = await governance.submit_proposal("butler", "policy_change", {})
    open_ids = {p.proposal_id for p in await governance.list_open_proposals()}
    assert open_ids == {p1, p2}
    await governance.cast_vote("user", p1, "approve")
    open_ids = {p.proposal_id for p in await governance.list_open_proposals()}
    assert open_ids == {p2}


# -- voting with different weights -------------------------------------- #


async def test_weighted_voting_and_quorum(
    governance: GovernanceEngine, reputation: ReputationEngine
) -> None:
    # High-reputation agent approves, fresh (0.5) agent denies.
    await reputation.record_task_completion("strong", True)
    await reputation.record_task_completion("strong", True)
    strong_weight = await reputation.vote_weight("strong")
    assert strong_weight > 0.5
    proposal_id = await governance.submit_proposal("butler", "spawn_agent", {}, quorum=0.66)
    result = await governance.cast_vote("strong", proposal_id, "approve")
    assert result == {"status": "passed", "quorum_reached": True}
    # A later deny cannot overturn a resolved proposal.
    with pytest.raises(ValueError):
        await governance.cast_vote("fresh", proposal_id, "deny")


async def test_single_approve_meets_ratio_quorum(
    governance: GovernanceEngine,
) -> None:
    proposal_id = await governance.submit_proposal("butler", "replace_agent", {}, quorum=0.9)
    await governance.cast_vote("butler", proposal_id, "approve")
    # Butler alone: 1.0 / (1.0 + 0.0) meets 0.9, so this passes outright.
    proposal = await governance.get_proposal_status(proposal_id)
    assert proposal.status == "passed"


async def test_mixed_votes_below_quorum_stay_open(governance: GovernanceEngine) -> None:
    proposal_id = await governance.submit_proposal("butler", "policy_change", {}, quorum=0.75)
    # Butler (1.0) approves then a 0.5 agent denies via re-vote-free fresh ids.
    await governance.cast_vote("butler", proposal_id, "deny")
    # Single deny vote: approve ratio 0.0 -> still open (no auto-deny on votes).
    proposal = await governance.get_proposal_status(proposal_id)
    assert proposal.status == "open"
    assert await governance.check_quorum(proposal_id) is False


async def test_abstain_votes_excluded_from_quorum(governance: GovernanceEngine) -> None:
    proposal_id = await governance.submit_proposal("butler", "agent_restart", {}, quorum=0.66)
    await governance.cast_vote("butler", proposal_id, "abstain")
    assert await governance.check_quorum(proposal_id) is False
    result = await governance.cast_vote("user", proposal_id, "approve")
    assert result["status"] == "passed"


async def test_cast_vote_rejects_bad_decision(governance: GovernanceEngine) -> None:
    proposal_id = await governance.submit_proposal("butler", "spawn_agent", {})
    with pytest.raises(ValueError):
        await governance.cast_vote("butler", proposal_id, "maybe")


async def test_vote_creates_backend_event(
    governance: GovernanceEngine, backend: FakeLatticeBackend
) -> None:
    proposal_id = await governance.submit_proposal("butler", "spawn_agent", {})
    before = len(backend.events)
    await governance.cast_vote("butler", proposal_id, "approve")
    assert len(backend.events) > before
    vote_edges = [e for e in backend.edges.values() if e["edge_type"] == EdgeType.VOTED_ON]
    assert len(vote_edges) == 1
    assert vote_edges[0]["properties"]["decision"] == "approve"


# -- proposal resolution (pass / deny / expire) -------------------------- #


async def test_resolve_passed(governance: GovernanceEngine) -> None:
    proposal_id = await governance.submit_proposal("butler", "spawn_agent", {}, quorum=0.5)
    await governance.cast_vote("butler", proposal_id, "approve")
    # Already auto-resolved to passed; explicit resolve is idempotent.
    assert await governance.resolve_proposal(proposal_id) == "passed"


async def test_resolve_denied_without_quorum(governance: GovernanceEngine) -> None:
    proposal_id = await governance.submit_proposal("butler", "policy_change", {}, quorum=0.99)
    await governance.cast_vote("butler", proposal_id, "deny")
    assert await governance.resolve_proposal(proposal_id) == "denied"


async def test_expired_proposal_handling(governance: GovernanceEngine) -> None:
    proposal_id = await governance.submit_proposal("butler", "spawn_agent", {}, ttl_seconds=1)
    # Force expiry without sleeping.
    async with governance._lock:  # type: ignore[attr-defined]
        stored = governance._proposals[proposal_id]  # type: ignore[attr-defined]
        stored.expires_at = utc_now() - timedelta(seconds=1)
    assert await governance.resolve_proposal(proposal_id) == "expired"
    assert await governance.check_quorum(proposal_id) is False
    result = await governance.cast_vote("butler", proposal_id, "approve")
    assert result == {"status": "expired", "quorum_reached": False}
    assert await governance.list_open_proposals() == []


# -- vote weights: butler / user / agent --------------------------------- #


async def test_vote_weight_trusted_and_agent(reputation: ReputationEngine) -> None:
    assert await reputation.vote_weight("butler") == 1.0
    assert await reputation.vote_weight("Butler") == 1.0
    assert await reputation.vote_weight("user") == 1.0
    assert await reputation.vote_weight("USER") == 1.0
    assert await reputation.vote_weight("agent-9") == pytest.approx(0.5)
    await reputation.record_task_completion("agent-9", True)
    assert await reputation.vote_weight("agent-9") == pytest.approx(1.0)


async def test_human_override_passes_immediately(governance: GovernanceEngine) -> None:
    proposal_id = await governance.submit_proposal("butler", "replace_agent", {}, quorum=0.99)
    result = await governance.cast_vote("user", proposal_id, "approve")
    assert result == {"status": "passed", "quorum_reached": True}


# -- reputation calculation ---------------------------------------------- #


async def test_reputation_baseline_and_tasks(reputation: ReputationEngine) -> None:
    rep = await reputation.get_reputation("new-agent")
    assert rep.score == pytest.approx(0.5)
    assert rep.tasks_completed == 0
    rep = await reputation.record_task_completion("new-agent", True)
    assert rep.tasks_completed == 1
    assert rep.score == pytest.approx(1.0)
    rep = await reputation.record_task_completion("new-agent", False)
    assert rep.tasks_failed == 1
    assert rep.score == pytest.approx(0.5)


async def test_reputation_sixty_forty_blend(reputation: ReputationEngine) -> None:
    rep = await reputation.record_task_completion("agent-1", True, peer_rating=1.0)
    assert rep.score == pytest.approx(0.6 * 1.0 + 0.4 * 1.0)
    rep = await reputation.record_task_completion("agent-1", False, peer_rating=0.0)
    # success 1/2 = 0.5, peer avg 0.5 -> 0.5
    assert rep.score == pytest.approx(0.5)
    direct = Reputation(agent_id="x", tasks_completed=3, tasks_failed=1, peer_ratings=[1.0, 1.0])
    assert ReputationEngine.compute_score(direct) == pytest.approx(0.6 * 0.75 + 0.4 * 1.0)


async def test_rolling_window_for_peer_ratings(reputation: ReputationEngine) -> None:
    for i in range(25):
        await reputation.submit_peer_rating("rater", "agent-w", 1.0 if i % 2 == 0 else 0.0)
    rep = await reputation.get_reputation("agent-w")
    assert len(rep.peer_ratings) == 20


async def test_peer_rating_validation(reputation: ReputationEngine) -> None:
    assert await reputation.submit_peer_rating("a", "b", 0.8, context="good work") is True
    with pytest.raises(ValueError):
        await reputation.submit_peer_rating("a", "b", 1.5)
    with pytest.raises(ValueError):
        await reputation.record_task_completion("a", True, peer_rating=-0.1)


async def test_leaderboard_ordering(reputation: ReputationEngine) -> None:
    await reputation.record_task_completion("low", False)
    await reputation.record_task_completion("high", True)
    board = await reputation.get_leaderboard(limit=2)
    assert [r.agent_id for r in board] == ["high", "low"]
    assert await reputation.get_leaderboard(limit=1) == board[:1]


# -- concurrency ---------------------------------------------------------- #


async def test_concurrent_vote_casting(governance: GovernanceEngine) -> None:
    # Deny votes never trigger auto-resolve, so all 10 concurrent ballots
    # must land exactly once each — this exercises the atomic vote path.
    proposal_id = await governance.submit_proposal("butler", "spawn_agent", {}, quorum=0.99)
    voters = [f"voter-{i}" for i in range(10)]

    async def _vote(voter: str) -> dict:
        return await governance.cast_vote(voter, proposal_id, "deny")

    results = await asyncio.gather(*(_vote(v) for v in voters))
    assert all(r["status"] == "open" for r in results)
    assert all(r["quorum_reached"] is False for r in results)
    proposal = await governance.get_proposal_status(proposal_id)
    assert len(proposal.votes) == 10
    assert proposal.status == "open"
    assert {v.voter_id for v in proposal.votes} == set(voters)
    assert await governance.resolve_proposal(proposal_id) == "denied"
