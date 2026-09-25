"""Tests for the LLM tool-calling driver."""

from __future__ import annotations

import json
from typing import Any

from agency.tools.base import ToolContext, ToolResult, ToolSpec
from agency.tools.driver import ToolDriver, _extract_json


def make_ctx(task_id: str = "task-1") -> ToolContext:
    return ToolContext(agent_id="agent-1", task_id=task_id)


class FakeLLM:
    """Scripted LLM: pops one response per generate call."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def generate(self, prompt: str, context: dict[str, Any] | None = None) -> str:
        self.calls.append((prompt, context))
        if not self._responses:
            raise AssertionError("FakeLLM ran out of scripted responses")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeRegistry:
    """Dict-backed registry implementing call() and list_specs()."""

    def __init__(self) -> None:
        self._handlers: dict[str, Any] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def add_spec(
        self,
        name: str,
        description: str = "fake tool",
        parameters: dict[str, Any] | None = None,
        handler: Any = None,
    ) -> ToolSpec:
        spec = ToolSpec(name=name, description=description, parameters=parameters or {})
        self._handlers[name] = (spec, handler)
        return spec

    def list_specs(self) -> list[ToolSpec]:
        return [spec for spec, _ in self._handlers.values()]

    async def call(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.calls.append((name, args))
        if name not in self._handlers:
            return ToolResult(tool=name, ok=False, error=f"unknown tool: {name}")
        _, handler = self._handlers[name]
        if handler is None:
            return ToolResult(tool=name, ok=True, output={"echo": args}, evidence={})
        result = handler(args)
        if isinstance(result, ToolResult):
            if not result.tool:
                result.tool = name
            return result
        return ToolResult(tool=name, ok=True, output=result, evidence={})


def make_registry_with_search() -> FakeRegistry:
    registry = FakeRegistry()
    registry.add_spec(
        "web_search",
        "Search the web",
        {"type": "object"},
        handler=lambda args: ToolResult(
            tool="web_search",
            ok=True,
            output={"results": ["r1"]},
            evidence={"urls": ["https://example.com/a"]},
        ),
    )
    return registry


# --- happy path ---


async def test_single_tool_call_then_final() -> None:
    registry = make_registry_with_search()
    llm = FakeLLM(
        [
            '{"action": {"name": "web_search", "args": {"q": "rust"}}}',
            '{"final": "Rust news [1]"}',
        ]
    )
    driver = ToolDriver(registry, llm)
    result = await driver.run("latest rust news", "You are helpful.", make_ctx())
    assert result.status == "completed"
    assert result.final_answer == "Rust news [1]"
    assert len(result.steps) == 1
    assert result.steps[0].tool == "web_search"
    assert result.steps[0].ok is True
    assert result.llm_calls == 2


async def test_json_in_code_fences_parsed() -> None:
    registry = make_registry_with_search()
    llm = FakeLLM(
        [
            '```json\n{"action": {"name": "web_search", "args": {"q": "x"}}}\n```',
            '{"final": "done"}',
        ]
    )
    driver = ToolDriver(registry, llm)
    result = await driver.run("task", "sys", make_ctx())
    assert result.status == "completed"
    assert result.final_answer == "done"
    assert len(result.steps) == 1


async def test_json_with_leading_prose_parsed() -> None:
    registry = make_registry_with_search()
    llm = FakeLLM(
        [
            'Sure! {"action": {"name": "web_search", "args": {"q": "x"}}} hope this helps',
            '{"final": "done"}',
        ]
    )
    driver = ToolDriver(registry, llm)
    result = await driver.run("task", "sys", make_ctx())
    assert result.status == "completed"
    assert len(result.steps) == 1


async def test_json_with_trailing_prose_parsed() -> None:
    parsed = _extract_json('prefix {"final": "hi"} suffix')
    assert parsed == {"final": "hi"}


async def test_extract_json_returns_none_without_object() -> None:
    assert _extract_json("no braces here") is None
    assert _extract_json("") is None
    assert _extract_json("[1, 2, 3]") is None


# --- malformed handling ---


async def test_malformed_then_recovery() -> None:
    registry = make_registry_with_search()
    llm = FakeLLM(
        [
            "this is not json at all",
            '{"action": {"name": "web_search", "args": {}}}',
            '{"final": "recovered"}',
        ]
    )
    driver = ToolDriver(registry, llm)
    result = await driver.run("task", "sys", make_ctx())
    assert result.status == "completed"
    assert result.final_answer == "recovered"
    assert len(result.steps) == 1
    # retry observation appended to the second prompt
    assert "not valid JSON" in llm.calls[1][0]


async def test_two_consecutive_malformed_fails_closed() -> None:
    registry = make_registry_with_search()
    llm = FakeLLM(["garbage one", "garbage two"])
    driver = ToolDriver(registry, llm)
    result = await driver.run("task", "sys", make_ctx())
    assert result.status == "parse_error"
    assert result.final_answer == "Could not produce a final answer."
    assert result.steps == []
    assert result.llm_calls == 2


# --- tool errors / unknown tools ---


async def test_tool_error_loop_continues() -> None:
    registry = FakeRegistry()
    registry.add_spec(
        "flaky",
        "fails once",
        handler=lambda args: ToolResult(tool="flaky", ok=False, error="boom failed"),
    )
    llm = FakeLLM(
        [
            '{"action": {"name": "flaky", "args": {}}}',
            '{"final": "gave up gracefully"}',
        ]
    )
    driver = ToolDriver(registry, llm)
    result = await driver.run("task", "sys", make_ctx())
    assert result.status == "completed"
    assert len(result.steps) == 1
    assert result.steps[0].ok is False
    assert result.steps[0].error == "boom failed"
    # observation containing the error is shown to the model on retry
    assert "boom failed" in llm.calls[1][0]


async def test_unknown_tool_loop_continues() -> None:
    registry = FakeRegistry()
    llm = FakeLLM(
        [
            '{"action": {"name": "nope_tool", "args": {}}}',
            '{"final": "done anyway"}',
        ]
    )
    driver = ToolDriver(registry, llm)
    result = await driver.run("task", "sys", make_ctx())
    assert result.status == "completed"
    assert result.final_answer == "done anyway"
    assert len(result.steps) == 1
    assert "unknown tool" in llm.calls[1][0].lower()


# --- iteration limits ---


async def test_max_iterations_refusal() -> None:
    registry = make_registry_with_search()
    llm = FakeLLM(
        [
            '{"action": {"name": "web_search", "args": {}}}',
            "I refuse to answer",
        ]
    )
    driver = ToolDriver(registry, llm, max_tool_iterations=1)
    result = await driver.run("task", "sys", make_ctx())
    assert result.status == "max_iterations"
    assert result.final_answer == "I refuse to answer"
    assert len(result.steps) == 1
    assert result.llm_calls == 2
    # forced-final instruction was sent
    assert "You have used all tool calls" in llm.calls[1][0]


async def test_max_iterations_accepts_late_final() -> None:
    registry = make_registry_with_search()
    llm = FakeLLM(
        [
            '{"action": {"name": "web_search", "args": {}}}',
            '{"final": "late answer"}',
        ]
    )
    driver = ToolDriver(registry, llm, max_tool_iterations=1)
    result = await driver.run("task", "sys", make_ctx())
    assert result.status == "completed"
    assert result.final_answer == "late answer"


# --- llm errors ---


async def test_llm_raises() -> None:
    registry = make_registry_with_search()
    llm = FakeLLM([RuntimeError("connection lost")])
    driver = ToolDriver(registry, llm)
    result = await driver.run("task", "sys", make_ctx())
    assert result.status == "llm_error"
    assert result.final_answer == "LLM error: connection lost"
    assert result.steps == []


async def test_llm_raises_after_steps_preserved() -> None:
    registry = make_registry_with_search()
    llm = FakeLLM(
        [
            '{"action": {"name": "web_search", "args": {}}}',
            RuntimeError("went away"),
        ]
    )
    driver = ToolDriver(registry, llm)
    result = await driver.run("task", "sys", make_ctx())
    assert result.status == "llm_error"
    assert len(result.steps) == 1
    assert result.llm_calls == 1


# --- evidence ---


async def test_evidence_aggregation() -> None:
    registry = FakeRegistry()
    registry.add_spec(
        "tool_a",
        "a",
        handler=lambda args: ToolResult(
            tool="tool_a",
            ok=True,
            output="a",
            evidence={
                "urls": ["https://x/1", "https://x/2"],
                "sources": ["s1"],
                "queries": ["q1"],
                "engine": "ddg",
            },
        ),
    )
    registry.add_spec(
        "tool_b",
        "b",
        handler=lambda args: ToolResult(
            tool="tool_b",
            ok=True,
            output="b",
            evidence={
                "urls": ["https://x/2", "https://x/3"],
                "sources": ["s1", "s2"],
                "queries": ["q2"],
                "engine": "ddg",
            },
        ),
    )
    llm = FakeLLM(
        [
            '{"action": {"name": "tool_a", "args": {}}}',
            '{"action": {"name": "tool_b", "args": {}}}',
            '{"final": "done"}',
        ]
    )
    driver = ToolDriver(registry, llm)
    result = await driver.run("task", "sys", make_ctx())
    assert result.status == "completed"
    assert result.evidence["urls"] == ["https://x/1", "https://x/2", "https://x/3"]
    assert result.evidence["sources"] == ["s1", "s2"]
    assert result.evidence["queries"] == ["q1", "q2"]
    assert result.evidence["engine"] == "ddg"


# --- prompt shape ---


async def test_prompt_contains_specs_and_task() -> None:
    registry = FakeRegistry()
    registry.add_spec("web_search", "Search the web")
    registry.add_spec("web_fetch", "Fetch a URL")
    llm = FakeLLM(['{"final": "ok"}'])
    driver = ToolDriver(registry, llm)
    prompt = driver._build_prompt(
        "research rust 2026",
        "You are the Research Agent.",
        registry.list_specs(),
        [],
    )
    assert "web_search" in prompt
    assert "web_fetch" in prompt
    assert "research rust 2026" in prompt
    assert "AVAILABLE TOOLS" in prompt
    assert '"action"' in prompt
    assert '"final"' in prompt
    # specs are valid JSON embedded in the prompt
    assert json.loads(prompt.split("AVAILABLE TOOLS:")[1].split("\n")[1])


async def test_llm_receives_task_id_in_context() -> None:
    registry = make_registry_with_search()
    llm = FakeLLM(['{"final": "ok"}'])
    driver = ToolDriver(registry, llm)
    await driver.run("task", "sys", make_ctx(task_id="task-abc"))
    assert llm.calls[0][1] == {"task_id": "task-abc", "strict": True}


def test_extract_json_nested_and_first_wins() -> None:
    parsed = _extract_json('{"action": {"name": "t", "args": {"a": {"b": 1}}}}')
    assert parsed == {"action": {"name": "t", "args": {"a": {"b": 1}}}}
    assert _extract_json("not json {oops") is None
