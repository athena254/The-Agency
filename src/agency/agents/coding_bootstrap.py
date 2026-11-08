"""Bootstrap helper for the coding agent.

Provides :func:`bootstrap_coding_agent`, which registers the coding agent in
both the kernel (identity) and runtime (worker) registries and returns the
resulting runtime record.
"""

from __future__ import annotations

from agency.agents import coding_identity
from agency.agents.registry import AgentRecord
from agency.agents.registry import AgentRegistry as RuntimeAgentRegistry
from agency.kernel.registry import AgentRegistry as KernelAgentRegistry

# Captured so we can tell whether either module-level binding was patched.
_ORIGINAL_REGISTER = coding_identity.register_coding_agent


def bootstrap_coding_agent(
    kernel_registry: KernelAgentRegistry,
    runtime_registry: RuntimeAgentRegistry,
) -> AgentRecord:
    """Register the coding agent and return its runtime record.

    Calls :func:`register_coding_agent` to create the identity and register it
    in both registries, then fetches and returns the runtime record created in
    ``runtime_registry``.
    """
    # Prefer an explicitly patched binding (in either module) over the
    # original implementation so monkeypatched tests are honoured.
    bootstrap_binding = globals().get("register_coding_agent")
    identity_binding = coding_identity.register_coding_agent

    if callable(bootstrap_binding) and bootstrap_binding is not _ORIGINAL_REGISTER:
        register = bootstrap_binding
    elif callable(identity_binding) and identity_binding is not _ORIGINAL_REGISTER:
        register = identity_binding
    else:
        register = identity_binding

    agent = register(kernel_registry, runtime_registry)
    record = runtime_registry.get_agent(agent.id)
    if record is None:  # pragma: no cover - defensive
        raise KeyError(f"runtime record missing for agent {agent.id!r}")
    return record


# Re-export for ``from agency.agents.coding_bootstrap import register_coding_agent``
# style patching/imports.
from agency.agents.coding_identity import register_coding_agent

__all__ = ["bootstrap_coding_agent", "register_coding_agent"]
