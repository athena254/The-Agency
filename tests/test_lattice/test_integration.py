"""Integration stub for the unified Lattice API.

Verifies the Lattice class can be imported and instantiated, and exercises
basic cross-component flows (agents, tasks, governance, reputation) against
the in-memory backend. Full Butler/Orchestrator wiring lands with the
concrete SQLite/Neo4j backends.
"""

from __future__ import annotations

import pytest

from agency.lattice.api import Lattice
from agency.lattice.factory import get_lattice, reset_lattice
from agency.lattice.models import LatticeConfig


@pytest.fixture
async def lattice() -> Lattice:
    """A fresh initialized Lattice backed by an in-memory database."""

    instance = Lattice(config=LatticeConfig(backend="sqlite", sqlite_path=":memory:"))
    await instance.initialize()
    yield instance
    await instance.close()


@pytest.fixture
async def clean_singleton():
    """Reset the factory singleton around each test."""

    await reset_lattice()
    yield
    await reset_lattice()


async def test_lattice_importable_and_instantiable() -> None:
    lattice = Lattice(config=LatticeConfig(backend="sqlite", sqlite_path=":memory:"))
    assert lattice.backend is not None
    assert lattice.governance is not None
    assert lattice.reputation is not None
    await lattice.initialize()
    await lattice.close()


async def test_factory_singleton_smoke(clean_singleton: None) -> None:
    first = await get_lattice(
        config=LatticeConfig(backend="sqlite", sqlite_path=":memory:")
    )
    second = await get_lattice()
    assert first is second


async def test_butler_smoke_get_agent_count_and_status(lattice: Lattice) -> None:
    await lattice.register_agent("butler-1", "coordinator", ["route"])
    assert await lattice.get_agent_count() == 1
    status = await lattice.get_status()
    assert status["agent_count"] == 1


async def test_orchestrator_smoke_task_registration(lattice: Lattice) -> None:
    await lattice.register_agent("agent-1", "worker", ["code"])
    first = await lattice.create_task("agent-1", "build", {"repo": "agency"})
    second = await lattice.create_task("agent-1", "review", {"repo": "agency"})
    await lattice.add_edge(second, first, "depends_on")
    assert await lattice.get_dependencies(second) == [first]
    assert await lattice.get_dependents(first) == [second]


async def test_agent_wing_namespaced_storage(lattice: Lattice) -> None:
    wing = await lattice.create_node("wing", {"owner": "agent-1"})
    assert wing
    items = await lattice.find_nodes("wing", {"owner": "agent-1"})
    assert any(item["id"] == wing for item in items)


async def test_governance_flow_smoke(lattice: Lattice) -> None:
    proposal_id = await lattice.submit_proposal("agent-1", "policy_change", {})
    result = await lattice.cast_vote("agent-1", proposal_id, "approve")
    assert result["quorum_reached"] is True
    status = await lattice.get_proposal_status(proposal_id)
    assert status.status == "passed"


async def test_reputation_flow_smoke(lattice: Lattice) -> None:
    await lattice.register_agent("agent-1", "worker", ["code"])
    updated = await lattice.update_reputation("agent-1", True, peer_rating=0.8)
    assert updated.tasks_completed == 1
    fetched = await lattice.get_reputation("agent-1")
    assert fetched.score == pytest.approx(updated.score)


async def test_concurrent_operations_smoke(lattice: Lattice) -> None:
    import asyncio

    ids = await asyncio.gather(
        *(lattice.create_node("task", {"n": i}) for i in range(10))
    )
    assert len(set(ids)) == 10
    assert len(await lattice.find_nodes("task", limit=50)) == 10
