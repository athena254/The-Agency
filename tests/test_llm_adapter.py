"""Hermetic tests for LLMAdapter failure diagnostics and log privacy.

Structlog bypasses pytest's ``caplog``, so these tests capture the adapter's
logger by swapping ``adapter._log`` for a small recording double. They assert
that a failing provider re-raises unchanged (status preserved) while the raw
exception body / prompt never reaches structured logs.
"""

from __future__ import annotations

from typing import Any

import pytest

from agency.llm.adapter import LLMAdapter


class _LogCapture:
    """Capture structlog-style calls (structlog bypasses pytest caplog)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def debug(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("debug", args, kwargs))

    def info(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("info", args, kwargs))

    def warning(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("warning", args, kwargs))

    def error(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("error", args, kwargs))

    def exception(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("exception", args, kwargs))

    def text(self) -> str:
        return " ".join(
            " ".join([str(a) for a in args] + [f"{k}={v}" for k, v in kwargs.items()])
            for _, args, kwargs in self.events
        )


async def test_generate_failure_logs_class_not_private_exception_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider failure re-raises, but its private body never reaches logs."""
    phrase = "private-user-phrase"
    prompt = "private user prompt"
    adapter = LLMAdapter(provider="ollama", model="missing-model")

    async def fail(_prompt: str, _model: str) -> str:
        raise ConnectionError(phrase)

    monkeypatch.setattr(adapter, "_ollama_generate", fail)
    logs = _LogCapture()
    monkeypatch.setattr(adapter, "_log", logs)

    with pytest.raises(ConnectionError, match=phrase):
        await adapter.generate(prompt)

    logged = logs.text()
    assert phrase not in logged
    assert prompt not in logged
    assert "llm_error" in logged  # diagnostic event name preserved
    assert "ConnectionError" in logged  # class-level diagnostic preserved
    # No raw exception body or traceback dump is emitted.
    assert all(level != "exception" for level, _, _ in logs.events)
    assert all("error" not in kwargs and "exc_info" not in kwargs for _, _, kwargs in logs.events)


async def test_unknown_provider_failure_re_raises_and_logs_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unsupported provider still raises ValueError; only its class is logged."""
    prompt = "private user prompt"
    adapter = LLMAdapter(provider="not-a-provider", model="missing-model")
    logs = _LogCapture()
    monkeypatch.setattr(adapter, "_log", logs)

    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        await adapter.generate(prompt)

    logged = logs.text()
    assert prompt not in logged
    assert "llm_error" in logged
    assert "ValueError" in logged
    assert all("error" not in kwargs for _, _, kwargs in logs.events)
