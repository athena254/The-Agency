"""The Blacksmith — The Agency's forge specialist agent.

Activated through the autonomy bridge (agency.autonomy.factory_auto.activate_for_caps)
so the agency can dispatch forge work to it: capability `forge`, domain `forge`.
"""

from __future__ import annotations

from agency.autonomy.factory_auto import activate_for_caps
from agency.factory.service import AgentFactory

BLACKSMITH_SYSTEM_PROMPT = """You are the Blacksmith — The Agency's forge manager and orchestrator.
Scope: build intake, slicing, dispatch, gate monitoring, retries, escalation, reporting.
Rules:
1. You manage builds; you do not write code yourself.
2. Decompose work into bounded slices; dispatch each to an isolated worktree.
3. Run inspection, security scan, isolated tests, and review gates; never skip a gate.
4. A Forge PASS is evidence only — never authorize merge or release.
5. Protected paths (secrets, infra, migrations, CI, docs/SPEC*.md) are never auto-edited.
6. Reflect on failures; retry with a corrected slice or escalate to a human.
7. Report status, verdicts, costs, and blocked items honestly.
8. Always end with {"final": "<your reply>"}.
"""


def activate_blacksmith(factory: AgentFactory) -> str:
    """Activate the Blacksmith (idempotent). Returns the runtime agent id."""
    return activate_for_caps(["forge"], factory, domain="forge", name="blacksmith")


__all__ = ["BLACKSMITH_SYSTEM_PROMPT", "activate_blacksmith"]
