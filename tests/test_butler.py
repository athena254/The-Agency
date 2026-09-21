"""Integration tests for the Butler conversational gateway.

Covers keyword routing, the orchestrator execution pipeline
(submit_task + execute_task), memory/audit persistence, and the
FastAPI HTTP surface.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from agency.butler.config import ButlerConfig
from agency.butler.router import MessageRouter
from agency.butler.server import create_app
from agency.butler.service import ButlerService
from agency.orchestrator import AgencyOrchestrator


@pytest_asyncio.fixture
async def service() -> ButlerService:
    svc = ButlerService(config=ButlerConfig(), orchestrator=AgencyOrchestrator())
    await svc.start()
    try:
        yield svc
    finally:
        await svc.stop()


# ------------------------------------------------------------------ #
# Router
# ------------------------------------------------------------------ #


def test_detect_domains() -> None:
    router = MessageRouter()
    assert router.detect_domain("scan for vulnerabilities and exploits") == "security"
    assert router.detect_domain("please remember what we discussed") == "memory"
    assert router.detect_domain("verify this forensic evidence report") == "evidence"


@pytest.mark.asyncio
async def test_router_routes_to_correct_agent(service: ButlerService) -> None:
    agent = await service.route("check the firewall for intrusions", {"sender": "t"})
    assert agent.domain == "security"
    agent = await service.route("recall our conversation history", {"sender": "t"})
    assert agent.domain == "memory"
    agent = await service.route("collect the forensic artifacts", {"sender": "t"})
    assert agent.domain == "evidence"


# ------------------------------------------------------------------ #
# Service pipeline
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_handle_message_returns_response(service: ButlerService) -> None:
    response = await service.handle_message("scan for vulnerabilities", "tester", {})
    assert isinstance(response, str) and response


@pytest.mark.asyncio
async def test_handle_message_uses_orchestrator_pipeline(service: ButlerService) -> None:
    before = await service.orchestrator.list_tasks()
    response = await service.handle_message("assess the risk exposure", "tester", {})
    assert response
    after = await service.orchestrator.list_tasks()
    assert len(after) == len(before) + 1
    assert after[-1].status.value == "completed"


@pytest.mark.asyncio
async def test_handle_message_stores_memory_and_audit(service: ButlerService) -> None:
    response = await service.handle_message("remember this security finding", "tester", {})
    assert response
    # The turn is persisted in the Butler's own memory store ...
    turns = await service._memory.list_all(limit=100)
    assert any("remember this security finding" in t.content for t in turns)
    # ... and audit-logged.
    entries = await service._audit.query()
    actions = {e.action for e in entries}
    assert "butler.handle_message" in actions
    assert "butler.route" in actions


@pytest.mark.asyncio
async def test_handle_message_rejects_empty(service: ButlerService) -> None:
    with pytest.raises(ValueError):
        await service.handle_message("   ", "tester", {})


# ------------------------------------------------------------------ #
# HTTP API
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_http_message_flow() -> None:
    app = create_app(ButlerConfig())
    # NOTE: httpx ASGI transport does not run the lifespan handler,
    # so inject an already-started service directly.
    svc = ButlerService(config=ButlerConfig(), orchestrator=AgencyOrchestrator())
    await svc.start()
    app.state.butler = svc
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            health = await client.get("/v1/health")
            assert health.status_code == 200
            assert health.json()["status"] in ("ok", "starting")

            agents = await client.get("/v1/agents")
            assert agents.status_code == 200
            assert len(agents.json()) >= 3

            for message, domain in [
                ("scan the network for threats", "security"),
                ("recall what we discussed yesterday", "memory"),
                ("verify the forensic evidence", "evidence"),
            ]:
                resp = await client.post(
                    "/v1/message", json={"message": message, "sender": "http-test"}
                )
                assert resp.status_code == 200, resp.text
                payload = resp.json()
                assert payload["domain"] == domain
                assert payload["response"]
    finally:
        await svc.stop()
