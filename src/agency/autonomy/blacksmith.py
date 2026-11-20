"""The Blacksmith — the Agency's forge manager.

Activated through the autonomy bridge (agency.autonomy.factory_auto.activate_for_caps)
so the agency can dispatch forge work to it: capability `forge`, domain `forge`.
The Blacksmith orchestrates and manages Forge builds; sticker workers do the
editing. Its identity pins the shared discipline segments plus the
forge-orchestrator role segment as a factory-issued system prompt.
"""

from __future__ import annotations

from agency.autonomy.factory_auto import activate_for_caps
from agency.factory.service import AgentFactory

BLACKSMITH_PROMPT_REFS: tuple[tuple[str, str], ...] = (
    ("agency-base-identity", "1.0.0"),
    ("agency-evidence-discipline", "1.0.0"),
    ("agency-l0-scope", "1.0.0"),
    ("agency-forge-orchestrator", "1.0.0"),
)


def activate_blacksmith(factory: AgentFactory) -> str:
    """Activate the Blacksmith (idempotent). Returns the runtime agent id."""
    return activate_for_caps(
        ["forge"],
        factory,
        domain="forge",
        name="blacksmith",
        prompt_refs=list(BLACKSMITH_PROMPT_REFS),
    )


__all__ = ["BLACKSMITH_PROMPT_REFS", "activate_blacksmith"]
