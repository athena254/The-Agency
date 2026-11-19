"""Named presets must not inherit another provider's generic credentials.

Regression cover for the TUI failure where ``Brain``/``LLMRouter`` selected the
``claude-haiku`` preset while the configured provider was Google Gemini: the
preset had no credentials of its own, so it inherited ``AGENCY_LLM_API_KEY`` and
``AGENCY_LLM_BASE_URL`` and sent Anthropic requests to Google's endpoint (404).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agency.llm.adapter import LLMAdapter
from agency.llm.config import ProviderKind, load_named_provider
from agency.llm.router import LLMRouter, TaskKind

_GENERIC_KEYS = (
    "LLM_API_KEY",
    "AGENCY_LLM_API_KEY",
    "LLM_BASE_URL",
    "AGENCY_LLM_BASE_URL",
    "LLM_MODEL",
    "AGENCY_LLM_MODEL",
)

_PRESET_KEYS = (
    "ANTHROPIC_API_KEY",
    "CLAUDE_API_KEY",
    "CLAUDE_BASE_URL",
    "CLAUDE_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "GPT_API_KEY",
    "GPT_BASE_URL",
    "INCEPTION_API_KEY",
    "AGENCY_INCEPTION_API_KEY",
)


def _configure_google_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single configured provider (Google) plus its generic credentials."""
    for key in (*_GENERIC_KEYS, *_PRESET_KEYS):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENCY_LLM_PROVIDER", "google")
    monkeypatch.setenv("AGENCY_LLM_API_KEY", "generic-key")
    monkeypatch.setenv("AGENCY_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
    monkeypatch.setenv("AGENCY_LLM_MODEL", "gemini-3.5-flash-lite")


def test_unconfigured_preset_has_no_key_or_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_google_default(monkeypatch)

    config = load_named_provider("claude-haiku", load_dotenv=False)

    assert config.provider is ProviderKind.ANTHROPIC
    assert config.api_key is None
    assert config.base_url is None


def test_preset_with_its_own_credentials_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_google_default(monkeypatch)
    monkeypatch.setenv("CLAUDE_API_KEY", "claude-key")
    monkeypatch.setenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

    config = load_named_provider("claude", load_dotenv=False)

    assert config.provider is ProviderKind.ANTHROPIC
    assert config.model == "claude-sonnet-4-20250514"
    assert config.api_key is not None
    assert config.api_key.get_secret_value() == "claude-key"


def test_inception_preset_still_uses_shared_inception_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_google_default(monkeypatch)
    monkeypatch.setenv("AGENCY_INCEPTION_API_KEY", "inception-key")

    config = load_named_provider("inception", load_dotenv=False)

    assert config.provider is ProviderKind.INCEPTION
    assert config.api_key is not None
    assert config.api_key.get_secret_value() == "inception-key"


def test_router_falls_back_to_default_adapter_when_preset_unusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_google_default(monkeypatch)
    default = LLMAdapter(provider="echo", model="default")
    seen: list[str] = []

    async def generate(prompt: str, context: dict[str, Any] | None = None) -> str:
        seen.append(prompt)
        return "from-default"

    monkeypatch.setattr(default, "generate", generate)
    router = LLMRouter(adapter=default, routes={TaskKind.FAST: ("claude-haiku",)})

    assert asyncio.run(router.route("short task")) == "from-default"
    assert seen == ["short task"]
