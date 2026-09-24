"""Tests for plain-English agent creation intent detection."""

from unittest.mock import AsyncMock

import pytest

from agency.telegram.handler import TelegramHandler


@pytest.mark.asyncio
async def test_username_less_users_have_distinct_butler_memory_identities() -> None:
    from agency.telegram.config import TelegramConfig

    butler = AsyncMock()
    butler.handle_message.return_value = "ok"
    handler = TelegramHandler(TelegramConfig(bot_token="test"), butler=butler)
    handler._adapter.send_message = AsyncMock()
    for user_id in (101, 202):
        await handler.handle_update(
            {"message": {"chat": {"id": user_id}, "from": {"id": user_id}, "text": "hello"}}
        )
    senders = [call.args[1] for call in butler.handle_message.await_args_list]
    assert senders == ["telegram:101", "telegram:202"]


@pytest.mark.asyncio
async def test_missing_telegram_user_id_cannot_enter_butler_memory() -> None:
    from agency.telegram.config import TelegramConfig

    butler = AsyncMock()
    butler.handle_message.return_value = "ok"
    handler = TelegramHandler(TelegramConfig(bot_token="test"), butler=butler)
    handler._adapter.send_message = AsyncMock()
    result = await handler.handle_update(
        {"message": {"chat": {"id": 123}, "from": {"username": "alice"}, "text": "hello"}}
    )
    assert result["status"] == "rejected"
    butler.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_message_uses_stable_user_id_too() -> None:
    from agency.telegram.config import TelegramConfig

    butler = AsyncMock()
    butler.handle_message.return_value = "ok"
    handler = TelegramHandler(TelegramConfig(bot_token="test"), butler=butler)
    await handler.process_message({"text": "hello", "from": {"id": 505}})
    assert butler.handle_message.await_args.args[1] == "telegram:505"
    with pytest.raises(ValueError, match="user ID"):
        await handler.process_message({"text": "hello", "from": {"username": "alice"}})


@pytest.fixture
def handler():
    """Create a handler with no butler for intent parsing tests."""
    from agency.telegram.config import TelegramConfig

    config = TelegramConfig(bot_token="test", allowed_chat_ids=["123"])
    return TelegramHandler(config=config, butler=None)


class TestDetectAgentCreationIntent:
    """Test the _detect_agent_creation_intent parser."""

    def test_create_agent_minimal(self, handler):
        result = handler._detect_agent_creation_intent("create a new agent")
        assert result == {}

    def test_make_bot_called(self, handler):
        result = handler._detect_agent_creation_intent("make a bot called StockBot")
        assert result.get("name") == "StockBot"

    def test_named_agent(self, handler):
        result = handler._detect_agent_creation_intent(
            "create agent named HealthTracker that tracks medical records"
        )
        assert result.get("name") == "HealthTracker"
        assert "tracks medical records" in result.get("purpose", "")

    def test_spawn_for_domain(self, handler):
        result = handler._detect_agent_creation_intent("spawn agent for finance")
        assert result.get("domain") == "finance"

    def test_agent_with_capabilities(self, handler):
        result = handler._detect_agent_creation_intent(
            "new agent called CryptoAgent that can analyze and predict prices"
        )
        assert result.get("name") == "CryptoAgent"
        assert "analyze" in result.get("capabilities", [])

    def test_no_filler_words(self, handler):
        result = handler._detect_agent_creation_intent("please create a new agent")
        assert result == {}

    def test_variety_of_domains(self, handler):
        for domain in ["finance", "health", "research", "coding", "trading"]:
            result = handler._detect_agent_creation_intent(f"create agent for {domain}")
            assert result.get("domain") == domain

    def test_empty_after_cleanup(self, handler):
        result = handler._detect_agent_creation_intent("agent")
        assert result == {} or result is None

    def test_complex_sentence(self, handler):
        result = handler._detect_agent_creation_intent(
            "i'd like a new agent called ResearchBot for academic research that summarizes papers and finds citations"
        )
        assert result.get("name") == "ResearchBot"
        assert result.get("domain") == "academic"
