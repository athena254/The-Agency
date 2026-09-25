"""Beta failure-integrity slice (offline, echo-only, no live calls).

Scope: ``src/agency/tools/driver.py`` + ``src/agency/butler/service.py``.
Covers brief_04_integrity cases: malformed reply, empty reply, max budget,
failed tool with no fake success, Butler failed-task status, completed
control, and timeout/provider errors.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agency.agents.executor import ExecutionResult, ExecutionStatus
from agency.butler.config import ButlerConfig
from agency.butler.service import ButlerService
from agency.kernel.identity import Agent
from agency.llm.adapter import LLMAdapter
from agency.tools.base import ToolContext, ToolResult, ToolSpec
from agency.tools.driver import ToolDriver

_FALLBACK_PARSE = "Could not produce a final answer."


def _ctx(task_id: str = "integrity-task") -> ToolContext:
    return ToolContext(agent_id="agent-1", task_id=task_id)


class _FakeLLM:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)

    async def generate(self, prompt: str, context: dict[str, Any] | None = None) -> str:
        assert self._responses, "FakeLLM ran out of scripted responses"
        _ = (prompt, context)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _FakeRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, Any] = {}

    def add(self, name: str, handler: Any = None) -> ToolSpec:
        spec = ToolSpec(name=name, description="fake", parameters={})
        self._handlers[name] = (spec, handler)
        return spec

    def list_specs(self) -> list[ToolSpec]:
        return [spec for spec, _ in self._handlers.values()]

    async def call(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if name not in self._handlers:
            return ToolResult(tool=name, ok=False, error=f"unknown tool: {name}")
        _, handler = self._handlers[name]
        if handler is None:
            return ToolResult(tool=name, ok=True, output={"echo": args})
        result = handler(args)
        if isinstance(result, ToolResult):
            if not result.tool:
                result.tool = name
            return result
        return ToolResult(tool=name, ok=True, output=result)


def _registry_with_search() -> _FakeRegistry:
    registry = _FakeRegistry()
    registry.add(
        "web_search",
        handler=lambda args: ToolResult(
            tool="web_search",
            ok=True,
            output={"results": ["r1"]},
            evidence={"urls": ["https://example.com/a"]},
        ),
    )
    return registry


# --- ToolDriver: parse exhaustion must not be a successful completion ---


@pytest.mark.asyncio
async def test_malformed_replies_are_parse_error_not_completed() -> None:
    driver = ToolDriver(_registry_with_search(), _FakeLLM(["garbage one", "garbage two"]))
    result = await driver.run("task", "sys", _ctx())
    assert result.status != "completed"
    assert result.status == "parse_error"
    assert result.final_answer == _FALLBACK_PARSE
    assert "garbage two" not in result.final_answer
    assert result.steps == []


@pytest.mark.asyncio
async def test_empty_replies_are_parse_error_not_completed() -> None:
    driver = ToolDriver(_registry_with_search(), _FakeLLM(["", "   "]))
    result = await driver.run("task", "sys", _ctx())
    assert result.status == "parse_error"
    assert result.final_answer == _FALLBACK_PARSE
    assert result.steps == []


# --- ToolDriver: budget exhaustion / failed tool must not fake success ---


@pytest.mark.asyncio
async def test_max_budget_unsuccessful_forced_final_is_non_success() -> None:
    registry = _registry_with_search()
    llm = _FakeLLM(
        [
            '{"action": {"name": "web_search", "args": {}}}',
            "I refuse to answer",
        ]
    )
    driver = ToolDriver(registry, llm, max_tool_iterations=1)
    result = await driver.run("task", "sys", _ctx())
    assert result.status == "max_iterations"
    assert result.status != "completed"


@pytest.mark.asyncio
async def test_failed_tool_without_honest_final_is_no_fake_success() -> None:
    registry = _FakeRegistry()
    registry.add(
        "flaky",
        handler=lambda args: ToolResult(tool="flaky", ok=False, error="boom failed"),
    )
    llm = _FakeLLM(
        [
            '{"action": {"name": "flaky", "args": {}}}',
            "still not json, no final answer",
        ]
    )
    driver = ToolDriver(registry, llm, max_tool_iterations=1)
    result = await driver.run("task", "sys", _ctx())
    assert result.status != "completed"
    assert len(result.steps) == 1
    assert result.steps[0].ok is False


@pytest.mark.asyncio
async def test_failed_tool_with_honest_final_stays_completed_recovery() -> None:
    """Recovery control: an honest final after a recoverable tool error stays completed."""
    registry = _FakeRegistry()
    registry.add(
        "flaky",
        handler=lambda args: ToolResult(tool="flaky", ok=False, error="boom failed"),
    )
    llm = _FakeLLM(
        [
            '{"action": {"name": "flaky", "args": {}}}',
            '{"final": "gave up gracefully"}',
        ]
    )
    driver = ToolDriver(registry, llm)
    result = await driver.run("task", "sys", _ctx())
    assert result.status == "completed"
    assert result.final_answer == "gave up gracefully"


# --- ToolDriver: timeout / provider errors surface, offline only ---


@pytest.mark.asyncio
async def test_llm_timeout_surfaces_as_llm_error() -> None:
    driver = ToolDriver(_registry_with_search(), _FakeLLM([TimeoutError("timed out")]))
    result = await driver.run("task", "sys", _ctx())
    assert result.status == "llm_error"
    assert result.final_answer.startswith("LLM error:")


@pytest.mark.asyncio
async def test_unknown_provider_error_surfaces_as_llm_error() -> None:
    adapter = LLMAdapter(provider="not-a-provider", model="missing")
    driver = ToolDriver(_registry_with_search(), adapter)
    result = await driver.run("task", "sys", _ctx())
    assert result.status == "llm_error"
    assert "Unsupported LLM provider" in result.final_answer


# --- ButlerService: failed task status must not become a success turn ---


def _agent() -> Agent:
    return Agent(name="general-agent", domain="general", capabilities=["general"])


class _StubOrchestrator:
    def __init__(self, result: ExecutionResult | Exception) -> None:
        self._result = result

    async def submit_task(self, **kwargs: Any) -> SimpleNamespace:
        _ = kwargs
        return SimpleNamespace(task_id="task-1", title="t")

    async def execute_task(
        self, task_id: str, context: dict[str, Any] | None = None
    ) -> ExecutionResult:
        _ = (task_id, context)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _service_with(stub: _StubOrchestrator, **config_kwargs: Any) -> ButlerService:
    from agency.memory.sms.store import MemoryStore

    config = ButlerConfig(timeout=5.0, **config_kwargs)
    svc = ButlerService(
        config=config,
        orchestrator=stub,  # type: ignore[arg-type]
        memory_store=MemoryStore(":memory:"),
    )
    svc._started = True
    svc._seeded = True
    return svc


def _patch_service(svc: ButlerService, agent: Agent, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _route(self: ButlerService, message: str, context: dict[str, Any]) -> Agent:
        _ = (message, context)
        return agent

    async def _audit(self: ButlerService, **kwargs: Any) -> None:
        _ = kwargs

    async def _recall(self: ButlerService, sender: str, message: str) -> str:
        _ = (sender, message)
        return ""

    async def _store(
        self: ButlerService, sender: str, agent_: Agent, message: str, response: str
    ) -> None:
        _ = (sender, agent_, message, response)

    monkeypatch.setattr(ButlerService, "route", _route)
    monkeypatch.setattr(ButlerService, "_audit_append", _audit)
    monkeypatch.setattr(ButlerService, "_recall_history", _recall)
    monkeypatch.setattr(ButlerService, "_store_turn", _store)


@pytest.mark.asyncio
async def test_butler_execute_failed_task_raises_not_success_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = ExecutionResult(
        task_id="task-1", status=ExecutionStatus.FAILED, output="looks fine", attempts=1
    )
    svc = _service_with(_StubOrchestrator(failed))
    _patch_service(svc, _agent(), monkeypatch)
    with pytest.raises(Exception, match="(?i)fail"):
        await svc.execute(_agent(), "hello", {"sender": "tester"})
    # No traceback/secrets leak via the typed message itself.
    try:
        await svc.execute(_agent(), "hello", {"sender": "tester"})
    except Exception as exc:  # noqa: BLE001 — asserting message hygiene.
        assert "Traceback" not in str(exc)
        assert "Bearer" not in str(exc)
        assert "looks fine" not in str(exc)


@pytest.mark.asyncio
async def test_butler_handle_message_failed_task_is_explicit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = ExecutionResult(
        task_id="task-1",
        status=ExecutionStatus.FAILED,
        output="fabricated success text",
        attempts=1,
    )
    svc = _service_with(_StubOrchestrator(failed))
    agent = _agent()
    _patch_service(svc, agent, monkeypatch)
    response = await svc.handle_message("hello", "tester", {}, memory_enabled=False)
    assert response
    assert "fabricated success text" not in response
    assert response.strip().lower() != "fabricated success text".lower()


@pytest.mark.asyncio
async def test_butler_completed_task_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = ExecutionResult(
        task_id="task-1", status=ExecutionStatus.COMPLETED, output="hello world", attempts=1
    )
    svc = _service_with(_StubOrchestrator(completed))
    agent = _agent()
    _patch_service(svc, agent, monkeypatch)
    assert await svc.execute(agent, "hello", {"sender": "tester"}) == "hello world"
    response = await svc.handle_message("hello", "tester", {}, memory_enabled=False)
    assert response == "hello world"


@pytest.mark.asyncio
async def test_butler_handle_message_timeout_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _service_with(_StubOrchestrator(TimeoutError("backend timed out")))
    agent = _agent()
    _patch_service(svc, agent, monkeypatch)
    response = await svc.handle_message("hello", "tester", {}, memory_enabled=False)
    assert "timed out" in response.lower()
