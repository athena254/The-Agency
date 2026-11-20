"""Butler HTTP /v1/message: authenticated senders, bound identity, scoped threads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from agency.butler.config import ButlerConfig
from agency.butler.server import create_app
from agency.butler.service import ButlerService
from agency.orchestrator import AgencyOrchestrator

OWNER_TOKEN = "sk-owner-token-with-at-least-24-chars"
AGENT_TOKEN = "sk-agent-token-with-at-least-24-char"


def _make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    tokens_file = tmp_path / "tokens.json"
    tokens_file.write_text(
        json.dumps(
            [
                {"token": OWNER_TOKEN, "caller_id": "danny", "role": "owner"},
                {"token": AGENT_TOKEN, "caller_id": "external-agent-1", "role": "agent"},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENCY_API_TOKENS", str(tokens_file))
    app = create_app()
    orchestrator = AgencyOrchestrator(memory_db_path=":memory:", skill_db_path=":memory:")
    app.state.orchestrator = orchestrator
    app.state.butler = ButlerService(config=ButlerConfig(), orchestrator=orchestrator, llm=None)
    return app, orchestrator


@pytest.fixture
async def butler_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """App + orchestrator with guaranteed teardown (no lifespan runs here)."""
    app, orchestrator = _make_app(tmp_path, monkeypatch)
    try:
        yield app, orchestrator
    finally:
        service = app.state.butler
        if service is not None:
            try:
                await service.stop()
            except Exception:  # noqa: S110 — teardown best effort
                pass


@pytest.mark.asyncio
async def test_unauthenticated_message_is_401_when_tokens_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = _make_app(tmp_path, monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/message", json={"message": "hello"})
    assert response.status_code == 401
    service = app.state.butler
    if service is not None:
        try:
            await service.stop()
        except Exception:  # noqa: S110
            pass


@pytest.mark.asyncio
async def test_unconfigured_tokens_keep_message_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENCY_API_TOKENS", raising=False)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/message", json={"message": "hello"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Peer authorization is unavailable."}


@pytest.mark.asyncio
async def test_authenticated_external_agent_gets_a_reply(butler_env) -> None:
    app, _ = butler_env
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/message",
            json={"message": "status please"},
            headers={"Authorization": f"Bearer {AGENT_TOKEN}"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["response"]
    assert body["agent_id"]
    assert body["domain"]


@pytest.mark.asyncio
async def test_sender_is_bound_not_spoofed(butler_env) -> None:
    """The caller-supplied 'sender' can never become the thread owner."""
    app, orchestrator = butler_env
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/message",
            json={"message": "hello", "context": {"sender": "danny"}},
            headers={"Authorization": f"Bearer {AGENT_TOKEN}"},
        )
    assert response.status_code == 200
    # The task's audit-relevant identity is the butler-routed agent, and the
    # butler stored the authenticated principal as the turn sender.
    tasks = await orchestrator._task_manager.list_tasks()
    assert tasks, "expected the message to become a task"
    # The turn context carries the bound sender, not the spoofed one.
    context = tasks[-1].input.get("context", {})
    assert context.get("sender") == "external-agent-1"
