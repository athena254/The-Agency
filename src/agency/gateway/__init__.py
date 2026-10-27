"""Multi-platform gateway foundation for The Agency.

Agency's Butler already fronts HTTP/Telegram. This package adds a small,
platform-agnostic adapter/router layer so additional platforms (Discord,
Slack, WhatsApp, Matrix, ...) can be plugged in without touching Butler or
the orchestrator. Each adapter normalizes its native payload into the common
:class:`InboundMessage` shape; the router dispatches to a single handler.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class InboundMessage:
    """A normalized inbound message from any platform."""

    platform: str
    sender_id: str
    channel_id: str
    text: str
    raw: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutboundMessage:
    """A normalized outbound reply."""

    platform: str
    channel_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


MessageHandler = Callable[[InboundMessage], Awaitable[OutboundMessage | None]]


class GatewayAdapter(Protocol):
    """Contract every platform adapter implements."""

    platform: str

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def send(self, message: OutboundMessage) -> None: ...

    def normalize(self, raw: dict[str, Any]) -> InboundMessage | None: ...


class GatewayRouter:
    """Holds adapters and routes inbound messages to one handler."""

    def __init__(self, handler: MessageHandler) -> None:
        self._handler = handler
        self._adapters: dict[str, GatewayAdapter] = {}

    def register(self, adapter: GatewayAdapter) -> None:
        self._adapters[adapter.platform] = adapter

    def get(self, platform: str) -> GatewayAdapter | None:
        return self._adapters.get(platform)

    @property
    def platforms(self) -> list[str]:
        return sorted(self._adapters)

    async def start_all(self) -> None:
        for adapter in self._adapters.values():
            await adapter.start()

    async def stop_all(self) -> None:
        for adapter in self._adapters.values():
            await adapter.stop()

    async def handle_raw(self, platform: str, raw: dict[str, Any]) -> OutboundMessage | None:
        adapter = self._adapters.get(platform)
        if adapter is None:
            return None
        inbound = adapter.normalize(raw)
        if inbound is None:
            return None
        reply = await self._handler(inbound)
        if reply is not None and reply.platform == platform:
            await adapter.send(reply)
        return reply

    async def send(self, message: OutboundMessage) -> None:
        adapter = self._adapters.get(message.platform)
        if adapter is not None:
            await adapter.send(message)
