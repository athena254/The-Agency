"""Factory-managed default agents: builtin prompt records, governed blueprint
seeding, and Butler routing through factory-issued identities (slices 1-3 of
docs/PLAN_FACTORY_MANAGED_DEFAULT_AGENTS.md)."""

from __future__ import annotations

import pytest

from agency.agents.business.agent import BUSINESS_SYSTEM_PROMPT
from agency.agents.general.agent import GENERAL_SYSTEM_PROMPT
from agency.agents.personal.agent import PERSONAL_SYSTEM_PROMPT
from agency.agents.research.agent import RESEARCH_SYSTEM_PROMPT
from agency.agents.security.agent import SECURITY_SYSTEM_PROMPT
from agency.agents.work.agent import WORK_SYSTEM_PROMPT
from agency.factory.compose import compose_system_prompt
from agency.factory.prompts import (
    PromptKind,
    PromptRegistry,
    PromptStatus,
)
from agency.factory.prompts_builtin import ensure_builtin_prompts
from agency.factory.requested_agent import BASE_PROMPT_REFS

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
