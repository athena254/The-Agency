"""Grant-gated /v1 route tests (Tier 6 / B0-B1)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from agency.consensus.grants import GrantLedger
from agency.peers.identities import PeerIdentity
from agency.peers.membership import derive_genesis_membership


def _genesis(tmp_path: Path):
    ids = [PeerIdentity.from_private_key(Ed25519PrivateKey.generate()) for _ in range(4)]
    members = [
        {"peer_id": i.peer_id, "public_key": i.fingerprint, "roles": ["validator"], "joined_epoch": 1}
        for i in ids
    ]
    mem = derive_genesis_membership(members, policy_version="peer-policy/1")
    path = tmp_path / "genesis.json"
    path.write_text(
        json.dumps({"policy_version": "peer-policy/1", "members": members}), encoding="utf-8"
    )
    return ids, mem, path


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


def test_read_route_requires_grant(tmp_path, monkeypatch):
    _ids, _mem, genesis = _genesis(tmp_path)
    monkeypatch.setenv("AGENCY_GENESIS_PATH", str(genesis))
    from agency.api.server import create_app

    with TestClient(create_app()) as client:
        assert client.get("/v1/agents").status_code == 503


def test_read_route_with_valid_grant(tmp_path, monkeypatch):
    ids, _mem, genesis = _genesis(tmp_path)
    monkeypatch.setenv("AGENCY_GENESIS_PATH", str(genesis))
    from agency.api.server import create_app

    with TestClient(create_app()) as client:
        ledger = client.app.state.grant_ledger
        header = _grant_header(ledger, ids[0], "GET /v1/agents")
        response = client.get("/v1/agents", headers={"X-Agency-Grant": header})
        assert response.status_code == 200
