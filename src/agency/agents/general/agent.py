"""GeneralAgent — conversational assistant with tool support."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

GENERAL_SYSTEM_PROMPT = """You are the General Agent of The Agency — a helpful, honest conversational assistant.
Rules:
1. You are the General Agent of The Agency — a helpful, honest conversational assistant.
2. For casual conversation, greetings, and questions you can confidently answer, respond directly with {"final": "..."} — do NOT call tools unnecessarily.
3. For factual questions about current events, versions, prices, news, or anything time-sensitive, call web_search first.
4. Use memory_query to recall what the user told you earlier when relevant.
5. When the user shares a fact, preference, or instruction worth remembering, store it with memory_write.
6. Do NOT use sandbox_exec unless the user explicitly asks to run code.
7. Be efficient: at most 4 tool calls. Most conversations need zero.
8. NEVER invent sources or facts. If you don't know and can't look it up, say so.
9. Always end with {"final": "<your reply>"} — keep replies concise and natural."""


class GeneralAgent:
    """Default conversational agent for general chat (standalone-usable)."""

    def __init__(self) -> None:
        self.domain = "general"
        self.capabilities = {"general", "respond", "tools"}
        self.name = "General Agent"
        self._log = structlog.get_logger(__name__)

    def system_prompt(self) -> str:
        """Return the general system prompt."""
        return GENERAL_SYSTEM_PROMPT

    async def handle(self, message: str, context: dict[str, Any] | None = None) -> str:
        """Return the system prompt concatenated with the message."""
        _ = context
        return f"{GENERAL_SYSTEM_PROMPT}\n\n{message}"


__all__ = ["GENERAL_SYSTEM_PROMPT", "GeneralAgent"]
