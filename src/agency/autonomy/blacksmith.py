"""The Blacksmith — the Agency's forge specialist agent.

Activated through the autonomy bridge (agency.autonomy.factory_auto.activate_for_caps)
so the agency can dispatch forge work to it: capability `forge`, domain `forge`.
"""

from __future__ import annotations

from agency.autonomy.factory_auto import activate_for_caps
from agency.factory.service import AgentFactory


def activate_blacksmith(factory: AgentFactory) -> str:
    """Activate the Blacksmith (idempotent). Returns the runtime agent id."""
    return activate_for_caps(["forge"], factory, domain="forge", name="blacksmith")


__all__ = ["activate_blacksmith"]
