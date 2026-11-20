"""Durable, SQLite-backed replay/revocation state for task-scoped grants (A4/P2).

Boundary/evidence note: this module persists only verification state — used
nonces, issued grant digests, and revoked grant digests with canonical revocation
evidence. It performs no actuation, reads no clock, and opens no network; all
time comes from the caller-supplied ``bft_time_ms`` committed-header value.
Cryptographic verification is delegated unchanged to
:mod:`agency.consensus.grants`, and pinned membership supplies the only trusted
public keys. No private key material is ever persisted.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final, Self

from agency.consensus.grants import (
    ExecutionGrant,
    GrantAction,
    GrantLedger,
    GrantRevocation,
    VoterCertificate,
    revoke_grant,
    verify_revocation,
)
from agency.peer_envelope import EnvelopeError
from agency.peers.canonical import digest_json, validate_digest, validate_peer_id
from agency.peers.identities import PeerIdentity
from agency.peers.membership import Membership

__all__ = ["DurableGrantStore", "GrantStoreError"]

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS used_nonces (
    nonce TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS issued_grants (
    grant_digest TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS revoked_grants (
    grant_digest TEXT PRIMARY KEY,
    revoker_peer_id TEXT NOT NULL,
    reason_digest TEXT NOT NULL,
    evidence_digest TEXT NOT NULL
);
"""
_NONCE_RE: Final = re.compile(r"[0-9a-f]{32}\Z")
_REVOCATION_DOMAIN: Final = "grant-store-revocation-v1"


class GrantStoreError(ValueError):
    """A persisted record failed closed validation, or the store is closed."""


def _validate_nonce(value: Any) -> str:
    if type(value) is not str or _NONCE_RE.fullmatch(value) is None:
        raise GrantStoreError("invalid persisted grant nonce")
    return value


def _revocation_evidence_digest(grant_digest: str, revoker_peer_id: str, reason_digest: str) -> str:
    return digest_json(
        {
            "domain": _REVOCATION_DOMAIN,
            "grant_digest": grant_digest,
            "revoker_peer_id": revoker_peer_id,
            "reason_digest": reason_digest,
        }
    )


class DurableGrantStore(GrantLedger):
    """SQLite-backed replay/revocation tracker. Verification is inherited.

    The inherited in-memory sets are the working view of the durable tables:
    they are loaded on open and written through on every mutation, so
    :meth:`verify` keeps delegating to :func:`agency.consensus.grants.verify_grant`
    without a second verification model.
    """

    def __init__(self, *, membership: Membership, db_path: str | Path = ":memory:") -> None:
        super().__init__(membership=membership)
        self._issued_digests: set[str] = set()
        self._conn: sqlite3.Connection | None = sqlite3.connect(str(db_path))
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        try:
            self._load()
        except (EnvelopeError, GrantStoreError) as exc:
            self.close()
            raise GrantStoreError(f"corrupt durable grant store: {exc}") from exc

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            raise GrantStoreError("grant store is closed")
        return self._conn

    def _load(self) -> None:
        conn = self._db()
        for (nonce,) in conn.execute("SELECT nonce FROM used_nonces"):
            self._used_nonces.add(_validate_nonce(nonce))
        for (digest,) in conn.execute("SELECT grant_digest FROM issued_grants"):
            self._issued_digests.add(validate_digest(digest, "issued grant_digest"))
        query = (
            "SELECT grant_digest, revoker_peer_id, reason_digest, evidence_digest "
            "FROM revoked_grants"
        )
        for digest, revoker, reason, evidence in conn.execute(query):
            grant_digest = validate_digest(digest, "revoked grant_digest")
            revoker_peer_id = validate_peer_id(revoker, "revoker_peer_id")
            reason_digest = validate_digest(reason, "reason_digest")
            expected = _revocation_evidence_digest(grant_digest, revoker_peer_id, reason_digest)
            if evidence != expected:
                raise GrantStoreError("tampered revocation evidence")
            self._revoked_digests.add(grant_digest)

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
    def used_nonces(self) -> frozenset[str]:
        return frozenset(self._used_nonces)

    @property
    def issued_digests(self) -> frozenset[str]:
        return frozenset(self._issued_digests)

    @property
    def revoked_digests(self) -> frozenset[str]:
        return frozenset(self._revoked_digests)

    def record_used(self, grant: ExecutionGrant) -> None:
        nonce = _validate_nonce(grant.nonce)
        conn = self._db()
        conn.execute("INSERT OR IGNORE INTO used_nonces (nonce) VALUES (?)", (nonce,))
        conn.commit()
        self._used_nonces.add(nonce)

    def record_issued(self, grant: ExecutionGrant) -> None:
        digest = grant.digest
        conn = self._db()
        conn.execute("INSERT OR IGNORE INTO issued_grants (grant_digest) VALUES (?)", (digest,))
        conn.commit()
        self._issued_digests.add(digest)

    def issue(
        self,
        issuer: PeerIdentity,
        *,
        proposal_id: str,
        task_id: str,
        issued_at_ms: int,
        expires_at_ms: int,
        affected_consumers: Sequence[str],
        nonce: str | None = None,
    ) -> ExecutionGrant:
        grant = super().issue(
            issuer,
            proposal_id=proposal_id,
            task_id=task_id,
            issued_at_ms=issued_at_ms,
            expires_at_ms=expires_at_ms,
            affected_consumers=affected_consumers,
            nonce=nonce,
        )
        self.record_issued(grant)
        return grant

    def issue_format(
        self,
        issuer: PeerIdentity,
        *,
        proposal_id: str,
        task_id: str,
        issued_at_ms: int,
        expires_at_ms: int,
        affected_consumers: Sequence[str],
        action: GrantAction,
        idempotency_key: str,
        expected_current: int,
        voter_certificate: VoterCertificate,
        signers: Sequence[PeerIdentity],
        nonce: str | None = None,
    ) -> ExecutionGrant:
        grant = super().issue_format(
            issuer,
            proposal_id=proposal_id,
            task_id=task_id,
            issued_at_ms=issued_at_ms,
            expires_at_ms=expires_at_ms,
            affected_consumers=affected_consumers,
            action=action,
            idempotency_key=idempotency_key,
            expected_current=expected_current,
            voter_certificate=voter_certificate,
            signers=signers,
            nonce=nonce,
        )
        self.record_issued(grant)
        return grant

    def revoke(
        self, revoker: PeerIdentity, grant: ExecutionGrant, *, reason_digest: str
    ) -> GrantRevocation:
        revocation = revoke_grant(revoker, grant, reason_digest=reason_digest)
        if not verify_revocation(revocation, pinned_issuers=self.pinned_issuers):
            raise GrantStoreError("revocation is not signed by a pinned issuer")
        digest = grant.digest
        conn = self._db()
        conn.execute(
            "INSERT OR IGNORE INTO revoked_grants "
            "(grant_digest, revoker_peer_id, reason_digest, evidence_digest) "
            "VALUES (?, ?, ?, ?)",
            (
                digest,
                revocation.revoker_peer_id,
                revocation.reason_digest,
                _revocation_evidence_digest(
                    digest, revocation.revoker_peer_id, revocation.reason_digest
                ),
            ),
        )
        conn.commit()
        self._revoked_digests.add(digest)
        return revocation
