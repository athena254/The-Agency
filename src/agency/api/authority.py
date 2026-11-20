"""Grant-verifying dependency for sensitive HTTP routers (Tier 6 / B0).

Replaces the former unconditional 503 with a fail-closed verifier. It is
deliberately NOT an owner-token switch (SPEC_KNOWN_PEER_PROTOCOL §5): every
request must carry a signed, task-scoped grant bound to the exact method+path,
verified against locally pinned membership, expiry, replay and revocation.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any, NoReturn

from fastapi import HTTPException, Request, status

from agency.consensus.grants import GrantError, grant_from_payload

READ_SCOPES: frozenset[str] = frozenset(
    {
        "GET /v1/agents",
        "GET /v1/tasks",
        "GET /v1/evidence",
        "GET /v1/governance/proposals",
        "GET /v1/risk",
        "GET /v1/bridges",
        "GET /v1/memory/search",
        "GET /v1/bridges/health",
    }
)


def _deny(detail: str = "Peer authorization is unavailable.") -> NoReturn:
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


def _is_read_scope(scope: str) -> bool:
    return any(scope == prefix or scope.startswith(prefix + "/") for prefix in READ_SCOPES)


def _writes_open() -> bool:
    return os.environ.get("AGENCY_V1_OPEN_WRITES", "").strip() == "1"


def _verify_finality(request: Request) -> None:
    """Policy placeholder: finality is UNDECIDED (spec §10), so writes stay
    closed unless an explicit owner policy and proof header are present."""
    if not os.environ.get("AGENCY_FINALITY_POLICY", "").strip():
        _deny("Finality policy is not configured; write routes remain closed.")
    raw = request.headers.get("X-Agency-Finality", "")
    if not raw:
        _deny("Finality proof is required for write routes.")
    try:
        proof: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        _deny("Malformed finality proof.")
    if not isinstance(proof.get("app_hash"), str) or not proof["app_hash"].strip():
        _deny("Finality proof lacks an app hash.")
    if not isinstance(proof.get("height"), int) or proof["height"] < 1:
        _deny("Finality proof lacks a committed height.")


def require_peer_authority(request: Request) -> None:
    """Verify a signed grant for the exact HTTP scope, or fail closed."""
    ledger = getattr(request.app.state, "grant_ledger", None)
    membership = getattr(request.app.state, "peer_membership", None)
    if ledger is None or membership is None:
        _deny()

    scope = f"{request.method} {request.url.path}"
    is_read = _is_read_scope(scope)
    if not is_read and not _writes_open():
        _deny("Write routes are closed until owner policy enables them.")

    raw = request.headers.get("X-Agency-Grant", "")
    if not raw:
        _deny("Signed grant is required.")
    try:
        payload = json.loads(base64.b64decode(raw, validate=True).decode("utf-8"))
        grant = grant_from_payload(payload)
    except (ValueError, KeyError, TypeError, GrantError):
        _deny("Malformed signed grant.")

    if is_read and grant.task_id not in {scope, "READ"}:
        _deny("Grant is not scoped to this read route.")
    if not is_read and grant.task_id != scope:
        _deny("Grant is not scoped to this write route.")

    bft_time = getattr(request.app.state, "grant_bft_time_ms", None)
    if not isinstance(bft_time, int):
        _deny("No committed BFT time is available.")
    verification = ledger.verify(grant, bft_time_ms=bft_time)
    if not verification.ok:
        _deny(f"Grant rejected: {verification.reason.value if verification.reason else 'unknown'}")

    if not is_read:
        _verify_finality(request)
