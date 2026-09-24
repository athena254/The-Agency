"""Public governance HTTP must not accept caller-asserted voter identities."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agency.api.routers.governance import router
from agency.lattice.api import Lattice
from agency.lattice.models import LatticeConfig


@pytest.mark.asyncio
async def test_governance_vote_requires_configured_token_and_verified_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENCY_GOVERNANCE_TOKEN", raising=False)
    lattice = Lattice(LatticeConfig(backend="memory"))
    await lattice.initialize()
    try:
        proposal_id = await lattice.submit_proposal("butler", "spawn_agent", {"name": "test"})
        app = FastAPI()
        app.state.lattice = lattice
        app.include_router(router)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            url = "/v1/governance/vote"
            forged = {"proposal_id": proposal_id, "voter_id": "butler", "decision": "approve"}
            assert (await client.post(url, json=forged)).status_code == 503
            monkeypatch.setenv("AGENCY_GOVERNANCE_TOKEN", "local-test-token")
            assert (await client.post(url, json=forged)).status_code == 403
            headers = {"X-Agency-Governance-Token": "local-test-token"}
            assert (await client.post(url, json=forged, headers=headers)).status_code == 422
            assert (await lattice.get_proposal_status(proposal_id)).votes == []
            response = await client.post(
                url, json={"proposal_id": proposal_id, "decision": "approve"}, headers=headers
            )
            assert response.status_code == 200
            votes = (await lattice.get_proposal_status(proposal_id)).votes
            assert [vote.voter_id for vote in votes] == ["user"]
    finally:
        await lattice.close()


@pytest.mark.asyncio
async def test_governance_proposer_is_authenticated_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENCY_GOVERNANCE_TOKEN", "local-test-token")
    lattice = Lattice(LatticeConfig(backend="memory"))
    await lattice.initialize()
    try:
        app = FastAPI()
        app.state.lattice = lattice
        app.include_router(router)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"X-Agency-Governance-Token": "local-test-token"}
            assert (await client.get("/v1/governance/proposals")).status_code == 403
            payload = {"name": "test", "proposer": "butler"}
            assert (
                await client.post("/v1/governance/propose", json=payload, headers=headers)
            ).status_code == 422
            response = await client.post(
                "/v1/governance/propose", json={"name": "test"}, headers=headers
            )
            assert response.status_code == 200
            assert response.json()["proposer"] == "user"
    finally:
        await lattice.close()
