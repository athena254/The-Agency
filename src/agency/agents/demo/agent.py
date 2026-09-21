"""Demo agent for The Agency."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class DemoAgent:
    """A simple demo agent for testing via Telegram.

    Responds to basic commands and echoes messages.
    Works without API keys (echo mode).
    """

    def __init__(self, name: str = "demo", prefix: str = "🤖") -> None:
        self._name = name
        self._prefix = prefix
        self._log = structlog.get_logger(__name__)

    async def handle(self, message: str, context: dict[str, Any] | None = None) -> str:
        """Handle a message and return a response."""
        text = message.strip()
        lower = text.lower()

        if lower in ("/start", "hello", "hi", "hey"):
            return self._welcome()
        elif lower in ("/help", "help"):
            return self._help()
        elif lower in ("/status", "status"):
            return self._status()
        elif lower.startswith("scan"):
            return self._scan(text)
        elif lower.startswith("remember"):
            return self._remember(text)
        else:
            return self._echo(text)

    async def get_info(self) -> dict[str, Any]:
        """Get agent info."""
        return {
            "name": self._name,
            "type": "demo",
            "capabilities": ["echo", "help", "status", "scan", "remember"],
        }

    def _welcome(self) -> str:
        return (
            f"{self._prefix} *Welcome to The Agency!*\n\n"
            "I'm your demo agent. I can:\n"
            "• Respond to greetings\n"
            "• Show help (`/help`)\n"
            "• Show status (`/status`)\n"
            "• Run mock scans (`scan <target>`)\n"
            "• Remember things (`remember <text>`)\n"
            "• Echo anything else\n\n"
            "Try: `scan the target for vulnerabilities`"
        )

    def _help(self) -> str:
        return (
            f"{self._prefix} *Available Commands*\n\n"
            "`/start` — Welcome message\n"
            "`/help` — This help text\n"
            "`/status` — System status\n"
            "`scan <target>` — Mock security scan\n"
            "`remember <text>` — Store in memory\n"
            "Anything else — Echo response"
        )

    def _status(self) -> str:
        return (
            f"{self._prefix} *System Status*\n\n"
            f"Agent: {self._name}\n"
            f"Time: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            "Mode: echo (no API key)\n"
            "Memory: ok\n"
            "Evidence: ok\n"
            "Risk: ok"
        )

    def _scan(self, text: str) -> str:
        target = text[4:].strip() or "target"
        return (
            f"{self._prefix} *Mock Security Scan*\n\n"
            f"Target: `{target}`\n"
            "Status: ✅ Completed\n"
            "Findings: 0 critical, 0 high, 1 low\n"
            "Duration: 0.01s\n\n"
            "_This is a demo scan. Real scans require configuration._"
        )

    def _remember(self, text: str) -> str:
        content = text[8:].strip() or "nothing"
        return (
            f"{self._prefix} *Remembered*\n\n"
            f"Stored: `{content}`\n"
            "Memory tier: NORMAL\n"
            "Auto-aging: enabled"
        )

    def _echo(self, text: str) -> str:
        return f"{self._prefix} Echo: {text}"
