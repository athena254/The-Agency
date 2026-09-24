"""Telegram webhook authentication and body parsing boundaries."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.requests import Request

from agency.telegram.config import TelegramConfig
from agency.telegram.server import create_app

_AUTH = [(b"x-telegram-bot-api-secret-token", b"test")]


class _NoopHandler:
    async def handle_update(self, _body: dict[str, Any]) -> None:
        pytest.fail("invalid requests must not reach the handler")

    async def close(self) -> None:
        pass


def _endpoint(secret: str = "test") -> Any:
    app = create_app(TelegramConfig(bot_token="test", webhook_secret=secret), _NoopHandler())
    return next(route.endpoint for route in app.routes if route.path == "/telegram/webhook")


@pytest.mark.asyncio
async def test_webhook_without_configured_secret_denies_forged_update() -> None:
    async def receive() -> dict[str, Any]:
        return {
            "type": "http.request",
            "body": b'{"message":{"text":"create agent"}}',
            "more_body": False,
        }

    request = Request(
        {"type": "http", "method": "POST", "path": "/telegram/webhook", "headers": []}, receive
    )
    assert (await _endpoint("")(request)).status_code == 503


@pytest.mark.asyncio
async def test_webhook_checks_secret_before_reading_body() -> None:
    async def receive() -> dict[str, Any]:
        pytest.fail("unauthenticated request body must not be read")

    request = Request(
        {"type": "http", "method": "POST", "path": "/telegram/webhook", "headers": []}, receive
    )
    assert (await _endpoint()(request)).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"{", b"[]", b"null"], ids=["invalid-json", "array", "null"])
async def test_malformed_webhook_body_returns_400(body: bytes) -> None:
    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {"type": "http", "method": "POST", "path": "/telegram/webhook", "headers": _AUTH}, receive
    )
    assert (await _endpoint()(request)).status_code == 400


@pytest.mark.asyncio
async def test_disconnected_webhook_body_returns_400() -> None:
    async def receive() -> dict[str, str]:
        return {"type": "http.disconnect"}

    request = Request(
        {"type": "http", "method": "POST", "path": "/telegram/webhook", "headers": _AUTH}, receive
    )
    assert (await _endpoint()(request)).status_code == 400
