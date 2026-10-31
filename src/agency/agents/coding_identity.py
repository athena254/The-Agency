"""Kernel/runtime identity registration for the coding agent.

Provides :func:`register_coding_agent`, which creates an :class:`Agent`
identity with the coding capabilities (``code-edit``, ``verify``,
``inspect``) bounded to L0 observation only and registers it in both the
kernel (identity) and runtime (worker) registries.
"""

from __future__ import annotations

from typing import Any

from agency.agents.registry import AgentRegistry as RuntimeAgentRegistry
from agency.kernel.identity import Agent, Capability
from agency.kernel.policies import ActionClass
from agency.kernel.registry import AgentRegistry as KernelAgentRegistry


def register_coding_agent(
    kernel_registry: KernelAgentRegistry,
    runtime_registry: RuntimeAgentRegistry,
    *,
    agent_id: str = "coding-agent",
    name: str = "Coding Agent",
) -> Agent:
    """Create and register the coding agent identity in both registries.

    The identity carries only L0-bounded capabilities (``code-edit``,
    ``verify``, ``inspect``) and no permission grants. Returns the created
    :class:`Agent`.
    """
    capabilities = [
        Capability(name="code-edit", max_level=ActionClass.L0_OBSERVATION),
        Capability(name="verify", max_level=ActionClass.L0_OBSERVATION),
        Capability(name="inspect", max_level=ActionClass.L0_OBSERVATION),
    ]
    agent = Agent(id=agent_id, name=name, capabilities=capabilities)
    kernel_registry.register(agent)
    runtime_registry.register(agent)
    return agent


__all__ = ["register_coding_agent"]