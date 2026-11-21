"""Agent intake stays peer-gated even with owner bearer tokens configured.

The task-lifecycle audit (2026-11-20) flagged the agent-intake path as
"503-blocked". Investigation (2026-11-21) classifies it as an *intentional*
security gate, not an accidental gap:

* ``agency/api/authority.py``: ``require_peer_authority`` is "deliberately not
  an owner-token switch" and must only be replaced by an authenticated,
  independently validated peer execution grant.
* ``docs/SPEC_BETA_BOUNDARY_AND_PEER_EXECUTION.md`` release gate: "until peer
  certificates and actuator checks exist, no consequential route may
  create/revoke agents" — ``POST /v1/agents`` creates agent identities.
* ``tests/test_beta_http_cutover.py`` pins the route closed at the spec level.

This test pins the boundary in the other direction too: configuring
``AGENCY_API_TOKENS`` (which opened ``POST /v1/tasks`` and butler
``/v1/message``, gaps.md #18) does NOT open agent creation. Unauthenticated
requests are denied by the middleware exactly as before.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from agency.api.server import create_app

OWNER_TOKEN = "sk-owner-token-with-at-least-24-chars"


@pytest.fixture
def tokenized_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    tokens_file = tmp_path / "tokens.json"
    tokens_file.write_text(
        json.dumps([{"token": OWNER_TOKEN, "caller_id": "danny", "role": "owner"}]),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENCY_API_TOKENS", str(tokens_file))
    return create_app()


@pytest.mark.asyncio
async def test_owner_token_does_not_open_agent_creation(tokenized_app) -> None:
    app = tokenized_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/agents",
            json={"name": "external-agent", "domain": "general"},
            headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "Peer authorization is unavailable."}


@pytest.mark.asyncio
async def test_agent_intake_without_token_stays_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENCY_API_TOKENS", raising=False)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/agents", json={"name": "external-agent"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Peer authorization is unavailable."}


@pytest.mark.asyncio
async def test_task_intake_with_owner_token_still_works(tokenized_app) -> None:
    """Control: the same token config opens the sanctioned task intake (#18)."""
    app = tokenized_app
    app.state.task_manager = None  # middleware + auth must fire before any state use
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/tasks",
            json={"title": "x"},
            headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
        )
    # Past the middleware + auth boundary; route-level 503 (manager unset)
    # proves the request reached the router authenticated.
    assert response.status_code == 503
    assert response.json() == {"detail": "Task manager is not initialised."}
