"""Bootstrap of installed agents into the runtime kernel registry.

Applies `InstalledAgent` metadata (from ``data/agents/agents.yaml``) at
startup. Idempotent: agents already registered are reused; grants are
union-only.
"""

from __future__ import annotations

from typing import Any

from agency.agents.install import InstallPlan, InstallPlanEntry, apply_install_plan
from agency.factory.manager import AgentManager, InstalledAgent


def ensure_installed_agents(
    manager: AgentManager,
    kernel_registry: Any,
    *,
    agent_ids: set[str] | None = None,
) -> list[str]:
    """Install all installed agents (optionally restricted to ``agent_ids``)."""
    agents = [agent for agent in manager.installed() if agent_ids is None or agent.id in agent_ids]
    plan = InstallPlan(tuple(InstallPlanEntry(agent) for agent in agents))
    return apply_install_plan(plan, kernel_registry)


def apply_installed_agents(agents: list[InstalledAgent], kernel_registry: Any) -> list[str]:
    """Install an explicit list of installed agents."""
    plan = InstallPlan(tuple(InstallPlanEntry(agent) for agent in agents))
    return apply_install_plan(plan, kernel_registry)


__all__ = ["apply_installed_agents", "ensure_installed_agents"]
