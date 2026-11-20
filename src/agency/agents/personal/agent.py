"""PersonalAgent — life, health, relationships, habits, calendar."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

from agency.agents._prompt_shared import ESCALATION_RULE, OUTPUT_CONTRACT

PERSONAL_SYSTEM_PROMPT = (
    """You are the Personal Agent of The Agency — a trusted life-management specialist.
Scope: habits, health, relationships, calendar, home, personal goals, daily routines, well-being.
Rules:
1. Be warm, concise, and practical. Respect privacy — treat personal data as sensitive.
2. For scheduling/time questions, use utc_time. For facts/current events, use web_search then web_fetch.
3. Recall what the user told you earlier with memory_query before answering personal questions.
4. Store durable facts/preferences with memory_write (title + concise summary).
5. Never invent personal details. If you don't know, ask or say so.
6. Plan multi-step life tasks before acting; confirm destructive/irreversible changes with the user.
7. Reflect on your answer for completeness and safety before finalizing.
"""
    f"8. {ESCALATION_RULE}\n"
    f"9. {OUTPUT_CONTRACT}"
)


class PersonalAgent:
    """Domain specialist for personal life management (standalone-usable)."""

    def __init__(self) -> None:
        self.domain = "personal"
        self.capabilities = {
            "personal",
            "habits",
            "health",
            "relationships",
            "calendar",
            "tools",
            "respond",
        }
        self.name = "Personal Agent"
        self._log = structlog.get_logger(__name__)

    def system_prompt(self) -> str:
        return PERSONAL_SYSTEM_PROMPT

    async def handle(self, message: str, context: dict[str, Any] | None = None) -> str:
        _ = context
        return f"{PERSONAL_SYSTEM_PROMPT}\n\n{message}"


__all__ = ["PERSONAL_SYSTEM_PROMPT", "PersonalAgent"]
