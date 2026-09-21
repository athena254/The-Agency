"""Telegram message handler."""

from __future__ import annotations

from typing import Any

import structlog

from agency.telegram.adapter import TelegramAdapter
from agency.telegram.config import TelegramConfig

logger = structlog.get_logger(__name__)


class TelegramHandler:
    """Handles incoming Telegram messages and routes them to the Butler."""

    def __init__(self, config: TelegramConfig, butler: Any = None) -> None:
        self._config = config
        self._butler = butler
        self._adapter = TelegramAdapter(config)
        self._log = structlog.get_logger(__name__)

    async def handle_update(self, update: dict[str, Any]) -> dict[str, Any]:
        """Handle a single Telegram update."""
        message = update.get("message", {})
        if not message:
            return {"status": "ignored", "reason": "no message"}

        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "")
        sender = message.get("from", {}).get("username", "unknown")

        if not text:
            return {"status": "ignored", "reason": "empty message"}

        # Check allowed chat IDs
        if self._config.allowed_chat_ids and chat_id not in self._config.allowed_chat_ids:
            return {"status": "rejected", "reason": "chat not allowed"}

        # Process via Butler if available
        if self._butler:
            response = await self._butler.handle_message(text, sender, {"chat_id": chat_id})
        else:
            response = f"Echo: {text}"

        # Send response
        if chat_id:
            await self._adapter.send_message(chat_id, response)

        return {"status": "ok", "chat_id": chat_id, "response_length": len(response)}

    async def process_message(self, message: dict[str, Any]) -> str:
        """Process a message and return the response text."""
        text = message.get("text", "")
        sender = message.get("from", {}).get("username", "unknown")

        if self._butler:
            return await self._butler.handle_message(text, sender, {})
        return f"Echo: {text}"

    async def send_response(self, chat_id: int, text: str) -> dict[str, Any]:
        """Send a response to a Telegram chat."""
        return await self._adapter.send_message(chat_id, text)

    async def close(self) -> None:
        await self._adapter.close()
