"""Telegram configuration."""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

from dotenv import load_dotenv

# Load .env before reading env vars
load_dotenv()

_BETA_TRUE_VALUES = {"1", "true", "yes", "on"}
_BETA_FALSE_VALUES = {"", "0", "false", "no", "off"}


def _parse_beta_mode(explicit: bool | str | None) -> bool:
    if explicit is None:
        return os.environ.get("AGENCY_BETA_MODE", "").strip().lower() in _BETA_TRUE_VALUES
    if isinstance(explicit, bool):
        return explicit
    if isinstance(explicit, str):
        normalized = explicit.strip().lower()
        if normalized in _BETA_TRUE_VALUES:
            return True
        if normalized in _BETA_FALSE_VALUES:
            return False
        raise ValueError(f"invalid beta_mode value: {explicit!r}")
    return bool(explicit)


def _parse_env_allowlist(raw: str) -> frozenset[int]:
    parts = [p.strip() for p in raw.split(",")]
    ids: set[int] = set()
    for part in parts:
        if not part or not part.isdigit():
            raise ValueError(f"invalid TELEGRAM_ALLOWED_USER_IDS entry: {part!r}")
        value = int(part)
        if value <= 0:
            raise ValueError(f"invalid TELEGRAM_ALLOWED_USER_IDS entry: {part!r}")
        ids.add(value)
    if not ids:
        raise ValueError("beta mode requires a nonempty TELEGRAM_ALLOWED_USER_IDS list")
    return frozenset(ids)


def _normalize_explicit_allowlist(value: Iterable[int]) -> frozenset[int]:
    if isinstance(value, (str, bytes)):
        raise TypeError("allowed_user_ids must be an iterable of ints, not a string")
    try:
        items = list(value)
    except TypeError as exc:
        raise ValueError("allowed_user_ids must be an iterable of ints") from exc
    ids: set[int] = set()
    for item in items:
        if type(item) is not int or item <= 0:
            raise ValueError(f"invalid allowed_user_id: {item!r}")
        ids.add(item)
    return frozenset(ids)


class TelegramConfig:
    """Telegram bot configuration.

    Loads from environment variables with TELEGRAM_ prefix.
    """

    def __init__(
        self,
        beta_mode: bool | str | None = None,
        allowed_user_ids: Iterable[int] | None = None,
        **kwargs: Any,
    ) -> None:
        self.bot_token: str = kwargs.get("bot_token", "") or os.environ.get(
            "TELEGRAM_BOT_TOKEN", ""
        )
        self.api_base: str = kwargs.get("api_base", "") or os.environ.get(
            "TELEGRAM_API_BASE", "https://api.telegram.org"
        )
        self.webhook_url: str | None = kwargs.get("webhook_url", None) or os.environ.get(
            "TELEGRAM_WEBHOOK_URL"
        )
        self.webhook_secret: str | None = kwargs.get("webhook_secret", None) or os.environ.get(
            "TELEGRAM_WEBHOOK_SECRET"
        )
        self.allowed_chat_ids: list[int] = kwargs.get("allowed_chat_ids", []) or []
        self.beta_mode: bool = _parse_beta_mode(beta_mode)
        if self.beta_mode:
            if allowed_user_ids is not None:
                normalized = _normalize_explicit_allowlist(allowed_user_ids)
                if not normalized:
                    raise ValueError("beta mode requires a nonempty allowed_user_ids list")
                self.allowed_user_ids: frozenset[int] = normalized
            else:
                raw = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "")
                if not raw.strip():
                    raise ValueError("beta mode requires a nonempty TELEGRAM_ALLOWED_USER_IDS list")
                self.allowed_user_ids = _parse_env_allowlist(raw)
        else:
            # Non-beta never reads the ambient invite env var, so a stray
            # production allowlist cannot silently change developer behavior.
            if allowed_user_ids is not None:
                self.allowed_user_ids = _normalize_explicit_allowlist(allowed_user_ids)
            else:
                self.allowed_user_ids = frozenset()
        self.max_message_length: int = int(kwargs.get("max_message_length", 4096))
        self.timeout: float = float(kwargs.get("timeout", 30.0))
        self.parse_mode: str = kwargs.get("parse_mode", "Markdown")

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token)

    @property
    def api_url(self) -> str:
        return f"{self.api_base}/bot{self.bot_token}"
