"""Grant-verifying dependency for sensitive HTTP routers (Tier 6 / B0).

Replaces the former unconditional 503 with a fail-closed verifier. It is
deliberately NOT an owner-token switch (SPEC_KNOWN_PEER_PROTOCOL §5): every
request must carry a signed, task-scoped grant bound to the exact method+path,
verified against locally pinned membership, expiry, replay and revocation.

Intake routes (``POST /v1/tasks``, butler ``POST /v1/message``, gaps.md #18)
accept this grant as a first-class alternative to bearer tokens:
:func:`intake_dependency` routes any request presenting an ``Authorization``
header through the unchanged bearer path, and any request presenting only
``X-Agency-Grant`` through :func:`verify_grant_intake` — actuator-side
verification via :func:`agency.consensus.grant_guard.enforce_grant` (the nonce
is consumed at the point of use, so replays are rejected). Invalid, expired,
revoked or wrongly-scoped grants fail closed with 401/403 from a fixed public
catalog; bearer behaviour is byte-for-byte unchanged.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Final, NoReturn

import structlog
from fastapi import FastAPI, HTTPException, Request, status

from agency.api.auth import Principal, require_principal
from agency.consensus.grant_guard import enforce_grant
from agency.consensus.grants import (
    GrantError,
    GrantRejection,
    grant_from_payload,
)

log = structlog.get_logger(__name__)

GRANT_HEADER: Final = "X-Agency-Grant"

# Fixed public error catalog for intake grant failures (401 = the presented
# credential is invalid; 403 = valid-shaped credential, not usable here).
_INTAKE_401_REJECTIONS: Final[frozenset[GrantRejection]] = frozenset(
    {
        GrantRejection.MALFORMED,
        GrantRejection.INVALID_SIGNATURE,
        GrantRejection.UNAUTHORIZED_ISSUER,
        GrantRejection.INSUFFICIENT_SIGNERS,
        GrantRejection.MISSING_ATTESTATION,
        GrantRejection.MISSING_COVERAGE,
    }
)

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

    if scope.startswith("GET /v1/memory/search"):
        owner_scope = request.query_params.get("agent_id")
        if owner_scope and owner_scope not in grant.affected_consumers:
            _deny("Memory owner scope is not covered by the grant")

    if not is_read:
        _verify_finality(request)


# --------------------------------------------------------------------------- #
# Intake grant path (gaps.md #18 route-enforcement tail)
# --------------------------------------------------------------------------- #


def _reject_intake_grant(reason: GrantRejection | None) -> NoReturn:
    """Map a verification failure to the fixed 401/403 catalog. Fail closed."""

    label = reason.value if reason is not None else "unknown"
    if reason in _INTAKE_401_REJECTIONS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Grant rejected: {label}.",
        )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Grant rejected: {label}.",
    )


def verify_grant_intake(request: Request, scope: str) -> tuple[str, str]:
    """Verify the ``X-Agency-Grant`` header for an intake route. Fail closed.

    The grant's ``task_id`` field must equal the exact HTTP scope string
    (e.g. ``"POST /v1/tasks"``), matching the pinned write-route convention of
    :func:`require_peer_authority`. Verification runs through
    :func:`agency.consensus.grant_guard.enforce_grant`, so a first successful
    intake consumes the grant nonce at the point of use — any later
    presentation of the same grant is rejected as a replay.

    Returns
    -------
    (grant_digest, issuer_peer_id)
        Bound server-side; the caller identity for the intake comes from the
        verified grant, never from request input.

    Raises
    ------
    HTTPException
        503 when no grant ledger / committed BFT time is available, 401 on a
        malformed or cryptographically invalid grant, 403 on expired,
        revoked, replayed, wrong-epoch or wrongly-scoped grants.
    """

    ledger = getattr(request.app.state, "grant_ledger", None)
    if ledger is None:
        _deny()
    bft_time = getattr(request.app.state, "grant_bft_time_ms", None)
    if not isinstance(bft_time, int):
        _deny("No committed BFT time is available.")

    raw = request.headers.get(GRANT_HEADER, "")
    if not raw:
        _deny("Signed grant is required.")
    try:
        payload = json.loads(base64.b64decode(raw, validate=True).decode("utf-8"))
        grant = grant_from_payload(payload)
    except (ValueError, KeyError, TypeError, GrantError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed signed grant.",
        ) from None

    if grant.task_id != scope:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Grant is not scoped to this route.",
        )

    verification = enforce_grant(grant, ledger=ledger, bft_time_ms=bft_time)
    if not verification.ok:
        _reject_intake_grant(verification.reason)
    return verification.grant_digest, grant.issuer_peer_id


def intake_dependency(http_scope: str, required_scope: str):  # type: ignore[no-untyped-def]
    """Bearer-or-grant authentication dependency for an intake route.

    A request presenting an ``Authorization`` header always takes the bearer
    path (:func:`agency.api.auth.require_principal`) — its responses are
    byte-for-byte unchanged, and a presented bearer credential is never
    bypassed by a grant header. A request presenting only ``X-Agency-Grant``
    takes the grant path (:func:`verify_grant_intake`) and is bound to the
    verified grant issuer. With neither credential the call falls through to
    the bearer path, preserving today's exact 401/503 behaviour.

    ``request.state.auth_kind`` records ``"bearer"`` or ``"grant"`` and
    ``request.state.grant_id`` the verified grant digest (``None`` for
    bearer) for the intake audit trail.
    """

    async def dependency(request: Request) -> Principal:
        if request.headers.get("authorization") is not None:
            principal = await require_principal(request, required_scope)
            request.state.auth_kind = "bearer"
            request.state.grant_id = None
            return principal
        raw = request.headers.get(GRANT_HEADER, "")
        if raw:
            grant_digest, issuer_peer_id = verify_grant_intake(request, http_scope)
            request.state.auth_kind = "grant"
            request.state.grant_id = grant_digest
            # Least privilege: the verified issuer gets exactly the intake
            # scope, never owner capabilities.
            return Principal(issuer_peer_id, "peer", frozenset({required_scope}))
        principal = await require_principal(request, required_scope)
        request.state.auth_kind = "bearer"
        request.state.grant_id = None
        return principal

    return dependency


GENESIS_PATH_ENV: Final = "AGENCY_GENESIS_PATH"
GRANT_STORE_DB_ENV: Final = "AGENCY_GRANT_STORE_DB"
GRANT_BFT_TIME_ENV: Final = "AGENCY_GRANT_BFT_TIME_MS"
DEFAULT_GRANT_BFT_TIME_MS: Final = 1000


def attach_grant_authority(application: FastAPI) -> None:
    """Load pinned genesis membership + the grant ledger onto ``app.state``.

    Shared by the api and butler lifespans (gaps.md #18): sets
    ``peer_authority`` / ``peer_membership`` / ``grant_ledger`` /
    ``grant_bft_time_ms`` when ``AGENCY_GENESIS_PATH`` exists. The ledger is a
    :class:`~agency.consensus.grant_store.DurableGrantStore` when
    ``AGENCY_GRANT_STORE_DB`` is configured (durable replay/revocation state),
    else the in-memory :class:`~agency.consensus.grants.GrantLedger`. Any
    failure leaves every attribute ``None`` (fail closed) and logs a warning.
    """

    for name in ("peer_authority", "peer_membership", "grant_ledger", "grant_bft_time_ms"):
        setattr(application.state, name, None)
    genesis_path = os.environ.get(GENESIS_PATH_ENV, "data/consensus/genesis.json")
    if not os.path.exists(genesis_path):
        return
    try:
        from agency.consensus.genesis import load_genesis
        from agency.consensus.grant_store import DurableGrantStore
        from agency.consensus.grants import GrantLedger
        from agency.peers.authority import PeerAuthority, PeerRegistry

        membership = load_genesis(genesis_path)
        registry = PeerRegistry()
        for member in membership.members:
            registry.register_public_key(member.peer_id, member.public_key)
        application.state.peer_authority = PeerAuthority(membership, registry)
        application.state.peer_membership = membership
        store_db = os.environ.get(GRANT_STORE_DB_ENV, "").strip()
        if store_db:
            application.state.grant_ledger = DurableGrantStore(
                membership=membership, db_path=store_db
            )
        else:
            application.state.grant_ledger = GrantLedger(membership=membership)
        application.state.grant_bft_time_ms = int(
            os.environ.get(GRANT_BFT_TIME_ENV, str(DEFAULT_GRANT_BFT_TIME_MS))
        )
        log.info("api.authority.genesis_loaded", path=genesis_path)
    except BaseException as exc:  # noqa: BLE001 - startup must not crash
        application.state.peer_authority = None
        application.state.peer_membership = None
        application.state.grant_ledger = None
        application.state.grant_bft_time_ms = None
        log.warning("api.authority.genesis_failed", error_type=type(exc).__name__)


def detach_grant_authority(application: FastAPI) -> None:
    """Release the grant state installed by :func:`attach_grant_authority`.

    Closes a durable grant store (releasing its SQLite connection); in-memory
    ledgers and the pinned membership need no teardown.
    """

    ledger = getattr(application.state, "grant_ledger", None)
    close = getattr(ledger, "close", None)
    if callable(close):
        close()
    for name in ("peer_authority", "peer_membership", "grant_ledger", "grant_bft_time_ms"):
        setattr(application.state, name, None)
