"""Telegram configuration."""

from __future__ import annotations

import os

from dotenv import load_dotenv

# Load .env before reading env vars
load_dotenv()


class TelegramConfig:
    """Telegram bot configuration.

    Loads from environment variables with TELEGRAM_ prefix.
    """

    def __init__(self, **kwargs) -> None:
        self.bot_token: str = kwargs.get("bot_token", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.api_base: str = kwargs.get("api_base", "") or os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")
        self.webhook_url: str | None = kwargs.get("webhook_url", None) or os.environ.get("TELEGRAM_WEBHOOK_URL")
        self.webhook_secret: str | None = kwargs.get("webhook_secret", None) or os.environ.get("TELEGRAM_WEBHOOK_SECRET")
        self.allowed_chat_ids: list[int] = kwargs.get("allowed_chat_ids", []) or []
        self.max_message_length: int = int(kwargs.get("max_message_length", 4096))
        self.timeout: float = float(kwargs.get("timeout", 30.0))
        self.parse_mode: str = kwargs.get("parse_mode", "Markdown")

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token)

    @property
    def api_url(self) -> str:
        return f"{self.api_base}/bot{self.bot_token}"
