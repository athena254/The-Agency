"""Generic webhook adapter: inbound via HTTP POST, outbound via callback.

Useful as a bridge for any platform that can POST JSON to Agency and accept
a JSON reply. Platform-specific adapters can subclass or replace normalize().
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from agency.gateway import InboundMessage, OutboundMessage


class WebhookAdapter:
    """Platform-agnostic adapter driven by an inbound normalizer + send hook."""

    def __init__(
        self,
        platform: str,
        normalize: Callable[[dict[str, Any]], InboundMessage | None] | None = None,
        send_hook: Callable[[OutboundMessage], Awaitable[None]] | None = None,
    ) -> None:
        self.platform = platform
        self._normalize = normalize or self._default_normalize
        self._send_hook = send_hook

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(self, message: OutboundMessage) -> None:
        if self._send_hook is not None:
            await self._send_hook(message)

    def normalize(self, raw: dict[str, Any]) -> InboundMessage | None:
        return self._normalize(raw)

    @staticmethod
    def _default_normalize(raw: dict[str, Any]) -> InboundMessage | None:
        text = raw.get("text") or raw.get("message") or raw.get("content")
        if not isinstance(text, str) or not text.strip():
            return None
        sender = str(raw.get("sender_id") or raw.get("user") or raw.get("from") or "unknown")
        channel = str(raw.get("channel_id") or raw.get("channel") or raw.get("chat_id") or sender)
        platform = str(raw.get("platform") or "webhook")
        return InboundMessage(
            platform=platform,
            sender_id=sender,
            channel_id=channel,
            text=text.strip(),
            raw=raw,
        )
