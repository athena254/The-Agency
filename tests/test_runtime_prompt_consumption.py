"""Runtime consumption of factory-issued system prompts."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agency.agents.prompts import MAX_ISSUED_PROMPT_CHARS, issued_system_prompt
from agency.kernel.identity import Agent, TrustLevel
from agency.orchestrator import AgencyOrchestrator

FACTORY_PROMPT = "# Factory Rules\nEvidence first. Stay in scope."


class _RecordingLLM:
    """Minimal LLM adapter double that records prompts."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate(self, prompt: str, context: dict[str, Any] | None = None) -> str:
        self.prompts.append(prompt)
        return "ok"


def _factory_agent(*, domain: str = "general", prompt: str = FACTORY_PROMPT) -> Agent:
    return Agent(
        name="factory-agent",
        domain=domain,
        capabilities=[],
        trust_level=TrustLevel.UNKNOWN,
        metadata={"system_prompt": prompt},
    )


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({}, ""),
        ({"other": "x"}, ""),
        ({"system_prompt": FACTORY_PROMPT}, FACTORY_PROMPT),
        ({"system_prompt": f"  {FACTORY_PROMPT}  "}, FACTORY_PROMPT),
    ],
)
def test_issued_system_prompt_reads_identity_metadata(
    metadata: dict[str, Any], expected: str
) -> None:
    agent = Agent(name="a", domain="general", capabilities=[], metadata=metadata)
    assert issued_system_prompt(agent) == expected


def test_issued_system_prompt_absent_agent() -> None:
    assert issued_system_prompt(None) == ""


@pytest.mark.parametrize(
    "metadata",
    [
        {"system_prompt": 42},
        {"system_prompt": "   "},
        {"system_prompt": "x" * (MAX_ISSUED_PROMPT_CHARS + 1)},
    ],
)
def test_issued_system_prompt_rejects_malformed_metadata(metadata: dict[str, Any]) -> None:
    agent = Agent(name="a", domain="general", capabilities=[], metadata=metadata)
    with pytest.raises((TypeError, ValueError), match="system_prompt"):
        issued_system_prompt(agent)


@pytest.mark.asyncio
async def test_tool_agent_uses_factory_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgencyOrchestrator(memory_db_path=":memory:")
    await orch.start()
    try:
        agent = _factory_agent()
        orch._identity_registry.register(agent)
        orch._runtime_registry.register(agent)
        task = await orch.submit_task("RE task", "trace the feature", agent.id)
        assert orch._tool_driver is not None
        fake_run = AsyncMock(
            return_value=SimpleNamespace(
                status="completed", steps=[], llm_calls=0, final_answer="ok", evidence=[]
            )
        )
        monkeypatch.setattr(orch._tool_driver, "run", fake_run)

        await orch.execute_task(
            task.task_id, context={"sender": "telegram:1", "assistant_name": "Atlas"}
        )

        system_prompt = fake_run.await_args.kwargs["system_prompt"]
        assert system_prompt == FACTORY_PROMPT
        assert "presentation label" not in system_prompt
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_classic_agent_path_prefixes_factory_prompt() -> None:
    recorder = _RecordingLLM()
    orch = AgencyOrchestrator(llm=recorder, memory_db_path=":memory:")
    await orch.start()
    try:
        agent = _factory_agent(domain="security")
        orch._identity_registry.register(agent)
        orch._runtime_registry.register(agent)
        task = await orch.submit_task("audit", "inspect the target", agent.id)

        await orch.execute_task(task.task_id)

        assert recorder.prompts
        assert recorder.prompts[0].startswith(FACTORY_PROMPT)
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_converse_isolated_prefixes_factory_prompt() -> None:
    recorder = _RecordingLLM()
    orch = AgencyOrchestrator(llm=recorder, memory_db_path=":memory:")
    await orch.start()
    try:
        agent = _factory_agent()
        orch._identity_registry.register(agent)

        await orch.converse_isolated("hello", agent.id)

        assert recorder.prompts
        assert recorder.prompts[0].startswith(FACTORY_PROMPT)
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_malformed_issued_prompt_fails_closed() -> None:
    orch = AgencyOrchestrator(memory_db_path=":memory:")
    await orch.start()
    try:
        agent = Agent(
            name="broken",
            domain="general",
            capabilities=[],
            metadata={"system_prompt": 7},
        )
        orch._identity_registry.register(agent)
        orch._runtime_registry.register(agent)
        task = await orch.submit_task("task", "do work", agent.id)

        with pytest.raises((TypeError, ValueError), match="system_prompt"):
            await orch.execute_task(task.task_id)
    finally:
        await orch.stop()
