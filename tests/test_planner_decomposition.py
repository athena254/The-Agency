"""Hermetic tests for the LLM-backed decomposition strategy (gaps #21f).

No live provider is contacted: the adapter is stubbed everywhere. The suite
pins (a) the default strategy resolves to the regex splitter under the echo
provider and to the LLM splitter only for a live provider, (b) strict JSON
parse+validate (schema, count cap, string bounds), and (c) that any failure
falls back to the regex strategy with an honest log line.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agency.agents.planner import (
    LLM_DECOMPOSE_MAX_SUBTASKS,
    AgentPlanner,
    SubtaskSpec,
    _default_strategy,
    _parse_llm_plan,
    make_llm_strategy,
)


class _LogCapture:
    """Capture structlog-style calls (structlog bypasses pytest caplog)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def debug(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("debug", args, kwargs))

    def info(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("info", args, kwargs))

    def warning(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("warning", args, kwargs))

    def error(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("error", args, kwargs))

    def exception(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("exception", args, kwargs))


class _StubLLMAdapter:
    """Minimal stand-in for LLMAdapter with one canned reply.

    Accepts the ``config=...`` keyword so it can stand in for the real
    adapter class in the default-strategy resolver.
    """

    def __init__(self, reply: str | Exception = "", **_kwargs: Any) -> None:
        self._reply = reply
        self.prompts: list[str] = []

    async def generate(self, prompt: str, context: dict[str, Any] | None = None) -> str:
        self.prompts.append(prompt)
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


_VALID_REPLY = """[
  {"title": "Research pricing", "description": "Collect list prices for the three competitors.",
   "required_capabilities": ["Research"]},
  {"title": "Write comparison", "description": "Draft the one-page comparison table.",
   "required_capabilities": ["write"]}
]"""


def _failure_reasons(logs: _LogCapture) -> list[dict[str, Any]]:
    return [
        kwargs
        for _, args, kwargs in logs.events
        if args and args[0] == "planner.llm_decompose_failed"
    ]


# --- Strict parse+validate --- #


def test_parse_maps_valid_plan() -> None:
    specs = _parse_llm_plan(_VALID_REPLY, max_subtasks=LLM_DECOMPOSE_MAX_SUBTASKS)
    assert specs is not None
    assert [s.title for s in specs] == ["Research pricing", "Write comparison"]
    # required_capabilities pass through the existing lowercase normalization.
    assert specs[0].required_capabilities == ["research"]
    assert specs[1].required_capabilities == ["write"]
    assert specs[0].depends_on == []


def test_parse_tolerates_single_code_fence() -> None:
    fenced = f"```json\n{_VALID_REPLY}\n```"
    specs = _parse_llm_plan(fenced, max_subtasks=LLM_DECOMPOSE_MAX_SUBTASKS)
    assert specs is not None and len(specs) == 2


def test_parse_accepts_single_string_capability() -> None:
    reply = '[{"title": "T", "description": "D", "required_capabilities": "test"}]'
    specs = _parse_llm_plan(reply, max_subtasks=2)
    assert specs is not None and specs[0].required_capabilities == ["test"]


def test_parse_rejects_malformed_and_prose_replies() -> None:
    assert _parse_llm_plan("not json at all", max_subtasks=6) is None
    assert _parse_llm_plan("Sure! Here is the plan: []", max_subtasks=6) is None
    assert _parse_llm_plan("   \n", max_subtasks=6) is None


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "object not array"},
        [],
        ["a plain string"],
        [{"title": "T", "description": "D", "extra": 1}],
        [{"description": "missing title"}],
        [{"title": "   "}],
        [{"title": "x" * 201}],
        [{"title": "T", "description": 42}],
        [{"title": "T", "description": "d" * 2001}],
        [{"title": "T", "required_capabilities": [1, 2]}],
        [{"title": "T", "required_capabilities": {"a": 1}}],
    ],
)
def test_parse_rejects_schema_and_bound_violations(payload: Any) -> None:
    reply = json.dumps(payload)
    assert _parse_llm_plan(reply, max_subtasks=LLM_DECOMPOSE_MAX_SUBTASKS) is None


def test_parse_enforces_count_cap() -> None:
    item = {"title": "T", "description": "D", "required_capabilities": []}
    over_cap = json.dumps([item] * (LLM_DECOMPOSE_MAX_SUBTASKS + 1))
    assert _parse_llm_plan(over_cap, max_subtasks=LLM_DECOMPOSE_MAX_SUBTASKS) is None
    at_cap = json.dumps([item] * LLM_DECOMPOSE_MAX_SUBTASKS)
    specs = _parse_llm_plan(at_cap, max_subtasks=LLM_DECOMPOSE_MAX_SUBTASKS)
    assert specs is not None and len(specs) == LLM_DECOMPOSE_MAX_SUBTASKS


# --- Strategy behavior (stubbed adapter, no network) --- #


def test_strategy_maps_valid_output() -> None:
    stub = _StubLLMAdapter(_VALID_REPLY)
    strategy = make_llm_strategy(stub)
    specs = strategy("Research the pricing of three competitor products")
    assert [s.title for s in specs] == ["Research pricing", "Write comparison"]
    # Prompt hygiene: the task text goes out; tool/memory definitions do not.
    assert len(stub.prompts) == 1
    assert "Research the pricing of three competitor products" in stub.prompts[0]
    assert "at most 6" in stub.prompts[0]
    assert "memory_query" not in stub.prompts[0]


def test_strategy_falls_back_on_garbage_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    logs = _LogCapture()
    monkeypatch.setattr("agency.agents.planner.logger", logs)
    task = "Step 1: inspect. Step 2: report."
    strategy = make_llm_strategy(_StubLLMAdapter("this is not json"))
    specs = strategy(task)
    assert [s.title for s in specs] == [s.title for s in _default_strategy(task)]
    assert any(k.get("reason") == "invalid_llm_output" for k in _failure_reasons(logs))


def test_strategy_falls_back_on_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    logs = _LogCapture()
    monkeypatch.setattr("agency.agents.planner.logger", logs)
    task = "Step 1: inspect. Step 2: report."
    strategy = make_llm_strategy(_StubLLMAdapter(RuntimeError("provider down")))
    specs = strategy(task)
    assert [s.title for s in specs] == [s.title for s in _default_strategy(task)]
    assert any(
        k.get("reason") == "provider_error" and k.get("error_class") == "RuntimeError"
        for k in _failure_reasons(logs)
    )


def test_strategy_falls_back_on_over_cap_reply() -> None:
    item = '{"title": "T", "description": "D"}'
    reply = "[" + ", ".join([item] * (LLM_DECOMPOSE_MAX_SUBTASKS + 1)) + "]"
    strategy = make_llm_strategy(_StubLLMAdapter(reply))
    specs = strategy("Write a report")
    assert [s.title for s in specs] == [s.title for s in _default_strategy("Write a report")]


def test_strategy_uses_custom_fallback() -> None:
    calls: list[str] = []

    def fallback(task: str) -> list[SubtaskSpec]:
        calls.append(task)
        return [SubtaskSpec(title="floor")]

    strategy = make_llm_strategy(_StubLLMAdapter("garbage"), fallback=fallback)
    assert [s.title for s in strategy("do the thing")] == ["floor"]
    assert calls == ["do the thing"]


def test_strategy_works_inside_running_event_loop() -> None:
    """The sync seam bridges to a helper thread when a loop is already running."""
    strategy = make_llm_strategy(_StubLLMAdapter(_VALID_REPLY))

    async def call() -> list[SubtaskSpec]:
        return strategy("Research pricing then write the comparison")

    specs = asyncio.run(call())
    assert [s.title for s in specs] == ["Research pricing", "Write comparison"]


# --- Default strategy selection --- #


def test_default_selection_is_regex_under_echo(test_runtime_registry: Any) -> None:
    """Hermetic default (conftest pins AGENCY_LLM_PROVIDER=echo): regex floor."""
    planner = AgentPlanner(test_runtime_registry)
    assert planner._strategy is _default_strategy


def test_default_selection_uses_llm_for_live_provider(
    test_runtime_registry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live provider selects the LLM strategy; selection makes no network call."""
    monkeypatch.setenv("AGENCY_LLM_PROVIDER", "openai")
    monkeypatch.setenv("AGENCY_LLM_MODEL", "stub-model")
    monkeypatch.setenv("AGENCY_LLM_API_KEY", "stub-key")
    monkeypatch.setenv("AGENCY_LLM_BASE_URL", "https://stub.invalid/v1")
    stub = _StubLLMAdapter(_VALID_REPLY)
    monkeypatch.setattr("agency.llm.adapter.LLMAdapter", lambda config=None, **kwargs: stub)

    planner = AgentPlanner(test_runtime_registry)
    assert planner._strategy is not _default_strategy

    graph = planner.plan("Research the pricing of three competitor products and write a comparison")
    assert [s.title for s in graph.subtasks] == ["Research pricing", "Write comparison"]
    # Linear dependency chaining still applies to LLM-produced plans.
    assert graph.subtasks[0].depends_on == []
    assert graph.subtasks[1].depends_on == [graph.subtasks[0].subtask_id]
    assert stub.prompts  # the LLM was actually consulted through the stub


def test_explicit_strategy_still_wins(test_runtime_registry: Any) -> None:
    def custom(task: str) -> list[SubtaskSpec]:
        return [SubtaskSpec(title=f"custom: {task}")]

    planner = AgentPlanner(test_runtime_registry, strategy=custom)
    assert planner._strategy is custom
    graph = planner.plan("anything")
    assert [s.title for s in graph.subtasks] == ["custom: anything"]


def test_live_selection_falls_back_when_llm_fails(
    test_runtime_registry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a live provider configured, garbage LLM output still yields a plan."""
    monkeypatch.setenv("AGENCY_LLM_PROVIDER", "openai")
    monkeypatch.setenv("AGENCY_LLM_MODEL", "stub-model")
    monkeypatch.setenv("AGENCY_LLM_API_KEY", "stub-key")
    monkeypatch.setenv("AGENCY_LLM_BASE_URL", "https://stub.invalid/v1")
    logs = _LogCapture()
    monkeypatch.setattr("agency.agents.planner.logger", logs)
    monkeypatch.setattr(
        "agency.llm.adapter.LLMAdapter",
        lambda config=None, **kwargs: _StubLLMAdapter("garbage"),
    )

    planner = AgentPlanner(test_runtime_registry)
    graph = planner.plan("Fix the failing import-order test in module X")
    assert graph.subtasks  # regex floor, never an empty plan
    assert any(k.get("reason") == "invalid_llm_output" for k in _failure_reasons(logs))
