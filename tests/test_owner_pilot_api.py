"""Regression gate for the unconfigured owner pilot API surface."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from agency.prototype.app import create_app


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
