"""Tests for the Inception Labs (mercury-3) LLM provider."""

from __future__ import annotations

import pytest

from agency.llm.adapter import LLMAdapter
from agency.llm.config import ProviderKind, load_config, load_named_provider


def test_inception_provider_kind_exists() -> None:
    assert ProviderKind("inception") is ProviderKind.INCEPTION
    assert str(ProviderKind.INCEPTION) == "inception"


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INCEPTION_API_KEY", "test-inception-key")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    config = load_config(provider="inception")
    assert config.provider is ProviderKind.INCEPTION
    assert config.model == "mercury-3"
    assert config.api_key is not None
    assert config.api_key.get_secret_value() == "test-inception-key"

    adapter = LLMAdapter(config=config)
    assert adapter.provider == "inception"
    assert adapter.model == "mercury-3"


def test_named_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INCEPTION_API_KEY", "named-key")
    config = load_named_provider("inception")
    assert config.provider is ProviderKind.INCEPTION
    assert config.model == "mercury-3"
    assert config.api_key is not None
    assert config.api_key.get_secret_value() == "named-key"


def test_detect_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("INCEPTION_API_KEY", "detect-key")
    assert load_config().provider is ProviderKind.INCEPTION


@pytest.mark.asyncio
async def test_generate_uses_inception_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    captured: dict[str, object] = {}

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "hello from mercury"}}]}

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
        provider="inception",
        model="mercury-3",
        base_url="https://api.inceptionlabs.ai/v1",
        api_key="secret-key",
        timeout=60.0,
    )
    result = await adapter.generate("hi")
    assert result == "hello from mercury"
    assert captured["url"] == "https://api.inceptionlabs.ai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret-key"
    assert captured["json"]["model"] == "mercury-3"
