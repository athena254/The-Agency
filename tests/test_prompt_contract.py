"""Prompt content contracts for every shipped system prompt (pure string checks, no LLM).

Guards the invariants the runtime depends on: no duplicated instruction lines
inside a prompt, the identity/honesty/output structure present in every domain
prompt, and every factory prompt slot having a matching parameter provider.
"""

from __future__ import annotations

import re

import pytest

from agency.agents.business.agent import BUSINESS_SYSTEM_PROMPT
from agency.agents.general.agent import GENERAL_SYSTEM_PROMPT
from agency.agents.personal.agent import PERSONAL_SYSTEM_PROMPT
from agency.agents.research.agent import RESEARCH_SYSTEM_PROMPT
from agency.agents.security.agent import SECURITY_SYSTEM_PROMPT
from agency.agents.work.agent import WORK_SYSTEM_PROMPT
from agency.factory.prompts_builtin import builtin_specs
from agency.forge.prompt_params import REA_TOOL_PARAM_NAMES

DOMAIN_PROMPTS: dict[str, str] = {
    "personal": PERSONAL_SYSTEM_PROMPT,
    "work": WORK_SYSTEM_PROMPT,
    "business": BUSINESS_SYSTEM_PROMPT,
    "security": SECURITY_SYSTEM_PROMPT,
    "general": GENERAL_SYSTEM_PROMPT,
    "research": RESEARCH_SYSTEM_PROMPT,
}

_SLOT_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]{0,63})\}\}")
_RULE_NUM_RE = re.compile(r"^\d+\.\s*")


@pytest.mark.parametrize("domain", sorted(DOMAIN_PROMPTS))
def test_no_duplicated_lines_inside_a_prompt(domain: str) -> None:
    lines = [
        _RULE_NUM_RE.sub("", line.strip())
        for line in DOMAIN_PROMPTS[domain].splitlines()
        if line.strip()
    ]
    duplicates = sorted({line for line in lines if lines.count(line) > 1})
    assert duplicates == [], f"{domain} prompt repeats lines: {duplicates}"


@pytest.mark.parametrize("domain", sorted(DOMAIN_PROMPTS))
def test_domain_prompts_carry_identity_honesty_output_contract(domain: str) -> None:
    prompt = DOMAIN_PROMPTS[domain]
    assert "of The Agency" in prompt, f"{domain} prompt lacks its identity line"
    assert re.search(r"never.{0,40}(invent|fabricate)", prompt, re.IGNORECASE), (
        f"{domain} prompt lacks an honesty rule"
    )
    assert '{"final"' in prompt, f"{domain} prompt lacks the output contract"


def test_manifest_prompt_slots_have_param_providers() -> None:
    for spec in builtin_specs():
        for slot in _SLOT_RE.findall(spec.body):
            assert slot in REA_TOOL_PARAM_NAMES, (
                f"prompt {spec.prompt_id} uses slot {slot} with no param provider"
            )
