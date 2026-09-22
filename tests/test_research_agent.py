"""Tests for the ResearchAgent domain agent."""

from __future__ import annotations

from agency.agents.research import RESEARCH_SYSTEM_PROMPT, ResearchAgent
from agency.agents.research.agent import RESEARCH_SYSTEM_PROMPT as PROMPT_DIRECT
from agency.tools.base import ToolContext, ToolSpec
from agency.tools.driver import ToolDriver


class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def generate(self, prompt: str, context: dict | None = None) -> str:
        _ = (prompt, context)
        return self._responses.pop(0)


class FakeRegistry:
    def __init__(self, specs: list[ToolSpec]) -> None:
        self._specs = specs

    def list_specs(self) -> list[ToolSpec]:
        return self._specs

    async def call(self, name: str, args: dict, ctx: ToolContext):  # pragma: no cover
        raise AssertionError("not used in prompt test")


def test_prompt_contains_key_rules() -> None:
    assert "web_search FIRST" in RESEARCH_SYSTEM_PROMPT
    assert "Never answer from memory alone" in RESEARCH_SYSTEM_PROMPT
    assert "web_fetch" in RESEARCH_SYSTEM_PROMPT
    assert "[N]" in RESEARCH_SYSTEM_PROMPT
    assert "memory_write" in RESEARCH_SYSTEM_PROMPT
    assert "NEVER invent sources" in RESEARCH_SYSTEM_PROMPT


def test_prompt_mentions_sandbox_restriction() -> None:
    assert "sandbox_exec" in RESEARCH_SYSTEM_PROMPT


def test_agent_attributes() -> None:
    agent = ResearchAgent()
    assert agent.domain == "research"
    assert agent.capabilities == {"research", "tools"}
    assert agent.name == "Research Agent"


def test_system_prompt_method() -> None:
    agent = ResearchAgent()
    assert agent.system_prompt() == RESEARCH_SYSTEM_PROMPT
    assert PROMPT_DIRECT == RESEARCH_SYSTEM_PROMPT


async def test_handle_returns_prompt_plus_message() -> None:
    agent = ResearchAgent()
    result = await agent.handle("latest news on Rust 2026")
    assert "latest news on Rust 2026" in result
    assert RESEARCH_SYSTEM_PROMPT in result
    assert result.index(RESEARCH_SYSTEM_PROMPT) < result.index("latest news on Rust 2026")


async def test_prompt_builds_with_tool_specs() -> None:
    specs = [
        ToolSpec(name="web_search", description="Search the web", parameters={}),
        ToolSpec(name="web_fetch", description="Fetch a URL", parameters={}),
        ToolSpec(name="memory_write", description="Store a finding", parameters={}),
    ]
    registry = FakeRegistry(specs)
    driver = ToolDriver(registry, FakeLLM(['{"final": "ok"}']))
    agent = ResearchAgent()
    prompt = driver._build_prompt("research rust", agent.system_prompt(), specs, [])
    assert "web_search" in prompt
    assert "web_fetch" in prompt
    assert "memory_write" in prompt
    assert "research rust" in prompt
