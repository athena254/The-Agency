"""Integration tests: tool-capable research path end-to-end.

Covers the orchestrator tool-loop branch, the /research Telegram command,
and router keyword routing — with a fake LLM and a fake web transport so
no network access is needed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from agency.agents.research import RESEARCH_SYSTEM_PROMPT, ResearchAgent
from agency.butler.router import MessageRouter
from agency.orchestrator import AgencyOrchestrator
from agency.tools.base import ToolContext
from agency.tools.builtin import register_all
from agency.tools.driver import ToolDriver
from agency.tools.registry import ToolRegistry


class FakeLLM:
    """Scripted LLM: pops one response per generate() call."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def generate(self, prompt: str, context: dict | None = None) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            return '{"final": "no more scripted responses"}'
        return self.responses.pop(0)


def _ddg_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        text="""
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Frust">Rust 2026 news</a>
    <a class="result__snippet">Rust releases and RFCs in 2026.</a>
    """,
    )


def _article_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, text="<html><body><p>Rust 2026: edition news here.</p></body></html>"
    )


def _transport(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


class TestOrchestratorToolBranch:
    """The tool-capable branch in execute_task."""

    @pytest.mark.asyncio
    async def test_research_agent_runs_tool_loop(self):
        # Pass the fake through the constructor so BOTH the classic
        # executor and the tool driver see it.
        fake = FakeLLM(
            [
                '{"action": {"name": "web_search", "args": {"query": "rust 2026"}}}',
                '{"action": {"name": "web_fetch", "args": {"url": "https://example.com/rust"}}}',
                '{"final": "Rust 2026 summary [1]. Sources: https://example.com/rust"}',
            ]
        )
        orch = AgencyOrchestrator(llm=fake)
        await orch.start()
        try:
            agent = await orch.register_agent("researcher", "research", ["research", "tools"])
            task = await orch.submit_task(
                title="Research test",
                description="rust 2026 news",
                agent_id=agent.id,
            )
            result = await orch.execute_task(task.task_id)
            assert result.status.value == "completed"
            assert "Rust 2026" in str(result.output)
            # The tool loop must have run: 3 LLM calls (search, fetch, final)
            assert len(fake.prompts) == 3
            # First prompt contains the research system prompt + tool specs
            assert "web_search" in fake.prompts[0]
        finally:
            await orch.stop()

    @pytest.mark.asyncio
    async def test_non_tool_agent_unchanged_path(self):
        # Pass the fake through the constructor — the classic executor
        # captures the adapter at __init__ time.
        fake = FakeLLM(["plain llm response"])
        orch = AgencyOrchestrator(llm=fake)
        await orch.start()
        try:
            # security is a non-tool-capable domain (tools are research/general)
            agent = await orch.register_agent("sec", "security", ["respond"])
            task = await orch.submit_task(
                title="Chat", description="hello there", agent_id=agent.id
            )
            await orch.execute_task(task.task_id)
            # Non-tool agent: still runs the classic executor path (at
            # least one LLM call) and never sees the tool prompt.
            assert fake.prompts, "executor should have called the LLM"
            assert "AVAILABLE TOOLS" not in fake.prompts[0]
        finally:
            await orch.stop()


class TestResearchTelegramCommand:
    """/research command through the handler."""

    @pytest.fixture
    def handler(self):
        from agency.telegram.config import TelegramConfig
        from agency.telegram.handler import TelegramHandler

        config = TelegramConfig(bot_token="test:token", allowed_chat_ids=[1])
        return TelegramHandler(config)

    @pytest.mark.asyncio
    async def test_research_usage_error(self, handler):
        response = await handler._handle_research("   ", "user")
        assert "Usage" in response

    @pytest.mark.asyncio
    async def test_research_no_butler(self, handler):
        response = await handler._handle_research("rust news", "user")
        assert "Butler not running" in response

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["failed", "timeout", "cancelled"])
    async def test_research_failure_is_not_announced_as_complete(self, handler, status):
        from agency.agents.executor import ExecutionStatus

        orchestrator = SimpleNamespace(
            list_agents=AsyncMock(
                return_value=[SimpleNamespace(id="research-1", domain="research")]
            ),
            submit_task=AsyncMock(return_value=SimpleNamespace(task_id="task-1")),
            execute_task=AsyncMock(
                return_value=SimpleNamespace(
                    status=ExecutionStatus(status), output="private upstream error and key"
                )
            ),
        )
        handler._butler = SimpleNamespace(orchestrator=orchestrator)
        response = await handler._handle_research("rust news", "telegram:101")
        assert "Research complete" not in response
        assert "private upstream error" not in response
        assert "could not" in response.lower() or "timed out" in response.lower()
        await handler.close()


class TestRouterResearchKeywords:
    """Deterministic routing to the research domain."""

    def test_research_message_routes(self):
        router = MessageRouter()
        from agency.kernel.identity import Agent, Capability

        agent = Agent(
            name="Research Agent",
            domain="research",
            capabilities=[Capability(name="research")],
        )
        router.register_agent(agent)
        assert router.detect_domain("research the latest rust news") == "research"
        assert router.detect_domain("look up who is grace hopper") == "research"

    def test_research_agent_attributes(self):
        ra = ResearchAgent()
        assert ra.domain == "research"
        assert "tools" in ra.capabilities
        assert "web_search" in RESEARCH_SYSTEM_PROMPT or "search" in RESEARCH_SYSTEM_PROMPT


class TestDriverWithRealRegistry:
    """Driver against the real registry + mocked web transport."""

    @pytest.mark.asyncio
    async def test_full_search_fetch_flow(self):
        registry = ToolRegistry()
        register_all(registry, transport=_transport(_ddg_handler))
        # Override the fetch transport separately: register a dedicated
        # WebFetchTool with the article handler.
        from agency.tools.builtin.web import WebFetchTool

        registry._tools["web_fetch"] = WebFetchTool(transport=_transport(_article_handler))

        fake = FakeLLM(
            [
                '{"action": {"name": "web_search", "args": {"query": "rust 2026"}}}',
                '{"action": {"name": "web_fetch", "args": {"url": "https://example.com/rust"}}}',
                '{"final": "Done: Rust 2026 edition news. Sources: https://example.com/rust"}',
            ]
        )
        driver = ToolDriver(registry=registry, llm=fake)
        ctx = ToolContext(agent_id="agent-1", task_id="task-1")
        result = await driver.run(
            task="rust 2026 news",
            system_prompt=RESEARCH_SYSTEM_PROMPT,
            ctx=ctx,
        )
        assert result.status == "completed"
        assert len(result.steps) == 2
        assert result.steps[0].tool == "web_search"
        assert result.steps[0].ok is True
        assert result.steps[1].tool == "web_fetch"
        assert result.steps[1].ok is True
        assert "example.com" in result.final_answer
        # Evidence aggregation carries the fetched URL
        urls = result.evidence.get("urls", []) + result.evidence.get("sources", [])
        assert any("example.com" in u for u in urls)
