"""No Telegram route may manufacture governance votes without peer consensus."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agency.telegram.config import TelegramConfig
from agency.telegram.handler import TelegramHandler
from telegram_bot import TelegramBot


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "/propose_agent StockBot finance analyze",
        "/propose_agent@RemexMemorybot StockBot finance analyze",
        "/propose-agent StockBot finance analyze",
        "Please create one called StockBot for finance",
        "Please set up an agent named ResearchBot",
        "Can you spin up a bot called ResearchBot?",
        "create a new agent called StockBot",
    ],
)
async def test_nonbeta_telegram_creation_does_not_submit_or_vote(text: str) -> None:
    lattice = SimpleNamespace(submit_proposal=AsyncMock())
    orchestrator = SimpleNamespace(_lattice=lattice, resolve_agent_proposal=AsyncMock())
    butler = SimpleNamespace(orchestrator=orchestrator, handle_message=AsyncMock())
    handler = TelegramHandler(TelegramConfig(bot_token="test", beta_mode=False), butler=butler)
    handler._adapter.send_message = AsyncMock()
    try:
        result = await handler.handle_update(
            {"message": {"from": {"id": 101}, "chat": {"id": 101, "type": "private"}, "text": text}}
        )
        assert result == {"status": "rejected", "reason": "agent creation disabled"}
        lattice.submit_proposal.assert_not_awaited()
        orchestrator.resolve_agent_proposal.assert_not_awaited()
        butler.handle_message.assert_not_awaited()
    finally:
        await handler.close()


@pytest.mark.asyncio
async def test_direct_creation_helper_cannot_cast_synthetic_votes() -> None:
    lattice = SimpleNamespace(submit_proposal=AsyncMock())
    orchestrator = SimpleNamespace(_lattice=lattice, resolve_agent_proposal=AsyncMock())
    handler = TelegramHandler(
        TelegramConfig(bot_token="test"), butler=SimpleNamespace(orchestrator=orchestrator)
    )
    try:
        response = await handler._handle_propose_agent("StockBot finance analyze", "telegram:101")
        assert "disabled" in response.lower()
        lattice.submit_proposal.assert_not_awaited()
        orchestrator.resolve_agent_proposal.assert_not_awaited()
    finally:
        await handler.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text", ["create an agent called StockBot", "/propose_agent@RemexMemorybot X d c"]
)
async def test_process_message_nonbeta_creation_is_refused_before_butler(text: str) -> None:
    butler = SimpleNamespace(handle_message=AsyncMock())
    handler = TelegramHandler(TelegramConfig(bot_token="test", beta_mode=False), butler=butler)
    try:
        reply = await handler.process_message({"from": {"id": 101}, "text": text})
        assert "disabled" in reply.lower()
        butler.handle_message.assert_not_awaited()
    finally:
        await handler.close()


def test_nonbeta_command_menu_hides_unavailable_governance() -> None:
    bot = object.__new__(TelegramBot)
    bot._config = TelegramConfig(bot_token="test", beta_mode=False)
    names = {command["command"] for command in bot.commands_for_mode()}
    assert "propose_agent" not in names
    assert "proposals" not in names
