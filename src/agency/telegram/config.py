"""Telegram configuration."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TelegramConfig(BaseSettings):
    """Telegram bot configuration.

    Loads from .env with TELEGRAM_ prefix.
    """

    model_config = SettingsConfigDict(env_prefix="TELEGRAM_", extra="ignore")

    bot_token: str = Field(default="", description="Telegram bot token")
    api_base: str = Field(
        default="https://api.telegram.org",
        description="Telegram Bot API base URL",
    )
    webhook_url: str | None = Field(default=None, description="Webhook URL")
    webhook_secret: str | None = Field(default=None, description="Webhook secret token")
    allowed_chat_ids: list[int] = Field(
        default_factory=list,
        description="Allowed chat IDs (empty = allow all)",
    )
    max_message_length: int = Field(default=4096, ge=1, le=4096)
    timeout: float = Field(default=30.0, gt=0)
    parse_mode: str = Field(default="Markdown", description="Message parse mode")

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token)

    @property
    def api_url(self) -> str:
        return f"{self.api_base}/bot{self.bot_token}"
