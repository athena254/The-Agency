"""The Blacksmith — The Agency's forge specialist agent.

Activated through the autonomy bridge (agency.autonomy.factory_auto.activate_for_caps)
so the agency can dispatch forge work to it: capability `forge`, domain `forge`.
Its system prompt is the pinned `agency-forge-orchestrator` segment (single
source of truth in `factory/prompts_builtin/segments/`); the factory composes
it with the shared base-identity, evidence-discipline, and L0-scope segments.
"""

from __future__ import annotations

from agency.autonomy.factory_auto import activate_for_caps
from agency.factory.service import AgentFactory

PROMPT_REFS: tuple[tuple[str, str], ...] = (
    ("agency-base-identity", "1.0.0"),
    ("agency-evidence-discipline", "1.0.0"),
    ("agency-l0-scope", "1.0.0"),
    ("agency-forge-orchestrator", "1.0.0"),
)


def activate_blacksmith(factory: AgentFactory) -> str:
    """Activate the Blacksmith (idempotent). Returns the runtime agent id.

    The factory must have prompt callbacks configured (``is_prompt_published``
    + ``prompt_resolver``) so the pinned segments can be validated; otherwise
    activation fails closed.
    """
    return activate_for_caps(
        ["forge"],
        factory,
        domain="forge",
        name="blacksmith",
        prompt_refs=PROMPT_REFS,
    )


__all__ = ["PROMPT_REFS", "activate_blacksmith"]
