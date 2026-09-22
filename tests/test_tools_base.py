"""Tests for tool core types + ToolRegistry (brief 1)."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from pydantic import ValidationError

from agency.tools import (
    ToolContext,
    ToolError,
    ToolRegistry,
    ToolResult,
    ToolRisk,
    ToolSpec,
    build_default_registry,
)
from agency.tools.registry import _validate_args


def make_ctx(**kwargs: Any) -> ToolContext:
    base: dict[str, Any] = {"agent_id": "agent-1", "task_id": "task-1"}
    base.update(kwargs)
    return ToolContext(**base)


class EchoTool:
    """Minimal valid tool used across tests."""

    def __init__(self, spec: ToolSpec | None = None) -> None:
        self.spec = spec or ToolSpec(
            name="echo",
            description="Echo args back.",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(tool=self.spec.name, ok=True, output=args["text"])


class FakeAudit:
    """Async audit recorder with the kwargs-style append interface."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def append(
        self,
        agent: str | None = None,
        action: str | None = None,
        result: str | None = None,
        target: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> str:
        self.entries.append(
            {
                "agent": agent,
                "action": action,
                "result": result,
                "target": target,
                "evidence": evidence or {},
            }
        )
        return str(len(self.entries))


# --- ToolSpec --- #


def test_spec_good_name_accepted() -> None:
    spec = ToolSpec(name="web_search", description="Search the web.")
    assert spec.name == "web_search"
    assert spec.risk is ToolRisk.READ_ONLY
    assert spec.parameters == {}


def test_spec_bad_name_rejected() -> None:
    with pytest.raises(ValueError):
        ToolSpec(name="", description="empty")
    with pytest.raises(ValueError):
        ToolSpec(name="Bad-Name", description="uppercase + dash")
    with pytest.raises(ValueError):
        ToolSpec(name="1abc", description="leading digit")


def test_spec_frozen() -> None:
    spec = ToolSpec(name="echo", description="Echo.")
    with pytest.raises(FrozenInstanceError):
        spec.name = "other"  # type: ignore[misc]


# --- ToolResult --- #


def test_result_defaults() -> None:
    result = ToolResult(tool="echo", ok=True)
    assert result.output is None
    assert result.error is None
    assert result.duration_ms == 0
    assert result.evidence == {}


def test_result_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        ToolResult(tool="echo", ok=True, bogus_field=1)  # type: ignore[call-arg]


# --- Registry register/get/list --- #


def test_register_get_list_specs() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    spec_b = ToolSpec(name="aaa_tool", description="A.")
    tool_b = EchoTool(spec=spec_b)
    registry.register(tool_b)
    assert registry.get("echo").spec.name == "echo"
    names = [s.name for s in registry.list_specs()]
    assert names == sorted(names) == ["aaa_tool", "echo"]


def test_register_duplicate_raises() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    with pytest.raises(ToolError):
        registry.register(EchoTool())


def test_get_unknown_raises_with_tool_attr() -> None:
    registry = ToolRegistry()
    with pytest.raises(ToolError) as exc_info:
        registry.get("nope")
    assert exc_info.value.tool == "nope"


# --- call(): unknown + schema violations --- #


async def test_call_unknown_tool() -> None:
    registry = ToolRegistry()
    result = await registry.call("missing", {}, make_ctx())
    assert result.ok is False
    assert "unknown tool: missing" in (result.error or "")


async def test_call_missing_required() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    result = await registry.call("echo", {}, make_ctx())
    assert result.ok is False
    assert "required" in (result.error or "").lower()


async def test_call_wrong_type() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    result = await registry.call("echo", {"text": 123}, make_ctx())
    assert result.ok is False
    assert "string" in (result.error or "").lower()


async def test_call_unknown_extra_property() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    result = await registry.call("echo", {"text": "hi", "zzz": 1}, make_ctx())
    assert result.ok is False
    assert "zzz" in (result.error or "")


def test_validate_args_unit() -> None:
    spec = ToolSpec(
        name="t",
        description="t",
        parameters={
            "type": "object",
            "properties": {"n": {"type": "integer"}},
            "required": ["n"],
            "additionalProperties": False,
        },
    )
    assert _validate_args(spec, {"n": 1}) is None
    assert _validate_args(spec, {}) is not None
    assert _validate_args(spec, {"n": True}) is not None  # bool is not integer
    assert _validate_args(spec, {"n": 1, "extra": 2}) is not None


# --- call(): success / raise / timeout --- #


async def test_call_success_path() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    result = await registry.call("echo", {"text": "hello"}, make_ctx())
    assert result.ok is True
    assert result.output == "hello"
    assert result.duration_ms >= 0


async def test_call_tool_raises() -> None:
    class Boom:
        spec = ToolSpec(name="boom", description="Boom.")

        async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
            raise RuntimeError("kaput")

    registry = ToolRegistry()
    registry.register(Boom())
    result = await registry.call("boom", {}, make_ctx())
    assert result.ok is False
    assert "RuntimeError" in (result.error or "")
    assert "kaput" in (result.error or "")


async def test_call_timeout() -> None:
    class Slow:
        spec = ToolSpec(name="slow", description="Slow.")

        async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
            await asyncio.sleep(5)
            return ToolResult(tool="slow", ok=True, output="late")

    registry = ToolRegistry(default_timeout_s=0.05)
    registry.register(Slow())
    result = await registry.call("slow", {}, make_ctx())
    assert result.ok is False
    assert "timeout" in (result.error or "").lower()


async def test_set_timeout_override_respected() -> None:
    class Slow:
        spec = ToolSpec(name="slow2", description="Slow.")

        async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
            await asyncio.sleep(0.3)
            return ToolResult(tool="slow2", ok=True, output="done")

    registry = ToolRegistry(default_timeout_s=30.0)
    registry.register(Slow())
    registry.set_timeout("slow2", 0.05)
    timed_out = await registry.call("slow2", {}, make_ctx())
    assert timed_out.ok is False
    assert "timeout" in (timed_out.error or "").lower()

    registry.set_timeout("slow2", 10.0)
    succeeded = await registry.call("slow2", {}, make_ctx())
    assert succeeded.ok is True
    assert succeeded.output == "done"


def test_set_timeout_unknown_raises() -> None:
    registry = ToolRegistry()
    with pytest.raises(ToolError):
        registry.set_timeout("ghost", 1.0)


# --- audit --- #


async def test_audit_receives_entries() -> None:
    audit = FakeAudit()
    registry = ToolRegistry(audit=audit)
    registry.register(EchoTool())
    ctx = make_ctx()
    ok_result = await registry.call("echo", {"text": "hi"}, ctx)
    assert ok_result.ok is True
    fail_result = await registry.call("echo", {}, ctx)
    assert fail_result.ok is False
    assert len(audit.entries) == 2
    assert audit.entries[0]["action"] == "tool.call"
    assert audit.entries[0]["agent"] == "agent-1"
    assert audit.entries[1]["action"] == "tool.error"
    assert "error" in audit.entries[1]["evidence"]


def test_build_default_registry_empty() -> None:
    registry = build_default_registry()
    assert isinstance(registry, ToolRegistry)
    assert registry.list_specs() == []


def test_build_default_registry_with_tools() -> None:
    registry = build_default_registry(tools=[EchoTool()])
    assert [s.name for s in registry.list_specs()] == ["echo"]
