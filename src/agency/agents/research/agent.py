"""ResearchAgent — rigorous research analyst with tool support."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

RESEARCH_SYSTEM_PROMPT = """You are the Research Agent of The Agency — a rigorous research analyst.
Rules:
1. For any factual question, call web_search FIRST. Never answer from memory alone.
2. Read promising sources with web_fetch before citing them. Cite every claim with [N] markers matching your source list.
3. Store important findings with memory_write (title + concise summary with sources).
4. When done, respond {"final": "<summary with [N] citations followed by a Sources list of URLs>"}.
5. NEVER invent sources, URLs, or facts. If search fails or returns nothing, say so plainly in the final answer.
6. Do not use sandbox_exec unless the task explicitly requires running code."""


class ResearchAgent:
    """Domain specialist for research tasks (standalone-usable)."""

    def __init__(self) -> None:
        self.domain = "research"
        self.capabilities = {"research", "tools"}
        self.name = "Research Agent"
        self._log = structlog.get_logger(__name__)

    def system_prompt(self) -> str:
        """Return the research system prompt."""
        return RESEARCH_SYSTEM_PROMPT

    async def handle(self, message: str, context: dict[str, Any] | None = None) -> str:
        """Return the system prompt concatenated with the message."""
        _ = context
        return f"{RESEARCH_SYSTEM_PROMPT}\n\n{message}"


__all__ = ["RESEARCH_SYSTEM_PROMPT", "ResearchAgent"]
