"""Tests for the local prototype API (``agency.prototype.app``).

These tests exercise the real FastAPI lifespan via :class:`TestClient`. The
Butler is injected (a recording fake, or a real echo-backed service) so no
live provider, Telegram connection, or tracked database is touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agency.prototype.app as prototype_app
from agency.butler.config import ButlerConfig
from agency.butler.service import ButlerService
from agency.kernel.identity import Agent
from agency.llm.adapter import LLMAdapter
from agency.orchestrator import AgencyOrchestrator
from agency.prototype.app import create_app

LOCAL_BASE = "http://127.0.0.1"
CSRF_HEADER = "X-Prototype-CSRF"


@dataclass
class _StubConfig:
    """Minimal stand-in for :class:`ButlerConfig` (no ``.env`` access)."""

    max_message_length: int = 60


class _FakeRouter:
    """Exposes a fixed roster so tests prove the API reads the service roster."""

    def __init__(self, agents: list[Agent]) -> None:
        self._agents = agents

    def list_agents(self) -> list[Agent]:
        return list(self._agents)


class FakeButler:
    """Duck-typed Butler recording lifecycle and message calls."""

    def __init__(self, agents: list[Agent] | None = None) -> None:
        self.router = _FakeRouter(agents or [])
        self.orchestrator: Any = None
        self.config = _StubConfig()
        self.started = 0
        self.stopped = 0
        self.calls: list[dict[str, Any]] = []

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1

    async def handle_message(
        self,
        message: str,
        sender: str,
        context: dict[str, Any],
        *,
        memory_enabled: bool = True,
    ) -> str:
        self.calls.append(
            {
                "message": message,
                "sender": sender,
                "context": context,
                "memory_enabled": memory_enabled,
            }
        )
        return f"reply:{message}"


def _agent(agent_id: str, name: str, domain: str) -> Agent:
    return Agent(id=agent_id, name=name, domain=domain)


def _csrf(client: TestClient) -> str:
    response = client.get("/api/session")
    assert response.status_code == 200
    token = response.json()["csrf_token"]
    assert isinstance(token, str) and token
    return token


def _post_headers(token: str, origin: str | None = LOCAL_BASE) -> dict[str, str]:
    headers = {CSRF_HEADER: token}
    if origin is not None:
        headers["Origin"] = origin
    return headers


# --------------------------------------------------------------------------- #
# App factory + lifespan
# --------------------------------------------------------------------------- #


def test_create_app_returns_fastapi_and_keeps_injected_butler() -> None:
    butler = FakeButler()
    app = create_app(butler)
    assert isinstance(app, FastAPI)
    assert app.state.butler is butler
    assert isinstance(app.state.csrf_token, str) and app.state.csrf_token


def test_create_app_without_butler_builds_app() -> None:
    app = create_app()
    assert isinstance(app, FastAPI)
    assert app.state.butler is None


def test_lifespan_starts_and_stops_injected_butler_once() -> None:
    butler = FakeButler()
    app = create_app(butler)
    with TestClient(app, base_url=LOCAL_BASE):
        assert butler.started == 1
        assert butler.stopped == 0
    assert butler.started == 1
    assert butler.stopped == 1


# --------------------------------------------------------------------------- #
# GET endpoints
# --------------------------------------------------------------------------- #


def test_health_reports_running_butler_and_agent_count() -> None:
    agents = [_agent("a1", "Alpha", "general"), _agent("a2", "Beta", "security")]
    with TestClient(create_app(FakeButler(agents)), base_url=LOCAL_BASE) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "butler", "agents"}
    assert body["status"] == "ok"
    assert body["butler"] == "running"
    assert body["agents"] == 2


def test_agents_endpoint_returns_service_roster() -> None:
    agents = [_agent("a1", "Alpha", "general"), _agent("a2", "Beta", "security")]
    with TestClient(create_app(FakeButler(agents)), base_url=LOCAL_BASE) as client:
        response = client.get("/api/agents")
    assert response.status_code == 200
    assert response.json() == {
        "agents": [
            {"id": "a1", "name": "Alpha", "domain": "general"},
            {"id": "a2", "name": "Beta", "domain": "security"},
        ]
    }


def test_session_issues_stable_csrf_token() -> None:
    with TestClient(create_app(FakeButler()), base_url=LOCAL_BASE) as client:
        first = client.get("/api/session")
        second = client.get("/api/session")
    assert first.status_code == 200
    token = first.json()["csrf_token"]
    assert isinstance(token, str) and token
    assert second.json()["csrf_token"] == token


def test_session_rejects_non_loopback_host() -> None:
    with TestClient(create_app(FakeButler()), base_url=LOCAL_BASE) as client:
        response = client.get("/api/session", headers={"host": "evil.example.com"})
    assert response.status_code == 403
    assert response.json()["error"]


# --------------------------------------------------------------------------- #
# POST /api/chat — success
# --------------------------------------------------------------------------- #


def test_chat_success_calls_butler_with_fixed_local_sender() -> None:
    butler = FakeButler()
    with TestClient(create_app(butler), base_url=LOCAL_BASE) as client:
        token = _csrf(client)
        response = client.post(
            "/api/chat",
            json={"message": "hello there"},
            headers=_post_headers(token),
        )
    assert response.status_code == 200
    assert response.json() == {"response": "reply:hello there"}
    assert len(butler.calls) == 1
    call = butler.calls[0]
    assert call["message"] == "hello there"
    assert call["sender"] == "local:prototype"
    assert call["context"] == {}
    assert call["memory_enabled"] is False


# --------------------------------------------------------------------------- #
# POST /api/chat — rejections happen before the Butler call
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "hi", "sender": "root"},
        {"message": "hi", "context": {"sender": "root"}},
        {"message": "hi", "memory_enabled": True},
        {"message": "hi", "trusted": True},
        {"message": "hi", "agent_id": "a1"},
    ],
)
def test_chat_rejects_caller_supplied_fields(payload: dict[str, Any]) -> None:
    butler = FakeButler()
    with TestClient(create_app(butler), base_url=LOCAL_BASE) as client:
        token = _csrf(client)
        response = client.post("/api/chat", json=payload, headers=_post_headers(token))
    assert response.status_code == 422
    assert isinstance(response.json()["error"], str) and response.json()["error"]
    assert butler.calls == []


def test_chat_rejects_empty_message() -> None:
    butler = FakeButler()
    with TestClient(create_app(butler), base_url=LOCAL_BASE) as client:
        token = _csrf(client)
        response = client.post("/api/chat", json={"message": "   "}, headers=_post_headers(token))
    assert response.status_code == 422
    assert butler.calls == []


def test_chat_rejects_overlong_message_without_echoing_it() -> None:
    butler = FakeButler()
    secret_text = "s" * (butler.config.max_message_length + 1)
    with TestClient(create_app(butler), base_url=LOCAL_BASE) as client:
        token = _csrf(client)
        response = client.post(
            "/api/chat", json={"message": secret_text}, headers=_post_headers(token)
        )
    assert response.status_code == 413
    assert response.json()["error"]
    assert secret_text not in response.text
    assert butler.calls == []


def test_chat_rejects_non_loopback_host() -> None:
    butler = FakeButler()
    with TestClient(create_app(butler), base_url=LOCAL_BASE) as client:
        token = _csrf(client)
        response = client.post(
            "/api/chat",
            json={"message": "hi"},
            headers={**_post_headers(token), "host": "evil.example.com"},
        )
    assert response.status_code == 403
    assert butler.calls == []


def test_chat_rejects_foreign_origin() -> None:
    butler = FakeButler()
    with TestClient(create_app(butler), base_url=LOCAL_BASE) as client:
        token = _csrf(client)
        response = client.post(
            "/api/chat",
            json={"message": "hi"},
            headers=_post_headers(token, origin="http://evil.example.com"),
        )
    assert response.status_code == 403
    assert butler.calls == []


def test_chat_rejects_missing_and_wrong_csrf() -> None:
    butler = FakeButler()
    with TestClient(create_app(butler), base_url=LOCAL_BASE) as client:
        missing = client.post("/api/chat", json={"message": "hi"})
        wrong = client.post(
            "/api/chat", json={"message": "hi"}, headers={CSRF_HEADER: "not-the-token"}
        )
    assert missing.status_code == 403
    assert wrong.status_code == 403
    assert butler.calls == []


def test_chat_rejects_non_json_content_type() -> None:
    butler = FakeButler()
    with TestClient(create_app(butler), base_url=LOCAL_BASE) as client:
        token = _csrf(client)
        response = client.post(
            "/api/chat",
            content="message=hi",
            headers={CSRF_HEADER: token, "Content-Type": "application/x-www-form-urlencoded"},
        )
    assert response.status_code == 415
    assert butler.calls == []


def test_no_wildcard_cors_headers() -> None:
    with TestClient(create_app(FakeButler()), base_url=LOCAL_BASE) as client:
        health = client.get("/api/health")
        preflight = client.options(
            "/api/chat",
            headers={
                "Origin": LOCAL_BASE,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": CSRF_HEADER,
            },
        )
    assert health.headers.get("access-control-allow-origin") is None
    assert preflight.headers.get("access-control-allow-origin") != "*"


# --------------------------------------------------------------------------- #
# Static assets (owned by the UI lane; none are invented here)
# --------------------------------------------------------------------------- #


def test_static_index_and_assets_are_served_when_present(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html><title>ok</title>", encoding="utf-8")
    (static_dir / "app.js").write_text("console.log('ok');", encoding="utf-8")
    monkeypatch.setattr(prototype_app, "STATIC_DIR", static_dir)

    with TestClient(create_app(FakeButler()), base_url=LOCAL_BASE) as client:
        root = client.get("/")
        asset = client.get("/assets/app.js")

    assert root.status_code == 200
    assert "<!doctype html>" in root.text
    assert asset.status_code == 200
    assert "console.log" in asset.text


def test_missing_static_root_returns_json_error(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(prototype_app, "STATIC_DIR", tmp_path / "missing")

    with TestClient(create_app(FakeButler()), base_url=LOCAL_BASE) as client:
        response = client.get("/")

    assert response.status_code == 404
    assert isinstance(response.json()["error"], str)


# --------------------------------------------------------------------------- #
# Real Butler + real lifespan over the explicit echo provider
# --------------------------------------------------------------------------- #


def test_real_butler_echo_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_lattice() -> None:
        return None

    # Keep the real Butler/orchestrator but avoid the shared Lattice backend
    # so the test never opens the tracked ``data/lattice.db``.
    monkeypatch.setattr("agency.orchestrator.get_lattice", _no_lattice)
    orchestrator = AgencyOrchestrator(
        memory_db_path=":memory:",
        llm=LLMAdapter(provider="echo", model="echo"),
    )
    service = ButlerService(
        config=ButlerConfig(llm_provider="none"),
        orchestrator=orchestrator,
    )
    app = create_app(service)
    with TestClient(app, base_url=LOCAL_BASE) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert health.json()["agents"] >= 1

        agents = client.get("/api/agents")
        assert agents.status_code == 200
        assert len(agents.json()["agents"]) >= 1

        token = _csrf(client)
        response = client.post(
            "/api/chat",
            json={"message": "scan the host for vulnerabilities"},
            headers=_post_headers(token),
        )
    assert response.status_code == 200
    assert isinstance(response.json()["response"], str) and response.json()["response"]
