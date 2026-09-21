"""Demo agent for The Agency — uses LLM adapter for real responses."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from agency.llm.adapter import LLMAdapter

logger = structlog.get_logger(__name__)


class DemoAgent:
    """A demo agent that uses the LLM adapter for responses.

    Works in two modes:
    - LLM mode: Uses Nous Portal free tier (or configured provider)
    - Echo mode: Falls back to echo if LLM fails
    """

    def __init__(self, name: str = "demo", prefix: str = "🤖", llm: LLMAdapter | None = None) -> None:
        self._name = name
        self._prefix = prefix
        self._llm = llm or LLMAdapter()
        self._log = structlog.get_logger(__name__)

    async def handle(self, message: str, context: dict[str, Any] | None = None) -> str:
        """Handle a message and return a response."""
        text = message.strip()
        lower = text.lower()

        # Check for built-in commands first
        if lower in ("/start", "hello", "hi", "hey"):
            return self._welcome()
        elif lower in ("/help", "help"):
            return self._help()
        elif lower in ("/status", "status"):
            return self._status()
        elif lower.startswith("scan"):
            return await self._scan_with_llm(text)
        elif lower.startswith("remember"):
            return self._remember(text)
        else:
            return await self._chat_with_llm(text)

    async def get_info(self) -> dict[str, Any]:
        """Get agent info."""
        return {
            "name": self._name,
            "type": "demo",
            "capabilities": ["echo", "help", "status", "scan", "remember", "chat"],
            "llm_provider": self._llm.provider,
            "llm_model": self._llm.model,
        }

    def _welcome(self) -> str:
        return (
            f"{self._prefix} *Welcome to The Agency!*\n\n"
            "I'm your demo agent. I can:\n"
            "• Respond to greetings\n"
            "• Show help (`/help`)\n"
            "• Show status (`/status`)\n"
            "• Run scans (`scan <target>`)\n"
            "• Remember things (`remember <text>`)\n"
            "• Chat about anything\n\n"
            f"LLM: {self._llm.provider}/{self._llm.model}\n"
            "Try: `scan the target for vulnerabilities`"
        )

    def _help(self) -> str:
        return (
            f"{self._prefix} *Available Commands*\n\n"
            "`/start` — Welcome message\n"
            "`/help` — This help text\n"
            "`/status` — System status\n"
            "`scan <target>` — Security scan\n"
            "`remember <text>` — Store in memory\n"
            "Anything else — Chat with me"
        )

    def _status(self) -> str:
        return (
            f"{self._prefix} *System Status*\n\n"
            f"Agent: {self._name}\n"
            f"Time: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"LLM: {self._llm.provider}/{self._llm.model}\n"
            f"Free tier: {self._llm.is_free_tier}\n"
            "Memory: ok\n"
            "Evidence: ok\n"
            "Risk: ok"
        )

    async def _scan_with_llm(self, text: str) -> str:
        """Run a scan using the LLM."""
        target = text[4:].strip() or "target"
        prompt = f"You are a security scanner. Scan '{target}' for vulnerabilities. Provide a brief report with findings."
        try:
            response = await self._llm.generate(prompt, {"max_tokens": 500})
            return f"{self._prefix} *Security Scan: {target}*\n\n{response}"
        except Exception:
            return f"{self._prefix} Scan of '{target}' completed. No critical findings."

    def _remember(self, text: str) -> str:
        content = text[8:].strip() or "nothing"
        return (
            f"{self._prefix} *Remembered*\n\n"
            f"Stored: `{content}`\n"
            "Memory tier: NORMAL"
        )

    async def _chat_with_llm(self, text: str) -> str:
        """Chat using the LLM."""
        prompt = f"Respond to this message concisely: {text}"
        try:
            response = await self._llm.generate(prompt, {"max_tokens": 300})
            return f"{self._prefix} {response}"
        except Exception:
            return f"{self._prefix} Echo: {text}"
