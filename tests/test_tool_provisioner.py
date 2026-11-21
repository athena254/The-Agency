"""Tests for the ToolProvisioner (Phase 2 of tool provisioning).

The provisioner is a reporter: per declared tool name it produces exactly one
honest status. It never installs, never builds, and never mutates a registry.
Registry and catalog probe are injectable so tests are deterministic.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agency.autonomy.forge_auto import ToolBuildResult
from agency.tools.base import ToolResult, ToolRisk, ToolSpec
from agency.tools.provision import (
    BUILDABLE,
    CATALOG_AVAILABLE,
    MAX_REQUIREMENTS,
    MCP_BRIDGE_UNCONFIGURED,
    NEEDS_INSTALL,
    PROVISIONED,
    UNKNOWN,
    ToolProvisionReport,
    build_missing,
    provision_and_build,
    resolve,
)
from agency.tools.registry import ToolRegistry


def _reg(*names: str) -> SimpleNamespace:
    """A minimal registry stand-in: anything with .list_specs()."""
    return SimpleNamespace(list_specs=lambda: [SimpleNamespace(name=name) for name in names])


def _probe(available: bool):
    calls = []

    def check(spec) -> bool:
        calls.append(spec.name)
        return available

    check.calls = calls  # type: ignore[attr-defined]
    return check


def test_provisioned_when_registered() -> None:
    report = resolve(("memory_query",), registry=_reg("memory_query"))
    assert len(report.items) == 1
    item = report.items[0]
    assert item.tool == "memory_query"
    assert item.status == PROVISIONED
    assert "registry" in item.detail


def test_provisioned_takes_precedence_over_catalog() -> None:
    # "ghidra" is a real catalog name; registry presence must win.
    probe = _probe(False)
    report = resolve(("ghidra",), registry=_reg("ghidra"), catalog_probe=probe)
    assert report.items[0].status == PROVISIONED
    assert probe.calls == []  # catalog never consulted


def test_catalog_available_when_probe_true() -> None:
    report = resolve(("ghidra",), catalog_probe=_probe(True))
    assert report.items[0].status == CATALOG_AVAILABLE


def test_needs_install_carries_install_hint() -> None:
    report = resolve(("ghidra",), catalog_probe=_probe(False))
    item = report.items[0]
    assert item.status == NEEDS_INSTALL
    assert item.detail == "Download from ghidra-sre.org; set GHIDRA_HOME"


def test_mcp_bridge_entry_is_not_needs_install() -> None:
    probe = _probe(False)
    report = resolve(("ghidramcp",), catalog_probe=probe)
    item = report.items[0]
    assert item.status == MCP_BRIDGE_UNCONFIGURED
    assert item.detail == "MCP server entry; bridging lands in Phase 4b"
    assert probe.calls == []  # bridge entries cannot be probed


def test_buildable_with_build_spec() -> None:
    report = resolve(
        ("my_tool",),
        registry=_reg("memory_query"),
        build_specs={"my_tool": {"code": "def run(): ...", "tests": "..."}},
    )
    item = report.items[0]
    assert item.status == BUILDABLE
    assert item.detail == "forge build spec available"


def test_buildable_only_when_registry_and_catalog_do_not_know_the_name() -> None:
    specs = {"ghidra": {"code": "...", "tests": "..."}}
    probe = _probe(True)
    report = resolve(("ghidra",), build_specs=specs, catalog_probe=probe)
    assert report.items[0].status == CATALOG_AVAILABLE
    report = resolve(("ghidra",), registry=_reg("ghidra"), build_specs=specs)
    assert report.items[0].status == PROVISIONED


def test_unknown_fails_closed() -> None:
    report = resolve(("nosuchtool",), registry=_reg("memory_query"))
    item = report.items[0]
    assert item.status == UNKNOWN
    assert item.detail == "no registered, catalog, or build-spec source knows this tool"


def test_statuses_follow_requirement_order() -> None:
    report = resolve(
        ("memory_query", "ghidra", "ghidramcp", "nosuchtool"),
        registry=_reg("memory_query"),
        catalog_probe=_probe(False),
    )
    assert [item.status for item in report.items] == [
        PROVISIONED,
        NEEDS_INSTALL,
        MCP_BRIDGE_UNCONFIGURED,
        UNKNOWN,
    ]
    assert [item.tool for item in report.items] == [
        "memory_query",
        "ghidra",
        "ghidramcp",
        "nosuchtool",
    ]


def test_duplicate_requirements_collapse_to_one_item() -> None:
    report = resolve(("ghidra", "ghidra"), catalog_probe=_probe(True))
    assert [item.tool for item in report.items] == ["ghidra"]


def test_empty_requirements_yield_empty_report() -> None:
    report = resolve(())
    assert report.items == ()


def test_more_than_64_requirements_rejected() -> None:
    too_many = tuple(f"tool_{i}" for i in range(MAX_REQUIREMENTS + 1))
    with pytest.raises(ValueError):
        resolve(too_many)
    ok = tuple(f"tool_{i}" for i in range(MAX_REQUIREMENTS))
    assert len(resolve(ok).items) == MAX_REQUIREMENTS


def test_non_string_and_blank_entries_fail_closed() -> None:
    with pytest.raises(TypeError):
        resolve(("ghidra", 42))  # type: ignore[list-item]
    with pytest.raises(ValueError):
        resolve(("ghidra", "  "))


def test_report_carries_id_and_timestamp() -> None:
    report = resolve(("ghidra",), catalog_probe=_probe(True))
    assert uuid.UUID(report.report_id)  # parses as a UUID
    parsed = datetime.fromisoformat(report.created_at)
    assert parsed.tzinfo is not None  # UTC-aware ISO timestamp


def test_report_is_frozen() -> None:
    report = resolve(("ghidra",), catalog_probe=_probe(True))
    with pytest.raises(ValidationError):
        report.items = ()  # type: ignore[misc]


def test_default_catalog_probe_reports_without_raising() -> None:
    # Real PATH probing: the honest outcome is one of the two catalog states.
    report = resolve(("ghidra",))
    assert report.items[0].status in {CATALOG_AVAILABLE, NEEDS_INSTALL}


# --------------------------------------------------------------------- #
# build_missing + provision_and_build (Phase 5 wiring; hermetic builders)
# --------------------------------------------------------------------- #


class _StubTool:
    """Minimal registered tool: enough for list_specs and a call."""

    def __init__(self, name: str) -> None:
        self.spec = ToolSpec(name=name, description="stub", risk=ToolRisk.READ_ONLY)

    async def run(self, args: dict, ctx: object) -> ToolResult:
        return ToolResult(tool=self.spec.name, ok=True, output={})


def _fake_builder(
    outcomes: dict[str, ToolBuildResult],
    calls: list[str],
    register_on_pass: bool = True,
):
    """Injectable build_tool_fn: records names, registers only on success."""

    def build(name: str, code: str, tests: str, registry: object) -> ToolBuildResult:
        calls.append(name)
        if registry is not None and outcomes[name].registered and register_on_pass:
            registry.register(_StubTool(name))
        return outcomes[name]

    return build


def _mixed_report() -> ToolProvisionReport:
    """PROVISIONED + BUILDABLE + UNKNOWN in one report (injectable probe)."""
    return resolve(
        ("known", "buildable", "nosuchtool"),
        registry=_reg("known"),
        build_specs={"buildable": {"code": "def run(): ...", "tests": "..."}},
    )


def test_build_missing_builds_only_buildable_items() -> None:
    calls: list[str] = []
    outcome = ToolBuildResult(
        name="buildable", gate="PASS", sha256="0" * 64, registered=True, detail="registered"
    )
    registry = ToolRegistry()
    registry.register(_StubTool("known"))
    results = build_missing(
        _mixed_report(),
        build_specs={"buildable": {"code": "c", "tests": "t"}},
        registry=registry,
        build_tool_fn=_fake_builder({"buildable": outcome}, calls),
    )
    assert calls == ["buildable"]  # never called for PROVISIONED or UNKNOWN
    assert set(results) == {"buildable"}
    assert results["buildable"].gate == "PASS"
    assert {spec.name for spec in registry.list_specs()} == {"known", "buildable"}


def test_build_missing_skips_buildable_without_spec() -> None:
    calls: list[str] = []
    report = resolve(("gap",), registry=_reg(), build_specs={"gap": {"code": "c", "tests": "t"}})
    assert report.items[0].status == BUILDABLE  # precondition: it IS buildable
    # A BUILDABLE item whose spec is absent from the mapping handed to
    # build_missing cannot be built: honest no-op, nothing registered.
    results = build_missing(
        report,
        build_specs={"other": {"code": "c", "tests": "t"}},
        registry=ToolRegistry(),
        build_tool_fn=_fake_builder({}, calls),
    )
    assert calls == []
    assert results == {}


def test_build_missing_returns_failed_result_without_registering() -> None:
    calls: list[str] = []
    outcome = ToolBuildResult(
        name="buildable", gate="FAIL", sha256="0" * 64, registered=False, detail="gate failed"
    )
    registry = ToolRegistry()
    results = build_missing(
        _mixed_report(),
        build_specs={"buildable": {"code": "c", "tests": "t"}},
        registry=registry,
        build_tool_fn=_fake_builder({"buildable": outcome}, calls, register_on_pass=False),
    )
    assert results["buildable"].gate == "FAIL"  # honest: failure stays failure
    assert {spec.name for spec in registry.list_specs()} == set()  # nothing registered


def test_provision_and_build_flips_registered_tool_to_provisioned() -> None:
    calls: list[str] = []
    outcome = ToolBuildResult(
        name="weather_lookup", gate="PASS", sha256="0" * 64, registered=True, detail="registered"
    )
    registry = ToolRegistry()
    registry.register(_StubTool("memory_query"))
    final = provision_and_build(
        ("memory_query", "weather_lookup"),
        registry=registry,
        build_specs={"weather_lookup": {"code": "c", "tests": "t"}},
        build_tool_fn=_fake_builder({"weather_lookup": outcome}, calls),
    )
    assert calls == ["weather_lookup"]
    assert [item.status for item in final.items] == [PROVISIONED, PROVISIONED]


def test_provision_and_build_failed_build_stays_buildable_with_gate_detail() -> None:
    calls: list[str] = []
    outcome = ToolBuildResult(
        name="weather_lookup",
        gate="FAIL",
        sha256="0" * 64,
        report_id="rep-1",
        registered=False,
        detail="forge gate failed; tool not registered",
    )
    registry = ToolRegistry()
    registry.register(_StubTool("memory_query"))
    final = provision_and_build(
        ("memory_query", "weather_lookup"),
        registry=registry,
        build_specs={"weather_lookup": {"code": "c", "tests": "t"}},
        build_tool_fn=_fake_builder({"weather_lookup": outcome}, calls, register_on_pass=False),
    )
    weather = final.items[1]
    assert calls == ["weather_lookup"]
    assert weather.status == BUILDABLE
    assert "build gate FAIL" in weather.detail
    assert "forge gate failed; tool not registered" in weather.detail
    assert {spec.name for spec in registry.list_specs()} == {"memory_query"}


def test_provision_and_build_without_buildable_is_pure_resolve() -> None:
    calls: list[str] = []
    final = provision_and_build(
        ("nosuchtool",),
        registry=_reg(),
        build_specs={},
        build_tool_fn=_fake_builder({}, calls),
    )
    assert calls == []  # UNKNOWN: no build attempted
    assert final.items[0].status == UNKNOWN
