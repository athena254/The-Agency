"""Tests for the Blacksmith forge-specialist activation (idempotent)."""

from __future__ import annotations

from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.autonomy.blacksmith import activate_blacksmith
from agency.autonomy.factory_auto import AUTO_VERSION, blueprint_id_for_caps
from agency.factory.service import AgentFactory, BlueprintStatus
from agency.kernel.registry import AgentRegistry as KernelRegistry


def test_blacksmith_activation_is_idempotent():
    runtimes = RuntimeRegistry()
    factory = AgentFactory(
        ":memory:",
        kernel_registry=KernelRegistry(),
        runtime_registry=runtimes,
        allowed_capabilities={"forge"},
    )
    first = activate_blacksmith(factory)
    second = activate_blacksmith(factory)
    assert first == second
    bp = factory.get(blueprint_id_for_caps(["forge"]), AUTO_VERSION)
    assert bp.status is BlueprintStatus.ACTIVE
    assert bp.runtime_agent_id == first
    assert any(r.agent_id == first for r in runtimes.find_agents_by_capability("forge"))
    factory.close()
