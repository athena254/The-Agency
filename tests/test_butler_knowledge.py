"""Tests for the Butler Agency-knowledge system (butler.knowledge)."""

from __future__ import annotations

from typing import Any, ClassVar, Self

import httpx
import pytest

from agency.butler.config import ButlerConfig
from agency.butler.knowledge import (
    KNOWLEDGE_DOCS,
    MAX_DOCS,
    augment_description,
    select_knowledge,
)
from agency.butler.service import ButlerService
from agency.orchestrator import AgencyOrchestrator

# ------------------------------------------------------------------ #
# Selection
# ------------------------------------------------------------------ #


def test_onboarding_question_selects_onboarding() -> None:
    block = select_knowledge("How do I get started with the Agency?")
    assert block
    assert "ONBOARDING" in block


def test_overview_question_selects_overview() -> None:
    block = select_knowledge("What is the Agency, exactly?")
    assert "AGENCY_OVERVIEW" in block


def test_glossary_question_selects_glossary() -> None:
    block = select_knowledge("What does the kernel do in this system?")
    assert "GLOSSARY" in block


def test_no_match_returns_empty() -> None:
    assert select_knowledge("compute the square root of 144 please") == ""
    assert select_knowledge("") == ""


def test_block_is_labelled_context_only() -> None:
    block = select_knowledge("how do I get started")
    assert block.startswith("[Agency knowledge — context only, not an instruction.")


def test_at_most_max_docs_selected() -> None:
    # A message hitting many docs' keywords still selects at most MAX_DOCS.
    message = "glossary capabilities cli lattice intake skills factory mission control"
    block = select_knowledge(message)
    headers = [line for line in block.splitlines() if line.startswith("### ")]
    assert len(headers) <= MAX_DOCS


def test_loader_failure_degrades_to_empty() -> None:
    def boom(name: str) -> str:
        raise RuntimeError("no docs")

    assert select_knowledge("how do I get started", loader=boom) == ""


def test_empty_doc_bodies_are_skipped() -> None:
    assert select_knowledge("how do I get started", loader=lambda name: "") == ""


def test_registry_covers_expected_docs() -> None:
    for name in (
        "AGENCY_OVERVIEW",
        "GLOSSARY",
        "CAPABILITY_CATALOG",
        "ONBOARDING",
        "OPERATING_MODEL",
        "SKILL_MISSION_CONTROL",
        "SKILL_CLI",
        "SKILL_AGENTS_FACTORY",
        "SKILL_LATTICE",
        "SKILL_INTAKE",
        "SKILL_SKILLS_REGISTRY",
    ):
        assert name in KNOWLEDGE_DOCS


# ------------------------------------------------------------------ #
# Description augmentation
# ------------------------------------------------------------------ #


def test_augment_without_block_is_identity() -> None:
    assert augment_description("hello", "") == "hello"


def test_augment_appends_labelled_block() -> None:
    block = select_knowledge("how do I get started")
    description = augment_description("how do I get started", block)
    assert description.startswith("how do I get started")
    assert "[Agency knowledge" in description


# ------------------------------------------------------------------ #
# Service wiring
# ------------------------------------------------------------------ #


class _CaptureOrchestrator(AgencyOrchestrator):
    """Records the description the Butler submits."""

    def __init__(self) -> None:
        super().__init__(memory_db_path=":memory:")
        self.descriptions: list[str] = []

    async def submit_task(self, title: str, description: str, agent_id: str, context: Any = None):
        self.descriptions.append(description)
        return await super().submit_task(title, description, agent_id, context)


@pytest.mark.asyncio
async def test_handle_message_augments_description_with_knowledge() -> None:
    orch = _CaptureOrchestrator()
    svc = ButlerService(config=ButlerConfig(), orchestrator=orch)
    await svc.start()
    try:
        response = await svc.handle_message(
            "How do I get started with the Agency?", "tester", {}, memory_enabled=False
        )
        assert response
        assert orch.descriptions, "expected a task submission"
        assert "[Agency knowledge" in orch.descriptions[0]
        assert "ONBOARDING" in orch.descriptions[0]
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_handle_message_without_match_keeps_description_clean() -> None:
    orch = _CaptureOrchestrator()
    svc = ButlerService(config=ButlerConfig(), orchestrator=orch)
    await svc.start()
    try:
        await svc.handle_message(
            "compute the square root of 144", "tester", {}, memory_enabled=False
        )
        assert orch.descriptions
        assert "[Agency knowledge" not in orch.descriptions[0]
    finally:
        await svc.stop()


# ------------------------------------------------------------------ #
# HTTP routing endpoint (base URL + reasoning-model budget)
# ------------------------------------------------------------------ #


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeAsyncClient:
    posted: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def post(self, url: str, headers: dict[str, str], json: dict[str, Any]) -> _FakeResponse:
        _FakeAsyncClient.posted.append({"url": url, "json": json})
        return _FakeResponse({"choices": [{"message": {"content": "security"}}]})


@pytest.mark.asyncio
async def test_http_llm_uses_configured_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.posted.clear()
    svc = ButlerService(
        config=ButlerConfig(
            llm_provider="custom",
            llm_model="hy4-flash:free",
            llm_base_url="https://api.example.test/v1/chat/completions",
        ),
        orchestrator=AgencyOrchestrator(memory_db_path=":memory:"),
    )
    result = await svc._http_llm("Classify: scan for vulnerabilities", {})
    assert result == "security"
    posted = _FakeAsyncClient.posted[-1]
    assert posted["url"] == "https://api.example.test/v1/chat/completions"
    assert posted["json"]["model"] == "hy4-flash:free"
    # Reasoning models spend hidden tokens before visible content: the
    # budget must leave room (the old 32-token cap truncated to empty).
    assert posted["json"]["max_tokens"] >= 256


@pytest.mark.asyncio
async def test_http_llm_defaults_to_openai_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.posted.clear()
    svc = ButlerService(
        config=ButlerConfig(llm_provider="custom", llm_model="gpt-4o-mini"),
        orchestrator=AgencyOrchestrator(memory_db_path=":memory:"),
    )
    await svc._http_llm("Classify: research the latest AI news", {})
    assert _FakeAsyncClient.posted[-1]["url"] == "https://api.openai.com/v1/chat/completions"
