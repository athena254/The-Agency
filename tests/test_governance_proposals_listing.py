"""Slice D: the grant-gated proposals listing shows affected parties and votes.

The GUI ballot panel reads GET /v1/governance/proposals; these tests prove the
enriched fields (eligible_voters, votes with evidence) are rendered from real
Lattice model data and that the route stays fail-closed without a scoped grant.
"""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agency.api.routers.governance import router
from agency.consensus.grants import GrantLedger
from agency.lattice.api import Lattice
from agency.lattice.models import LatticeConfig
from agency.peers.identities import PeerIdentity
from agency.peers.membership import derive_genesis_membership

READ_SCOPE = "GET /v1/governance/proposals"


def _membership():
    ids = [PeerIdentity.from_private_key(Ed25519PrivateKey.generate()) for _ in range(4)]
    members = [
        {
            "peer_id": i.peer_id,
            "public_key": i.fingerprint,
            "roles": ["validator"],
            "joined_epoch": 1,
        }
        for i in ids
    ]
    return ids, derive_genesis_membership(members, policy_version="peer-policy/1")


def _grant_header(ledger: GrantLedger, issuer: PeerIdentity, task_id: str) -> str:
    grant = ledger.issue(
        issuer,
        proposal_id="prop_" + "a" * 32,
        task_id=task_id,
        issued_at_ms=1000,
        expires_at_ms=61000,
        affected_consumers=[],
    )
    payload = {
        "proposal_id": grant.proposal_id,
        "task_id": grant.task_id,
        "issuer_peer_id": grant.issuer_peer_id,
        "membership_epoch": grant.membership_epoch,
        "membership_root": grant.membership_root,
        "nonce": grant.nonce,
        "issued_at_ms": grant.issued_at_ms,
        "expires_at_ms": grant.expires_at_ms,
        "affected_consumers": list(grant.affected_consumers),
        "attestation": {
            "attestor_peer_id": grant.attestation.attestor_peer_id,
            "signature": grant.attestation.signature.hex(),
        },
        "signature": grant.signature.hex(),
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


@pytest.mark.asyncio
async def test_listing_without_grant_fails_closed():
    _ids, mem = _membership()
    lattice = Lattice(LatticeConfig(backend="memory"))
    await lattice.initialize()
    try:
        app = FastAPI()
        app.include_router(router)
        app.state.lattice = lattice
        app.state.peer_membership = mem
        app.state.grant_ledger = GrantLedger(membership=mem)
        app.state.grant_bft_time_ms = 1000
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/v1/governance/proposals")).status_code == 503
    finally:
        await lattice.close()


@pytest.mark.asyncio
async def test_listing_renders_eligible_voters_and_votes_from_models():
    ids, mem = _membership()
    lattice = Lattice(LatticeConfig(backend="memory"))
    await lattice.initialize()
    try:
        proposal_id = await lattice.submit_proposal(
            "butler",
            "spawn_agent",
            {"name": "archivist"},
            eligible_voters=[ids[0].peer_id, ids[1].peer_id],
        )
        await lattice.cast_vote(ids[0].peer_id, proposal_id, "approve", evidence=["finding-1"])
        stored = await lattice.get_proposal_status(proposal_id)
        stored_vote = stored.votes[0]

        app = FastAPI()
        app.include_router(router)
        app.state.lattice = lattice
        app.state.peer_membership = mem
        ledger = GrantLedger(membership=mem)
        app.state.grant_ledger = ledger
        app.state.grant_bft_time_ms = 1000

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # A grant scoped to a different read route must not open this one.
            wrong_scope = _grant_header(ledger, ids[0], "GET /v1/agents")
            wrong = await client.get(
                "/v1/governance/proposals", headers={"X-Agency-Grant": wrong_scope}
            )
            assert wrong.status_code == 503

            header = _grant_header(ledger, ids[0], READ_SCOPE)
            response = await client.get(
                "/v1/governance/proposals", headers={"X-Agency-Grant": header}
            )
            assert response.status_code == 200, response.text
            proposals = response.json()["proposals"]
            assert len(proposals) == 1
            body = proposals[0]
            assert body["proposal_id"] == proposal_id
            assert body["proposal_type"] == "spawn_agent"
            assert body["proposer_id"] == "butler"
            assert body["status"] == "open"
            assert body["quorum_required"] == stored.quorum_required
            assert body["expires_at"] == stored.expires_at.isoformat()
            assert body["eligible_voters"] == [ids[0].peer_id, ids[1].peer_id]
            assert body["vote_count"] == 1
            assert body["votes"] == [
                {
                    "voter_id": stored_vote.voter_id,
                    "decision": stored_vote.decision,
                    "evidence": ["finding-1"],
                    "weight": stored_vote.weight,
                    "timestamp": stored_vote.timestamp.isoformat(),
                }
            ]
    finally:
        await lattice.close()
