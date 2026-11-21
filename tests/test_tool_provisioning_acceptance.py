"""Live acceptance: the whole tool-provisioning chain, real Forge gates.

PLAN_TOOL_PROVISIONING §3.7/§5, phase 5. One chain, walked once per module:
a blueprint declares ``tool_requirements`` on a prompt-capable factory; the
provisioner resolves memory_query PROVISIONED (stub registered),
weather_lookup BUILDABLE (real build spec), nonexistent_widget UNKNOWN;
``provision_and_build`` runs the REAL :func:`build_tool` (inspection +
security scan + pytest in the isolated subprocess copy) and registers the
tool on PASS; the final report is honest; the per-agent allowlist admits the
built tool and denies everyone else with the fixed error; the availability
segment names every requirement and never drops the unknown one.

Offline: no network, no live provider; the only execution is the real gate
pipeline that :func:`build_tool` always runs.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.autonomy.forge_auto import ToolBuildResult, build_tool
from agency.factory.compose import compose_system_prompt
from agency.factory.prompts import PromptRegistry
from agency.factory.prompts_builtin import ensure_builtin_prompts
from agency.factory.service import AgentFactory, BlueprintStatus
from agency.kernel.registry import AgentRegistry as KernelRegistry
from agency.tools.availability_params import (
    AVAILABILITY_PROMPT_ID,
    MISSING_BUILD_SLOT,
    PROVISIONED_SLOT,
    tool_availability_params,
)
from agency.tools.base import ToolContext, ToolResult, ToolRisk, ToolSpec
from agency.tools.provision import (
    BUILDABLE,
    PROVISIONED,
    UNKNOWN,
    provision_and_build,
    resolve,
)
from agency.tools.registry import ToolRegistry

_NOT_PROVISIONED = "tool not provisioned for this agent"

REQUIREMENTS = ("memory_query", "weather_lookup", "nonexistent_widget")

WEATHER_CODE = """\
_FORECAST = {"forecast": "sunny", "high_c": 21, "wind_kph": 9}


def run(args, ctx):
    city = str((args or {}).get("city", "")).strip()
    if not city:
        raise ValueError("city is required")
    return {"city": city, "forecast": dict(_FORECAST)}
"""

WEATHER_TESTS = """\
import pytest

import tool


def test_requires_city():
    with pytest.raises(ValueError):
        tool.run({}, None)


def test_returns_fixed_forecast():
    assert tool.run({"city": "Berlin"}, None) == {
        "city": "Berlin",
        "forecast": {"forecast": "sunny", "high_c": 21, "wind_kph": 9},
    }
"""

BUILD_SPECS = {"weather_lookup": {"code": WEATHER_CODE, "tests": WEATHER_TESTS}}


class _MemoryStub:
    """Registry-provided memory_query so the requirement is PROVISIONED."""

    def __init__(self) -> None:
        self.spec = ToolSpec(
            name="memory_query", description="stub memory query", risk=ToolRisk.READ_ONLY
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(tool=self.spec.name, ok=True, output={"hits": []})


class _RecordingAudit:
    """Kwargs-style audit log capturing appended entries."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def append(self, **kwargs: Any) -> None:
        self.entries.append(kwargs)


class _RealBuildSpy:
    """Delegates to the REAL build_tool (real gates), recording each name."""

    def __init__(self) -> None:
        self.names: list[str] = []
        self.results: dict[str, ToolBuildResult] = {}

    def __call__(self, name: str, code: str, tests: str, registry: Any) -> ToolBuildResult:
        self.names.append(name)
        result = build_tool(name, code, tests, registry)
        self.results[name] = result
        return result


@pytest.fixture(scope="module")
def chain() -> dict[str, Any]:
    """Walk the whole chain once; tests assert the individual links."""
    audit = _RecordingAudit()
    prompts = PromptRegistry(":memory:")
    ensure_builtin_prompts(prompts)
    factory = AgentFactory(
        ":memory:",
        kernel_registry=KernelRegistry(),
        runtime_registry=RuntimeRegistry(),
        authorizer=lambda actor, perm: perm == "agent.approve",
        allowed_capabilities={"general"},
        is_prompt_published=prompts.is_published,
        prompt_resolver=prompts,
    )
    try:
        # (a) blueprint declares its domain tool requirements.
        factory.create(
            "scout",
            "1.0.0",
            "Scout",
            capabilities=("general",),
            tool_requirements=("memory_query", "weather_lookup"),
            creator="owner",
        )
        factory.approve(
            "scout", "1.0.0", approver="steward", evidence="acceptance: provisioning chain"
        )
        registry = ToolRegistry(audit=audit)
        registry.register(_MemoryStub())
        spy = _RealBuildSpy()
        initial = resolve(REQUIREMENTS, registry=registry, build_specs=BUILD_SPECS)
        final = provision_and_build(
            REQUIREMENTS, registry=registry, build_specs=BUILD_SPECS, build_tool_fn=spy
        )
        composed = compose_system_prompt(
            [(AVAILABILITY_PROMPT_ID, "1.0.0")],
            resolver=prompts,
            params=tool_availability_params(final),
        )
        yield {
            "blueprint": factory.get("scout", "1.0.0"),
            "registry": registry,
            "audit": audit,
            "spy": spy,
            "initial": initial,
            "final": final,
            "composed": composed,
        }
    finally:
        factory.close()
        prompts.close()


# --------------------------------------------------------------------- #
# (a) blueprint declaration
# --------------------------------------------------------------------- #


def test_a_blueprint_declares_tool_requirements(chain: dict[str, Any]) -> None:
    blueprint = chain["blueprint"]
    assert blueprint.status is BlueprintStatus.APPROVED  # human-approved, activation-ready
    assert blueprint.tool_requirements == ("memory_query", "weather_lookup")


# --------------------------------------------------------------------- #
# (b) honest initial resolution
# --------------------------------------------------------------------- #


def test_b_initial_resolution_is_honest(chain: dict[str, Any]) -> None:
    initial = chain["initial"]
    assert [(item.tool, item.status) for item in initial.items] == [
        ("memory_query", PROVISIONED),
        ("weather_lookup", BUILDABLE),
        ("nonexistent_widget", UNKNOWN),
    ]
    assert initial.items[1].detail == "forge build spec available"


# --------------------------------------------------------------------- #
# (c) real Forge gates build and register the missing tool
# --------------------------------------------------------------------- #


def test_c_real_gates_build_weather_lookup(chain: dict[str, Any]) -> None:
    spy: _RealBuildSpy = chain["spy"]
    assert spy.names == ["weather_lookup"]  # only the BUILDABLE gap was built
    result = spy.results["weather_lookup"]
    assert result.gate == "PASS"
    assert result.registered is True
    assert result.report_id  # gate evidence exists
    assert result.sha256 == hashlib.sha256(WEATHER_CODE.encode("utf-8")).hexdigest()
    registry: ToolRegistry = chain["registry"]
    assert "weather_lookup" in {spec.name for spec in registry.list_specs()}


# --------------------------------------------------------------------- #
# (d) final report reflects post-build reality
# --------------------------------------------------------------------- #


def test_d_final_report_flips_built_tool_to_provisioned(chain: dict[str, Any]) -> None:
    final = chain["final"]
    assert [(item.tool, item.status) for item in final.items] == [
        ("memory_query", PROVISIONED),
        ("weather_lookup", PROVISIONED),
        ("nonexistent_widget", UNKNOWN),
    ]
    assert final.items[1].detail == "registered tool available (source: registry)"


# --------------------------------------------------------------------- #
# (e) per-agent allowlist enforcement, seen from outside
# --------------------------------------------------------------------- #


async def test_e_allowlist_admits_built_tool_and_denies_others(
    chain: dict[str, Any],
) -> None:
    registry: ToolRegistry = chain["registry"]
    allowed = ToolContext(
        agent_id="agent-1",
        task_id="task-1",
        metadata={"allowed_tools": frozenset({"memory_query", "weather_lookup"})},
    )
    ok = await registry.call("weather_lookup", {"city": "Berlin"}, allowed)
    assert ok.ok is True, ok.error
    assert ok.output == {
        "city": "Berlin",
        "forecast": {"forecast": "sunny", "high_c": 21, "wind_kph": 9},
    }
    memory = await registry.call("memory_query", {}, allowed)
    assert memory.ok is True, memory.error

    # A different agent's allowlist without weather_lookup: fixed denial.
    other = ToolContext(
        agent_id="agent-2",
        task_id="task-2",
        metadata={"allowed_tools": frozenset({"memory_query"})},
    )
    denied = await registry.call("weather_lookup", {"city": "Berlin"}, other)
    assert denied.ok is False
    assert denied.error == _NOT_PROVISIONED
    entries = [e for e in chain["audit"].entries if e.get("target") == "weather_lookup"]
    assert entries[-1]["action"] == "tool.error"  # the denial was audited


# --------------------------------------------------------------------- #
# (f) honesty: unknown requirement is never built, never dropped
# --------------------------------------------------------------------- #


def test_f_unknown_requirement_never_built(chain: dict[str, Any]) -> None:
    final = chain["final"]
    spy: _RealBuildSpy = chain["spy"]
    widget = next(item for item in final.items if item.tool == "nonexistent_widget")
    assert widget.status == UNKNOWN
    assert widget.detail == "no registered, catalog, or build-spec source knows this tool"
    assert "nonexistent_widget" not in spy.names
    assert len(spy.names) == 1  # exactly one build was attempted in the chain


# --------------------------------------------------------------------- #
# (g) availability segment names every requirement honestly
# --------------------------------------------------------------------- #


def test_g_availability_segment_reports_final_reality(chain: dict[str, Any]) -> None:
    final = chain["final"]
    composed = chain["composed"]
    params = tool_availability_params(final)
    assert params[PROVISIONED_SLOT] == "memory_query, weather_lookup"
    assert params[MISSING_BUILD_SLOT] == "nonexistent_widget"
    assert "{{" not in composed.text  # no unfilled slots (fail-closed compose)
    assert "Available now: memory_query, weather_lookup" in composed.text
    assert "Missing (must be built): nonexistent_widget" in composed.text
