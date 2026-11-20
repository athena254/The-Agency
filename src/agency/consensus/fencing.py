"""Resource-side actuator fencing and idempotent finality receipts (A4/P2).

This is the actuator-side companion to :mod:`agency.consensus.grant_guard`: a
caller-supplied actuator is wrapped so that each grant is verified at the point
of use, each resource advances a monotonic fencing token, and each executed
grant digest replays its recorded outcome exactly once. Verification is fully
delegated to :func:`agency.consensus.grant_guard.enforce_grant` and the real
grant types; no parallel grant model is introduced.

Deterministic and local: no wall clock, no randomness, no network and no
external side effect beyond the single actuator call. Committed time is supplied
by the caller as ``bft_time_ms``. Receipts are append-only;
:class:`FencingStateStore` persists resource tokens, executed grant digests and
a chained receipt log to SQLite. External-resource reconciliation remains a
follow-up.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Final, Protocol, Self

from agency.consensus.grant_guard import enforce_grant
from agency.consensus.grants import ExecutionGrant, GrantLedger, GrantVerification
from agency.peers.canonical import (
    SchemaError,
    canonical_json,
    digest_hex,
    validate_digest,
    validate_string,
    validate_u64,
)

__all__ = [
    "FencedActuator",
    "FencingAttempt",
    "FencingInput",
    "FencingRejection",
    "FencingStateStore",
    "FencingStoreError",
    "FinalityReceipt",
    "fencing_from_payload",
    "fencing_from_state_token",
]

_FINALITY_DOMAIN: Final = "actuator-finality-v1"
_TOKEN_DOMAIN: Final = "fencing-store-token-v1"
_LOG_DOMAIN: Final = "fencing-store-log-v1"
_GENESIS_CHAIN: Final = "0" * 64
_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS resource_tokens (
    resource_id TEXT PRIMARY KEY,
    token INTEGER NOT NULL,
    integrity_digest TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS executed_grants (
    grant_digest TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    integrity_digest TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receipt_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    grant_digest TEXT NOT NULL,
    integrity_digest TEXT NOT NULL,
    chain_digest TEXT NOT NULL
);
"""


class FencingRejection(str, Enum):
    """Closed set of deterministic fencing-layer failures."""

    STALE_TOKEN = "stale_token"
    CONFLICTING_OUTCOME = "conflicting_outcome"
    CONFLICTING_RESOURCE = "conflicting_resource"


@dataclass(frozen=True, slots=True)
class FencingInput:
    """Resource-scoped fencing values passed into one actuator call."""

    expected_current: int
    next_token: int

    def __post_init__(self) -> None:
        validate_u64(self.expected_current, "expected_current")
        validate_u64(self.next_token, "next_token")
        if self.next_token != self.expected_current + 1:
            raise SchemaError("next_token must be expected_current + 1")


def fencing_from_payload(payload: Mapping[str, Any]) -> FencingInput:
    """Read fencing values from a validated grant payload when one is available."""

    return FencingInput(
        expected_current=validate_u64(payload.get("expected_current"), "expected_current"),
        next_token=validate_u64(payload.get("next_token"), "next_token"),
    )


def fencing_from_state_token(state_token: int) -> FencingInput:
    """Derive fencing values from an explicit caller-supplied resource state token."""

    current = validate_u64(state_token, "state_token")
    return FencingInput(expected_current=current, next_token=current + 1)


@dataclass(frozen=True, slots=True)
class FinalityReceipt:
    """Append-only outcome record binding a grant digest to one fenced effect."""

    grant_digest: str
    resource_id: str
    fencing_token: int
    outcome: str

    def __post_init__(self) -> None:
        validate_string(self.grant_digest, "grant_digest", maximum=128)
        validate_string(self.resource_id, "resource_id", maximum=128)
        validate_u64(self.fencing_token, "fencing_token")
        validate_string(self.outcome, "outcome", maximum=4096)

    def body(self) -> dict[str, Any]:
        """Canonical projection; excludes no field, including the effect outcome."""

        return {
            "domain": _FINALITY_DOMAIN,
            "grant_digest": self.grant_digest,
            "resource_id": self.resource_id,
            "fencing_token": self.fencing_token,
            "outcome": self.outcome,
        }

    def canonical(self) -> bytes:
        return canonical_json(self.body())

    def digest(self) -> str:
        return digest_hex(self.canonical())


class FencingStoreError(ValueError):
    """A persisted fencing record failed closed validation, or the store is closed."""


def _token_integrity(resource_id: str, token: int) -> str:
    return digest_hex(
        canonical_json(
            {
                "domain": _TOKEN_DOMAIN,
                "resource_id": resource_id,
                "token": token,
            }
        )
    )


def _chain_digest(previous: str, receipt_digest: str) -> str:
    return digest_hex(
        canonical_json(
            {
                "domain": _LOG_DOMAIN,
                "previous": previous,
                "receipt_digest": receipt_digest,
            }
        )
    )


class _FencingState(Protocol):
    @property
    def receipts(self) -> tuple[FinalityReceipt, ...]: ...

    def receipt(self, grant_digest: str) -> FinalityReceipt | None: ...

    def token(self, resource_id: str) -> int: ...

    def ensure_token(self, resource_id: str, expected_current: int) -> None: ...

    def commit(self, *, resource_id: str, next_token: int, receipt: FinalityReceipt) -> None: ...


class _InMemoryFencingState:
    def __init__(self) -> None:
        self._tokens: dict[str, int] = {}
        self._outcomes: dict[str, FinalityReceipt] = {}
        self._receipts: list[FinalityReceipt] = []

    @property
    def receipts(self) -> tuple[FinalityReceipt, ...]:
        return tuple(self._receipts)

    def receipt(self, grant_digest: str) -> FinalityReceipt | None:
        return self._outcomes.get(grant_digest)

    def token(self, resource_id: str) -> int:
        return self._tokens.get(resource_id, 0)

    def ensure_token(self, resource_id: str, expected_current: int) -> None:
        if resource_id not in self._tokens:
            self._tokens[resource_id] = expected_current

    def commit(self, *, resource_id: str, next_token: int, receipt: FinalityReceipt) -> None:
        self._tokens[resource_id] = next_token
        self._outcomes[receipt.grant_digest] = receipt
        self._receipts.append(receipt)


class FencingStateStore:
    """SQLite-backed fencing state used as a drop-in actuator state backend.

    Resource tokens, executed grant digests and a chained append-only receipt
    log are loaded on open and written through on each commit. Every persisted
    record carries a canonical integrity digest and the log is hash-chained, so
    a modified, missing or reordered record makes opening the store fail closed.
    """

    def __init__(self, *, db_path: str | Path = ":memory:") -> None:
        self._tokens: dict[str, int] = {}
        self._outcomes: dict[str, FinalityReceipt] = {}
        self._receipts: list[FinalityReceipt] = []
        self._last_chain: str = _GENESIS_CHAIN
        self._conn: sqlite3.Connection | None = sqlite3.connect(str(db_path))
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        try:
            self._load()
        except (SchemaError, FencingStoreError) as exc:
            self.close()
            raise FencingStoreError(f"corrupt durable fencing store: {exc}") from exc

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            raise FencingStoreError("fencing store is closed")
        return self._conn

    def _load(self) -> None:
        conn = self._db()
        for resource_id, token, integrity in conn.execute(
            "SELECT resource_id, token, integrity_digest FROM resource_tokens"
        ):
            name = validate_string(resource_id, "resource_id", maximum=128)
            value = validate_u64(token, "token")
            validate_digest(integrity, "token integrity_digest")
            if integrity != _token_integrity(name, value):
                raise FencingStoreError("tampered resource token")
            self._tokens[name] = value
        for digest, resource_id, fencing_token, outcome, integrity in conn.execute(
            "SELECT grant_digest, resource_id, fencing_token, outcome, integrity_digest "
            "FROM executed_grants"
        ):
            grant_digest = validate_digest(digest, "grant_digest")
            receipt = FinalityReceipt(
                grant_digest=grant_digest,
                resource_id=resource_id,
                fencing_token=fencing_token,
                outcome=outcome,
            )
            validate_digest(integrity, "receipt integrity_digest")
            if receipt.digest() != integrity:
                raise FencingStoreError("tampered executed grant receipt")
            self._outcomes[grant_digest] = receipt
        previous = _GENESIS_CHAIN
        logged: list[str] = []
        query = (
            "SELECT seq, grant_digest, integrity_digest, chain_digest FROM receipt_log ORDER BY seq"
        )
        for seq, digest, integrity, chain in conn.execute(query):
            validate_u64(seq, "receipt_log seq")
            grant_digest = validate_digest(digest, "logged grant_digest")
            validate_digest(integrity, "logged integrity_digest")
            validate_digest(chain, "chain_digest")
            logged_receipt = self._outcomes.get(grant_digest)
            if logged_receipt is None or logged_receipt.digest() != integrity:
                raise FencingStoreError("receipt log entry does not match a stored receipt")
            if _chain_digest(previous, integrity) != chain:
                raise FencingStoreError("tampered receipt log chain")
            logged.append(grant_digest)
            self._receipts.append(logged_receipt)
            previous = chain
        if len(logged) != len(self._outcomes) or set(logged) != set(self._outcomes):
            raise FencingStoreError("receipt log is incomplete")
        self._last_chain = previous

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        self.close()

    @property
    def receipts(self) -> tuple[FinalityReceipt, ...]:
        return tuple(self._receipts)

    def receipt(self, grant_digest: str) -> FinalityReceipt | None:
        return self._outcomes.get(grant_digest)

    def token(self, resource_id: str) -> int:
        return self._tokens.get(resource_id, 0)

    def ensure_token(self, resource_id: str, expected_current: int) -> None:
        conn = self._db()
        if resource_id in self._tokens:
            return
        name = validate_string(resource_id, "resource_id", maximum=128)
        token = validate_u64(expected_current, "expected_current")
        conn.execute(
            "INSERT INTO resource_tokens (resource_id, token, integrity_digest) VALUES (?, ?, ?)",
            (name, token, _token_integrity(name, token)),
        )
        conn.commit()
        self._tokens[name] = token

    def commit(self, *, resource_id: str, next_token: int, receipt: FinalityReceipt) -> None:
        if receipt.resource_id != resource_id:
            raise FencingStoreError("receipt resource does not match the fenced resource")
        token = validate_u64(next_token, "next_token")
        receipt_digest = receipt.digest()
        chain = _chain_digest(self._last_chain, receipt_digest)
        conn = self._db()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO resource_tokens (resource_id, token, integrity_digest) "
                    "VALUES (?, ?, ?) ON CONFLICT(resource_id) DO UPDATE SET "
                    "token = excluded.token, integrity_digest = excluded.integrity_digest",
                    (resource_id, token, _token_integrity(resource_id, token)),
                )
                conn.execute(
                    "INSERT INTO executed_grants "
                    "(grant_digest, resource_id, fencing_token, outcome, integrity_digest) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        receipt.grant_digest,
                        receipt.resource_id,
                        receipt.fencing_token,
                        receipt.outcome,
                        receipt_digest,
                    ),
                )
                conn.execute(
                    "INSERT INTO receipt_log (grant_digest, integrity_digest, chain_digest) "
                    "VALUES (?, ?, ?)",
                    (receipt.grant_digest, receipt_digest, chain),
                )
        except sqlite3.IntegrityError as exc:
            raise FencingStoreError("grant digest was already executed") from exc
        self._tokens[resource_id] = token
        self._outcomes[receipt.grant_digest] = receipt
        self._receipts.append(receipt)
        self._last_chain = chain


@dataclass(frozen=True, slots=True)
class FencingAttempt:
    """What the wrapper did with one grant, including verification and rejection."""

    executed: bool
    verification: GrantVerification | None
    receipt: FinalityReceipt | None
    rejection: FencingRejection | None

    @property
    def accepted(self) -> bool:
        return self.rejection is None and (self.verification is None or self.verification.ok)

    @property
    def outcome(self) -> str | None:
        return self.receipt.outcome if self.receipt is not None else None


class FencedActuator:
    """Verify, fence and execute a grant at most once per digest."""

    def __init__(
        self,
        ledger: GrantLedger,
        *,
        actuator: Callable[[ExecutionGrant], str],
        store: FencingStateStore | None = None,
    ) -> None:
        self._ledger = ledger
        self._actuator = actuator
        self._state: _FencingState = store if store is not None else _InMemoryFencingState()

    @property
    def receipts(self) -> tuple[FinalityReceipt, ...]:
        """Append-only receipt log in execution order."""

        return self._state.receipts

    def receipt(self, grant_digest: str) -> FinalityReceipt | None:
        return self._state.receipt(grant_digest)

    def token(self, resource_id: str) -> int:
        return self._state.token(resource_id)

    def execute(
        self,
        grant: ExecutionGrant,
        *,
        bft_time_ms: int,
        resource_id: str,
        fencing: FencingInput,
        expected_outcome: str | None = None,
    ) -> FencingAttempt:
        """Verify, replay or execute ``grant`` against one fenced resource."""

        validate_string(resource_id, "resource_id", maximum=128)
        digest = grant.digest
        existing = self._state.receipt(digest)
        if existing is not None:
            if expected_outcome is not None and expected_outcome != existing.outcome:
                return FencingAttempt(False, None, existing, FencingRejection.CONFLICTING_OUTCOME)
            if existing.resource_id != resource_id:
                return FencingAttempt(False, None, existing, FencingRejection.CONFLICTING_RESOURCE)
            return FencingAttempt(False, None, existing, None)

        verification = enforce_grant(grant, ledger=self._ledger, bft_time_ms=bft_time_ms)
        if not verification.ok:
            return FencingAttempt(False, verification, None, None)

        self._state.ensure_token(resource_id, fencing.expected_current)
        if self._state.token(resource_id) != fencing.expected_current:
            return FencingAttempt(False, verification, None, FencingRejection.STALE_TOKEN)

        outcome = self._actuator(grant)
        receipt = FinalityReceipt(digest, resource_id, fencing.next_token, outcome)
        self._state.commit(resource_id=resource_id, next_token=fencing.next_token, receipt=receipt)
        return FencingAttempt(True, verification, receipt, None)
