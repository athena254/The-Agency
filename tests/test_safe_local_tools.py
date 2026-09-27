"""Focused regressions for the two opt-in local read-only tools.

Covers ``calculator`` (bounded AST arithmetic; never ``eval``/``exec``) and
``utc_time`` (constructor-injected timezone-aware clock): correct results,
schema validation, hostile expressions, length/depth/magnitude bounds, no
network or filesystem access, ``register_all -> ToolRegistry.call``
usability, and ``BetaToolPolicy`` denial even for a trusted principal.
Fully offline: no live DNS/provider, no canonical runtime DB.
"""

from __future__ import annotations

import builtins
import inspect
import os
import socket
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from agency.orchestrator import AgencyOrchestrator
from agency.tools.base import BetaPrincipal, BetaToolPolicy, ToolContext, ToolRisk
from agency.tools.builtin import (
    register_all,
    register_beta_search_only,
    register_opt_in_local_tools,
)
from agency.tools.builtin import utc_time as utc_time_module
from agency.tools.builtin.calculator import CalculatorTool
from agency.tools.builtin.utc_time import UtcTimeTool
from agency.tools.driver import ToolDriver
from agency.tools.registry import ToolRegistry

_LEGACY_TOOLS = ("memory_query", "memory_write", "sandbox_exec", "web_fetch", "web_search")
_NEW_TOOLS = ("calculator", "utc_time")

# Fixed error vocabulary: callers get a stable message, never their input.
_CALC_ERRORS = frozenset(
    {
        "calculator: invalid request",
        "calculator: expression too long",
        "calculator: expression too complex",
        "calculator: unsupported syntax",
        "calculator: operand out of range",
        "calculator: division by zero",
        "calculator: result out of range",
        "calculator: result is not finite",
    }
)

# Hostile/unsupported inputs: calls, names, attributes, other operators,
# other literals, non-finite magnitudes, and unbounded complexity.
_HOSTILE_EXPRESSIONS = (
    "__import__('os').system('ls')",
    "open('/etc/passwd')",
    "abs(-1)",
    "eval('1+1')",
    "exec('x=1')",
    "(1).__class__",
    "1.real",
    "x + 1",
    "1;2",
    "1 and 2",
    "1 if True else 2",
    "lambda: 1",
    "[1, 2]",
    "{'a': 1}",
    "(1, 2)",
    "set()",
    "'1' + '2'",
    "True",
    "None",
    "1 & 2",
    "1 | 2",
    "1 << 70",
    "1 >> 2",
    "1 @ 2",
    "2**10",
    "10**10**10",
    "2 ** 3",
    "1j",
    "0x10",
    "0b11",
    "1e",
    "1+",
    "*2",
    "()",
    "   ",
    "$",
    "1e400",
    "1e308*1e308",
    "1e15*1e15",
    "1000000000000000000",
    "1" + "+1" * 59,
    "(" * 12 + "1" + ")" * 12,
    "-" * 13 + "1",
    "0." + "1" * 119,
)


def _ctx(**kwargs: Any) -> ToolContext:
    base: dict[str, Any] = {"agent_id": "agent-1", "task_id": "task-1"}
    base.update(kwargs)
    return ToolContext(**base)


def _trusted_ctx(**kwargs: Any) -> ToolContext:
    return _ctx(beta_principal=BetaPrincipal(telegram_user_id=12345, private_chat=True), **kwargs)


def _clock(moment: datetime) -> tuple[Callable[[], datetime], list[None]]:
    """Return an injected clock plus a call counter."""
    calls: list[None] = []

    def _now() -> datetime:
        calls.append(None)
        return moment

    return _now, calls


def _fixed_moment() -> datetime:
    return datetime(2026, 1, 1, 5, 30, 15, tzinfo=timezone(timedelta(hours=5)))


def _blocked(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("network or filesystem access attempted")


# --------------------------------------------------------------------- #
# calculator: correct arithmetic
# --------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2+2", 4),
        ("10-4", 6),
        ("6*7", 42),
        ("9/2", 4.5),
        ("1/4", 0.25),
        ("-5+2", -3),
        ("+5", 5),
        ("-(3+4)", -7),
        ("2--3", 5),
        ("(2+3)*(4-1)", 15),
        ("  7 + 1  ", 8),
        ("1.5*2", 3.0),
        ("0.1+0.2", 0.3),
        ("2+3*4", 14),
        ("(2+3)*4", 20),
        ("10-2-3", 5),
        ("100/10/2", 5.0),
        ("((((1+1))))", 2),
        ("1" + "+1" * 9, 10),
    ],
)
async def test_calculator_evaluates_bounded_arithmetic(expression: str, expected: float) -> None:
    tool = CalculatorTool()
    result = await tool.run({"expression": expression}, _ctx())
    assert result.ok is True, result.error
    assert result.output["result"] == pytest.approx(expected)
    assert result.output["expression"] == expression
    assert result.evidence["expression_chars"] == len(expression)


async def test_calculator_result_is_numeric_and_evidence_carries_no_input() -> None:
    tool = CalculatorTool()
    result = await tool.run({"expression": "6*7"}, _ctx())
    assert result.ok is True
    assert isinstance(result.output["result"], (int, float))
    assert result.error is None


async def test_calculator_division_by_zero_is_fixed_error() -> None:
    tool = CalculatorTool()
    for expression in ("1/0", "0/0", "1/(2-2)"):
        result = await tool.run({"expression": expression}, _ctx())
        assert result.ok is False
        assert result.error == "calculator: division by zero"
        assert expression not in (result.error or "")


# --------------------------------------------------------------------- #
# calculator: hostile input, bounds, no leak
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("expression", _HOSTILE_EXPRESSIONS)
async def test_calculator_rejects_hostile_expressions(expression: str) -> None:
    tool = CalculatorTool()
    result = await tool.run({"expression": expression}, _ctx())
    assert result.ok is False
    assert result.error in _CALC_ERRORS
    if expression.strip():
        assert expression.strip() not in (result.error or "")


async def test_calculator_bounds_are_inclusive_at_the_documented_limit() -> None:
    """A 120-character expression is allowed; 121 is not (spec: <= 120)."""
    tool = CalculatorTool()
    allowed = "0." + "1" * 118
    assert len(allowed) == 120
    ok_result = await tool.run({"expression": allowed}, _ctx())
    assert ok_result.ok is True, ok_result.error

    rejected = "0." + "1" * 119
    assert len(rejected) == 121
    long_result = await tool.run({"expression": rejected}, _ctx())
    assert long_result.ok is False
    assert long_result.error == "calculator: expression too long"


async def test_calculator_rejects_excessive_nodes_independently_of_length() -> None:
    tool = CalculatorTool()
    # Under the 120-char limit but far too many AST nodes.
    result = await tool.run({"expression": "1" + "+1" * 59}, _ctx())
    assert result.ok is False
    assert result.error == "calculator: expression too complex"


async def test_calculator_rejects_excessive_depth() -> None:
    tool = CalculatorTool()
    ok_nested = "(" * 11 + "1" + ")" * 11
    ok_result = await tool.run({"expression": ok_nested}, _ctx())
    assert ok_result.ok is True, ok_result.error
    too_deep = "(" * 12 + "1" + ")" * 12
    deep_result = await tool.run({"expression": too_deep}, _ctx())
    assert deep_result.ok is False
    assert deep_result.error == "calculator: expression too complex"


async def test_calculator_rejects_huge_literals_and_results() -> None:
    tool = CalculatorTool()
    at_limit = await tool.run({"expression": "1000000000000000"}, _ctx())
    assert at_limit.ok is True, at_limit.error
    assert at_limit.output["result"] == 1e15
    huge_literal = await tool.run({"expression": "1000000000000000000"}, _ctx())
    assert huge_literal.ok is False
    assert huge_literal.error == "calculator: operand out of range"
    huge_product = await tool.run({"expression": "1e15*1e15"}, _ctx())
    assert huge_product.ok is False
    assert huge_product.error == "calculator: result out of range"
    overflow = await tool.run({"expression": "1e308*1e308"}, _ctx())
    assert overflow.ok is False
    assert overflow.error == "calculator: operand out of range"


@pytest.mark.parametrize(
    "args",
    [{}, {"expression": ""}, {"expression": "   "}, {"expression": 7}, {"expression": None}],
)
async def test_calculator_requires_one_string_expression(args: dict[str, Any]) -> None:
    tool = CalculatorTool()
    result = await tool.run(dict(args), _ctx())
    assert result.ok is False
    assert result.error == "calculator: invalid request"


async def test_calculator_never_echoes_or_logs_hostile_input() -> None:
    """Errors and structlog events must not contain the raw expression."""
    marker = "secret-marker-X7B9z"
    tool = CalculatorTool()
    with capture_logs() as captured:
        result = await tool.run(
            {"expression": f"__import__('{marker}').system('{marker}')"}, _ctx()
        )
    assert result.ok is False
    assert result.error in _CALC_ERRORS
    assert marker not in (result.error or "")
    assert marker not in repr(captured)


async def test_calculator_source_has_no_eval_or_exec() -> None:
    source = inspect.getsource(inspect.getmodule(CalculatorTool) or CalculatorTool)
    assert "eval(" not in source
    assert "exec(" not in source


# --------------------------------------------------------------------- #
# utc_time
# --------------------------------------------------------------------- #


async def test_utc_time_returns_utc_iso_from_injected_clock() -> None:
    clock, _ = _clock(_fixed_moment())
    tool = UtcTimeTool(clock=clock)
    result = await tool.run({}, _ctx())
    assert result.ok is True, result.error
    assert result.output["utc"] == "2026-01-01T00:30:15+00:00"
    parsed = datetime.fromisoformat(result.output["utc"])
    assert parsed.utcoffset() == timedelta(0)
    assert result.output["tz"] == "UTC"
    assert "CEST" not in result.output["utc"]


async def test_utc_time_never_uses_a_caller_supplied_timezone() -> None:
    clock, calls = _clock(_fixed_moment())
    registry = ToolRegistry()
    registry.register(UtcTimeTool(clock=clock))
    result = await registry.call("utc_time", {"timezone": "Europe/Berlin"}, _ctx())
    assert result.ok is False
    assert "invalid args" in (result.error or "").lower()
    assert calls == []


async def test_utc_time_rejects_every_argument() -> None:
    clock, calls = _clock(_fixed_moment())
    registry = ToolRegistry()
    registry.register(UtcTimeTool(clock=clock))
    for args in ({"tz": "UTC"}, {"now": True}, {"format": "%Y"}, {"": None}):
        result = await registry.call("utc_time", args, _ctx())
        assert result.ok is False
        assert "invalid args" in (result.error or "").lower()
    assert calls == []


async def test_utc_time_rejects_naive_injected_clock() -> None:
    clock, _ = _clock(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC).replace(tzinfo=None))
    tool = UtcTimeTool(clock=clock)
    result = await tool.run({}, _ctx())
    assert result.ok is False
    assert result.error == "utc_time: clock unavailable"


async def test_utc_time_clock_failure_is_a_fixed_error() -> None:
    marker = "synthetic-clock-secret"

    def _boom() -> datetime:
        raise RuntimeError(marker)

    tool = UtcTimeTool(clock=_boom)
    result = await tool.run({}, _ctx())
    assert result.ok is False
    assert result.error == "utc_time: clock unavailable"
    assert marker not in (result.error or "")

    tool_bad_type = UtcTimeTool(clock=lambda: "2026-01-01T00:00:00Z")  # type: ignore[arg-type,return-value]
    bad_type = await tool_bad_type.run({}, _ctx())
    assert bad_type.ok is False
    assert bad_type.error == "utc_time: clock unavailable"


async def test_utc_time_module_has_no_timezone_database_or_path_input() -> None:
    """A caller-controlled timezone/file path must be structurally impossible."""
    source = inspect.getsource(utc_time_module)
    for forbidden in ("zoneinfo", "tzset", "open(", "environ", "astimezone(naive"):
        assert forbidden not in source.replace("dt.astimezone(timezone.utc)", "")


# --------------------------------------------------------------------- #
# nonbeta registration usability
# --------------------------------------------------------------------- #


async def test_register_all_preserves_legacy_tools_without_new_tools() -> None:
    registry = ToolRegistry()
    tools = register_all(registry, transport=httpx.MockTransport(_blocked))
    names = {spec.name for spec in registry.list_specs()}
    assert set(_LEGACY_TOOLS) == names
    assert not set(_NEW_TOOLS) & names
    assert {tool.spec.name for tool in tools} == names
    assert len(tools) == 5
    extra = register_opt_in_local_tools(registry)
    assert {tool.spec.name for tool in extra} == set(_NEW_TOOLS)


async def test_beta_constructor_advertises_only_search_and_denies_local_tools() -> None:
    registry = ToolRegistry(beta_policy=BetaToolPolicy())
    register_beta_search_only(registry, transport=httpx.MockTransport(_blocked))
    assert {spec.name for spec in registry.list_specs()} == {"web_search"}
    prompt = ToolDriver(registry, llm=None)._build_prompt(
        "test", "system", registry.list_specs(), []
    )
    assert '"name": "web_search"' in prompt
    assert '"name": "calculator"' not in prompt
    assert '"name": "utc_time"' not in prompt
    for name in _NEW_TOOLS:
        assert (await registry.call(name, {}, _trusted_ctx())).ok is False


async def test_orchestrator_legacy_open_registry_does_not_advertise_new_tools() -> None:
    orchestrator = AgencyOrchestrator(memory_db_path=":memory:")
    orchestrator._build_tool_layer()
    registry = orchestrator._tool_registry
    driver = orchestrator._tool_driver
    assert registry is not None and driver is not None
    names = {spec.name for spec in registry.list_specs()}
    assert names == set(_LEGACY_TOOLS)
    prompt = driver._build_prompt("test", "system", registry.list_specs(), [])
    for name in _NEW_TOOLS:
        assert name not in prompt
        assert (await registry.call(name, {}, _ctx())).ok is False


async def test_register_all_to_registry_call_end_to_end() -> None:
    registry = ToolRegistry()
    register_all(registry, transport=httpx.MockTransport(_blocked))
    register_opt_in_local_tools(registry)
    ctx = _ctx()
    calc = await registry.call("calculator", {"expression": "6*7"}, ctx)
    assert calc.ok is True, calc.error
    assert calc.output["result"] == 42
    clock = await registry.call("utc_time", {}, ctx)
    assert clock.ok is True, clock.error
    assert clock.output["utc"].endswith("+00:00")


async def test_registered_tools_are_labelled_read_only() -> None:
    registry = ToolRegistry()
    register_all(registry, transport=httpx.MockTransport(_blocked))
    register_opt_in_local_tools(registry)
    for name in _NEW_TOOLS:
        assert registry.get(name).spec.risk is ToolRisk.READ_ONLY
        assert registry.get(name).spec.parameters["additionalProperties"] is False


async def test_registry_rejects_calculator_schema_violations() -> None:
    registry = ToolRegistry()
    register_all(registry, transport=httpx.MockTransport(_blocked))
    register_opt_in_local_tools(registry)
    ctx = _ctx()
    for args in ({}, {"expr": "2+2"}, {"expression": "2+2", "extra": 1}, {"expression": [1]}):
        result = await registry.call("calculator", args, ctx)
        assert result.ok is False
        assert "invalid args" in (result.error or "").lower()


async def test_local_tools_touch_no_network_or_filesystem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Replacing socket.socket breaks the Windows asyncio event-loop self-pipe.
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(os, "open", _blocked)
    monkeypatch.setattr(builtins, "open", _blocked)
    clock, _ = _clock(_fixed_moment())
    registry = ToolRegistry()
    register_all(registry, transport=httpx.MockTransport(_blocked))
    register_opt_in_local_tools(registry)
    registry.get("utc_time")._clock = clock  # type: ignore[attr-defined]
    ctx = _ctx()
    assert (await registry.call("calculator", {"expression": "6*7"}, ctx)).ok is True
    assert (await registry.call("utc_time", {}, ctx)).ok is True


async def test_local_tools_ignore_injected_side_effect_capabilities() -> None:
    """memory/sandbox/lattice/audit seams must never be touched."""

    class _Exploding:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"tool touched ctx.{name}")

    ctx = _ctx(
        memory_store=_Exploding(),
        sandbox_manager=_Exploding(),
        lattice=_Exploding(),
        audit=_Exploding(),
    )
    registry = ToolRegistry()
    register_all(registry, transport=httpx.MockTransport(_blocked))
    register_opt_in_local_tools(registry)
    assert (await registry.call("calculator", {"expression": "2+2"}, ctx)).ok is True
    assert (await registry.call("utc_time", {}, ctx)).ok is True


# --------------------------------------------------------------------- #
# beta boundary: still denied, still not enabled
# --------------------------------------------------------------------- #


async def test_beta_denies_both_tools_even_for_a_trusted_principal() -> None:
    clock, clock_calls = _clock(_fixed_moment())
    registry = ToolRegistry(beta_policy=BetaToolPolicy())
    register_beta_search_only(registry, transport=httpx.MockTransport(_blocked))
    registry.register(CalculatorTool())
    registry.register(UtcTimeTool())
    registry.get("utc_time")._clock = clock  # type: ignore[attr-defined]
    calc_tool = registry.get("calculator")
    ran: list[dict[str, Any]] = []

    async def _must_not_run(args: dict[str, Any], ctx: ToolContext) -> Any:
        ran.append(args)
        raise AssertionError("calculator ran under beta policy")

    calc_tool.run = _must_not_run  # type: ignore[method-assign]

    ctx = _trusted_ctx()
    calc = await registry.call("calculator", {"expression": "6*7"}, ctx)
    clocked = await registry.call("utc_time", {}, ctx)
    for result in (calc, clocked):
        assert result.ok is False
        assert "beta policy denied" in (result.error or "").lower()
        assert "allowlist" in (result.error or "").lower()
    assert ran == []
    assert clock_calls == []


async def test_beta_denies_both_tools_without_identity() -> None:
    clock, clock_calls = _clock(_fixed_moment())
    registry = ToolRegistry(beta_policy=BetaToolPolicy())
    registry.register(CalculatorTool())
    registry.register(UtcTimeTool(clock=clock))
    ctx = _ctx()
    assert (await registry.call("calculator", {"expression": "2+2"}, ctx)).ok is False
    assert (await registry.call("utc_time", {}, ctx)).ok is False
    assert clock_calls == []


async def test_attacker_supplied_allowlist_cannot_widen_beta() -> None:
    """Call-time args/metadata never change the deployed allowlist."""
    registry = ToolRegistry(beta_policy=BetaToolPolicy())
    registry.register(CalculatorTool())
    registry.register(UtcTimeTool())
    forged = _ctx(
        metadata={
            "sender": "telegram:12345",
            "allowed_tools": ["calculator", "utc_time"],
            "beta": True,
        }
    )
    calc = await registry.call("calculator", {"expression": "2+2"}, forged)
    clocked = await registry.call("utc_time", {}, forged)
    assert calc.ok is False
    assert "trusted" in (calc.error or "").lower()
    assert clocked.ok is False
    assert "trusted" in (clocked.error or "").lower()

    # An allowlist smuggled through validated args dies at schema validation.
    smuggled = await registry.call(
        "calculator", {"expression": "2+2", "allowed_tools": ["*"]}, forged
    )
    assert smuggled.ok is False
    assert "invalid args" in (smuggled.error or "").lower()


def test_deployed_beta_policy_still_allowlists_web_search_only() -> None:
    default_allowed = inspect.signature(BetaToolPolicy).parameters["allowed_tools"].default
    assert default_allowed is None
    policy = BetaToolPolicy()
    ctx = _trusted_ctx()
    for name in _NEW_TOOLS:
        spec = CalculatorTool().spec if name == "calculator" else UtcTimeTool().spec
        decision = policy(spec, {}, ctx)
        assert decision.allowed is False
        assert "allowlist" in decision.reason.lower()


async def test_beta_web_search_still_allowed_alongside_denials() -> None:
    """The new tools did not displace the existing bounded beta capability."""
    registry = ToolRegistry(beta_policy=BetaToolPolicy())
    register_beta_search_only(registry, transport=httpx.MockTransport(_blocked))
    denied = await registry.call("utc_time", {}, _trusted_ctx())
    assert denied.ok is False
    verdict = BetaToolPolicy()(
        registry.get("web_search").spec, {"query": "agency beta"}, _trusted_ctx()
    )
    assert verdict.allowed is True
