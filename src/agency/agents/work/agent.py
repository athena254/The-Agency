"""WorkAgent — tasks, projects, productivity, professional workflow."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

from agency.agents._prompt_shared import ESCALATION_RULE, OUTPUT_CONTRACT

WORK_SYSTEM_PROMPT = (
    """You are the Work Agent of The Agency — a productivity and professional-work specialist.
Scope: tasks, projects, deadlines, professional communication, workflow optimization, focus, deliverables.
Rules:
1. Be structured and outcome-oriented. Break work into clear steps with owners and deadlines.
2. Use utc_time for scheduling; web_search/web_fetch for research; memory_query/memory_write for context.
3. Recall prior work context with memory_query before planning.
4. Store decisions, action items, and follow-ups with memory_write.
5. Never fabricate deliverables or status. Report blockers honestly.
6. Plan multi-step work explicitly; verify each step's output before declaring done.
7. Reflect on plans for gaps, risks, and missing dependencies.
"""
    f"8. {ESCALATION_RULE}\n"
    f"9. {OUTPUT_CONTRACT}"
)


class WorkAgent:
    """Domain specialist for professional work and productivity (standalone-usable)."""

    def __init__(self) -> None:
        self.domain = "work"
        self.capabilities = {"work", "tasks", "projects", "productivity", "tools", "respond"}
        self.name = "Work Agent"
        self._log = structlog.get_logger(__name__)

    def system_prompt(self) -> str:
        return WORK_SYSTEM_PROMPT

    async def handle(self, message: str, context: dict[str, Any] | None = None) -> str:
        _ = context
        return f"{WORK_SYSTEM_PROMPT}\n\n{message}"


__all__ = ["WORK_SYSTEM_PROMPT", "WorkAgent"]
