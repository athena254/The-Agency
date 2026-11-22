"""Intake grant path: signed grants as a first-class alternative to bearer tokens.

Pins the gaps.md #18 route-enforcement swap on ``POST /v1/tasks`` and butler
``POST /v1/message``:

* a valid ``X-Agency-Grant`` (same wire format as the grant-gated routes)
  authenticates the intake actuator-side — nonce consumed at the point of use;
* expired, revoked, replayed, tampered, unauthorised and wrongly-scoped grants
  fail closed with 401/403 from the fixed public catalog;
* bearer behaviour is byte-for-byte unchanged (a presented ``Authorization``
  header always wins and its failures are never bypassed by a grant);
* the intake audit/log line records the auth kind and, on the grant path, the
  verified grant digest;
* ``POST /v1/agents`` stays 503-blocked even with a valid grant (spec-pinned
  boundary, gaps.md #21e).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from structlog.testing import capture_logs

from agency.consensus.grants import GrantLedger
from agency.peers.canonical import digest_json
from agency.peers.identities import PeerIdentity
from agency.peers.membership import derive_genesis_membership

POLICY_VERSION = "peer-policy/1"
PROPOSAL_ID = "prop_" + "a" * 32
TASKS_SCOPE = "POST /v1/tasks"
MESSAGE_SCOPE = "POST /v1/message"

OWNER_TOKEN = "sk-owner-token-with-at-least-24-chars"
AGENT_TOKEN = "sk-agent-token-with-at-least-24-char"


def _genesis() -> tuple[list[PeerIdentity], list[dict[str, Any]], Any]:
    ids = [PeerIdentity.from_private_key(Ed25519PrivateKey.generate()) for _ in range(4)]
    members = [
        {
            "peer_id": identity.peer_id,
            "public_key": identity.fingerprint,
            "roles": ["validator"],
            "joined_epoch": 1,
        }
        for identity in ids
    ]
    return ids, members, derive_genesis_membership(members, policy_version=POLICY_VERSION)


def _write_genesis(tmp_path: Path, members: list[dict[str, Any]]) -> str:
    path = tmp_path / "genesis.json"
    path.write_text(
        json.dumps({"policy_version": POLICY_VERSION, "members": members}), encoding="utf-8"
    )
    return str(path)


def _write_tokens(tmp_path: Path, entries: list[dict[str, str]]) -> str:
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return str(path)


def _encode_grant(payload: dict[str, Any]) -> str:
    return base64.b64encode(json.dumps(payload).encode()).decode()


def _grant_header(ledger: GrantLedger, issuer: PeerIdentity, scope: str) -> str:
    """Issue + encode a grant in the pinned wire format (base64 JSON payload)."""

    grant = ledger.issue(
        issuer,
        proposal_id=PROPOSAL_ID,
        task_id=scope,
        issued_at_ms=1000,
        expires_at_ms=61000,
        affected_consumers=[],
    )
    return _encode_grant(
        {
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
    )


def _api_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tokens: bool, members: list[dict[str, Any]]
) -> TestClient:
    """API server pinned to the given genesis members (lifespan run)."""

    monkeypatch.setenv("AGENCY_GENESIS_PATH", _write_genesis(tmp_path, members))
    monkeypatch.setenv("AGENCY_TASKS_DB", str(tmp_path / "tasks.db"))
    if tokens:
        monkeypatch.setenv(
            "AGENCY_API_TOKENS",
            _write_tokens(
                tmp_path,
                [
                    {"token": OWNER_TOKEN, "caller_id": "danny", "role": "owner"},
                    {"token": AGENT_TOKEN, "caller_id": "agent-coder-1", "role": "agent"},
                ],
            ),
        )
    else:
        monkeypatch.delenv("AGENCY_API_TOKENS", raising=False)
    from agency.api.server import create_app

    return TestClient(create_app())


# --------------------------------------------------------------------------- #
# POST /v1/tasks — grant accepted, fail-closed rejections, bearer unchanged
# --------------------------------------------------------------------------- #


def test_grant_intake_accepted_without_tokens(tmp_path, monkeypatch):
    ids, members, mem = _genesis()
    with _api_client(tmp_path, monkeypatch, tokens=False, members=members) as client:
        local = GrantLedger(membership=mem)
        header = _grant_header(local, ids[0], TASKS_SCOPE)
        response = client.post(
            "/v1/tasks",
            json={"title": "grant demo", "input": {"description": "harmless noop"}},
            headers={"X-Agency-Grant": header},
        )
    assert response.status_code == 201, response.text
    assert response.json()["created_by"] == ids[0].peer_id  # bound to the verified issuer


def test_grant_intake_accepted_alongside_tokens(tmp_path, monkeypatch):
    ids, members, mem = _genesis()
    with _api_client(tmp_path, monkeypatch, tokens=True, members=members) as client:
        local = GrantLedger(membership=mem)
        header = _grant_header(local, ids[0], TASKS_SCOPE)
        response = client.post(
            "/v1/tasks",
            json={"title": "grant demo"},
            headers={"X-Agency-Grant": header},
        )
    assert response.status_code == 201, response.text
    assert response.json()["created_by"] == ids[0].peer_id


def test_expired_grant_rejected_403(tmp_path, monkeypatch):
    ids, members, mem = _genesis()
    with _api_client(tmp_path, monkeypatch, tokens=False, members=members) as client:
        local = GrantLedger(membership=mem)
        header = _grant_header(local, ids[0], TASKS_SCOPE)
        client.app.state.grant_bft_time_ms = 61000  # committed time at/after expiry
        response = client.post("/v1/tasks", json={"title": "x"}, headers={"X-Agency-Grant": header})
    assert response.status_code == 403
    assert response.json() == {"detail": "Grant rejected: expired."}


def test_revoked_grant_rejected_403(tmp_path, monkeypatch):
    ids, members, mem = _genesis()
    with _api_client(tmp_path, monkeypatch, tokens=False, members=members) as client:
        local = GrantLedger(membership=mem)
        # Deterministic inputs reproduce the exact grant referenced by the header.
        issued = local.issue(
            ids[0],
            proposal_id=PROPOSAL_ID,
            task_id=TASKS_SCOPE,
            issued_at_ms=1000,
            expires_at_ms=61000,
            affected_consumers=[],
        )
        header = _grant_header(local, ids[0], TASKS_SCOPE)
        # Revoke on the actuator's own ledger, signed by a pinned issuer.
        client.app.state.grant_ledger.revoke(ids[1], issued, reason_digest=digest_json({"r": "1"}))
        response = client.post("/v1/tasks", json={"title": "x"}, headers={"X-Agency-Grant": header})
    assert response.status_code == 403
    assert response.json() == {"detail": "Grant rejected: revoked."}


def test_wrong_scope_grant_rejected_403(tmp_path, monkeypatch):
    ids, members, mem = _genesis()
    with _api_client(tmp_path, monkeypatch, tokens=False, members=members) as client:
        local = GrantLedger(membership=mem)
        header = _grant_header(local, ids[0], "GET /v1/agents")  # not the intake scope
        response = client.post("/v1/tasks", json={"title": "x"}, headers={"X-Agency-Grant": header})
    assert response.status_code == 403
    assert response.json() == {"detail": "Grant is not scoped to this route."}


def test_tampered_grant_rejected_401(tmp_path, monkeypatch):
    ids, members, mem = _genesis()
    with _api_client(tmp_path, monkeypatch, tokens=False, members=members) as client:
        local = GrantLedger(membership=mem)
        header = _grant_header(local, ids[0], TASKS_SCOPE)
        payload = json.loads(base64.b64decode(header).decode())
        signature = payload["signature"]
        payload["signature"] = signature[:-2] + ("00" if signature[-2:] != "00" else "11")
        tampered = _encode_grant(payload)
        response = client.post(
            "/v1/tasks", json={"title": "x"}, headers={"X-Agency-Grant": tampered}
        )
    assert response.status_code == 401
    assert response.json() == {"detail": "Grant rejected: invalid_signature."}


def test_malformed_grant_rejected_401(tmp_path, monkeypatch):
    _ids, members, _mem = _genesis()
    with _api_client(tmp_path, monkeypatch, tokens=False, members=members) as client:
        response = client.post(
            "/v1/tasks", json={"title": "x"}, headers={"X-Agency-Grant": "not-base64!!"}
        )
    assert response.status_code == 401
    assert response.json() == {"detail": "Malformed signed grant."}


def test_unauthorized_issuer_grant_rejected_401(tmp_path, monkeypatch):
    _ids, members, _mem = _genesis()
    with _api_client(tmp_path, monkeypatch, tokens=False, members=members) as client:
        outsider = PeerIdentity.from_private_key(Ed25519PrivateKey.generate())
        # A ledger pinned to the outsider only — the server's genesis does
        # not know this issuer, so the signature check must fail closed.
        foreign = GrantLedger(
            membership=derive_genesis_membership(
                [
                    {
                        "peer_id": outsider.peer_id,
                        "public_key": outsider.fingerprint,
                        "roles": ["validator"],
                        "joined_epoch": 1,
                    }
                ],
                policy_version=POLICY_VERSION,
            )
        )
        header = _grant_header(foreign, outsider, TASKS_SCOPE)
        response = client.post("/v1/tasks", json={"title": "x"}, headers={"X-Agency-Grant": header})
    assert response.status_code == 401
    assert response.json() == {"detail": "Grant rejected: unauthorized_issuer."}


def test_grant_replay_rejected_403(tmp_path, monkeypatch):
    ids, members, mem = _genesis()
    with _api_client(tmp_path, monkeypatch, tokens=False, members=members) as client:
        local = GrantLedger(membership=mem)
        header = _grant_header(local, ids[0], TASKS_SCOPE)
        headers = {"X-Agency-Grant": header}
        first = client.post("/v1/tasks", json={"title": "first"}, headers=headers)
        second = client.post("/v1/tasks", json={"title": "second"}, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 403
    assert second.json() == {"detail": "Grant rejected: replay."}


def test_bearer_path_unchanged_with_grant_header_present(tmp_path, monkeypatch):
    ids, members, mem = _genesis()
    with _api_client(tmp_path, monkeypatch, tokens=True, members=members) as client:
        local = GrantLedger(membership=mem)
        grant_header = _grant_header(local, ids[0], TASKS_SCOPE)
        response = client.post(
            "/v1/tasks",
            json={"title": "bearer", "created_by": "spoofed"},
            headers={"Authorization": f"Bearer {AGENT_TOKEN}", "X-Agency-Grant": grant_header},
        )
        assert response.status_code == 201
        assert response.json()["created_by"] == "agent-coder-1"  # bearer identity, byte-for-byte
        bad = client.post(
            "/v1/tasks",
            json={"title": "bad bearer"},
            headers={
                "Authorization": "Bearer not-a-real-token-value-1234",
                "X-Agency-Grant": grant_header,
            },
        )
    assert bad.status_code == 401  # a bad bearer is never bypassed by a valid grant
    assert bad.json() == {"detail": "Invalid or missing bearer token."}


def test_no_credentials_behaviour_unchanged(tmp_path, monkeypatch):
    _ids, members, _mem = _genesis()
    with _api_client(tmp_path, monkeypatch, tokens=True, members=members) as client:
        response = client.post("/v1/tasks", json={"title": "x"})
    assert response.status_code == 401


def test_no_tokens_no_genesis_still_503(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENCY_API_TOKENS", raising=False)
    monkeypatch.delenv("AGENCY_GENESIS_PATH", raising=False)
    monkeypatch.setenv("AGENCY_TASKS_DB", str(tmp_path / "tasks.db"))
    from agency.api.server import create_app

    with TestClient(create_app()) as client:
        response = client.post("/v1/tasks", json={"title": "x"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Peer authorization is unavailable."}


def test_auth_kind_recorded_in_intake_log(tmp_path, monkeypatch):
    ids, members, mem = _genesis()
    with _api_client(tmp_path, monkeypatch, tokens=True, members=members) as client:
        local = GrantLedger(membership=mem)
        grant_header = _grant_header(local, ids[0], TASKS_SCOPE)
        with capture_logs() as grant_logs:
            granted = client.post(
                "/v1/tasks", json={"title": "g"}, headers={"X-Agency-Grant": grant_header}
            )
        with capture_logs() as bearer_logs:
            bearer = client.post(
                "/v1/tasks",
                json={"title": "b"},
                headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
            )
    assert granted.status_code == 201 and bearer.status_code == 201
    grant_line = next(e for e in grant_logs if e["event"] == "api.task.created")
    assert grant_line["auth_kind"] == "grant"
    assert grant_line["principal"] == ids[0].peer_id
    assert isinstance(grant_line["grant_id"], str) and len(grant_line["grant_id"]) == 64
    bearer_line = next(e for e in bearer_logs if e["event"] == "api.task.created")
    assert bearer_line["auth_kind"] == "bearer"
    assert bearer_line["grant_id"] is None
    assert bearer_line["principal"] == "danny"


def test_agent_intake_stays_blocked_with_valid_grant(tmp_path, monkeypatch):
    ids, members, mem = _genesis()
    with _api_client(tmp_path, monkeypatch, tokens=True, members=members) as client:
        local = GrantLedger(membership=mem)
        header = _grant_header(local, ids[0], "POST /v1/agents")
        response = client.post(
            "/v1/agents",
            json={"name": "peer-agent", "domain": "general"},
            headers={"X-Agency-Grant": header},
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "Peer authorization is unavailable."}


# --------------------------------------------------------------------------- #
# Butler POST /v1/message — grant accepted, fail-closed, bearer unchanged
# --------------------------------------------------------------------------- #


def _butler_app(mem) -> Any:
    from agency.butler.config import ButlerConfig
    from agency.butler.server import create_app
    from agency.butler.service import ButlerService
    from agency.orchestrator import AgencyOrchestrator

    app = create_app()
    orchestrator = AgencyOrchestrator(memory_db_path=":memory:", skill_db_path=":memory:")
    app.state.orchestrator = orchestrator
    app.state.butler = ButlerService(config=ButlerConfig(), orchestrator=orchestrator, llm=None)
    app.state.grant_ledger = GrantLedger(membership=mem)
    app.state.grant_bft_time_ms = 1000
    return app


async def _teardown_butler(app) -> None:
    service = app.state.butler
    if service is not None:
        try:
            await service.stop()
        except Exception:  # noqa: BLE001, S110 — teardown best effort
            pass


@pytest.mark.asyncio
async def test_butler_grant_intake_accepted(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AGENCY_API_TOKENS", raising=False)
    ids, _members, mem = _genesis()
    app = _butler_app(mem)
    try:
        local = GrantLedger(membership=mem)
        header = _grant_header(local, ids[0], MESSAGE_SCOPE)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with capture_logs() as logs:
                first = await client.post(
                    "/v1/message",
                    json={"message": "grant hello"},
                    headers={"X-Agency-Grant": header},
                )
            second = await client.post(
                "/v1/message",
                json={"message": "grant hello 2"},
                headers={"X-Agency-Grant": header},
            )
    finally:
        await _teardown_butler(app)
    assert first.status_code == 200, first.text
    # Replay: the nonce was consumed at the point of use on the first intake.
    assert second.status_code == 403
    assert second.json() == {"detail": "Grant rejected: replay."}
    line = next(e for e in logs if e["event"] == "butler.api.message")
    assert line["auth_kind"] == "grant"
    assert line["sender"] == ids[0].peer_id


@pytest.mark.asyncio
async def test_butler_expired_grant_rejected_403(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AGENCY_API_TOKENS", raising=False)
    ids, _members, mem = _genesis()
    app = _butler_app(mem)
    try:
        local = GrantLedger(membership=mem)
        header = _grant_header(local, ids[0], MESSAGE_SCOPE)
        app.state.grant_bft_time_ms = 61000
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/v1/message", json={"message": "late"}, headers={"X-Agency-Grant": header}
            )
    finally:
        await _teardown_butler(app)
    assert response.status_code == 403
    assert response.json() == {"detail": "Grant rejected: expired."}


@pytest.mark.asyncio
async def test_butler_wrong_scope_grant_rejected_403(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AGENCY_API_TOKENS", raising=False)
    ids, _members, mem = _genesis()
    app = _butler_app(mem)
    try:
        local = GrantLedger(membership=mem)
        header = _grant_header(local, ids[0], TASKS_SCOPE)  # tasks scope, not message
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/v1/message", json={"message": "hi"}, headers={"X-Agency-Grant": header}
            )
    finally:
        await _teardown_butler(app)
    assert response.status_code == 403
    assert response.json() == {"detail": "Grant is not scoped to this route."}


@pytest.mark.asyncio
async def test_butler_bearer_unchanged_and_not_bypassed(tmp_path, monkeypatch) -> None:
    ids, _members, mem = _genesis()
    monkeypatch.delenv("AGENCY_GENESIS_PATH", raising=False)
    monkeypatch.setenv(
        "AGENCY_API_TOKENS",
        _write_tokens(
            tmp_path, [{"token": AGENT_TOKEN, "caller_id": "external-agent-1", "role": "agent"}]
        ),
    )
    app = _butler_app(mem)
    try:
        local = GrantLedger(membership=mem)
        grant_header = _grant_header(local, ids[0], MESSAGE_SCOPE)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ok = await client.post(
                "/v1/message",
                json={"message": "status please"},
                headers={"Authorization": f"Bearer {AGENT_TOKEN}", "X-Agency-Grant": grant_header},
            )
            bad = await client.post(
                "/v1/message",
                json={"message": "sneak"},
                headers={
                    "Authorization": "Bearer wrong-token-entirely-12345",
                    "X-Agency-Grant": grant_header,
                },
            )
            anonymous = await client.post("/v1/message", json={"message": "anon"})
    finally:
        await _teardown_butler(app)
    assert ok.status_code == 200
    assert bad.status_code == 401  # a bad bearer is not bypassed by a grant
    assert anonymous.status_code == 401
