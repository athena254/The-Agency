"""Tests for the unified Lattice API and singleton factory."""

from __future__ import annotations

import pytest

from agency.lattice.api import Lattice
from agency.lattice.factory import get_lattice, reset_lattice
from agency.lattice.models import EdgeType, LatticeConfig, NodeType


@pytest.fixture
async def lattice() -> Lattice:
    """A fresh initialized Lattice backed by an in-memory database."""

    instance = Lattice(config=LatticeConfig(backend="sqlite", sqlite_path=":memory:"))
    await instance.initialize()
    yield instance
    await instance.close()


@pytest.fixture
async def clean_singleton():
    """Ensure the factory singleton is reset before and after each test."""

    await reset_lattice()
    yield
    await reset_lattice()


# -- singleton factory ------------------------------------------------- #


async def test_get_lattice_returns_same_instance(
    clean_singleton: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LATTICE_SQLITE_PATH", ":memory:")
    first = await get_lattice()
    second = await get_lattice()
    assert first is second


async def test_reset_lattice_creates_new_instance(clean_singleton: None) -> None:
    first = await get_lattice(
        config=LatticeConfig(backend="sqlite", sqlite_path=":memory:")
    )
    await reset_lattice()
    second = await get_lattice(
        config=LatticeConfig(backend="sqlite", sqlite_path=":memory:")
    )
    assert first is not second


# -- node CRUD lifecycle ----------------------------------------------- #


async def test_node_crud_lifecycle(lattice: Lattice) -> None:
    node_id = await lattice.create_node(
        NodeType.TASK, {"title": "write report"}, actor="butler"
    )
    assert isinstance(node_id, str) and node_id

    node = await lattice.get_node(node_id)
    assert node is not None
    assert node["properties"]["title"] == "write report"

    assert await lattice.update_node(node_id, {"title": "write summary"}) is True
    updated = await lattice.get_node(node_id)
    assert updated is not None
    assert updated["properties"]["title"] == "write summary"

    found = await lattice.find_nodes(NodeType.TASK, {"title": "write summary"})
    assert any(item["id"] == node_id for item in found)

    assert await lattice.delete_node(node_id) is True
    assert await lattice.get_node(node_id) is None
    assert await lattice.delete_node("missing-id") is False


# -- edge operations ---------------------------------------------------- #


async def test_edge_operations(lattice: Lattice) -> None:
    source = await lattice.create_node(NodeType.TASK, {"title": "a"})
    target = await lattice.create_node(NodeType.TASK, {"title": "b"})

    edge_id = await lattice.add_edge(
        source, target, EdgeType.DEPENDS_ON, {"weight": 1}, actor="butler"
    )
    assert isinstance(edge_id, str) and edge_id

    edges = await lattice.get_edges(source, direction="out")
    assert any(edge["id"] == edge_id for edge in edges)

    assert await lattice.remove_edge(edge_id) is True
    assert await lattice.get_edges(source, direction="out") == []
    assert await lattice.remove_edge("missing-edge") is False


# -- graph traversal ---------------------------------------------------- #


async def test_graph_traversal_three_hops(lattice: Lattice) -> None:
    nodes = [
        await lattice.create_node(NodeType.TASK, {"title": f"t{i}"}) for i in range(4)
    ]
    for i in range(3):
        await lattice.add_edge(nodes[i], nodes[i + 1], EdgeType.DEPENDS_ON)

    reachable = await lattice.traverse(nodes[0], EdgeType.DEPENDS_ON, max_depth=3)
    assert reachable == nodes[1:]

    shallow = await lattice.traverse(nodes[0], EdgeType.DEPENDS_ON, max_depth=1)
    assert shallow == [nodes[1]]


async def test_pathfinding(lattice: Lattice) -> None:
    source = await lattice.create_node(NodeType.TASK, {"title": "start"})
    middle = await lattice.create_node(NodeType.TASK, {"title": "middle"})
    target = await lattice.create_node(NodeType.TASK, {"title": "end"})
    isolated = await lattice.create_node(NodeType.TASK, {"title": "isolated"})

    await lattice.add_edge(source, middle, EdgeType.DEPENDS_ON)
    await lattice.add_edge(middle, target, EdgeType.DEPENDS_ON)

    assert await lattice.find_path(source, target) == [source, middle, target]
    assert await lattice.find_path(source, isolated) is None

    # Dependencies are transitive: source depends on middle AND target.
    assert await lattice.get_dependencies(source) == [middle, target]
    assert await lattice.get_dependencies(middle) == [target]
    assert await lattice.get_dependents(target) == [middle, source]

    # Direct (single-edge) relationships resolve exactly.
    direct_a = await lattice.create_node(NodeType.TASK, {"title": "da"})
    direct_b = await lattice.create_node(NodeType.TASK, {"title": "db"})
    await lattice.add_edge(direct_a, direct_b, EdgeType.DEPENDS_ON)
    assert await lattice.get_dependencies(direct_a) == [direct_b]
    assert await lattice.get_dependents(direct_b) == [direct_a]


# -- event sourcing ----------------------------------------------------- #


async def test_event_sourcing_on_writes(lattice: Lattice) -> None:
    node_id = await lattice.create_node(
        NodeType.AGENT, {"agent_id": "a1"}, actor="butler"
    )
    await lattice.update_node(node_id, {"status": "active"}, actor="butler")

    trail = await lattice.get_audit_trail(node_id)
    event_types = {event.event_type for event in trail}
    assert {"node_created", "node_updated"} <= event_types

    by_actor = await lattice.get_events(actor="butler")
    assert all(event.actor == "butler" for event in by_actor)
    assert len(by_actor) >= 2

    replayed = await lattice.replay_events(from_ts=trail[0].timestamp)
    assert len(replayed) >= len(trail)


# -- convenience methods ------------------------------------------------ #


async def test_agent_registration_convenience(lattice: Lattice) -> None:
    node_id = await lattice.register_agent("agent-1", "worker", ["code", "review"])
    node = await lattice.get_node(node_id)
    assert node is not None
    assert node["node_type"] == NodeType.AGENT.value
    assert node["properties"]["agent_id"] == "agent-1"
    assert await lattice.get_agent_count() == 1


async def test_task_lifecycle(lattice: Lattice) -> None:
    await lattice.register_agent("agent-1", "worker", ["code"])
    task_id = await lattice.create_task(
        "agent-1", "summarize", {"doc": "spec.md"}
    )
    active = await lattice.get_active_tasks()
    assert any(task["id"] == task_id for task in active)

    assert await lattice.complete_task(task_id, {"summary": "done"}) is True
    task = await lattice.get_node(task_id)
    assert task is not None
    assert task["properties"]["status"] == "completed"
    assert await lattice.get_active_tasks() == []


async def test_evidence_linking(lattice: Lattice) -> None:
    evidence = await lattice.create_node(NodeType.EVIDENCE, {"url": "log.txt"})
    target = await lattice.create_node(NodeType.DELIVERABLE, {"name": "report"})

    edge_id = await lattice.link_evidence(evidence, target)
    assert isinstance(edge_id, str) and edge_id

    edges = await lattice.get_edges(evidence, direction="out")
    assert any(
        edge["id"] == edge_id and edge["edge_type"] == EdgeType.EVIDENCE_FOR.value
        for edge in edges
    )


# -- governance + reputation -------------------------------------------- #


async def test_governance_flow(lattice: Lattice) -> None:
    proposal_id = await lattice.submit_proposal(
        "butler", "spawn_agent", {"role": "reviewer"}, quorum=0.5
    )
    result = await lattice.cast_vote("butler", proposal_id, "approve")
    # cast_vote(voter_id, proposal_id, decision)
    assert result["proposal_id"] == proposal_id
    assert result["voter_id"] == "butler"

    status = await lattice.get_proposal_status(proposal_id)
    assert status.status == "passed"
    assert await lattice.check_quorum(proposal_id) is True


async def test_reputation_flow(lattice: Lattice) -> None:
    before = await lattice.get_reputation("agent-9")
    assert before.score == pytest.approx(0.5)

    improved = await lattice.update_reputation("agent-9", True, peer_rating=0.9)
    assert improved.tasks_completed == 1
    assert improved.score > before.score

    degraded = await lattice.update_reputation("agent-9", False)
    assert degraded.tasks_failed == 1
    assert degraded.score < improved.score


# -- vectors ------------------------------------------------------------ #


async def test_vector_search_graceful(lattice: Lattice) -> None:
    # Unknown collections degrade to an empty list, never an error.
    assert await lattice.search_vectors("missing", [1.0, 0.0], limit=5) == []
    assert await lattice.search_by_text("missing", "hello", limit=5) == []

    # Storage works; without Qdrant, semantic search degrades gracefully.
    assert (
        await lattice.upsert_vector(
            "agency_vectors",
            [("v1", [1.0, 0.0], {"topic": "lens protocol"})],
        )
        is True
    )
    hits = await lattice.search_vectors("agency_vectors", [1.0, 0.0], limit=5)
    assert isinstance(hits, list)  # [] on SQLite, ranked hits on Qdrant/memory
    assert await lattice.delete_vectors("agency_vectors", ["v1"]) is True


async def test_vector_ranking_in_memory() -> None:
    """The in-memory fallback ranks by cosine similarity."""

    from agency.lattice.api import InMemoryLatticeBackend

    backend = InMemoryLatticeBackend()
    await backend.initialize()
    await backend.upsert_vector(
        "docs",
        [
            ("v1", [1.0, 0.0], {"topic": "graphs"}),
            ("v2", [0.0, 1.0], {"topic": "vectors"}),
        ],
    )
    hits = await backend.search_vectors("docs", [1.0, 0.0], limit=2)
    assert [hit["id"] for hit in hits] == ["v1", "v2"]
    await backend.close()


# -- health & status ----------------------------------------------------- #


async def test_health_status_counts(lattice: Lattice) -> None:
    await lattice.register_agent("agent-1", "worker", ["code"])
    task_id = await lattice.create_task("agent-1", "summarize", {})

    status = await lattice.get_status()
    assert status["agent_count"] == 1
    assert status["node_count"] >= 2
    assert status["event_count"] >= 2
    active = await lattice.get_active_tasks()
    assert any(task["id"] == task_id for task in active)
    assert status.get("active_task_count", len(active)) == len(active)
