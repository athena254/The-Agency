"""Ollama URL compatibility is preserved by the lint-only suffix cleanup."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from agency.llm.adapter import LLMAdapter


@pytest.mark.parametrize(
    ("base_url", "expected_url"),
    [
        ("http://localhost:11434", "http://localhost:11434/api/generate"),
        ("http://localhost:11434/v1", "http://localhost:11434/api/generate"),
        ("http://localhost:11434/proxy/v1", "http://localhost:11434/proxy/api/generate"),
        ("http://localhost:11434/v1/models", "http://localhost:11434/v1/models/api/generate"),
    ],
)
async def test_ollama_suffix_preserves_endpoint_and_payload(
    monkeypatch: pytest.MonkeyPatch, base_url: str, expected_url: str
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"response": "answer"})

    client_type = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return client_type(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    adapter = LLMAdapter(provider="ollama", model="test-model", base_url=base_url)
    assert await adapter.generate("test prompt") == "answer"
    assert len(requests) == 1
    assert str(requests[0].url) == expected_url
    assert json.loads(requests[0].content) == {
        "model": "test-model",
        "prompt": "test prompt",
        "stream": False,
    }
