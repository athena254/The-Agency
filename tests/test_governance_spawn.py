"""Tests for governance-driven agent spawning."""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from unittest.mock import MagicMock, AsyncMock

from agency.orchestrator import AgencyOrchestrator
from agency.lattice.api import Lattice
from agency.lattice.backends.base import LatticeBackend
from agency.lattice.models import (
    ConsensusProposal,
    NodeType,
    LatticeEvent,
    Vote,
    utc_now,
)
from agency.kernel.identity import TrustLevel


class FakeRegistry:
    """In-memory fake for both identity and runtime registries."""

    def __init__(self):
        self._agents = []

    def register(self, agent):
        self._agents.append(agent)

    def list_agents(self):
        return list(self._agents)


class FakeLatticeBackend(LatticeBackend):
    """Minimal in-memory backend that records everything."""

    def __init__(self):
        self.nodes = {}
        self.edges = {}
        self.events = []
        self.proposals = {}
        self.reputations = {}
        self.vectors = {}

    async def initialize(self):
        pass

    async def close(self):
        pass

    async def create_node(self, node_type, properties, actor="system"):
        nid = f"n-{len(self.nodes)}"
        self.nodes[nid] = {"id": nid, "type": node_type.value, "properties": dict(properties), "created_at": utc_now().isoformat(), "updated_at": utc_now().isoformat()}
        self._record("node_created", actor, nid, properties)
        return nid

    async def get_node(self, node_id):
        return self.nodes.get(node_id)

    async def update_node(self, node_id, properties, actor="system"):
        if node_id not in self.nodes:
            return False
        self.nodes[node_id]["properties"].update(properties)
        self.nodes[node_id]["updated_at"] = utc_now().isoformat()
        return True

    async def delete_node(self, node_id, actor="system"):
        return self.nodes.pop(node_id, None) is not None

    async def find_nodes(self, node_type=None, filters=None, limit=100, offset=0):
        results = []
        for n in self.nodes.values():
            if node_type and n["type"] != node_type.value:
                continue
            if filters:
                match = all(n["properties"].get(k) == v for k, v in filters.items())
                if not match:
                    continue
            results.append(dict(n))
        return results[offset:offset + limit]

    async def add_edge(self, source_id, target_id, edge_type, properties=None, actor="system"):
        eid = f"e-{len(self.edges)}"
        self.edges[eid] = {"id": eid, "source_id": source_id, "target_id": target_id, "type": edge_type.value, "properties": dict(properties or {}), "created_at": utc_now().isoformat()}
        return eid

    async def get_edges(self, node_id, direction="both", edge_type=None):
        results = []
        for e in self.edges.values():
            is_source = e["source_id"] == node_id
            is_target = e["target_id"] == node_id
            if edge_type and e["type"] != edge_type.value:
                continue
            if direction == "out" and not is_source:
                continue
            if direction == "in" and not is_target:
                continue
            results.append(dict(e))
        return results

    async def remove_edge(self, edge_id, actor="system"):
        return self.edges.pop(edge_id, None) is not None

    async def traverse(self, start_node, edge_type=None, max_depth=3, direction="out"):
        return []

    async def find_path(self, source, target, max_depth=5):
        return None

    async def get_dependencies(self, task_id):
        return []

    async def get_dependents(self, task_id):
        return []

    async def upsert_vector(self, collection, vectors):
        store = self.vectors.setdefault(collection, {})
        for vid, vec, payload in vectors:
            store[vid] = (list(vec), dict(payload))
        return True

    async def search_vectors(self, collection, query_vector, limit=10, filters=None):
        return []

    async def search_by_text(self, collection, text, embedder, limit=10):
        return []

    async def delete_vectors(self, collection, ids):
        store = self.vectors.get(collection, {})
        removed = any(store.pop(i, None) is not None for i in ids)
        return removed

    async def submit_proposal(self, proposer_id, proposal_type, payload, quorum=0.5, ttl_seconds=3600):
        pid = f"prop-{len(self.proposals)}"
        self.proposals[pid] = ConsensusProposal(
            proposal_id=pid,
            proposer_id=proposer_id,
            proposal_type=proposal_type,
            quorum_required=quorum,
            votes=[],
            status="open",
            expires_at=utc_now() + __import__("datetime").timedelta(seconds=ttl_seconds),
        )
        self._record("proposal_submitted", proposer_id, pid, {"proposal_type": proposal_type, **dict(payload)})
        return pid

    async def cast_vote(self, voter_id, proposal_id, decision, evidence=None):
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            raise KeyError(f"Unknown proposal: {proposal_id}")
        proposal.votes.append(Vote(voter_id=voter_id, proposal_id=proposal_id, decision=decision, evidence=list(evidence or [])))
        self._record("vote_cast", voter_id, proposal_id, {"decision": decision})
        await self.check_quorum(proposal_id)
        return True

    async def get_proposal_status(self, proposal_id):
        p = self.proposals.get(proposal_id)
        if not p:
            raise KeyError(f"Unknown proposal: {proposal_id}")
        return p

    async def list_open_proposals(self):
        return [p for p in self.proposals.values() if p.status == "open"]

    async def update_reputation(self, agent_id, success, peer_rating=None):
        return __import__("agency.lattice.models", fromlist=["Reputation"]).Reputation(agent_id=agent_id)

    async def get_reputation(self, agent_id):
        return __import__("agency.lattice.models", fromlist=["Reputation"]).Reputation(agent_id=agent_id)

    async def check_quorum(self, proposal_id):
        proposal = self.proposals.get(proposal_id)
        if not proposal or proposal.status != "open":
            return proposal.status == "passed" if proposal else False
        counted = [v for v in proposal.votes if v.decision in ("approve", "deny")]
        total = sum(v.weight for v in counted)
        if total <= 0:
            return False
        approve = sum(v.weight for v in counted if v.decision == "approve")
        deny = total - approve
        if approve / total >= proposal.quorum_required:
            proposal.status = "passed"
            return True
        if deny / total >= proposal.quorum_required:
            proposal.status = "denied"
        return False

    async def get_events(self, target_id=None, event_type=None, actor=None, limit=100):
        results = []
        for e in reversed(self.events):
            if target_id and e.target_id != target_id:
                continue
            if event_type and e.event_type != event_type:
                continue
            if actor and e.actor != actor:
                continue
            results.append(e)
            if len(results) >= limit:
                break
        return results

    async def replay_events(self, from_timestamp, to_timestamp=None):
        return [e for e in self.events if e.timestamp >= from_timestamp and (to_timestamp is None or e.timestamp <= to_timestamp)]

    async def get_audit_trail(self, node_id):
        return [e for e in self.events if e.target_id == node_id]

    async def get_status(self):
        return {"backend": "fake", "node_count": len(self.nodes), "edge_count": len(self.edges), "event_count": len(self.events)}

    async def get_agent_count(self):
        return sum(1 for n in self.nodes.values() if n.get("type") == NodeType.AGENT.value)

    async def get_active_tasks(self):
        return []

    async def register_agent(self, agent_id, agent_type, capabilities):
        nid = await self.create_node(NodeType.AGENT, {"agent_id": agent_id, "agent_type": agent_type, "capabilities": capabilities, "status": "active"}, actor=agent_id)
        return nid

    async def create_task(self, agent_id, task_type, payload):
        return await self.create_node(NodeType.TASK, {"task_type": task_type, "payload": dict(payload), "created_by": agent_id, "status": "open"}, actor=agent_id)

    async def complete_task(self, task_id, deliverable):
        return await self.update_node(task_id, {"status": "completed"})

    async def link_evidence(self, evidence_id, target_id):
        return await self.add_edge(evidence_id, target_id, __import__("agency.lattice.models", fromlist=["EdgeType"]).EdgeType.EVIDENCE_FOR)

    def _record(self, event_type, actor, target_id, payload):
        self.events.append(LatticeEvent(
            event_id=f"evt-{len(self.events)}",
            timestamp=utc_now(),
            event_type=event_type,
            actor=actor,
            target_id=target_id,
            payload=dict(payload),
        ))


@pytest.fixture
def orchestrator():
    """Orchestrator with mocked registries but real Lattice backend."""
    orch = AgencyOrchestrator.__new__(AgencyOrchestrator)
    orch._config = {}
    orch._log = MagicMock()
    orch._identity_registry = FakeRegistry()
    orch._runtime_registry = FakeRegistry()
    orch._policy_engine = MagicMock()
    orch._lattice = None
    orch._spawned_proposals = set()
    orch._started = False
    return orch


@pytest.fixture
def backend():
    return FakeLatticeBackend()


@pytest.fixture
def lattice(backend):
    lat = Lattice.__new__(Lattice)
    lat.config = MagicMock()
    lat.backend = backend
    lat.governance = MagicMock()
    lat.reputation = MagicMock()
    return lat


@pytest.mark.asyncio
async def test_full_spawn_flow(orchestrator, backend, lattice):
    """Test: submit proposal → vote twice → agent appears in registry."""
    orchestrator._lattice = lattice

    # Create proposal
    proposal_id = await lattice.submit_proposal(
        proposer_id="test_user",
        proposal_type="spawn_agent",
        payload={"name": "TestBot", "domain": "testing", "capabilities": ["run_diagnostics", "report"]},
        quorum=0.66,
    )

    # Vote 1: butler (weight 1.0)
    await orchestrator.resolve_agent_proposal(
        proposal_id=proposal_id,
        voter_id="butler",
        decision="approve",
        evidence=["test"],
    )

    # Vote 2: user (weight 1.0) — quorum reached
    result = await orchestrator.resolve_agent_proposal(
        proposal_id=proposal_id,
        voter_id="user",
        decision="approve",
        evidence=["test"],
    )

    # Proposal should be passed
    assert result["status"] == "passed"

    # Agent should be in the registry
    agents = await orchestrator.list_agents()
    test_bot = next((a for a in agents if a.name == "TestBot"), None)
    assert test_bot is not None, "Agent should be in registry after proposal passes"
    assert test_bot.domain == "testing"


@pytest.mark.asyncio
async def test_spawn_with_no_name(orchestrator, backend, lattice):
    """Test: proposal without name should generate fallback name."""
    orchestrator._lattice = lattice

    proposal_id = await lattice.submit_proposal(
        proposer_id="test_user",
        proposal_type="spawn_agent",
        payload={"domain": "testing"},
        quorum=0.66,
    )

    await orchestrator.resolve_agent_proposal(
        proposal_id=proposal_id,
        voter_id="butler",
        decision="approve",
    )
    await orchestrator.resolve_agent_proposal(
        proposal_id=proposal_id,
        voter_id="user",
        decision="approve",
    )

    agents = await orchestrator.list_agents()
    assert len(agents) == 1
    assert agents[0].name.startswith("agent-")


@pytest.mark.asyncio
async def test_proposal_with_wrong_type(orchestrator, backend, lattice):
    """Test: non-spawn_agent proposals should not trigger spawn."""
    orchestrator._lattice = lattice

    proposal_id = await lattice.submit_proposal(
        proposer_id="test_user",
        proposal_type="policy_change",
        payload={"policy": "test"},
        quorum=0.5,
    )

    await orchestrator.resolve_agent_proposal(
        proposal_id=proposal_id,
        voter_id="butler",
        decision="approve",
    )

    # No agents should be spawned
    agents = await orchestrator.list_agents()
    assert len(agents) == 0
