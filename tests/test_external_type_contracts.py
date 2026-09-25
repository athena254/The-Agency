"""Hermetic checks for external provider response contracts."""

from __future__ import annotations

import httpx
import pytest

from agency.llm.adapter import LLMAdapter
from agency.telegram.adapter import TelegramAdapter
from agency.telegram.config import TelegramConfig


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload", [{"ok": True, "result": []}, {"ok": True, "result": "not a bot"}]
)
async def test_telegram_get_me_rejects_non_object_result(payload: object) -> None:
    adapter = TelegramAdapter(TelegramConfig(bot_token="test"))
    adapter._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
        base_url="https://example.test",
    )
    try:
        with pytest.raises(ValueError, match="Telegram.*result"):
            await adapter.get_me()
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_telegram_updates_reject_non_object_members() -> None:
    adapter = TelegramAdapter(TelegramConfig(bot_token="test"))
    adapter._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"ok": True, "result": [{"update_id": 1}, "bad"]}
            )
        ),
        base_url="https://example.test",
    )
    try:
        with pytest.raises(ValueError, match="Telegram.*result"):
            await adapter.get_updates()
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_telegram_valid_responses_are_preserved() -> None:
    """Strict validation must not reject well-formed Bot API payloads."""
    me = {"ok": True, "result": {"id": 42, "username": "agency_bot"}}
    updates = {"ok": True, "result": [{"update_id": 7, "message": {"text": "hi"}}]}
    adapter = TelegramAdapter(TelegramConfig(bot_token="test"))
    payloads = [me, updates]
    adapter._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payloads.pop(0))),
        base_url="https://example.test",
    )
    try:
        assert await adapter.get_me() == me["result"]
        assert await adapter.get_updates() == updates["result"]
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_explicit_llm_backend_rejects_non_text_content_without_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": None}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    adapter = LLMAdapter(provider="openai", model="test")
    prompt = "private prompt"
    # The adapter already refuses non-text content. That refusal is a
    # TypeError (wrong content type), not a ValueError, and its message must
    # stay generic so it can never echo the private prompt back to the caller.
    with pytest.raises(TypeError, match="LLM response content must be text") as excinfo:
        await adapter.generate(prompt, {"provider": "openai", "strict": True})
    assert prompt not in str(excinfo.value)
