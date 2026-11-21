"""Tests for agency tool-availability prompt parameters (Phase 2).

``tool_availability_params`` maps a ToolProvisionReport onto the three slots
of the ``agency-tool-availability`` segment; ``tool_param_provider_for``
mirrors ``forge/prompt_params.forge_tool_params``: fill only when the
blueprint pins the segment, empty dict otherwise. Slots are always filled
for pinned segments — missing tools land in the MISSING lists, never as
unfilled slots (composition fails closed on unfilled slots).
"""

from __future__ import annotations

from types import SimpleNamespace

from agency.factory.compose import compose_system_prompt
from agency.factory.prompts import PromptRegistry
from agency.factory.prompts_builtin import ensure_builtin_prompts
from agency.tools.availability_params import (
    AVAILABILITY_PROMPT_ID,
    MISSING_BUILD_SLOT,
    MISSING_INSTALL_SLOT,
    PROVISIONED_SLOT,
    tool_availability_params,
    tool_param_provider_for,
)
from agency.tools.provision import resolve

SEGMENT_REF = (AVAILABILITY_PROMPT_ID, "1.0.0")


def _mixed_report():
    """One report touching every status, with injectable probe/registry."""
    return resolve(
        ("memory_query", "ghidra", "jadx", "ghidramcp", "my_tool", "nosuchtool"),
        registry=SimpleNamespace(list_specs=lambda: [SimpleNamespace(name="memory_query")]),
        catalog_probe=lambda spec: spec.name == "ghidra",
        build_specs={"my_tool": {"code": "...", "tests": "..."}},
    )


def test_params_fill_all_slots_for_mixed_report() -> None:
    params = tool_availability_params(_mixed_report())
    assert set(params) == {PROVISIONED_SLOT, MISSING_INSTALL_SLOT, MISSING_BUILD_SLOT}
    assert params[PROVISIONED_SLOT] == "memory_query, ghidra"
    assert params[MISSING_INSTALL_SLOT] == "jadx, ghidramcp"
    assert params[MISSING_BUILD_SLOT] == "my_tool, nosuchtool"


def test_params_say_none_when_no_items() -> None:
    report = resolve(())
    params = tool_availability_params(report)
    assert params == {
        PROVISIONED_SLOT: "none",
        MISSING_INSTALL_SLOT: "none",
        MISSING_BUILD_SLOT: "none",
    }


def test_provider_returns_empty_when_segment_not_pinned() -> None:
    provider = tool_param_provider_for(("nosuchtool",))
    assert provider(SimpleNamespace(prompt_refs=())) == {}
    assert provider(SimpleNamespace(prompt_refs=(("agency-base-identity", "1.0.0"),))) == {}


def test_provider_fills_slots_when_segment_pinned() -> None:
    provider = tool_param_provider_for(("nosuchtool",))
    params = provider(SimpleNamespace(prompt_refs=(SEGMENT_REF,)))
    assert params[PROVISIONED_SLOT] == "none"
    assert params[MISSING_INSTALL_SLOT] == "none"
    assert params[MISSING_BUILD_SLOT] == "nosuchtool"


def test_provider_tolerates_blueprints_without_prompt_refs() -> None:
    provider = tool_param_provider_for(("nosuchtool",))
    assert provider(object()) == {}


def test_compose_with_real_segment_and_provider_params() -> None:
    registry = PromptRegistry(":memory:")
    try:
        ensure_builtin_prompts(registry)
        provider = tool_param_provider_for(("nosuchtool",))
        blueprint = SimpleNamespace(prompt_refs=(SEGMENT_REF,))
        composed = compose_system_prompt(
            [SEGMENT_REF], resolver=registry, params=provider(blueprint)
        )
        assert "{{" not in composed.text
        assert "nosuchtool" in composed.text
        assert "host facts" in composed.text
        assert "authorization" in composed.text
    finally:
        registry.close()


def test_compose_mixed_report_lists_every_bucket() -> None:
    registry = PromptRegistry(":memory:")
    try:
        ensure_builtin_prompts(registry)
        composed = compose_system_prompt(
            [SEGMENT_REF], resolver=registry, params=tool_availability_params(_mixed_report())
        )
        assert "{{" not in composed.text
        for name in ("memory_query", "ghidra", "jadx", "ghidramcp", "my_tool", "nosuchtool"):
            assert name in composed.text
    finally:
        registry.close()
