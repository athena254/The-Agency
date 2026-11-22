"""Governed factory blueprints for the built-in default domain agents.

Startup seeding (slice 2 of ``docs/PLAN_FACTORY_MANAGED_DEFAULT_AGENTS.md``):
turns each default domain agent (personal, work, business, security, research,
general) into a factory-issued identity whose system prompt is the versioned
builtin prompt record composed from ``BASE_PROMPT_REFS`` plus the domain body.

This is *not* the ``POST /v1/agent-requests`` intake path — it seeds the
fixed, reviewable default set at startup. Every lifecycle write still goes
through the governed :class:`~agency.factory.service.AgentFactory`
create → approve → activate transitions, so an explicit authorizer and a
distinct approver are mandatory inputs (fail closed: no authorizer, blank
approver, or self-approval aborts the seeding). The authorizer must accept
``approver`` for ``agent.approve`` (new seeding) and ``agent.recover``
(restart rehydration); anything else is denied fail-closed by the factory.

The caller must wire ``factory`` for prompts: ``is_prompt_published`` and
``prompt_resolver`` pointed at ``prompt_registry`` — approval re-validates
pinned prompt refs fail-closed. The factory's capability allowlist must
contain each domain name and ``"respond"`` (the blueprints pin exactly
``[domain, "respond"]``). Idempotency is derived from deterministic
blueprint ids (``<domain>-agent``): an already-ACTIVE blueprint is skipped
when its identity is still registered, and rehydrated through the factory's
explicit ``recover_active`` semantics otherwise (restart with fresh
registries; never auto-resurrected beyond what the service itself does).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from agency.factory.prompts_builtin import ensure_builtin_prompts
from agency.factory.requested_agent import BASE_PROMPT_REFS
from agency.factory.service import BlueprintStatus

if TYPE_CHECKING:
    from agency.factory.prompts import PromptRegistry
    from agency.factory.service import AgentFactory
    from agency.kernel.identity import Agent

DEFAULT_AGENT_DOMAINS: tuple[str, ...] = (
    "personal",
    "work",
    "business",
    "security",
    "research",
    "general",
)
DEFAULT_AGENT_PROMPT_VERSION = "1.0.0"
DEFAULT_AGENT_BLUEPRINT_VERSION = "1.0.0"
DEFAULT_AGENT_CREATOR = "factory-default-agents"


def default_agent_blueprint_id(domain: str) -> str:
    """Deterministic blueprint id for a default domain (restart-stable)."""
    return f"{domain}-agent"


def default_agent_prompt_id(domain: str) -> str:
    """Builtin prompt record id carrying the domain's system-prompt body."""
    return f"default-agent-{domain}"


def default_agent_prompt_refs(domain: str) -> list[tuple[str, str]]:
    """Pinned prompt refs for a default domain: shared segments + domain body."""
    return list(BASE_PROMPT_REFS) + [
        (default_agent_prompt_id(domain), DEFAULT_AGENT_PROMPT_VERSION)
    ]


def seed_default_agent_blueprints(
    factory: AgentFactory,
    prompt_registry: PromptRegistry,
    authorizer: Callable[[str, str], bool],
    *,
    approver: str,
    creator: str = DEFAULT_AGENT_CREATOR,
) -> dict[str, Agent]:
    """Create/approve/activate one governed blueprint per default domain.

    Idempotent: an ACTIVE blueprint is skipped when its issued identity is
    still registered, or rehydrated via ``factory.recover_active`` when the
    registries lost it (restart). Returns the issued identities keyed by
    domain. ``authorizer`` and ``approver`` are mandatory explicit inputs;
    the approver must differ from the creator (no self-approval).
    """
    if factory is None:
        raise TypeError("factory is required (fail closed).")
    if prompt_registry is None:
        raise TypeError("prompt_registry is required (fail closed).")
    if not callable(authorizer):
        raise TypeError("authorizer callback is required (fail closed).")
    if not isinstance(approver, str) or not approver.strip():
        raise ValueError("approver must be a nonblank string (fail closed).")
    if not isinstance(creator, str) or not creator.strip():
        raise ValueError("creator must be a nonblank string.")
    approver = approver.strip()
    creator = creator.strip()
    if approver == creator:
        raise ValueError("approver must differ from creator (no self-approval).")
    # Explicit pre-gate through the caller-supplied callback: no write happens
    # unless the approver is authorized for agent.approve. The factory's own
    # construction-time authorizer re-enforces this on every lifecycle write
    # (defense in depth); recovery re-checks for agent.recover there.
    try:
        allowed = authorizer(approver, "agent.approve")
    except PermissionError:
        raise
    except Exception as exc:
        raise PermissionError(f"authorizer failed closed: {exc}") from exc
    if allowed is not True:
        raise PermissionError(f"approver {approver!r} denied for agent.approve (fail closed).")

    ensure_builtin_prompts(prompt_registry)
    issued: dict[str, Agent] = {}
    for domain in DEFAULT_AGENT_DOMAINS:
        issued[domain] = _seed_domain(
            factory, domain, authorizer, approver=approver, creator=creator
        )
    return issued


def _seed_domain(
    factory: AgentFactory,
    domain: str,
    authorizer: Callable[[str, str], bool],
    *,
    approver: str,
    creator: str,
) -> Agent:
    blueprint_id = default_agent_blueprint_id(domain)
    versions = factory.list_versions(blueprint_id)
    active = [bp for bp in versions if bp.status is BlueprintStatus.ACTIVE]
    if active:
        bp = active[-1]
        runtime_id = bp.runtime_agent_id
        if runtime_id:
            existing: Agent | None = factory.kernel_registry.get(runtime_id)
            if existing is not None:
                return existing
        return factory.recover_active(
            blueprint_id,
            bp.version,
            actor=approver,
            evidence=f"Default {domain} agent identity rehydrated after registry loss",
        )
    factory.create(
        blueprint_id,
        DEFAULT_AGENT_BLUEPRINT_VERSION,
        blueprint_id,
        domain=domain,
        capabilities=[domain, "respond"],
        prompt_refs=default_agent_prompt_refs(domain),
        creator=creator,
    )
    factory.approve(
        blueprint_id,
        DEFAULT_AGENT_BLUEPRINT_VERSION,
        approver=approver,
        evidence=f"Default {domain} agent blueprint approved for startup seeding",
    )
    return factory.activate(blueprint_id, DEFAULT_AGENT_BLUEPRINT_VERSION)


__all__ = [
    "DEFAULT_AGENT_BLUEPRINT_VERSION",
    "DEFAULT_AGENT_CREATOR",
    "DEFAULT_AGENT_DOMAINS",
    "DEFAULT_AGENT_PROMPT_VERSION",
    "default_agent_blueprint_id",
    "default_agent_prompt_id",
    "default_agent_prompt_refs",
    "seed_default_agent_blueprints",
]
