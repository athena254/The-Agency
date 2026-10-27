"""Tests for Forge tool availability as factory prompt parameters."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest

from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.factory.prompts import PromptRegistry
from agency.factory.prompts_builtin import ensure_builtin_prompts
from agency.factory.service import AgentFactory
from agency.forge.prompt_params import (
    AVAILABILITY_PROMPT_ID,
    REA_TOOL_PARAM_NAMES,
    forge_tool_params,
    rea_prompt_params,
)
from agency.forge.tool_catalog import CATALOG, ToolSpec
from agency.kernel.registry import AgentRegistry as KernelRegistry

MAX_PARAM_CHARS = 512


def _checker(names: set[str]) -> Callable[[ToolSpec], bool]:
    return lambda spec: spec.name in names


def test_rea_prompt_params_reports_ready_and_lists() -> None:
    params = rea_prompt_params(tools=CATALOG, is_tool_available=_checker({"rea", "ghidra"}))
    assert set(params) == set(REA_TOOL_PARAM_NAMES)
    assert params["REA_READY"] == "yes"
    assert "rea" in params["RE_TOOLS_AVAILABLE"]
    assert "ghidra" in params["RE_TOOLS_AVAILABLE"]
    assert "jadx" in params["RE_TOOLS_MISSING"]
    for value in params.values():
        assert len(value) <= MAX_PARAM_CHARS


def test_rea_prompt_params_without_rea_core() -> None:
    params = rea_prompt_params(tools=CATALOG, is_tool_available=_checker({"jadx"}))
    assert params["REA_READY"] == "no"
    assert params["RE_TOOLS_AVAILABLE"] == "jadx"
    assert "rea" in params["RE_TOOLS_MISSING"]


def test_rea_prompt_params_all_and_none_available() -> None:
    all_names = {spec.name for spec in CATALOG if spec.command is not None}
    everything = rea_prompt_params(tools=CATALOG, is_tool_available=_checker(all_names))
    nothing = rea_prompt_params(tools=CATALOG, is_tool_available=_checker(set()))
    assert everything["RE_TOOLS_MISSING"] == "none"
    assert everything["REA_READY"] == "yes"
    assert nothing["RE_TOOLS_AVAILABLE"] == "none"
    assert nothing["REA_READY"] == "no"


def test_bridge_only_entries_are_excluded() -> None:
    params = rea_prompt_params(tools=CATALOG, is_tool_available=_checker(set()))
    available = {spec.name for spec in CATALOG if spec.command is None}
    for name in available:
        assert name not in params["RE_TOOLS_MISSING"]


def test_provider_only_fills_when_availability_segment_pinned() -> None:
    checker = _checker({"rea"})
    assert forge_tool_params(SimpleNamespace(prompt_refs=[]), is_tool_available=checker) == {}
    assert (
        forge_tool_params(
            SimpleNamespace(prompt_refs=[("reap-10-vulnerability-research", "1.0.0")]),
            is_tool_available=checker,
        )
        == {}
    )
    params = forge_tool_params(
        SimpleNamespace(prompt_refs=[(AVAILABILITY_PROMPT_ID, "1.0.0")]),
        is_tool_available=checker,
    )
    assert params["REA_READY"] == "yes"


def _factory(tmp_path, reg: PromptRegistry, **over) -> AgentFactory:
    params = {
        "authorizer": lambda actor, perm: perm == "agent.approve" and actor == "approver",
        "allowed_capabilities": {"inspect"},
        "kernel_registry": KernelRegistry(),
        "runtime_registry": RuntimeRegistry(),
        "is_prompt_published": reg.is_published,
        "prompt_resolver": reg,
    }
    params.update(over)
    return AgentFactory(str(tmp_path / "factory.db"), **params)


def _create_availability_blueprint(
    fac: AgentFactory, *, prompt_params: dict[str, str] | None = None
) -> None:
    fac.create(
        blueprint_id="rea-tools",
        version="1.0.0",
        name="REA Tools Agent",
        capabilities=["inspect"],
        prompt_refs=[
            ("reap-tool-availability", "1.0.0"),
            ("reap-safety-scope", "1.0.0"),
        ],
        prompt_params=prompt_params,
        creator="creator",
    )
    fac.approve("rea-tools", "1.0.0", approver="approver", evidence="e1")


def test_factory_activation_merges_tool_availability_params(tmp_path) -> None:
    reg = PromptRegistry(str(tmp_path / "prompts.db"))
    assert ensure_builtin_prompts(reg) > 0
    fac = _factory(
        tmp_path,
        reg,
        prompt_param_provider=lambda bp: forge_tool_params(
            bp, is_tool_available=_checker({"rea", "ghidra"})
        ),
    )
    try:
        _create_availability_blueprint(fac)
        agent = fac.activate("rea-tools", "1.0.0")

        prompt = agent.metadata["system_prompt"]
        assert "REA analysis core ready: yes" in prompt
        assert "Available tools: rea, ghidra" in prompt
        assert "jadx" in prompt
        assert agent.metadata["prompt_params"]["REA_READY"] == "yes"
        assert agent.metadata["prompt_composed_sha256"]
    finally:
        fac.close()
        reg.close()


def test_factory_rejects_conflicting_provider_param(tmp_path) -> None:
    reg = PromptRegistry(str(tmp_path / "prompts.db"))
    assert ensure_builtin_prompts(reg) > 0
    fac = _factory(
        tmp_path,
        reg,
        prompt_param_provider=lambda bp: forge_tool_params(bp, is_tool_available=_checker({"rea"})),
    )
    try:
        _create_availability_blueprint(fac, prompt_params={"REA_READY": "no"})
        with pytest.raises(ValueError, match="conflicts"):
            fac.activate("rea-tools", "1.0.0")
        assert fac.get("rea-tools", "1.0.0").runtime_agent_id is None
    finally:
        fac.close()
        reg.close()
