"""Telegram API failures must not expose bot tokens or response bodies."""

from __future__ import annotations

import httpx
import pytest

from agency.telegram.adapter import TelegramAdapter
from agency.telegram.config import TelegramConfig
from telegram_bot import _is_poll_conflict


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get_me", "set_my_commands", "send_message", "get_updates"])
async def test_rejected_telegram_payload_is_generic(method: str) -> None:
    secret = "SYNTHETIC_PRIVATE_DESCRIPTION"
    adapter = TelegramAdapter(TelegramConfig(bot_token="FAKE_TOKEN"))
    adapter._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"ok": False, "description": secret})
        ),
        base_url="https://api.telegram.org/botFAKE_TOKEN",
    )
    try:
        with pytest.raises(RuntimeError) as excinfo:
            if method == "set_my_commands":
                await adapter.set_my_commands([])
            elif method == "send_message":
                await adapter.send_message(123, "synthetic message")
            else:
                await getattr(adapter, method)()
        assert secret not in str(excinfo.value)
        assert "FAKE_TOKEN" not in str(excinfo.value)
    finally:
        await adapter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 409, 429])
async def test_http_failure_preserves_status_without_token_or_body(status: int) -> None:
    secret = "SYNTHETIC_PRIVATE_RESPONSE"
    token = "FAKE_TOKEN"
    adapter = TelegramAdapter(TelegramConfig(bot_token=token))
    adapter._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, text=secret)),
        base_url=f"https://api.telegram.org/bot{token}",
    )
    try:
        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            await adapter.get_updates()
        error = excinfo.value
        assert error.response.status_code == status
        assert secret not in str(error)
        assert token not in str(error)
        assert token not in str(error.request.url)
        assert token not in str(error.response.request.url)
        assert _is_poll_conflict(error) is (status == 409)
    finally:
        await adapter.close()
