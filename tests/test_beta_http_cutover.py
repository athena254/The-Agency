"""Until peer grants exist, HTTP apps do not expose side effects or private data."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agency.api.server import create_app as create_agency_app
from agency.butler.server import create_app as create_butler_app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1/agents"),
        ("POST", "/v1/agents"),
        ("DELETE", "/v1/agents/example"),
        ("PATCH", "/v1/agents/example/capabilities"),
        ("GET", "/v1/tasks"),
        ("POST", "/v1/tasks"),
        ("PATCH", "/v1/tasks/example"),
        ("POST", "/v1/tasks/example/messages"),
        ("POST", "/v1/evidence"),
        ("GET", "/v1/memory/search?q=secret&agent_id=other"),
        ("GET", "/v1/evidence"),
        ("GET", "/v1/risk/example"),
        ("POST", "/v1/bridges/example/execute"),
        ("GET", "/v1/bridges/example/health"),
        ("POST", "/v1/governance/propose"),
        ("POST", "/v1/governance/vote"),
        ("GET", "/v1/governance/proposals"),
    ],
)
async def test_agency_app_denies_unverified_http_authority_before_body(
    method: str, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENCY_GOVERNANCE_TOKEN", "local-test-token")
    app = create_agency_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(
            method,
            path,
            content=b"not json",
            headers={"X-Agency-Governance-Token": "local-test-token"},
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "Peer authorization is unavailable."}


@pytest.mark.asyncio
async def test_agency_health_remains_available() -> None:
    app = create_agency_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_butler_message_denied_before_service_and_body() -> None:
    app = create_butler_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/message",
            content=b"not json",
            headers={"X-Agency-Governance-Token": "local-test-token"},
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "Peer authorization is unavailable."}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("factory", "prefix", "path"),
    [
        (create_agency_app, "/agency", "/v1/agents"),
        (create_butler_app, "/butler", "/v1/message"),
    ],
)
async def test_prefixed_mount_cannot_bypass_child_deny(factory, prefix: str, path: str) -> None:
    parent = FastAPI()
    parent.mount(prefix, factory())
    async with AsyncClient(transport=ASGITransport(app=parent), base_url="http://test") as client:
        result = await client.post(prefix + path, content=b"not json")
    assert result.status_code == 503
    assert result.json() == {"detail": "Peer authorization is unavailable."}


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", [create_agency_app, create_butler_app])
async def test_cors_preflight_and_non_get_health_denied(factory) -> None:
    app = factory()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        health_post = await client.post("/v1/health")
        preflight = await client.options(
            "/v1/health",
            headers={"Origin": "http://untrusted", "Access-Control-Request-Method": "POST"},
        )
    for result in (health_post, preflight):
        assert result.status_code == 503
