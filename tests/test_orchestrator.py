"""Integration test for the Agency Orchestrator.

Tests the full pipeline: register agents → submit task → execute →
verify evidence, risk, audit, and memory are all updated.
"""

from __future__ import annotations

import pytest

from agency.llm.adapter import LLMAdapter
from agency.orchestrator import AgencyOrchestrator


@pytest.fixture
async def orchestrator():
    """Create and start an orchestrator for testing."""
    orch = AgencyOrchestrator()
    await orch.start()
    yield orch
    await orch.stop()


@pytest.mark.asyncio
async def test_register_agent(orchestrator: AgencyOrchestrator):
    """Test agent registration."""
    agent = await orchestrator.register_agent(
        name="scout",
        domain="security",
        capabilities=["inspect", "scan"],
    )
    assert agent.name == "scout"
    assert agent.domain == "security"
    assert agent.trust_level.value == "observed"
    assert len(agent.capabilities) == 2

    agents = await orchestrator.list_agents()
    assert len(agents) == 1
    assert agents[0].id == agent.id


@pytest.mark.asyncio
async def test_submit_and_execute_task(orchestrator: AgencyOrchestrator):
    """Test full task execution pipeline."""
    agent = await orchestrator.register_agent(
        name="analyst",
        domain="security",
        capabilities=["analyze"],
    )

    task = await orchestrator.submit_task(
        title="Test scan",
        description="Scan the target system for vulnerabilities",
        agent_id=agent.id,
    )
    assert task.title == "Test scan"
    assert task.status.value == "pending"

    result = await orchestrator.execute_task(task.task_id)
    assert result.status.value == "completed"
    assert result.task_id == task.task_id

    # Verify task status updated
    updated_task = await orchestrator.get_task(task.task_id)
    assert updated_task.status.value == "completed"


@pytest.mark.asyncio
async def test_orchestrator_uses_llm_adapter(orchestrator: AgencyOrchestrator):
    """Orchestrator must wire a shared LLMAdapter into the executor."""
    assert isinstance(orchestrator.llm_adapter, LLMAdapter)
    assert isinstance(orchestrator.executor.adapter, LLMAdapter)
    assert orchestrator.executor.adapter is orchestrator.llm_adapter
    # Executor's callable must be the adapter's generate method.
    assert orchestrator.executor.llm == orchestrator.llm_adapter.generate


@pytest.mark.asyncio
async def test_health_check(orchestrator: AgencyOrchestrator):
    """Test health check returns all components."""
    health = await orchestrator.health_check()
    assert health["status"] == "ok"
    assert health["orchestrator"] == "running"
    assert "identity_registry" in health
    assert "runtime_registry" in health
    assert "tasks" in health
    assert "memory" in health
    assert "evidence" in health
    assert "risk" in health
    assert "bridges" in health


@pytest.mark.asyncio
async def test_memory_search(orchestrator: AgencyOrchestrator):
    """Test memory search after task execution."""
    agent = await orchestrator.register_agent(
        name="memory_test",
        domain="test",
        capabilities=["test"],
    )

    task = await orchestrator.submit_task(
        title="Memory test",
        description="Test task for memory storage",
        agent_id=agent.id,
    )
    await orchestrator.execute_task(task.task_id)

    results = await orchestrator.search_memory("Memory test")
    assert len(results) >= 1
    assert any("Memory test" in r.content for r in results)


@pytest.mark.asyncio
async def test_list_tasks(orchestrator: AgencyOrchestrator):
    """Test task listing."""
    agent = await orchestrator.register_agent(
        name="list_test",
        domain="test",
        capabilities=["test"],
    )

    await orchestrator.submit_task("Task 1", "First task", agent.id)
    await orchestrator.submit_task("Task 2", "Second task", agent.id)

    tasks = await orchestrator.list_tasks()
    assert len(tasks) >= 2

    # Filter by status
    completed = await orchestrator.list_tasks(status="completed")
    assert all(t.status.value == "completed" for t in completed)
