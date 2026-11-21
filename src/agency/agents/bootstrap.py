"""Bootstrap of installed agents into the runtime kernel registry.

Applies `InstalledAgent` metadata (from ``data/agents/agents.yaml``) at
startup. Idempotent: agents already registered are reused; grants are
union-only.

Also hosts three startup/entry-point concerns:

* :func:`bootstrap_agents` — the single centralized agent bootstrap applied
  by every entry point (installed agents + external-skill attachment
  manifest grants; gaps.md #14).
* :func:`request_agent_authorized` — the fail-closed external entry-point
  adapter in front of ``RequestedAgentService.request_agent`` (gaps.md #6).
* :func:`pack_blueprint_from_prompt` — maps a quarantined ``PromptKind.PACK``
  prompt draft onto a governed ``AgentFactory`` DRAFT blueprint with the
  import provenance as the evidence chain (gaps.md #15).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import structlog

from agency.agents.install import InstallPlan, InstallPlanEntry, apply_install_plan
from agency.factory.github_skills import grant_manifest_capabilities
from agency.factory.manager import AgentManager, InstalledAgent, parse_version
from agency.factory.prompts import PromptKind, PromptRegistry, PromptStatus
from agency.factory.requested_agent import (
    MAX_PURPOSE_CHARS,
    RequestedAgentRequest,
    RequestedAgentResult,
    RequestedAgentService,
)
from agency.factory.service import AgentFactory

logger = structlog.get_logger(__name__)

AGENT_REQUEST_PERMISSION = "agent.request"


def ensure_installed_agents(
    manager: AgentManager,
    kernel_registry: Any,
    *,
    agent_ids: set[str] | None = None,
    factory: AgentFactory | None = None,
) -> list[str]:
    """Install all installed agents (optionally restricted to ``agent_ids``).

    Validates every manifest entry before install (gaps.md #16): duplicate
    manifest ids raise as a hard conflict; unparsable versions and — when a
    ``factory`` is supplied (the call site decides) — missing factory
    blueprints skip with a log so optional agents cannot block the manifest.
    """
    agents = [agent for agent in manager.installed() if agent_ids is None or agent.id in agent_ids]
    validation = validate_installed_agents(agents, factory=factory)
    logger.info("agents.install_validated", checks=list(validation.checks))
    if validation.skipped:
        logger.warning(
            "agents.install_validation_skipped",
            skipped=[
                {"agent_id": agent_id, "reason": reason} for agent_id, reason in validation.skipped
            ],
        )
    plan = InstallPlan(tuple(InstallPlanEntry(agent) for agent in validation.installed))
    return apply_install_plan(plan, kernel_registry)


@dataclass(frozen=True)
class InstallValidation:
    """Pre-install lifecycle validation record (gaps.md #16)."""

    installed: tuple[InstalledAgent, ...]
    skipped: tuple[tuple[str, str], ...]  # (agent_id, reason)
    checks: tuple[str, ...]  # what was validated, for reporting


def validate_installed_agents(
    agents: Sequence[InstalledAgent],
    *,
    factory: AgentFactory | None = None,
) -> InstallValidation:
    """Validate manifest entries before install (gaps.md #16).

    Duplicate manifest ids raise (hard conflict). Each entry's version must
    parse; when ``factory`` is provided, the agent must also have a factory
    blueprint under its manifest id (the key ``AgentManager.list_agents`` and
    ``get_agent_paths`` already resolve). Per-entry failures are skipped with
    a reason instead of blocking the rest of the manifest.
    """
    seen: set[str] = set()
    for agent in agents:
        if agent.id in seen:
            raise ValueError(f"duplicate agent id {agent.id!r} in the installed-agent manifest.")
        seen.add(agent.id)
    installed: list[InstalledAgent] = []
    skipped: list[tuple[str, str]] = []
    checks: list[str] = []
    for agent in agents:
        try:
            parse_version(agent.version)
        except ValueError:
            skipped.append((agent.id, f"invalid version {agent.version!r}"))
            continue
        checks.append(f"{agent.id}: version {agent.version} ok")
        if factory is not None:
            if not factory.list_versions(agent.id):
                skipped.append((agent.id, f"no factory blueprint for {agent.id!r}"))
                continue
            checks.append(f"{agent.id}: factory blueprint {agent.id!r} present")
        installed.append(agent)
    return InstallValidation(
        installed=tuple(installed), skipped=tuple(skipped), checks=tuple(checks)
    )


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


# --------------------------------------------------------------------------- #
# PACK prompt -> blueprint mapping (gaps.md #15)
# --------------------------------------------------------------------------- #

PACK_BLUEPRINT_CREATOR = "pack-import"
_BLUEPRINT_ID_MAX = 128  # same id bound as AgentFactory._check_id
_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class PackBlueprintResult:
    """Outcome of one PACK-prompt -> blueprint mapping attempt (gaps.md #15)."""

    blueprint_id: str
    version: str  # "" when skipped
    prompt_ref: tuple[str, str]
    purpose: str
    evidence: str  # the prompt's provenance string (the evidence chain)
    created: bool
    note: str  # skip reason or creation summary for the caller to report


def _blueprint_slug(name: str) -> str:
    """Derive a factory-valid blueprint id from a prompt name (gaps.md #15).

    Same id rules as elsewhere: ``^[A-Za-z0-9][A-Za-z0-9_.-]*$``, bounded at
    128 chars (``AgentFactory._check_id``); lowercased slug of the name.
    """
    slug = _SLUG_RE.sub("-", name.strip().casefold()).strip("-")
    return (slug or "pack-agent")[:_BLUEPRINT_ID_MAX]


def pack_blueprint_from_prompt(
    prompt_registry: PromptRegistry,
    factory: AgentFactory,
    prompt_id: str,
    version: str,
) -> PackBlueprintResult:
    """Map one quarantined PACK prompt draft to a governed DRAFT blueprint.

    Validates ``kind == PACK`` and DRAFT status, derives the blueprint id as
    the slug of the prompt name and pins ``prompt_refs=[(prompt_id, version)]``
    with no capabilities and creator ``pack-import``. The created blueprint
    stays DRAFT with no auto-approval and no auto-activation: the factory's
    normal governed approve/activate path applies, and the prompt's
    ``import-quarantine:...`` provenance travels with the result as the
    evidence chain. A blueprint that already exists under the derived id is
    skipped (dedup) with a clear note.
    """
    spec = prompt_registry.get(prompt_id, version)
    if spec.kind is not PromptKind.PACK:
        raise ValueError(
            f"prompt {(prompt_id, version)!r} is not a PACK prompt (kind={spec.kind.value})."
        )
    if spec.status is not PromptStatus.DRAFT:
        raise ValueError(
            f"prompt {(prompt_id, version)!r} is not DRAFT (status={spec.status.value})."
        )
    blueprint_id = _blueprint_slug(spec.name)
    purpose = (spec.description.strip() or spec.body)[:MAX_PURPOSE_CHARS].strip()
    prompt_ref = (prompt_id, version)
    if factory.list_versions(blueprint_id):
        return PackBlueprintResult(
            blueprint_id=blueprint_id,
            version="",
            prompt_ref=prompt_ref,
            purpose=purpose,
            evidence=spec.provenance,
            created=False,
            note=(
                f"blueprint {blueprint_id!r} already exists; skipped "
                "(dedup; the governed approve/activate path is unchanged)."
            ),
        )
    blueprint = factory.create(
        blueprint_id,
        "1.0.0",
        spec.name,
        capabilities=(),
        prompt_refs=[prompt_ref],
        creator=PACK_BLUEPRINT_CREATOR,
    )
    logger.info(
        "agents.pack_blueprint_mapped",
        blueprint_id=blueprint.blueprint_id,
        version=blueprint.version,
        prompt_id=prompt_id,
        prompt_version=version,
        evidence=spec.provenance,
    )
    return PackBlueprintResult(
        blueprint_id=blueprint.blueprint_id,
        version=blueprint.version,
        prompt_ref=prompt_ref,
        purpose=purpose,
        evidence=spec.provenance,
        created=True,
        note=(
            f"Created DRAFT blueprint {blueprint.blueprint_id} v{blueprint.version} from "
            f"PACK prompt {(prompt_id, version)}; evidence is the import provenance. "
            "No auto-approval/activation; use the governed approve/activate path."
        ),
    )


__all__ = [
    "AGENT_REQUEST_PERMISSION",
    "PACK_BLUEPRINT_CREATOR",
    "BootstrapResult",
    "InstallValidation",
    "PackBlueprintResult",
    "apply_installed_agents",
    "bootstrap_agents",
    "ensure_installed_agents",
    "pack_blueprint_from_prompt",
    "request_agent_authorized",
    "validate_installed_agents",
]
