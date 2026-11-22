"""Factory-managed default agents: builtin prompt records, governed blueprint
seeding, and Butler routing through factory-issued identities (slices 1-3 of
docs/PLAN_FACTORY_MANAGED_DEFAULT_AGENTS.md)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from agency.agents.business.agent import BUSINESS_SYSTEM_PROMPT
from agency.agents.general.agent import GENERAL_SYSTEM_PROMPT
from agency.agents.personal.agent import PERSONAL_SYSTEM_PROMPT
from agency.agents.prompts import issued_system_prompt
from agency.agents.registry import AgentRegistry as RuntimeAgentRegistry
from agency.agents.research.agent import RESEARCH_SYSTEM_PROMPT
from agency.agents.security.agent import SECURITY_SYSTEM_PROMPT
from agency.agents.work.agent import WORK_SYSTEM_PROMPT
from agency.factory.compose import compose_system_prompt
from agency.factory.default_agents import (
    DEFAULT_AGENT_DOMAINS,
    default_agent_blueprint_id,
    seed_default_agent_blueprints,
)
from agency.factory.prompts import (
    PromptKind,
    PromptRegistry,
    PromptStatus,
)
from agency.factory.prompts_builtin import ensure_builtin_prompts
from agency.factory.requested_agent import BASE_PROMPT_REFS
from agency.factory.service import AgentFactory, BlueprintStatus
from agency.kernel.registry import AgentRegistry as KernelRegistry

DEFAULT_DOMAIN_PROMPTS: dict[str, str] = {
    "personal": PERSONAL_SYSTEM_PROMPT,
    "work": WORK_SYSTEM_PROMPT,
    "business": BUSINESS_SYSTEM_PROMPT,
    "security": SECURITY_SYSTEM_PROMPT,
    "research": RESEARCH_SYSTEM_PROMPT,
    "general": GENERAL_SYSTEM_PROMPT,
}

DEFAULT_DOMAINS: tuple[str, ...] = tuple(DEFAULT_DOMAIN_PROMPTS)


def _normalized(text: str) -> str:
    """Collapse line endings so comparisons hold on LF and CRLF checkouts."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _registry(tmp_path) -> PromptRegistry:
    reg = PromptRegistry(str(tmp_path / "prompts.db"))
    ensure_builtin_prompts(reg)
    return reg


def _allow_approve_and_recover(actor: str, permission: str) -> bool:
    return permission in {"agent.approve", "agent.recover"}


def _default_factory(
    tmp_path,
    *,
    authorizer: Callable[[str, str], bool] = _allow_approve_and_recover,
    kernel_registry: KernelRegistry | None = None,
    factory_db: str = "factory.db",
) -> tuple[AgentFactory, PromptRegistry]:
    prompts = PromptRegistry(str(tmp_path / "prompts.db"))
    ensure_builtin_prompts(prompts)
    factory = AgentFactory(
        str(tmp_path / factory_db),
        authorizer=authorizer,
        allowed_capabilities=set(DEFAULT_AGENT_DOMAINS) | {"respond"},
        kernel_registry=kernel_registry if kernel_registry is not None else KernelRegistry(),
        runtime_registry=RuntimeAgentRegistry(),
        is_prompt_published=prompts.is_published,
        prompt_resolver=prompts,
    )
    return factory, prompts


# ------------------------------------------------------------------ #
# Slice 1 — builtin prompt records for the default domains
# ------------------------------------------------------------------ #


def test_default_agent_prompt_records_are_published(tmp_path) -> None:
    reg = _registry(tmp_path)
    try:
        for domain in DEFAULT_DOMAINS:
            spec = reg.get(f"default-agent-{domain}", "1.0.0")
            assert spec.status is PromptStatus.PUBLISHED
            assert spec.kind is PromptKind.BASE
            assert spec.domain == domain
            assert (
                spec.provenance == f"builtin:agency-factory/default-agents/default-agent-{domain}"
            )
    finally:
        reg.close()


@pytest.mark.parametrize("domain", DEFAULT_DOMAINS)
def test_composed_default_prompt_carries_segments_and_legacy_body(tmp_path, domain) -> None:
    """Behavior parity: the composed prompt is the three shared segments plus
    the unchanged legacy domain body."""
    reg = _registry(tmp_path)
    try:
        composed = compose_system_prompt(
            list(BASE_PROMPT_REFS) + [(f"default-agent-{domain}", "1.0.0")],
            resolver=reg,
        )
        segment_bodies = [
            reg.get(segment_id, "1.0.0").body.strip() for segment_id, _v in BASE_PROMPT_REFS
        ]
    finally:
        reg.close()
    assert composed.text
    assert composed.prompt_refs == BASE_PROMPT_REFS + ((f"default-agent-{domain}", "1.0.0"),)
    text = _normalized(composed.text)
    for segment_body in segment_bodies:
        assert _normalized(segment_body) in text
    assert _normalized(DEFAULT_DOMAIN_PROMPTS[domain].strip()) in text


# ------------------------------------------------------------------ #
# Slice 2 — governed blueprint seeding for the default domains
# ------------------------------------------------------------------ #


def test_seed_creates_active_blueprints_with_composed_prompts(tmp_path) -> None:
    factory, prompts = _default_factory(tmp_path)
    try:
        issued = seed_default_agent_blueprints(
            factory, prompts, _allow_approve_and_recover, approver="human-lead"
        )
        assert set(issued) == set(DEFAULT_AGENT_DOMAINS)
        for domain, agent in issued.items():
            assert agent.metadata["factory"] == "agent-factory-v1"
            assert agent.metadata["blueprint_id"] == default_agent_blueprint_id(domain)
            assert [c.name for c in agent.capabilities] == [domain, "respond"]
            prompt = issued_system_prompt(agent)
            assert prompt
            assert agent.metadata["system_prompt"] == prompt
            assert DEFAULT_DOMAIN_PROMPTS[domain].strip() in prompt
            refs = agent.metadata["prompt_refs"]
            assert [tuple(ref) for ref in refs] == list(BASE_PROMPT_REFS) + [
                (f"default-agent-{domain}", "1.0.0")
            ]
            bp = factory.get(default_agent_blueprint_id(domain), "1.0.0")
            assert bp.status is BlueprintStatus.ACTIVE
            assert bp.runtime_agent_id == agent.id
    finally:
        factory.close()
        prompts.close()


def test_seed_is_idempotent(tmp_path) -> None:
    factory, prompts = _default_factory(tmp_path)
    try:
        first = seed_default_agent_blueprints(
            factory, prompts, _allow_approve_and_recover, approver="human-lead"
        )
        second = seed_default_agent_blueprints(
            factory, prompts, _allow_approve_and_recover, approver="human-lead"
        )
        assert {d: a.id for d, a in second.items()} == {d: a.id for d, a in first.items()}
        for domain in DEFAULT_AGENT_DOMAINS:
            bid = default_agent_blueprint_id(domain)
            assert [bp.version for bp in factory.list_versions(bid)] == ["1.0.0"]
            assert len(factory.list_events(bid, "1.0.0")) == 3  # create, approve, activate
    finally:
        factory.close()
        prompts.close()


def test_seed_fails_closed_without_authorizer(tmp_path) -> None:
    factory, prompts = _default_factory(tmp_path)
    try:
        with pytest.raises(TypeError, match="authorizer"):
            seed_default_agent_blueprints(factory, prompts, None, approver="human-lead")  # type: ignore[arg-type]
    finally:
        factory.close()
        prompts.close()


def test_seed_fails_closed_on_blank_approver(tmp_path) -> None:
    factory, prompts = _default_factory(tmp_path)
    try:
        with pytest.raises(ValueError, match="approver"):
            seed_default_agent_blueprints(
                factory, prompts, _allow_approve_and_recover, approver="  "
            )
        with pytest.raises(ValueError, match="self-approval"):
            seed_default_agent_blueprints(
                factory,
                prompts,
                _allow_approve_and_recover,
                approver="factory-default-agents",
            )
        assert factory.list_versions("personal-agent") == []
    finally:
        factory.close()
        prompts.close()


def test_seed_fails_closed_when_authorizer_denies(tmp_path) -> None:
    factory, prompts = _default_factory(tmp_path, authorizer=lambda actor, permission: False)
    try:
        with pytest.raises(PermissionError):
            seed_default_agent_blueprints(
                factory, prompts, lambda actor, permission: False, approver="human-lead"
            )
        assert factory.list_versions("personal-agent") == []
    finally:
        factory.close()
        prompts.close()


def test_seed_records_approver_provenance(tmp_path) -> None:
    factory, prompts = _default_factory(tmp_path)
    try:
        seed_default_agent_blueprints(
            factory, prompts, _allow_approve_and_recover, approver="human-lead"
        )
        for domain in DEFAULT_AGENT_DOMAINS:
            bid = default_agent_blueprint_id(domain)
            bp = factory.get(bid, "1.0.0")
            assert bp.creator == "factory-default-agents"
            assert bp.approver == "human-lead"
            events = factory.list_events(bid, "1.0.0")
            approve_events = [e for e in events if e["to_status"] == "APPROVED"]
            assert approve_events and approve_events[0]["actor"] == "human-lead"
    finally:
        factory.close()
        prompts.close()


def test_seed_recovers_active_identity_after_restart(tmp_path) -> None:
    factory, prompts = _default_factory(tmp_path, factory_db="persistent-factory.db")
    try:
        first = seed_default_agent_blueprints(
            factory, prompts, _allow_approve_and_recover, approver="human-lead"
        )
    finally:
        factory.close()
        prompts.close()

    # Restart: same persisted factory DB, fresh in-memory registries.
    restarted_prompts = PromptRegistry(str(tmp_path / "prompts.db"))
    restarted_factory = AgentFactory(
        str(tmp_path / "persistent-factory.db"),
        authorizer=_allow_approve_and_recover,
        allowed_capabilities=set(DEFAULT_AGENT_DOMAINS) | {"respond"},
        kernel_registry=KernelRegistry(),
        runtime_registry=RuntimeAgentRegistry(),
        is_prompt_published=restarted_prompts.is_published,
        prompt_resolver=restarted_prompts,
    )
    try:
        recovered = seed_default_agent_blueprints(
            restarted_factory,
            restarted_prompts,
            _allow_approve_and_recover,
            approver="human-lead",
        )
        for domain, agent in recovered.items():
            assert agent.id == first[domain].id
            assert restarted_factory.kernel_registry.get(agent.id) is agent
            assert issued_system_prompt(agent) == issued_system_prompt(first[domain])
    finally:
        restarted_factory.close()
        restarted_prompts.close()
