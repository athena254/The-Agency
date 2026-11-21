"""Tests for the Butler persona reply-framing layer.

Covers the LLM-voiced framing path (prompt contract, document/message caps,
MEMORY exclusion, audit metadata), the deterministic verbatim fallback (no
LLM configured, LLM failure, empty reply, persona disabled), output-length
governance, missing-SOUL containment, and private-directory overrides.
"""

from __future__ import annotations

from collections.abc import Awaitable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from structlog.testing import capture_logs

from agency.agents.executor import LLMCallable
from agency.butler.config import ButlerConfig
from agency.butler.decision import FakeJevClient, JevDecisionModel
from agency.butler.documents import load_base as real_load_base
from agency.butler.persona import PersonaFraming
from agency.butler.service import ButlerService
from agency.orchestrator import AgencyOrchestrator

MESSAGE = "scan for vulnerabilities"
SENDER = "tester"
RAW_OUTPUT = "RAW-AGENT-OUTPUT"
FRAMED_OUTPUT = "FRAMED REPLY"

# ------------------------------------------------------------------ #
# Fixtures / helpers
# ------------------------------------------------------------------ #


class FakeLLM:
    """Recording fake LLM callable (sync or async) with a canned reply."""

    def __init__(
        self, reply: str | BaseException = FRAMED_OUTPUT, *, as_async: bool = False
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._reply = reply
        self._as_async = as_async

    def __call__(self, prompt: str, context: dict[str, Any]) -> str | Awaitable[str]:
        self.calls.append((prompt, dict(context)))
        if isinstance(self._reply, BaseException):
            raise self._reply
        if self._as_async:
            return self._reply_async()
        return self._reply

    async def _reply_async(self) -> str:
        assert not isinstance(self._reply, BaseException)
        return self._reply


def make_persona_service(llm: LLMCallable | None = None, **config_overrides: Any) -> ButlerService:
    """Build an unstarted Butler with decision routing (fake) so the injected
    LLM callable is used for persona framing only, never for routing."""
    config = ButlerConfig(decision_provider="jev", **config_overrides)
    svc = ButlerService(config=config, orchestrator=AgencyOrchestrator(), llm=llm)
    svc._decision_model = JevDecisionModel(
        FakeJevClient({"routes_to_security": 0.9}),
        svc.router,
        signal_threshold=config.jev_signal_threshold,
        uncertain_low=config.jev_uncertain_low,
    )
    return svc


def patch_echo(monkeypatch: pytest.MonkeyPatch, svc: ButlerService) -> None:
    """Pin the orchestrator's echo output to a deterministic final string."""
    dispatch = MagicMock(return_value='{"final":"' + RAW_OUTPUT + '"}')
    monkeypatch.setattr(svc.orchestrator._llm_adapter, "_echo_generate", dispatch)


async def raw_reply(monkeypatch: pytest.MonkeyPatch) -> str:
    """Run one identical turn with framing disabled to capture the raw reply."""
    svc = make_persona_service(persona_enabled=False)
    patch_echo(monkeypatch, svc)
    await svc.start()
    try:
        return await svc.handle_message(MESSAGE, SENDER, {})
    finally:
        await svc.stop()


async def handle_evidence(svc: ButlerService) -> dict[str, Any]:
    """Evidence of the most recent butler.handle_message audit entry."""
    entries = await svc._audit.query()
    handles = [e for e in entries if e.action == "butler.handle_message"]
    assert handles, "butler.handle_message must be audited"
    return handles[-1].evidence or {}


# ------------------------------------------------------------------ #
# PersonaFraming unit behavior
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_frame_uses_llm_and_builds_contract_prompt() -> None:
    docs = {
        "SOUL": "S" * 5000,
        "IDENTITY": "IDENTITY-BODY",
        "USER": "USER-BODY",
        "MEMORY": "MEMORY-SENTINEL-MUST-NOT-APPEAR",
        "BUTLER_CHARTER": "CHARTER-BODY",
    }
    fake = FakeLLM()
    persona = PersonaFraming(fake, docs, max_output=1000)
    evidence: dict[str, Any] = {}
    out = await persona.frame(
        "R" * 2500,
        message="M" * 2500,
        agent_name="sec-agent",
        thread_context=False,
        evidence=evidence,
    )
    assert out == FRAMED_OUTPUT
    assert evidence == {"framing": "llm", "framed_length": len(FRAMED_OUTPUT)}
    assert fake.calls, "the framing LLM must be consulted"
    prompt, context = fake.calls[0]
    # Persona docs: capped, present, MEMORY never crosses the boundary.
    assert "S" * 4000 in prompt and "S" * 4001 not in prompt
    assert "IDENTITY-BODY" in prompt and "USER-BODY" in prompt
    assert "CHARTER-BODY" in prompt
    assert "MEMORY-SENTINEL-MUST-NOT-APPEAR" not in prompt and "## MEMORY" not in prompt
    # Turn data: user message truncated, agent response preserved, metadata.
    assert "M" * 2000 in prompt and "M" * 2001 not in prompt
    assert "R" * 2500 in prompt
    assert "sec-agent" in prompt and "Continuing an existing thread: no" in prompt
    assert "# Framing contract" in prompt and "MUST NOT" in prompt
    assert context["purpose"] == "persona_framing"


@pytest.mark.asyncio
async def test_frame_thread_context_is_reported_in_prompt() -> None:
    fake = FakeLLM()
    persona = PersonaFraming(fake, {"SOUL": "soul body"})
    await persona.frame("answer", message="hi", agent_name="a", thread_context=True)
    assert "Continuing an existing thread: yes" in fake.calls[0][0]


@pytest.mark.asyncio
async def test_frame_deterministic_path_returns_response_unchanged() -> None:
    persona = PersonaFraming(None, {"SOUL": "soul body"})
    assert persona.has_llm is False
    evidence: dict[str, Any] = {}
    out = await persona.frame(
        "verbatim answer", message="hi", agent_name="a", thread_context=False, evidence=evidence
    )
    assert out == "verbatim answer"
    assert evidence == {"framing": "verbatim", "framed_length": len("verbatim answer")}


@pytest.mark.asyncio
async def test_frame_awaits_async_llm_callable() -> None:
    fake = FakeLLM(as_async=True)
    persona = PersonaFraming(fake, {"SOUL": "soul body"})
    out = await persona.frame("answer", message="hi", agent_name="a", thread_context=False)
    assert out == FRAMED_OUTPUT


@pytest.mark.asyncio
async def test_frame_llm_failure_falls_back_to_verbatim() -> None:
    fake = FakeLLM(RuntimeError("provider down"))
    persona = PersonaFraming(fake, {"SOUL": "soul body"})
    with capture_logs() as logs:
        out = await persona.frame("raw answer", message="hi", agent_name="a", thread_context=False)
    assert out == "raw answer"
    assert any(e["event"] == "butler.persona_framing_failed" for e in logs)
    # Logs carry metadata only — never prompt or message content.
    assert all("raw answer" not in str(e) and "soul body" not in str(e) for e in logs)


@pytest.mark.asyncio
async def test_frame_empty_llm_reply_falls_back_to_verbatim() -> None:
    fake = FakeLLM("   ")
    persona = PersonaFraming(fake, {"SOUL": "soul body"})
    out = await persona.frame("raw answer", message="hi", agent_name="a", thread_context=False)
    assert out == "raw answer"


@pytest.mark.asyncio
async def test_frame_truncates_to_max_output_with_marker() -> None:
    persona = PersonaFraming(None, {}, max_output=40)
    out = await persona.frame("x" * 100, message="hi", agent_name="a", thread_context=False)
    assert len(out) <= 40
    assert out.endswith("(truncated)")
    # No room for the marker: hard cut, still bounded.
    tiny = PersonaFraming(None, {}, max_output=5)
    out = await tiny.frame("y" * 100, message="hi", agent_name="a", thread_context=False)
    assert len(out) <= 5 and "(truncated)" not in out


# ------------------------------------------------------------------ #
# Service integration
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_framed_reply_flows_through_service(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeLLM()
    svc = make_persona_service(llm=fake)
    patch_echo(monkeypatch, svc)
    await svc.start()
    try:
        response = await svc.handle_message(MESSAGE, SENDER, {})
        assert response == FRAMED_OUTPUT
        # Decision routing handled routing; the fake was used for framing only.
        assert len(fake.calls) == 1
        prompt, context = fake.calls[0]
        assert MESSAGE in prompt and "security-agent" in prompt
        assert "# Framing contract" in prompt
        assert context["purpose"] == "persona_framing"
        evidence = await handle_evidence(svc)
        assert evidence["framing"] == "llm"
        assert evidence["response_length"] == len(FRAMED_OUTPUT)
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_framed_reply_persisted_to_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeLLM()
    svc = make_persona_service(llm=fake)
    patch_echo(monkeypatch, svc)
    await svc.start()
    try:
        workspace = svc.create_workspace(SENDER, "workspace")
        thread = svc.create_thread(SENDER, workspace.id, "thread")
        response = await svc.handle_message(MESSAGE, SENDER, {"thread_id": thread.id})
        assert response == FRAMED_OUTPUT
        messages = svc.thread_store.list_messages(SENDER, thread.id)
        assert [(m.role, m.content) for m in messages] == [
            ("user", MESSAGE),
            ("assistant", FRAMED_OUTPUT),
        ]
        assert "Continuing an existing thread: yes" in fake.calls[0][0]
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_verbatim_in_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = await raw_reply(monkeypatch)
    fake = FakeLLM(RuntimeError("provider down"))
    svc = make_persona_service(llm=fake)
    patch_echo(monkeypatch, svc)
    await svc.start()
    try:
        response = await svc.handle_message(MESSAGE, SENDER, {})
        assert response == expected
        assert len(fake.calls) == 1  # framing attempted exactly once, then contained
        assert (await handle_evidence(svc))["framing"] == "verbatim"
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_persona_disabled_skips_framing_and_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = await raw_reply(monkeypatch)
    fake = FakeLLM()
    svc = make_persona_service(llm=fake, persona_enabled=False)
    patch_echo(monkeypatch, svc)
    await svc.start()
    try:
        response = await svc.handle_message(MESSAGE, SENDER, {})
        assert response == expected
        assert fake.calls == []  # the configured LLM is never called
        assert (await handle_evidence(svc))["framing"] == "disabled"
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_no_llm_configured_returns_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = await raw_reply(monkeypatch)
    svc = make_persona_service(llm=None)
    patch_echo(monkeypatch, svc)
    await svc.start()
    try:
        response = await svc.handle_message(MESSAGE, SENDER, {})
        assert response == expected
        assert svc._persona is not None and svc._persona.has_llm is False
        assert (await handle_evidence(svc))["framing"] == "verbatim"
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_output_capped_to_persona_max_output_in_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeLLM("F" * 500)
    svc = make_persona_service(llm=fake, persona_max_output=40)
    patch_echo(monkeypatch, svc)
    await svc.start()
    try:
        response = await svc.handle_message(MESSAGE, SENDER, {})
        assert len(response) <= 40
        assert response.endswith("(truncated)")
        assert (await handle_evidence(svc))["framing"] == "llm"
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_soul_missing_disables_framing_with_loud_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty_root = tmp_path / "empty"
    empty_root.mkdir()

    def missing_docs() -> dict[str, str]:
        return real_load_base(private_root=empty_root, template_root=empty_root)

    monkeypatch.setattr("agency.butler.service.load_base", missing_docs)
    fake = FakeLLM()
    svc = make_persona_service(llm=fake)
    patch_echo(monkeypatch, svc)
    await svc.start()
    try:
        with capture_logs() as logs:
            response = await svc.handle_message(MESSAGE, SENDER, {})
        soul = [e for e in logs if e["event"] == "butler.persona_soul_missing"]
        assert soul and soul[0]["log_level"] == "warning"
        assert response  # the turn survives as the verbatim relay
        assert fake.calls == []  # framing disabled → no LLM call
        assert (await handle_evidence(svc))["framing"] == "disabled"
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_private_dir_overrides_persona_docs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = tmp_path / "private"
    doc_dir = private / "intelligence" / "butler"
    doc_dir.mkdir(parents=True)
    (doc_dir / "SOUL.md").write_text(
        "---\ntype: SOUL\nschema_version: 1\n---\n\nPRIVATE-SOUL-SENTINEL-A31\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENCY_PRIVATE_DATA_DIR", str(private))
    fake = FakeLLM()
    svc = make_persona_service(llm=fake)
    patch_echo(monkeypatch, svc)
    await svc.start()
    try:
        await svc.handle_message(MESSAGE, SENDER, {})
        assert fake.calls
        prompt = fake.calls[0][0]
        assert "PRIVATE-SOUL-SENTINEL-A31" in prompt
        # The remaining base docs still resolve from the committed templates.
        assert "## SOUL" in prompt and "## IDENTITY" in prompt
    finally:
        await svc.stop()
