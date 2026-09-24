"""Webhook body parsing handles malformed JSON and interrupted clients."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.requests import Request

from agency.telegram.config import TelegramConfig
from agency.telegram.server import create_app


class _NoopHandler:
    async def handle_update(self, _body: dict[str, Any]) -> None:
        pytest.fail("a malformed or disconnected body must not reach the handler")

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [b"{", b"[]", b"null"], ids=["invalid-json", "array", "null"]
)
async def test_malformed_webhook_body_returns_400(body: bytes) -> None:
    app = create_app(TelegramConfig(bot_token="test", webhook_secret="test"), _NoopHandler())
    endpoint = next(route.endpoint for route in app.routes if route.path == "/telegram/webhook")

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request({"type": "http", "method": "POST", "path": "/telegram/webhook", "headers": []}, receive)
    assert (await endpoint(request)).status_code == 400


@pytest.mark.asyncio
async def test_disconnected_webhook_body_returns_400() -> None:
    app = create_app(TelegramConfig(bot_token="test", webhook_secret="test"), _NoopHandler())
    endpoint = next(route.endpoint for route in app.routes if route.path == "/telegram/webhook")

    async def receive() -> dict[str, str]:
        return {"type": "http.disconnect"}

    request = Request({"type": "http", "method": "POST", "path": "/telegram/webhook", "headers": []}, receive)
    assert (await endpoint(request)).status_code == 400
