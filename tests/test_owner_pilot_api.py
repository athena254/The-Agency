"""Regression gate for the owner pilot API surface."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from agency.admission.auth import OwnerAuthenticator
from agency.prototype.app import create_app
from tests.test_owner_service import concrete as concrete_fixture
from tests.test_owner_service import make_service

concrete = concrete_fixture


async def test_unconfigured_pilot_capabilities_is_disabled_503() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        response = await client.get("/api/pilot/capabilities")
    assert response.status_code == 503, response.text
    assert response.json() == {
        "error": "Owner pilot unavailable.",
        "code": "disabled",
        "request_id": None,
        "task_id": None,
        "uncertain": False,
        "retryable": False,
    }


def test_create_app_exposes_explicit_pilot_injection():
    import inspect

    assert {"pilot", "authenticator"} <= set(inspect.signature(create_app).parameters)


@pytest.fixture
async def api(concrete):
    c = concrete
    service = make_service(c)
    auth = OwnerAuthenticator(
        owner_id="owner",
        subject_id="subject",
        bearer_secret=SecretStr("SYNTHETIC_OWNER_TOKEN_32_CHARACTERS"),
    )
    app = create_app(c["butler"], pilot=service, authenticator=auth)
    async with (
        app.router.lifespan_context(app),
        c["client"](transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client,
    ):
        client.headers.update(
            {
                "Authorization": "Bearer SYNTHETIC_OWNER_TOKEN_32_CHARACTERS",
                "X-Prototype-CSRF": app.state.csrf_token,
                "Origin": "http://127.0.0.1",
            },
        )
        yield client, c, app


async def test_actual_http_calculator_turn_and_replay_publish_once(api):
    client, c, _app = api
    caps = await client.get("/api/pilot/capabilities")
    assert caps.status_code == 200, caps.text
    assert caps.json()["tools"] == ["calculator", "utc_time", "web_search"]
    assert caps.json()["limits"]["model_calls_per_request"] <= 8
    made = await client.post(
        "/api/pilot/threads", json={"request_id": "thread", "title": "Synthetic"}
    )
    assert made.status_code == 201, made.text
    thread_id = made.json()["thread"]["id"]
    payload = {"request_id": "turn", "thread_id": thread_id, "message": "calculate 2+3"}
    accepted = await client.post("/api/pilot/tasks", json=payload)
    assert accepted.status_code == 202, accepted.text
    task_id = accepted.json()["task_id"]
    async with asyncio.timeout(10):
        while True:
            read = await client.get(f"/api/pilot/tasks/{task_id}")
            assert read.status_code == 200, read.text
            if read.json()["state"] not in {"ADMITTED", "RUNNING"}:
                break
            await asyncio.sleep(0.005)
    result = read.json()
    assert result["state"] == "COMPLETED", result
    assert result["response"] == "five"
    assert result["usage"] == {"model_attempts": 2, "tool_attempts": 1}
    assert result["assessment"]["task_id"] == task_id
    assert result["assessment"]["independently_verified"] is False
    replay = await client.post("/api/pilot/tasks", json=payload)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["task_id"] == task_id
    messages = await client.get(f"/api/pilot/threads/{thread_id}/messages")
    assert messages.status_code == 200
    assert [(m["role"], m["content"]) for m in messages.json()["messages"]] == [
        ("user", "calculate 2+3"),
        ("assistant", "five"),
    ]
    assert len(c["requests"]) == 2
    assert not c["butler"]._started and not c["orch"]._started


async def test_http_history_lists_real_threads_and_tasks(api):
    client, c, _ = api
    created = await client.post(
        "/api/pilot/threads", json={"request_id": "thread", "title": "History"}
    )
    thread_id = created.json()["thread"]["id"]
    listed = await client.get("/api/pilot/threads")
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()["threads"]] == [thread_id]
    tasks = await client.get("/api/pilot/tasks?limit=1")
    assert tasks.status_code == 200 and tasks.json() == {"tasks": []}
    assert c["requests"] == []


async def test_http_cancel_drains_execution_and_replay_does_not_rerun(api, monkeypatch):
    import httpx

    client, c, _ = api
    entered = asyncio.Event()

    async def held(request):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: c["client"](transport=httpx.MockTransport(held), **kw)
    )
    made = await client.post("/api/pilot/threads", json={"request_id": "thread", "title": "Cancel"})
    thread_id = made.json()["thread"]["id"]
    accepted = await client.post(
        "/api/pilot/tasks", json={"request_id": "turn", "thread_id": thread_id, "message": "wait"}
    )
    task_id = accepted.json()["task_id"]
    async with asyncio.timeout(5):
        await entered.wait()
        cancelled = await client.post(
            f"/api/pilot/tasks/{task_id}/cancel", json={"request_id": "cancel"}
        )
    assert cancelled.status_code == 202, cancelled.text
    assert cancelled.json()["task"]["state"] == "UNCERTAIN"
    assert c["service"]._execution.done()
    replay = await client.post(f"/api/pilot/tasks/{task_id}/cancel", json={"request_id": "cancel"})
    assert replay.status_code == 200 and replay.json()["task"]["replayed"] is True
    assert (await client.get(f"/api/pilot/threads/{thread_id}/messages")).json() == {"messages": []}
    blocked = await client.post(
        "/api/pilot/tasks", json={"request_id": "next", "thread_id": thread_id, "message": "next"}
    )
    assert blocked.status_code == 503 and blocked.json()["uncertain"] is True


async def test_pilot_shell_has_no_secrets_and_security_headers(api):
    client, c, _ = api
    for path in (
        "/pilot",
        "/assets/pilot.html",
        "/assets/pilot.js",
        "/assets/pilot.css",
        "/api/session",
        "/api/pilot/capabilities",
    ):
        response = await client.get(path)
        assert response.status_code == 200, (path, response.text)
        assert "SYNTHETIC_OWNER_TOKEN" not in response.text
        assert "SYNTHETIC_KEY" not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "default-src 'none'" in response.headers["content-security-policy"]
    assert c["requests"] == []


@pytest.mark.parametrize("path", ["/api/health", "/api/agents"])
async def test_pilot_legacy_metadata_requires_owner_authentication(api, path):
    client, c, _ = api
    client.headers.pop("Authorization")
    response = await client.get(path)
    assert response.status_code == 401, response.text
    assert response.json()["code"] == "unauthorized"
    assert c["requests"] == []


async def test_pilot_legacy_chat_disabled_before_body_parsing(api):
    client, c, _ = api
    response = await client.post(
        "/api/chat", content=b"not-json", headers={"content-type": "application/json"}
    )
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "disabled"
    assert c["requests"] == []


@pytest.mark.parametrize("path", ["/pilot", "/assets/pilot.js", "/api/session"])
async def test_bootstrap_rejects_foreign_origin_port(api, path):
    client, c, _ = api
    response = await client.get(path, headers={"Origin": "http://127.0.0.1:9999"})
    assert response.status_code == 403
    assert c["requests"] == []


@pytest.mark.parametrize(
    "path",
    [
        "/api/pilot/capabilities",
        "/api/pilot/threads",
        "/api/pilot/tasks",
        "/api/pilot/tasks/foreign",
        "/api/pilot/threads/foreign/messages",
    ],
)
async def test_read_routes_require_bearer(api, path):
    client, c, _ = api
    client.headers.pop("Authorization")
    response = await client.get(path)
    assert response.status_code == 401 and response.json()["code"] == "unauthorized"
    assert c["requests"] == []


@pytest.mark.parametrize(
    "headers,body,status",
    [
        ({"Authorization": "Bearer wrong"}, b"not-json", 401),
        ({"X-Prototype-CSRF": "wrong"}, b"not-json", 403),
        ({"Origin": "http://evil.invalid"}, b"not-json", 403),
        ({"content-type": "text/plain"}, b"{}", 415),
        ({"content-type": "application/json"}, b"x" * 32769, 413),
        (
            {"content-type": "application/json"},
            b'{"request_id":"r","title":"t","principal":"forged"}',
            422,
        ),
    ],
    ids=[
        "wrong-bearer",
        "wrong-csrf",
        "foreign-origin",
        "wrong-media",
        "oversized",
        "forged-principal",
    ],
)
async def test_write_guard_rejects_before_any_effect(api, headers, body, status):
    client, c, _ = api
    response = await client.post("/api/pilot/threads", content=body, headers=headers)
    assert response.status_code == status, response.text
    assert set(response.json()) == {
        "error",
        "code",
        "request_id",
        "task_id",
        "uncertain",
        "retryable",
    }
    assert (await c["service"].list_threads(c["owner"])) == []
    assert c["requests"] == []


async def test_http_replay_conflict_and_foreign_resources_are_sanitized(api):
    client, c, _ = api
    payload = {"request_id": "thread", "title": "Private synthetic"}
    first = await client.post("/api/pilot/threads", json=payload)
    replay = await client.post("/api/pilot/threads", json=payload)
    assert first.status_code == 201 and replay.status_code == 200
    assert replay.json()["thread"]["id"] == first.json()["thread"]["id"]
    conflict = await client.post("/api/pilot/threads", json={**payload, "title": "Changed"})
    assert conflict.status_code == 409 and "Private synthetic" not in conflict.text
    for path in ("/api/pilot/tasks/foreign", "/api/pilot/threads/foreign/messages"):
        response = await client.get(path)
        assert response.status_code == 404 and response.json()["code"] == "not_found"
    assert c["requests"] == []
