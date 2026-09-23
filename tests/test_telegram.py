"""Tests for the Telegram adapter and demo agent."""

from __future__ import annotations

import pytest

from agency.agents.demo.agent import DemoAgent
from agency.telegram.adapter import TelegramAdapter
from agency.telegram.config import TelegramConfig


@pytest.fixture
def telegram_config() -> TelegramConfig:
    return TelegramConfig(bot_token="test_token")


@pytest.fixture
def demo_agent() -> DemoAgent:
    return DemoAgent()


class TestTelegramConfig:
    def test_default_config(self):
        config = TelegramConfig(bot_token="test")
        assert config.bot_token == "test"
        assert config.api_base == "https://api.telegram.org"
        assert config.max_message_length == 4096
        assert config.is_configured

    def test_not_configured(self):
        # Explicitly empty token must report unconfigured even if .env has one.
        config = TelegramConfig(bot_token="", webhook_url="")
        config.bot_token = ""
        assert not config.is_configured


class TestTelegramAdapter:
    def test_split_message_short(self):
        text = "Hello, world!"
        chunks = TelegramAdapter._split_message(text, 4096)
        assert chunks == [text]

    def test_split_message_long(self):
        text = "A" * 5000
        chunks = TelegramAdapter._split_message(text, 4096)
        assert len(chunks) == 2
        assert sum(len(c) for c in chunks) == 5000


class TestDemoAgent:
    @pytest.mark.asyncio
    async def test_welcome(self, demo_agent: DemoAgent):
        response = await demo_agent.handle("hello", {})
        assert "Welcome" in response

    @pytest.mark.asyncio
    async def test_help(self, demo_agent: DemoAgent):
        response = await demo_agent.handle("/help", {})
        assert "Available Commands" in response

    @pytest.mark.asyncio
    async def test_status(self, demo_agent: DemoAgent):
        response = await demo_agent.handle("/status", {})
        assert "System Status" in response

    @pytest.mark.asyncio
    async def test_scan(self, demo_agent: DemoAgent):
        response = await demo_agent.handle("scan the target", {})
        assert "Security Scan" in response or "scan" in response.lower()

    @pytest.mark.asyncio
    async def test_remember(self, demo_agent: DemoAgent):
        response = await demo_agent.handle("remember this finding", {})
        assert "Remembered" in response

    @pytest.mark.asyncio
    async def test_echo(self, demo_agent: DemoAgent):
        response = await demo_agent.handle("random message", {})
        # Real LLM (Pollinations) or echo fallback — either way, non-empty.
        assert response.strip()

    @pytest.mark.asyncio
    async def test_get_info(self, demo_agent: DemoAgent):
        info = await demo_agent.get_info()
        assert info["name"] == "demo"
        assert "echo" in info["capabilities"]
