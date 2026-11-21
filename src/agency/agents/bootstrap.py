"""Bootstrap of installed agents into the runtime kernel registry.

Applies `InstalledAgent` metadata (from ``data/agents/agents.yaml``) at
startup. Idempotent: agents already registered are reused; grants are
union-only.

Also hosts two startup/entry-point concerns:

* :func:`bootstrap_agents` — the single centralized agent bootstrap applied
  by every entry point (installed agents + external-skill attachment
  manifest grants; gaps.md #14).
* :func:`request_agent_authorized` — the fail-closed external entry-point
  adapter in front of ``RequestedAgentService.request_agent`` (gaps.md #6).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from agency.agents.install import InstallPlan, InstallPlanEntry, apply_install_plan
from agency.factory.github_skills import grant_manifest_capabilities
from agency.factory.manager import AgentManager, InstalledAgent
from agency.factory.requested_agent import (
    RequestedAgentRequest,
    RequestedAgentResult,
    RequestedAgentService,
)

AGENT_REQUEST_PERMISSION = "agent.request"


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


# --------------------------------------------------------------------------- #
# Centralized startup bootstrap (gaps.md #14)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BootstrapResult:
    """Outcome of the centralized agent bootstrap."""

    installed_agents: tuple[str, ...]
    granted_capabilities: int


def bootstrap_agents(
    kernel_registry: Any,
    *,
    manager: AgentManager | None = None,
    manifest_path: str | None = None,
) -> BootstrapResult:
    """Centralized agent bootstrap shared by every entry point (gaps.md #14).

    Installs the manifest's agents first, then grants the external-skill
    attachment manifest's L0 capabilities. Ordering matters: a manifest entry
    referencing an installed agent resolves only once that agent is
    registered. ``manifest_path`` defaults to the repo-relative
    ``data/external_skill_attachments.json``.
    """
    installed = ensure_installed_agents(
        manager if manager is not None else AgentManager(), kernel_registry
    )
    if manifest_path is None:
        granted = grant_manifest_capabilities(kernel_registry)
    else:
        granted = grant_manifest_capabilities(kernel_registry, manifest_path)
    return BootstrapResult(installed_agents=tuple(installed), granted_capabilities=granted)


# --------------------------------------------------------------------------- #
# External agent.request entry point (gaps.md #6)
# --------------------------------------------------------------------------- #


def _normalize_allowlist(domains: Sequence[str]) -> frozenset[str]:
    return frozenset(part.strip().casefold() for part in domains if part and part.strip())


def request_agent_authorized(
    service: RequestedAgentService,
    request: RequestedAgentRequest,
    *,
    authorizer: Callable[[str, str], bool] | None,
    allowed_domains: Sequence[str] = (),
) -> RequestedAgentResult:
    """External entry point in front of ``RequestedAgentService.request_agent``.

    Fail-closed policy gate (gaps.md #6):

    * with no authorizer configured, or on an authorizer error, or for
      anything but exactly ``True`` for ``agent.request``, the request is
      denied;
    * an empty domain allowlist denies every request, and a request whose
      domain is outside the allowlist is rejected;
    * only then is the governed factory flow delegated to.
    """
    actor = request.creator.strip()
    if authorizer is None:
        raise PermissionError("no agent.request authorizer configured (fail closed).")
    try:
        allowed = authorizer(actor, AGENT_REQUEST_PERMISSION)
    except PermissionError:
        raise
    except Exception as exc:
        raise PermissionError(f"authorizer failed closed: {exc}") from exc
    if allowed is not True:
        raise PermissionError(f"actor {actor!r} denied for {AGENT_REQUEST_PERMISSION}.")
    domains = _normalize_allowlist(allowed_domains)
    if not domains:
        raise PermissionError("agent-request domain allowlist is empty (fail closed).")
    if request.domain.strip().casefold() not in domains:
        raise PermissionError(f"domain {request.domain!r} is not in the agent-request allowlist.")
    return service.request_agent(request)


__all__ = [
    "AGENT_REQUEST_PERMISSION",
    "BootstrapResult",
    "apply_installed_agents",
    "bootstrap_agents",
    "ensure_installed_agents",
    "request_agent_authorized",
]
