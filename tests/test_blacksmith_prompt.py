"""Tests for the pinned Blacksmith system prompt (forge-orchestrator segment).

The Blacksmith identity must pin its four segments at activation, compose a
real system prompt (never a bare chat path), and stay idempotent.
"""

from __future__ import annotations

import pytest

from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.autonomy.blacksmith import PROMPT_REFS, activate_blacksmith
from agency.autonomy.factory_auto import AUTO_VERSION, blueprint_id_for_caps
from agency.factory.prompts import PromptRegistry
from agency.factory.prompts_builtin import ensure_builtin_prompts
from agency.factory.service import AgentFactory, BlueprintStatus
from agency.kernel.registry import AgentRegistry as KernelRegistry


def _prompt_capable_factory(runtimes: RuntimeRegistry) -> AgentFactory:
    prompts = PromptRegistry(":memory:")
    ensure_builtin_prompts(prompts)
    return AgentFactory(
        ":memory:",
        kernel_registry=KernelRegistry(),
        runtime_registry=runtimes,
        allowed_capabilities={"forge"},
        is_prompt_published=prompts.is_published,
        prompt_resolver=prompts,
    )


def test_blacksmith_pins_four_prompt_refs_and_composes_prompt() -> None:
    runtimes = RuntimeRegistry()
    factory = _prompt_capable_factory(runtimes)
    agent_id = activate_blacksmith(factory)
    bp = factory.get(blueprint_id_for_caps(["forge"]), AUTO_VERSION)
    assert bp.status is BlueprintStatus.ACTIVE
    assert tuple(bp.prompt_refs) == PROMPT_REFS
    assert len(PROMPT_REFS) == 4
    record = next(r for r in runtimes.find_agents_by_capability("forge") if r.agent_id == agent_id)
    prompt = record.agent.metadata["system_prompt"]
    assert "do not write code" in prompt
    assert "human approval" in prompt
    # Agency global work rules are binding on the Blacksmith too.
    assert "KISS" in prompt
    assert "YAGNI" in prompt
    assert "reuse" in prompt.lower()
    assert "final plan" in prompt.lower()
    # Delegation contract: dispatches state objective/output/boundaries.
    assert "striker" in prompt.lower()
    factory.close()


def test_blacksmith_activation_is_idempotent_with_prompts() -> None:
    runtimes = RuntimeRegistry()
    factory = _prompt_capable_factory(runtimes)
    first = activate_blacksmith(factory)
    second = activate_blacksmith(factory)
    assert first == second
    factory.close()


def test_unpinned_factory_fails_closed() -> None:
    runtimes = RuntimeRegistry()
    factory = AgentFactory(
        ":memory:",
        kernel_registry=KernelRegistry(),
        runtime_registry=runtimes,
        allowed_capabilities={"forge"},
    )
    with pytest.raises(ValueError):
        activate_blacksmith(factory)
    factory.close()
