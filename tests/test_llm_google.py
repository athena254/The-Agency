"""Tests for the Google Gemini LLM provider."""

from __future__ import annotations

import pytest

from agency.config.keys import check_key_format
from agency.llm.adapter import LLMAdapter
from agency.llm.config import ProviderKind, load_config, load_named_provider


def test_google_provider_kind_exists() -> None:
    assert ProviderKind("google") is ProviderKind.GOOGLE
    assert str(ProviderKind.GOOGLE) == "google"


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
    # conftest pins AGENCY_LLM_PROVIDER/MODEL to the offline echo defaults;
    # drop them so this test exercises provider-specific resolution only.
    for key in ("LLM_PROVIDER", "AGENCY_LLM_PROVIDER", "AGENCY_LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    config = load_config(provider="google", load_dotenv=False)
    assert config.provider is ProviderKind.GOOGLE
    assert config.model == "gemini-3.5-flash-lite"
    assert config.api_key is not None
    assert config.api_key.get_secret_value() == "test-google-key"

    adapter = LLMAdapter(config=config)
    assert adapter.provider == "google"
    assert adapter.model == "gemini-3.5-flash-lite"


@pytest.mark.parametrize("preset", ["google", "gemini"])
def test_named_provider(monkeypatch: pytest.MonkeyPatch, preset: str) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "named-key")
    monkeypatch.setenv("GEMINI_API_KEY", "named-key")
    config = load_named_provider(preset, load_dotenv=False)
    assert config.provider is ProviderKind.GOOGLE
    assert config.model == "gemini-3.5-flash-lite"
    assert config.api_key is not None
    assert config.api_key.get_secret_value() == "named-key"


def test_gemini_provider_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "alias-key")
    monkeypatch.setenv("AGENCY_LLM_PROVIDER", "gemini")
    config = load_config(load_dotenv=False)
    assert config.provider is ProviderKind.GOOGLE
    assert load_config(provider="gemini", load_dotenv=False).provider is ProviderKind.GOOGLE


@pytest.mark.parametrize("credential_var", ["GOOGLE_API_KEY", "AGENCY_GOOGLE_API_KEY"])
def test_detect_provider(monkeypatch: pytest.MonkeyPatch, credential_var: str) -> None:
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "AGENCY_LLM_PROVIDER",
        "GOOGLE_API_KEY",
        "AGENCY_GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(credential_var, "detect-key")
    assert load_config(load_dotenv=False).provider is ProviderKind.GOOGLE


@pytest.mark.asyncio
async def test_generate_uses_google_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    captured: dict[str, object] = {}

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "candidates": [
                    {"content": {"parts": [{"text": "hello "}, {"text": "from gemini"}]}}
                ]
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    adapter = LLMAdapter(
        provider="google",
        model="gemini-3.5-flash-lite",
        api_key="secret-key",
        timeout=60.0,
    )
    result = await adapter.generate("hi")
    assert result == "hello from gemini"
    assert captured["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
    )
    assert "key=" not in str(captured["url"])
    assert captured["headers"]["x-goog-api-key"] == "secret-key"
    assert captured["json"]["contents"] == [{"parts": [{"text": "hi"}]}]
    assert captured["json"]["generationConfig"] == {
        "maxOutputTokens": 1024,
        "temperature": 0.7,
    }


@pytest.mark.parametrize("prefix", ["AIza", "AQ."])
def test_google_key_format_accepts_both_prefixes(prefix: str) -> None:
    key = f"{prefix}{'x' * 24}"
    for provider in ("google", "gemini"):
        ok, reason = check_key_format(provider, key)
        assert ok, reason
