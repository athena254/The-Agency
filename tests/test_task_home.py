"""Task workspace entry point; no providers or application startup required."""

import pytest
from httpx import ASGITransport, AsyncClient

from agency.prototype.app import create_app


async def test_configured_owner_home_is_the_same_persistent_workspace_as_pilot():
    # Routing uses explicit owner configuration, not a provider or startup probe.
    app = create_app(pilot=object(), authenticator=object())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        home = await client.get("/")
        pilot = await client.get("/pilot")
    assert home.status_code == pilot.status_code == 200
    assert home.content == pilot.content
    assert 'id="pilot-workspace"' in home.text
    assert home.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in home.headers["content-security-policy"]


async def test_legacy_home_retains_chat_and_links_to_persistent_tasks():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        home = await client.get("/")
        capabilities = await client.get("/api/pilot/capabilities")
    assert home.status_code == 200
    assert 'id="composer"' in home.text
    assert 'href="/pilot"' in home.text
    assert "Persistent task workspace" in home.text
    assert "requires configured owner mode" in home.text
    assert capabilities.status_code == 503


@pytest.mark.parametrize("path", ["/", "/pilot"])
async def test_configured_home_has_the_same_strict_bootstrap_origin_guard(path):
    app = create_app(pilot=object(), authenticator=object())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        foreign = await client.get(path, headers={"Origin": "http://localhost:9999"})
        same = await client.get(path, headers={"Origin": "http://127.0.0.1"})
    assert foreign.status_code == 403
    assert same.status_code == 200


async def test_task_workspace_explains_usage_is_attempt_counters_not_a_tool_event_log():
    app = create_app(pilot=object(), authenticator=object())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        home = await client.get("/")
    assert "These are attempt counters, not a tool event log" in home.text
    assert "not token usage or a bill" in home.text
