"""Shared prompt building blocks for the domain agents.

Single source for the harness output contract and the escalation rule so six
domain prompts cannot drift apart. Pure text constants — no logic, no imports.
"""

from __future__ import annotations

OUTPUT_CONTRACT = 'Always end with {"final": "<your reply>"}.'
ESCALATION_RULE = (
    "If a request leaves your scope or you are blocked after honest effort: stop and "
    "answer 'BLOCKED: <what> because <evidence>' in your final reply. Never improvise "
    "authority you were not issued."
)

__all__ = ["ESCALATION_RULE", "OUTPUT_CONTRACT"]
