"""Integration test proving the LLM adapter is wired into execution.

Pipeline covered:
    orchestrator -> AgentExecutor(llm=adapter) -> LLMAdapter.generate()
    -> evidence store / memory / audit.

Runs in echo mode (no API key required). A second test injects a stub
adapter returning a distinctive marker to prove executor output flows
from the adapter rather than a hardcoded echo backend.
"""

from __future__ import annotations

import pytest

from agency.agents.executor import AgentExecutor
from agency.llm.adapter import LLMAdapter
from agency.llm.config import LLMConfig, ProviderKind
from agency.orchestrator import AgencyOrchestrator


@pytest.fixture
async def orchestrator():
    orch = AgencyOrchestrator()
    await orch.start()
    yield orch
    await orch.stop()


@pytest.mark.asyncio
async def test_executor_defaults_to_llm_adapter():
    """AgentExecutor() with no args must build a working LLMAdapter."""
    ex = AgentExecutor()
    assert isinstance(ex.adapter, LLMAdapter)
    result = await ex.execute("hello wiring")
    assert result.status.value == "completed"
    # Echo fallback returns the prompt text (no API key in test env).
    assert "hello wiring" in str(result.output)


@pytest.mark.asyncio
async def test_executor_accepts_adapter_instance():
    """Passing an LLMAdapter explicitly must use its generate()."""
    adapter = LLMAdapter(
        config=LLMConfig(provider=ProviderKind.ECHO, model="echo")
    )
    ex = AgentExecutor(llm=adapter)
    assert ex.adapter is adapter
    result = await ex.execute("adapter instance check")
    assert result.status.value == "completed"
    assert "adapter instance check" in str(result.output)


@pytest.mark.asyncio
async def test_llm_adapter_echo_mode_without_api_keys():
    """Adapter must work with no credentials via echo fallback."""
    adapter = LLMAdapter(
        config=LLMConfig(provider=ProviderKind.ECHO, model="echo")
    )
    assert adapter.echo_mode is True
    text = await adapter.generate("ping", {"system": "terse"})
    assert isinstance(text, str)
    assert "ping" in text


@pytest.mark.asyncio
async def test_orchestrator_full_pipeline_uses_adapter(
    orchestrator: AgencyOrchestrator,
):
    """Create -> register -> submit -> execute; output via LLM adapter."""
    # Wiring assertions: shared adapter instance.
    assert isinstance(orchestrator.llm_adapter, LLMAdapter)
    assert orchestrator.executor.adapter is orchestrator.llm_adapter

    agent = await orchestrator.register_agent(
        name="wiring_probe",
        domain="test",
        capabilities=["test"],
    )
    task = await orchestrator.submit_task(
        title="LLM wiring probe",
        description="Prove executor output comes from the LLM adapter",
        agent_id=agent.id,
    )
    result = await orchestrator.execute_task(task.task_id)
    assert result.status.value == "completed"

    # Evidence findings must embed the adapter output (echo of subtask
    # prompt in offline mode), proving the executor called generate().
    findings = await orchestrator._evidence_store.list_findings()
    assert len(findings) >= 1
    assert any(
        "Prove executor output comes from the LLM adapter" in (f.evidence or "")
        or "LLM wiring probe" in (f.target or "")
        for f in findings
    )

    # Health must report the LLM provider.
    health = await orchestrator.health_check()
    assert "llm" in health
    assert "provider" in health["llm"]


@pytest.mark.asyncio
async def test_orchestrator_output_comes_from_adapter_not_hardcoded_echo():
    """Inject a stub adapter; its marker must surface in evidence."""
    marker = "LLM-ADAPTER-RESPONSE-42"

    stub = LLMAdapter(
        config=LLMConfig(provider=ProviderKind.ECHO, model="echo")
    )

    async def fake_generate(prompt: str, context: dict | None = None) -> str:
        return f"{marker}: {prompt}"

    stub.generate = fake_generate  # type: ignore[method-assign]

    orch = AgencyOrchestrator(llm=stub)
    await orch.start()
    try:
        assert orch.executor.adapter is stub
        agent = await orch.register_agent(
            name="stub_probe", domain="test", capabilities=["test"]
        )
        task = await orch.submit_task(
            title="Stub probe",
            description="stubbed adapter task",
            agent_id=agent.id,
        )
        result = await orch.execute_task(task.task_id)
        assert result.status.value == "completed"

        findings = await orch._evidence_store.list_findings()
        assert len(findings) >= 1
        assert any(marker in (f.evidence or "") for f in findings), (
            "executor output did not come from the injected LLM adapter"
        )
    finally:
        await orch.stop()
