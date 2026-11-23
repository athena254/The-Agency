"""Offline tests for cron/serious model selection and Forge review wiring."""

from __future__ import annotations

from typing import ClassVar

import pytest

from agency.forge import reviewer
from agency.forge.scheduler import SchedulerConfig
from agency.llm.cron_model import CRON_MODEL_ENV, DEFAULT_CRON_MODEL, cron_model
from agency.llm.serious_models import (
    DEFAULT_SERIOUS_MODEL,
    SERIOUS_MODEL_ENV,
    SERIOUS_MODELS,
    serious_model,
)


def test_cron_model_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CRON_MODEL_ENV, raising=False)
    assert cron_model() == DEFAULT_CRON_MODEL == "gemma-3-4b-it"
    monkeypatch.setenv(CRON_MODEL_ENV, "custom-cron-model")
    assert cron_model() == "custom-cron-model"
    monkeypatch.setenv(CRON_MODEL_ENV, "   ")
    assert cron_model() == DEFAULT_CRON_MODEL


def test_serious_model_default_override_and_elements(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SERIOUS_MODEL_ENV, raising=False)
    assert serious_model() == DEFAULT_SERIOUS_MODEL == "gemini-4-hydrogen"
    monkeypatch.setenv(SERIOUS_MODEL_ENV, "custom-serious")
    assert serious_model() == "custom-serious"
    assert serious_model("carbon") == SERIOUS_MODELS["carbon"] == "gemini-4-carbon"
    assert serious_model("argon") == SERIOUS_MODELS["argon"] == "gemini-4-argon"
    assert serious_model("hydrogen") == "gemini-4-hydrogen"
    assert serious_model("unknown-element") == "unknown-element"


def test_scheduler_config_model_follows_cron_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CRON_MODEL_ENV, raising=False)
    assert SchedulerConfig(command="agency forge run", schedule="daily").model == DEFAULT_CRON_MODEL
    monkeypatch.setenv(CRON_MODEL_ENV, "scheduled-model")
    assert SchedulerConfig(command="agency forge run", schedule="daily").model == "scheduled-model"


class _CaptureAdapter:
    """Stand-in for LLMAdapter that records the config it was built with."""

    captured: ClassVar[dict[str, object]] = {}

    def __init__(self, config: object = None, **kwargs: object) -> None:
        _CaptureAdapter.captured["config"] = config


def test_build_review_llm_echo_returns_none() -> None:
    # conftest pins the offline echo provider.
    assert reviewer.build_review_llm() is None


def test_build_review_llm_google_uses_serious_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENCY_LLM_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
    monkeypatch.delenv(SERIOUS_MODEL_ENV, raising=False)
    monkeypatch.setattr("agency.llm.adapter.LLMAdapter", _CaptureAdapter)
    _CaptureAdapter.captured = {}
    assert reviewer.build_review_llm() is not None
    config = _CaptureAdapter.captured["config"]
    assert config.model == "gemini-4-hydrogen"  # type: ignore[attr-defined]
    monkeypatch.setenv(SERIOUS_MODEL_ENV, "gemini-4-carbon")
    assert reviewer.build_review_llm() is not None
    assert _CaptureAdapter.captured["config"].model == "gemini-4-carbon"  # type: ignore[attr-defined]


def test_build_review_llm_keeps_model_for_non_google_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENCY_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("AGENCY_LLM_MODEL", "qwen3.5:0.8b")
    monkeypatch.setattr("agency.llm.adapter.LLMAdapter", _CaptureAdapter)
    _CaptureAdapter.captured = {}
    assert reviewer.build_review_llm() is not None
    config = _CaptureAdapter.captured["config"]
    assert config.model == "qwen3.5:0.8b"  # type: ignore[attr-defined]
