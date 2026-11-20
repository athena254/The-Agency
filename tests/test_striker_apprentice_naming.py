"""Naming taxonomy: strikers (external workers) and apprentices (factory-issued)."""

from __future__ import annotations

from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.autonomy.blacksmith import activate_blacksmith
from agency.autonomy.factory_auto import activate_for_caps
from agency.autonomy.loop_workers import ExternalWorker, striker_label
from agency.factory.service import AgentFactory
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


def test_striker_label_keeps_real_worker_identity() -> None:
    worker = ExternalWorker(["opencode"])
    assert worker.name == "opencode"
    assert striker_label(worker) == "striker:opencode"
    hermes = ExternalWorker(["hermes"])
    assert striker_label(hermes) == "striker:hermes"


def test_factory_spawned_agents_are_apprentices() -> None:
    factory, kernels = _factory()
    try:
        aid = activate_for_caps(["general"], factory)
        agent = kernels.get_required(aid)
        assert agent.name.startswith("apprentice-")
    finally:
        factory.close()


def test_blacksmith_keeps_manager_name() -> None:
    factory, kernels = _factory()
    try:
        aid = activate_blacksmith(factory)
        assert kernels.get_required(aid).name == "blacksmith"
    finally:
        factory.close()
