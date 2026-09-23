"""Tests for the GeneralAgent domain agent."""

from __future__ import annotations

from agency.agents.general import GENERAL_SYSTEM_PROMPT, GeneralAgent
from agency.agents.general.agent import GENERAL_SYSTEM_PROMPT as PROMPT_DIRECT


def test_prompt_contains_web_search_guidance() -> None:
    assert "web_search" in GENERAL_SYSTEM_PROMPT
    assert "current events" in GENERAL_SYSTEM_PROMPT


def test_prompt_contains_memory_guidance() -> None:
    assert "memory_query" in GENERAL_SYSTEM_PROMPT
    assert "memory_write" in GENERAL_SYSTEM_PROMPT


def test_prompt_contains_final_protocol() -> None:
    assert '{"final":' in GENERAL_SYSTEM_PROMPT


def test_prompt_contains_no_invention_rule() -> None:
    assert "NEVER invent sources or facts" in GENERAL_SYSTEM_PROMPT


def test_prompt_does_not_force_tools_for_casual_chat() -> None:
    assert "do NOT call tools unnecessarily" in GENERAL_SYSTEM_PROMPT
    assert "respond directly" in GENERAL_SYSTEM_PROMPT


def test_agent_attributes() -> None:
    agent = GeneralAgent()
    assert agent.domain == "general"
    assert agent.capabilities == {"general", "respond", "tools"}
    assert agent.name == "General Agent"


def test_system_prompt_method() -> None:
    agent = GeneralAgent()
    assert agent.system_prompt() == GENERAL_SYSTEM_PROMPT
    assert PROMPT_DIRECT == GENERAL_SYSTEM_PROMPT


async def test_handle_returns_prompt_plus_message() -> None:
    agent = GeneralAgent()
    result = await agent.handle("hello there")
    assert "hello there" in result
    assert GENERAL_SYSTEM_PROMPT in result
    assert result.index(GENERAL_SYSTEM_PROMPT) < result.index("hello there")


def test_prompt_under_2500_chars() -> None:
    assert len(GENERAL_SYSTEM_PROMPT) < 2500


def test_instantiation_side_effect_free() -> None:
    agent = GeneralAgent()
    assert set(vars(agent)) <= {"domain", "capabilities", "name", "_log"}
