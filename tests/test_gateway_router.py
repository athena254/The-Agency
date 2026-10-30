"""Offline regression tests for the platform-agnostic gateway router.

These tests exercise the public adapter contract, normalization rules, routing
semantics, reply delivery, and lifecycle fan-out without touching any network
or platform SDK.
"""

from __future__ import annotations

from typing import Any

import pytest

from agency.gateway import GatewayRouter, InboundMessage, OutboundMessage
from agency.gateway.webhook import WebhookAdapter


class RecordingAdapter:
    """Minimal adapter that records lifecycle and outbound calls."""

    def __init__(self, platform: str, normalized: InboundMessage | None = None) -> None:
        self.platform = platform
        self.normalized = normalized
        self.started = 0
        self.stopped = 0
        self.sent: list[OutboundMessage] = []
        self.raw_seen: list[dict[str, Any]] = []

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1

    async def send(self, message: OutboundMessage) -> None:
        self.sent.append(message)

    def normalize(self, raw: dict[str, Any]) -> InboundMessage | None:
        self.raw_seen.append(raw)
        return self.normalized


class OrderedAdapter(RecordingAdapter):
    """Adapter that records lifecycle calls in a shared event log."""

    def __init__(self, platform: str, events: list[str]) -> None:
        super().__init__(platform)
        self._events = events

    async def start(self) -> None:
        self._events.append(f"start:{self.platform}")
        await super().start()

    async def stop(self) -> None:
        self._events.append(f"stop:{self.platform}")
        await super().stop()


async def _unexpected_handler(_message: InboundMessage) -> OutboundMessage | None:
    pytest.fail("handler must not be called")


# ------------------------------------------------------------------ #
# Data model
# ------------------------------------------------------------------ #


def test_inbound_message_defaults_are_not_shared() -> None:
    first = InboundMessage("alpha", "sender", "channel", "hello")
    second = InboundMessage("alpha", "sender", "channel", "hello")

    first.raw["key"] = "value"
    first.metadata["trace"] = "abc"

    assert second.raw == {}
    assert second.metadata == {}


def test_outbound_message_defaults_are_not_shared() -> None:
    first = OutboundMessage("alpha", "channel", "hello")
    second = OutboundMessage("alpha", "channel", "hello")

    first.metadata["key"] = "value"

    assert second.metadata == {}


# ------------------------------------------------------------------ #
# Registration and lookup
# ------------------------------------------------------------------ #


def test_register_get_platforms_and_replacement() -> None:
    router = GatewayRouter(_unexpected_handler)
    beta = RecordingAdapter("beta")
    alpha = RecordingAdapter("alpha")
    router.register(beta)
    router.register(alpha)

    assert router.platforms == ["alpha", "beta"]
    assert router.get("alpha") is alpha
    assert router.get("missing") is None

    replacement = RecordingAdapter("alpha")
    router.register(replacement)

    assert router.get("alpha") is replacement
    assert router.platforms == ["alpha", "beta"]


# ------------------------------------------------------------------ #
# Routing and replies
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_handle_raw_unknown_platform_returns_none() -> None:
    router = GatewayRouter(_unexpected_handler)

    assert await router.handle_raw("nowhere", {"text": "hello"}) is None


@pytest.mark.asyncio
async def test_handle_raw_unparseable_payload_returns_none_without_dispatch() -> None:
    adapter = RecordingAdapter("alpha", normalized=None)
    router = GatewayRouter(_unexpected_handler)
    router.register(adapter)
    raw = {"unexpected": "shape"}

    result = await router.handle_raw("alpha", raw)

    assert result is None
    assert adapter.raw_seen == [raw]
    assert adapter.sent == []


@pytest.mark.asyncio
async def test_handle_raw_routes_normalized_message_and_sends_matching_reply() -> None:
    inbound = InboundMessage("alpha", "sender", "channel", "hello", raw={"native": 1})
    adapter = RecordingAdapter("alpha", normalized=inbound)
    reply = OutboundMessage("alpha", "channel", "world")
    received: list[InboundMessage] = []

    async def handler(message: InboundMessage) -> OutboundMessage | None:
        received.append(message)
        return reply

    router = GatewayRouter(handler)
    router.register(adapter)
    raw = {"payload": 1}

    result = await router.handle_raw("alpha", raw)

    assert result is reply
    assert received == [inbound]
    assert adapter.raw_seen == [raw]
    assert adapter.sent == [reply]


@pytest.mark.asyncio
async def test_handle_raw_returns_cross_platform_reply_without_sending() -> None:
    inbound = InboundMessage("alpha", "sender", "channel", "hello")
    adapter = RecordingAdapter("alpha", normalized=inbound)
    reply = OutboundMessage("beta", "channel", "wrong platform")

    async def handler(_message: InboundMessage) -> OutboundMessage | None:
        return reply

    router = GatewayRouter(handler)
    router.register(adapter)

    result = await router.handle_raw("alpha", {})

    assert result is reply
    assert adapter.sent == []


@pytest.mark.asyncio
async def test_handle_raw_without_reply_does_not_send() -> None:
    inbound = InboundMessage("alpha", "sender", "channel", "hello")
    adapter = RecordingAdapter("alpha", normalized=inbound)

    async def handler(_message: InboundMessage) -> OutboundMessage | None:
        return None

    router = GatewayRouter(handler)
    router.register(adapter)

    assert await router.handle_raw("alpha", {}) is None
    assert adapter.sent == []


@pytest.mark.asyncio
async def test_router_send_dispatches_only_for_registered_platform() -> None:
    adapter = RecordingAdapter("alpha")
    router = GatewayRouter(_unexpected_handler)
    router.register(adapter)
    message = OutboundMessage("alpha", "channel", "hello")

    await router.send(message)
    await router.send(OutboundMessage("missing", "channel", "hello"))

    assert adapter.sent == [message]


# ------------------------------------------------------------------ #
# Lifecycle
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_start_all_and_stop_all_visit_every_adapter_once_in_order() -> None:
    events: list[str] = []
    first = OrderedAdapter("first", events)
    second = OrderedAdapter("second", events)
    router = GatewayRouter(_unexpected_handler)
    router.register(first)
    router.register(second)

    await router.start_all()
    await router.stop_all()

    assert first.started == 1
    assert second.started == 1
    assert first.stopped == 1
    assert second.stopped == 1
    assert events == ["start:first", "start:second", "stop:first", "stop:second"]


# ------------------------------------------------------------------ #
# Webhook normalization
# ------------------------------------------------------------------ #


@pytest.mark.parametrize("raw", [{"text": "hello"}, {"message": "hello"}, {"content": "hello"}])
def test_webhook_default_normalize_accepts_text_aliases(raw: dict[str, Any]) -> None:
    inbound = WebhookAdapter("hook").normalize(raw)

    assert inbound is not None
    assert inbound.text == "hello"
    assert inbound.platform == "webhook"
    assert inbound.sender_id == "unknown"
    assert inbound.channel_id == "unknown"
    assert inbound.raw is raw


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"text": ""},
        {"text": "   "},
        {"text": 123},
        {"message": None},
        {"content": ["not", "text"]},
    ],
    ids=["missing", "empty", "blank", "non-string", "none", "list"],
)
def test_webhook_default_normalize_rejects_missing_or_blank_text(
    raw: dict[str, Any],
) -> None:
    assert WebhookAdapter("hook").normalize(raw) is None


def test_webhook_default_normalize_strips_text_and_honors_overrides() -> None:
    raw = {
        "text": "  hello world  ",
        "sender_id": "user-1",
        "channel_id": "room-1",
        "platform": "custom",
    }

    inbound = WebhookAdapter("hook").normalize(raw)

    assert inbound is not None
    assert inbound.text == "hello world"
    assert inbound.sender_id == "user-1"
    assert inbound.channel_id == "room-1"
    assert inbound.platform == "custom"
    assert inbound.raw is raw


@pytest.mark.parametrize(
    ("raw", "sender", "channel"),
    [
        ({"text": "hi", "sender_id": "s", "user": "u", "from": "f"}, "s", "s"),
        ({"text": "hi", "user": "u", "from": "f"}, "u", "u"),
        ({"text": "hi", "from": "f"}, "f", "f"),
        ({"text": "hi", "sender_id": "s", "channel": "ch"}, "s", "ch"),
        ({"text": "hi", "sender_id": "s", "chat_id": "cid"}, "s", "cid"),
        ({"text": "hi", "sender_id": "s", "channel": "ch", "chat_id": "cid"}, "s", "ch"),
    ],
    ids=["sender-id-wins", "user-fallback", "from-fallback", "channel", "chat-id", "channel-wins"],
)
def test_webhook_default_normalize_sender_and_channel_aliases(
    raw: dict[str, Any], sender: str, channel: str
) -> None:
    inbound = WebhookAdapter("hook").normalize(raw)

    assert inbound is not None
    assert inbound.sender_id == sender
    assert inbound.channel_id == channel


def test_webhook_custom_normalizer_replaces_default() -> None:
    seen: list[dict[str, Any]] = []
    expected = InboundMessage("custom", "sender", "channel", "fixed")

    def normalize(raw: dict[str, Any]) -> InboundMessage | None:
        seen.append(raw)
        return expected

    adapter = WebhookAdapter("hook", normalize=normalize)
    raw = {"anything": True}

    assert adapter.normalize(raw) is expected
    assert seen == [raw]


def test_webhook_custom_normalizer_can_reject_payload() -> None:
    adapter = WebhookAdapter("hook", normalize=lambda _raw: None)

    assert adapter.normalize({"text": "hello"}) is None


# ------------------------------------------------------------------ #
# Webhook lifecycle and outbound hook
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_webhook_lifecycle_is_a_noop() -> None:
    adapter = WebhookAdapter("hook")

    assert await adapter.start() is None
    assert await adapter.stop() is None


@pytest.mark.asyncio
async def test_webhook_send_uses_hook_only_when_configured() -> None:
    sent: list[OutboundMessage] = []

    async def hook(message: OutboundMessage) -> None:
        sent.append(message)

    message = OutboundMessage("hook", "channel", "hello")
    await WebhookAdapter("hook", send_hook=hook).send(message)
    await WebhookAdapter("hook").send(message)

    assert sent == [message]


@pytest.mark.asyncio
async def test_webhook_adapter_routes_through_router_end_to_end() -> None:
    sent: list[OutboundMessage] = []

    async def hook(message: OutboundMessage) -> None:
        sent.append(message)

    async def handler(inbound: InboundMessage) -> OutboundMessage | None:
        return OutboundMessage("hook", inbound.channel_id, f"echo:{inbound.text}")

    adapter = WebhookAdapter("hook", send_hook=hook)
    router = GatewayRouter(handler)
    router.register(adapter)

    reply = await router.handle_raw("hook", {"text": " ping ", "sender_id": "user-1"})

    assert reply == OutboundMessage("hook", "user-1", "echo:ping")
    assert sent == [reply]
