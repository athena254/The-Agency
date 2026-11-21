"""HTTP /v1/tasks intake: bearer auth, identity binding, scoped reads, guards.

The app is built without running its lifespan (routers get minimal state
manually), matching the repo's existing httpx ASGITransport test pattern.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from agency.api.server import create_app
from agency.kernel.registry import AgentRegistry
from agency.kernel.tasks import TaskManager, TaskStatus

OWNER_TOKEN = "sk-owner-token-with-at-least-24-chars"
AGENT1_TOKEN = "sk-agent1-token-with-at-least-24-char"
AGENT2_TOKEN = "sk-agent2-token-with-at-least-24-char"

TOKENS = [
    {"token": OWNER_TOKEN, "caller_id": "danny", "role": "owner"},
    {"token": AGENT1_TOKEN, "caller_id": "agent-coder-1", "role": "agent"},
    {"token": AGENT2_TOKEN, "caller_id": "agent-coder-2", "role": "agent"},
]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def app_and_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    tokens_file = tmp_path / "tokens.json"
    tokens_file.write_text(json.dumps(TOKENS), encoding="utf-8")
    monkeypatch.setenv("AGENCY_API_TOKENS", str(tokens_file))
    app = create_app()
    app.state.task_manager = TaskManager()
    registry = AgentRegistry()
    registry.register(id="danny-scout", name="Scout", domain="research")
    app.state.agent_registry = registry
    return app


async def _submit(client: AsyncClient, token: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": overrides.pop("title", "External request"),
        "created_by": overrides.pop("created_by", "spoofed"),
        "input": overrides.pop("input", {"description": "build the widget"}),
    }
    body.update(overrides)
    response = await client.post("/v1/tasks", json=body, headers=_auth(token))
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_unconfigured_tokens_keep_routes_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENCY_API_TOKENS", raising=False)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/tasks", json={"title": "x"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Peer authorization is unavailable."}


@pytest.mark.asyncio
async def test_missing_token_is_401(app_and_state) -> None:
    app = app_and_state
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/tasks", json={"title": "x"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_created_by_is_bound_to_principal(app_and_state) -> None:
    app = app_and_state
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = await _submit(client, AGENT1_TOKEN)
    assert payload["created_by"] == "agent-coder-1"  # body said "spoofed"


@pytest.mark.asyncio
async def test_owner_may_submit_on_behalf(app_and_state) -> None:
    app = app_and_state
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = await _submit(client, OWNER_TOKEN, created_by="agent-coder-2")
    assert payload["created_by"] == "agent-coder-2"


@pytest.mark.asyncio
async def test_non_owner_priority_clamped(app_and_state) -> None:
    app = app_and_state
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        clamped = await _submit(client, AGENT1_TOKEN, priority=9)
        owner_task = await _submit(client, OWNER_TOKEN, priority=9, created_by="danny")
    assert clamped["priority"] == 5
    assert owner_task["priority"] == 9


@pytest.mark.asyncio
async def test_scoped_list_and_foreign_get_404(app_and_state) -> None:
    app = app_and_state
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        mine = await _submit(client, AGENT1_TOKEN)
        foreign = await _submit(client, AGENT2_TOKEN)
        listed = await client.get("/v1/tasks", headers=_auth(AGENT2_TOKEN))
        owner_listed = await client.get("/v1/tasks", headers=_auth(OWNER_TOKEN))
        sneak = await client.get(f"/v1/tasks/{mine['task_id']}", headers=_auth(AGENT2_TOKEN))
    assert [t["task_id"] for t in listed.json()] == [foreign["task_id"]]
    assert {t["task_id"] for t in owner_listed.json()} == {mine["task_id"], foreign["task_id"]}
    assert sneak.status_code == 404


@pytest.mark.asyncio
async def test_result_visible_after_completion(app_and_state) -> None:
    app = app_and_state
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        task = await _submit(client, AGENT1_TOKEN)
        await app.state.task_manager.update_status(
            task["task_id"], TaskStatus.COMPLETED, output={"summary": "widget built"}
        )
        fetched = await client.get(f"/v1/tasks/{task['task_id']}", headers=_auth(AGENT1_TOKEN))
    body = fetched.json()
    assert body["status"] == "completed"
    assert body["output"]["summary"] == "widget built"


@pytest.mark.asyncio
async def test_creators_may_only_cancel(app_and_state) -> None:
    app = app_and_state
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        task = await _submit(client, AGENT1_TOKEN)
        completed = await client.patch(
            f"/v1/tasks/{task['task_id']}/status",
            json={"status": "completed"},
            headers=_auth(AGENT1_TOKEN),
        )
        cancelled = await client.patch(
            f"/v1/tasks/{task['task_id']}/status",
            json={"status": "blocked"},
            headers=_auth(AGENT1_TOKEN),
        )
        owner_done = await client.patch(
            f"/v1/tasks/{task['task_id']}/status",
            json={"status": "failed"},
            headers=_auth(OWNER_TOKEN),
        )
    assert completed.status_code == 403
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "blocked"
    assert owner_done.status_code == 200


@pytest.mark.asyncio
async def test_agent_id_validated_and_stored(app_and_state) -> None:
    app = app_and_state
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unknown = await client.post(
            "/v1/tasks",
            json={"title": "x", "agent_id": "ghost-agent"},
            headers=_auth(AGENT1_TOKEN),
        )
        ok = await _submit(client, AGENT1_TOKEN, agent_id="danny-scout")
        registered = {agent.id for agent in app.state.agent_registry.list_agents()}
        stored_agent = ok["input"].get("agent_id")
    assert unknown.status_code == 422
    assert stored_agent in registered


@pytest.mark.asyncio
async def test_oversized_description_rejected(app_and_state) -> None:
    app = app_and_state
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/tasks",
            json={"title": "x", "input": {"description": "x" * 8001}},
            headers=_auth(AGENT1_TOKEN),
        )
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_messages_append_scoped(app_and_state) -> None:
    app = app_and_state
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        task = await _submit(client, AGENT1_TOKEN)
        foreign = await client.post(
            f"/v1/tasks/{task['task_id']}/messages",
            json={"type": "note", "content": "hello"},
            headers=_auth(AGENT2_TOKEN),
        )
        own = await client.post(
            f"/v1/tasks/{task['task_id']}/messages",
            json={"type": "note", "content": "hi from submitter"},
            headers=_auth(AGENT1_TOKEN),
        )
    assert foreign.status_code == 404
    assert own.status_code == 201 and own.json()["created_by"] == "agent-coder-1"
