"""Tests for the Blacksmith's pinned forge-orchestrator system prompt."""

from __future__ import annotations

from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.autonomy.blacksmith import BLACKSMITH_PROMPT_REFS, activate_blacksmith
from agency.autonomy.factory_auto import (
    AUTO_VERSION,
    activate_for_caps,
    blueprint_id_for_caps,
)
from agency.factory.service import AgentFactory, BlueprintStatus
from agency.kernel.registry import AgentRegistry as KernelRegistry


def _factory() -> tuple[AgentFactory, KernelRegistry]:
    kernels = KernelRegistry()
    factory = AgentFactory(
        ":memory:",
        kernel_registry=kernels,
        runtime_registry=RuntimeRegistry(),
        allowed_capabilities={"forge", "general"},
    )
    return factory, kernels


def test_blacksmith_pins_forge_orchestrator_prompt() -> None:
    factory, kernels = _factory()
    try:
        aid = activate_blacksmith(factory)
        agent = kernels.get_required(aid)
        assert agent.metadata["prompt_refs"] == [list(r) for r in BLACKSMITH_PROMPT_REFS]
        prompt = agent.metadata["system_prompt"]
        assert "You orchestrate builds; you do not write code." in prompt
        assert "ship/merge/release always requires human approval" in prompt
        assert "Never claim a build, test or check that did not happen" in prompt
        assert agent.metadata["prompt_composed_sha256"]
    finally:
        factory.close()


def test_blacksmith_prompt_refs_are_published() -> None:
    factory, _kernels = _factory()
    try:
        activate_blacksmith(factory)
        resolver = factory._prompt_resolver
        assert resolver is not None
        for prompt_id, version in BLACKSMITH_PROMPT_REFS:
            assert resolver.is_published(prompt_id, version) is True
    finally:
        factory.close()


def test_blacksmith_activation_idempotent_with_prompt() -> None:
    factory, kernels = _factory()
    try:
        first = activate_blacksmith(factory)
        second = activate_blacksmith(factory)
        assert first == second
        bid = blueprint_id_for_caps(["forge"])
        assert len(factory.list_versions(bid)) == 1
        bp = factory.get(bid, AUTO_VERSION)
        assert bp.status is BlueprintStatus.ACTIVE
        agent = kernels.get_required(first)
        assert agent.metadata["prompt_refs"] == [list(r) for r in BLACKSMITH_PROMPT_REFS]
    finally:
        factory.close()


def test_activate_for_caps_default_pins_no_prompt() -> None:
    factory, kernels = _factory()
    try:
        aid = activate_for_caps(["general"], factory)
        agent = kernels.get_required(aid)
        assert "system_prompt" not in agent.metadata
    finally:
        factory.close()


def test_activate_for_caps_explicit_prompt_refs() -> None:
    factory, kernels = _factory()
    try:
        aid = activate_for_caps(
            ["forge"],
            factory,
            name="custom-forge",
            prompt_refs=[("agency-base-identity", "1.0.0")],
        )
        agent = kernels.get_required(aid)
        assert agent.metadata["prompt_refs"] == [["agency-base-identity", "1.0.0"]]
        assert "system_prompt" in agent.metadata
    finally:
        factory.close()
