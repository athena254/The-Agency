"""Telegram Bot API adapter."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

from agency.telegram.config import TelegramConfig

logger = structlog.get_logger(__name__)


class TelegramAdapter:
    """Async adapter for the Telegram Bot API.

    Supports sending messages, getting updates, and managing webhooks.
    """

    def __init__(self, config: TelegramConfig) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None
        self._log = structlog.get_logger(__name__)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._config.api_url,
                timeout=self._config.timeout,
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_me(self) -> dict[str, Any]:
        """Get bot info."""
        client = await self._get_client()
        resp = await client.get("/getMe")
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
        return data["result"]

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        parse_mode: str | None = None,
        disable_web_page_preview: bool = True,
    ) -> dict[str, Any]:
        """Send a message to a chat."""
        # Split long messages
        chunks = self._split_message(text, self._config.max_message_length)
        results = []
        for chunk in chunks:
            client = await self._get_client()
            payload: dict[str, Any] = {
                "chat_id": str(chat_id),
                "text": chunk,
                "disable_web_page_preview": disable_web_page_preview,
            }
            if parse_mode or self._config.parse_mode:
                payload["parse_mode"] = parse_mode or self._config.parse_mode

            resp = await client.post("/sendMessage", json=payload)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram API error: {data}")
            results.append(data["result"])
            # Small delay between chunks to avoid rate limits
            if len(chunks) > 1:
                await asyncio.sleep(0.5)

        return results[-1] if results else {}

    async def get_updates(
        self,
        offset: int | None = None,
        limit: int = 100,
        timeout: int = 30,
    ) -> list[dict[str, Any]]:
        """Poll for new updates."""
        client = await self._get_client()
        params: dict[str, Any] = {"limit": limit, "timeout": timeout}
        if offset is not None:
            params["offset"] = offset

        resp = await client.get("/getUpdates", params=params)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
        return data.get("result", [])

    async def set_webhook(self, url: str, secret_token: str | None = None) -> bool:
        """Set webhook URL."""
        client = await self._get_client()
        payload: dict[str, Any] = {"url": url}
        if secret_token:
            payload["secret_token"] = secret_token

        resp = await client.post("/setWebhook", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data.get("ok", False)

    async def delete_webhook(self) -> bool:
        """Delete webhook."""
        client = await self._get_client()
        resp = await client.post("/deleteWebhook")
        resp.raise_for_status()
        data = resp.json()
        return data.get("ok", False)

    @staticmethod
    def _split_message(text: str, max_length: int) -> list[str]:
        """Split a long message into chunks."""
        if len(text) <= max_length:
            return [text]

        chunks = []
        while text:
            if len(text) <= max_length:
                chunks.append(text)
                break
            # Find a good break point
            break_at = text.rfind("\n", 0, max_length)
            if break_at < max_length // 2:
                break_at = max_length
            chunks.append(text[:break_at])
            text = text[break_at:].lstrip()
        return chunks
