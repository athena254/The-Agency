"""Tests for tool_requirements activation wire-in (Phase 2).

``activate_for_caps`` accepts a keyword-only ``tool_requirements`` tuple that
is persisted on the blueprint (pure declaration, mirroring ``prompt_refs``).
``RequestedAgentService.request_agent`` passes ``tool_requirements`` through
to blueprint create, defaulting to the needs-catalog ``default`` profile only
when ``use_default_profile=True``.
"""

from __future__ import annotations

import pytest

from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.autonomy.factory_auto import AUTO_VERSION, activate_for_caps, blueprint_id_for_caps
from agency.factory.prompts import PromptRegistry
from agency.factory.prompts_builtin import ensure_builtin_prompts
from agency.factory.requested_agent import RequestedAgentRequest, RequestedAgentService
from agency.factory.service import AgentFactory, BlueprintStatus
from agency.kernel.registry import AgentRegistry as KernelRegistry
from agency.skills.registry import SkillRegistry, SkillStatus
from agency.tools.needs import default_requirements


def _tool_capable_factory(runtimes: RuntimeRegistry) -> AgentFactory:
    """Prompt-capable factory pattern (mirrors tests/test_blacksmith_prompt.py)."""
    prompts = PromptRegistry(":memory:")
    ensure_builtin_prompts(prompts)
    return AgentFactory(
        ":memory:",
        kernel_registry=KernelRegistry(),
        runtime_registry=runtimes,
        allowed_capabilities={"general"},
        is_prompt_published=prompts.is_published,
        prompt_resolver=prompts,
    )


def test_activate_for_caps_persists_tool_requirements() -> None:
    runtimes = RuntimeRegistry()
    factory = _tool_capable_factory(runtimes)
    try:
        agent_id = activate_for_caps(
            ["general"], factory=factory, tool_requirements=("memory_query", "web_search")
        )
        assert agent_id
        bp = factory.get(blueprint_id_for_caps(["general"]), AUTO_VERSION)
        assert bp.status is BlueprintStatus.ACTIVE
        assert bp.tool_requirements == ("memory_query", "web_search")
    finally:
        factory.close()


def test_activate_for_caps_is_idempotent_with_tool_requirements() -> None:
    runtimes = RuntimeRegistry()
    factory = _tool_capable_factory(runtimes)
    try:
        first = activate_for_caps(["general"], factory=factory, tool_requirements=("utc_time",))
        second = activate_for_caps(["general"], factory=factory, tool_requirements=("utc_time",))
        assert first == second
        bp = factory.get(blueprint_id_for_caps(["general"]), AUTO_VERSION)
        assert bp.tool_requirements == ("utc_time",)
    finally:
        factory.close()


def test_activate_for_caps_without_tool_requirements_stays_empty() -> None:
    runtimes = RuntimeRegistry()
    factory = _tool_capable_factory(runtimes)
    try:
        activate_for_caps(["general"], factory=factory)
        activate_for_caps(["general"], factory=factory, tool_requirements=())
        bp = factory.get(blueprint_id_for_caps(["general"]), AUTO_VERSION)
        assert bp.tool_requirements == ()
    finally:
        factory.close()


def test_activate_for_caps_rejects_invalid_tool_names() -> None:
    runtimes = RuntimeRegistry()
    factory = _tool_capable_factory(runtimes)
    try:
        with pytest.raises(ValueError):
            activate_for_caps(["general"], factory=factory, tool_requirements=("BAD NAME",))
        # Fail closed: no blueprint, no activation.
        assert factory.list_versions(blueprint_id_for_caps(["general"])) == []
    finally:
        factory.close()


# --- request_agent passthrough -----------------------------------------------


def _allow(actor: str, perm: str) -> bool:
    return perm == "agent.approve"


def _service(tmp_path) -> tuple[RequestedAgentService, AgentFactory]:
    prompts = PromptRegistry(str(tmp_path / "prompts.db"))
    ensure_builtin_prompts(prompts)
    skills = SkillRegistry(str(tmp_path / "skills.db"))
    factory = AgentFactory(
        str(tmp_path / "factory.db"),
        authorizer=_allow,
        allowed_capabilities={"general", "search", "forge"},
        kernel_registry=KernelRegistry(),
        runtime_registry=RuntimeRegistry(),
        is_prompt_published=prompts.is_published,
        prompt_resolver=prompts,
        is_skill_published=lambda sid, ver: skills.get(sid, ver).status is SkillStatus.PUBLISHED,
    )
    service = RequestedAgentService(factory=factory, prompt_registry=prompts, skill_registry=skills)
    return service, factory


def _request(**over) -> RequestedAgentRequest:
    args = {
        "blueprint_id": "scout",
        "name": "Scout",
        "purpose": "Search documents by keyword.",
        "creator": "owner",
        "domain": "research",
        "capabilities": ("search",),
    }
    args.update(over)
    return RequestedAgentRequest(**args)


def test_request_agent_use_default_profile_pins_default_tools(tmp_path) -> None:
    service, factory = _service(tmp_path)
    try:
        result = service.request_agent(_request(use_default_profile=True))
        bp = factory.get("scout", result.version)
        assert bp.tool_requirements == default_requirements()
        assert "memory_query" in bp.tool_requirements
    finally:
        factory.close()


def test_request_agent_default_leaves_tool_requirements_empty(tmp_path) -> None:
    service, factory = _service(tmp_path)
    try:
        result = service.request_agent(_request())
        bp = factory.get("scout", result.version)
        assert bp.tool_requirements == ()
    finally:
        factory.close()


def test_request_agent_explicit_requirements_pass_through(tmp_path) -> None:
    service, factory = _service(tmp_path)
    try:
        result = service.request_agent(_request(tool_requirements=("web_search", "web_fetch")))
        bp = factory.get("scout", result.version)
        assert bp.tool_requirements == ("web_search", "web_fetch")
    finally:
        factory.close()


def test_request_agent_explicit_requirements_win_over_default_profile(tmp_path) -> None:
    service, factory = _service(tmp_path)
    try:
        result = service.request_agent(
            _request(use_default_profile=True, tool_requirements=("web_search",))
        )
        bp = factory.get("scout", result.version)
        assert bp.tool_requirements == ("web_search",)
    finally:
        factory.close()
