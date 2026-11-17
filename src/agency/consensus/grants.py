"""Task-scoped execution grants — protocol foundation, not actuation (Slice C).

Boundary/evidence note: this module implements only the *verification shape* of
a short-lived, task-scoped execution grant (SPEC_KNOWN_PEER_PROTOCOL §12). It is
protocol foundation, NOT peer actuation and NOT release authorization. No
actuator, network, bridge call, wall clock or side-effecting resource is wired
here. :func:`verify_at_actuator` is a verification-only seam: a real actuator
(follow-up) must still enforce resource-side fencing and idempotency (§12.3),
durable intent/outcome records, and the independent human D-03 attestation gate.
ABCI++ and a real ordering engine remain follow-ups as well.

All time comes from pinned committed headers passed by the caller; ``time`` is
never read. Nonces are derived deterministically from signed inputs, never from
randomness. Ed25519 signing and canonical digests reuse the peers/ primitives.
"""

from __future__ import annotations

import re
from collections.abc import Container, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Final

from cryptography.exceptions import InvalidSignature

from agency.peer_envelope import PREFIX, EnvelopeError, canonical_json
from agency.peers.canonical import (
    MAX_MEMBERS,
    SchemaError,
    digest_hex,
    digest_json,
    validate_consumer_id,
    validate_digest,
    validate_peer_id,
    validate_proposal_id_format,
    validate_string,
    validate_timestamp,
    validate_u64,
)
from agency.peers.identities import PeerIdentity, PeerPublicKey
from agency.peers.membership import Membership

__all__ = [
    "MAX_GRANT_LIFETIME_MS",
    "NONCE_HEX_CHARS",
    "AffectedSetAttestation",
    "ExecutionGrant",
    "GrantError",
    "GrantLedger",
    "GrantRejection",
    "GrantRevocation",
    "GrantVerification",
    "issue_grant",
    "revoke_grant",
    "verify_at_actuator",
    "verify_grant",
    "verify_revocation",
]

MAX_GRANT_LIFETIME_MS: Final = 60 * 60 * 1000
NONCE_HEX_CHARS: Final = 32

_NONCE_RE: Final = re.compile(r"[0-9a-f]{32}\Z")
_SIG_BYTES: Final = 64
_ZERO_DIGEST: Final = "0" * 64
_GRANT_SIG_PREFIX: Final = PREFIX + b"task-grant-v1|"
_ATTEST_SIG_PREFIX: Final = PREFIX + b"affected-set-v1|"
_REVOKE_SIG_PREFIX: Final = PREFIX + b"grant-revoke-v1|"


class GrantError(ValueError):
    """Grant construction failed a pinned validation rule (fail closed)."""


class GrantRejection(str, Enum):
    """Closed set of deterministic grant verification failures."""

    MALFORMED = "malformed"
    INVALID_SIGNATURE = "invalid_signature"
    WRONG_EPOCH = "wrong_epoch"
    EXPIRED = "expired"
    INVALID_EXPIRY = "invalid_expiry"
    REPLAY = "replay"
    MISSING_ATTESTATION = "missing_attestation"
    REVOKED = "revoked"
    UNAUTHORIZED_ISSUER = "unauthorized_issuer"


@dataclass(frozen=True, slots=True)
class GrantVerification:
    """Fail-closed verification result. ``ok`` is not actuation authorization."""

    ok: bool
    reason: GrantRejection | None
    grant_digest: str

    @property
    def accepted(self) -> bool:
        return self.ok


@dataclass(frozen=True, slots=True)
class AffectedSetAttestation:
    """Issuer signature binding an affected-consumer set to a task."""

    attestor_peer_id: str
    signature: bytes

    def __post_init__(self) -> None:
        validate_peer_id(self.attestor_peer_id, "attestor_peer_id")
        if type(self.signature) is not bytes or len(self.signature) != _SIG_BYTES:
            raise SchemaError("invalid attestation signature")

    def body(
        self,
        *,
        proposal_id: str,
        task_id: str,
        affected_consumers: Sequence[str],
    ) -> dict[str, Any]:
        return {
            "domain": "affected-set-v1",
            "proposal_id": proposal_id,
            "task_id": task_id,
            "affected_consumers": list(affected_consumers),
        }

    def digest(
        self,
        *,
        proposal_id: str,
        task_id: str,
        affected_consumers: Sequence[str],
    ) -> str:
        return digest_json(
            self.body(
                proposal_id=proposal_id,
                task_id=task_id,
                affected_consumers=affected_consumers,
            )
        )


@dataclass(frozen=True, slots=True)
class ExecutionGrant:
    """A signed, short-lived grant for one task under one pinned membership."""

    proposal_id: str
    task_id: str
    issuer_peer_id: str
    membership_epoch: int
    membership_root: str
    nonce: str
    issued_at_ms: int
    expires_at_ms: int
    affected_consumers: tuple[str, ...]
    attestation: AffectedSetAttestation | None
    signature: bytes

    def __post_init__(self) -> None:
        validate_proposal_id_format(self.proposal_id, "proposal_id")
        validate_string(self.task_id, "task_id", maximum=128)
        validate_peer_id(self.issuer_peer_id, "issuer_peer_id")
        validate_u64(self.membership_epoch, "membership_epoch")
        validate_digest(self.membership_root, "membership_root")
        if type(self.nonce) is not str or _NONCE_RE.fullmatch(self.nonce) is None:
            raise SchemaError("invalid grant nonce")
        validate_timestamp(self.issued_at_ms, "issued_at_ms")
        validate_timestamp(self.expires_at_ms, "expires_at_ms")
        _validate_lifetime(self.issued_at_ms, self.expires_at_ms)
        if type(self.affected_consumers) is not tuple:
            raise SchemaError("affected_consumers must be a tuple")
        if len(self.affected_consumers) > MAX_MEMBERS:
            raise SchemaError("affected-consumer set exceeds its bound")
        seen: set[str] = set()
        for index, consumer in enumerate(self.affected_consumers):
            validate_consumer_id(consumer, f"affected_consumers[{index}]")
            if consumer in seen:
                raise SchemaError("duplicate affected consumer")
            seen.add(consumer)
        if type(self.signature) is not bytes or len(self.signature) != _SIG_BYTES:
            raise SchemaError("invalid grant signature")

    def affected_set_digest(self) -> str:
        return digest_json(
            {
                "domain": "affected-set-v1",
                "proposal_id": self.proposal_id,
                "task_id": self.task_id,
                "affected_consumers": sorted(self.affected_consumers),
            }
        )

    def attestation_digest(self) -> str | None:
        if self.attestation is None:
            return None
        return self.attestation.digest(
            proposal_id=self.proposal_id,
            task_id=self.task_id,
            affected_consumers=sorted(self.affected_consumers),
        )

    def body(self) -> dict[str, Any]:
        """Canonical signed projection; excludes the signature itself."""

        return {
            "domain": "task-grant-v1",
            "proposal_id": self.proposal_id,
            "task_id": self.task_id,
            "issuer_peer_id": self.issuer_peer_id,
            "membership_epoch": self.membership_epoch,
            "membership_root": self.membership_root,
            "nonce": self.nonce,
            "issued_at_ms": self.issued_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "affected_consumers": sorted(self.affected_consumers),
            "affected_set_digest": self.affected_set_digest(),
            "attestation_digest": self.attestation_digest(),
        }

    @property
    def digest(self) -> str:
        return digest_json(self.body())

    def _signing_bytes(self) -> bytes:
        return _GRANT_SIG_PREFIX + bytes.fromhex(self.digest)


@dataclass(frozen=True, slots=True)
class GrantRevocation:
    """A signed revocation referencing one grant digest."""

    grant_digest: str
    revoker_peer_id: str
    reason_digest: str
    signature: bytes

    def __post_init__(self) -> None:
        validate_digest(self.grant_digest, "grant_digest")
        validate_peer_id(self.revoker_peer_id, "revoker_peer_id")
        validate_digest(self.reason_digest, "reason_digest")
        if type(self.signature) is not bytes or len(self.signature) != _SIG_BYTES:
            raise SchemaError("invalid revocation signature")

    def body(self) -> dict[str, Any]:
        return {
            "domain": "grant-revocation-v1",
            "grant_digest": self.grant_digest,
            "revoker_peer_id": self.revoker_peer_id,
            "reason_digest": self.reason_digest,
        }


def _validate_lifetime(issued_at_ms: int, expires_at_ms: int) -> None:
    if expires_at_ms <= issued_at_ms:
        raise GrantError("grant expiry is not after issue")
    if expires_at_ms - issued_at_ms > MAX_GRANT_LIFETIME_MS:
        raise GrantError("grant lifetime exceeds the pinned ceiling")


def _derive_nonce(*, issuer_peer_id: str, proposal_id: str, task_id: str, issued_at_ms: int) -> str:
    """128-bit nonce derived deterministically from signed inputs, never at random."""

    return digest_json(
        {
            "domain": "grant-nonce-v1",
            "issuer_peer_id": issuer_peer_id,
            "proposal_id": proposal_id,
            "task_id": task_id,
            "issued_at_ms": issued_at_ms,
        }
    )[:NONCE_HEX_CHARS]


def _sign_attestation(
    issuer: PeerIdentity,
    *,
    proposal_id: str,
    task_id: str,
    affected_consumers: tuple[str, ...],
) -> AffectedSetAttestation:
    provisional = AffectedSetAttestation(issuer.peer_id, b"\x00" * _SIG_BYTES)
    digest = provisional.digest(
        proposal_id=proposal_id, task_id=task_id, affected_consumers=affected_consumers
    )
    signature = issuer.key.sign(_ATTEST_SIG_PREFIX + bytes.fromhex(digest))
    return replace(provisional, signature=signature)


def issue_grant(
    issuer: PeerIdentity,
    *,
    membership: Membership,
    proposal_id: str,
    task_id: str,
    issued_at_ms: int,
    expires_at_ms: int,
    affected_consumers: Sequence[str],
    nonce: str | None = None,
) -> ExecutionGrant:
    """Build and sign one task-scoped grant. Raises on malformed construction."""

    if not membership.has_member(issuer.peer_id):
        raise GrantError("issuer is not a member of the pinned epoch")
    consumers = tuple(sorted(affected_consumers))
    nonce_value = nonce or _derive_nonce(
        issuer_peer_id=issuer.peer_id,
        proposal_id=proposal_id,
        task_id=task_id,
        issued_at_ms=issued_at_ms,
    )
    attestation = _sign_attestation(
        issuer,
        proposal_id=proposal_id,
        task_id=task_id,
        affected_consumers=consumers,
    )
    provisional = ExecutionGrant(
        proposal_id=proposal_id,
        task_id=task_id,
        issuer_peer_id=issuer.peer_id,
        membership_epoch=membership.epoch,
        membership_root=membership.root,
        nonce=nonce_value,
        issued_at_ms=issued_at_ms,
        expires_at_ms=expires_at_ms,
        affected_consumers=consumers,
        attestation=attestation,
        signature=b"\x00" * _SIG_BYTES,
    )
    return replace(provisional, signature=issuer.key.sign(provisional._signing_bytes()))


def _safe_digest(grant: ExecutionGrant) -> str:
    try:
        return grant.digest
    except (EnvelopeError, SchemaError, TypeError, ValueError):
        return _ZERO_DIGEST


def verify_grant(
    grant: ExecutionGrant,
    *,
    expected_epoch: int,
    expected_root: str,
    bft_time_ms: int,
    pinned_issuers: Mapping[str, PeerPublicKey],
    used_nonces: Container[str] = (),
    revoked_digests: Container[str] = (),
) -> GrantVerification:
    """Verify a grant against pinned inputs. Fail closed; never raise on bad data."""

    def fail(reason: GrantRejection) -> GrantVerification:
        return GrantVerification(False, reason, _safe_digest(grant))

    if not isinstance(grant, ExecutionGrant):
        return fail(GrantRejection.MALFORMED)
    attestation = grant.attestation
    if attestation is None or attestation.attestor_peer_id != grant.issuer_peer_id:
        return fail(GrantRejection.MISSING_ATTESTATION)
    try:
        canonical_body = canonical_json(grant.body())
    except (EnvelopeError, SchemaError, TypeError, ValueError):
        return fail(GrantRejection.MALFORMED)

    issuer_key = pinned_issuers.get(grant.issuer_peer_id)
    if issuer_key is None:
        return fail(GrantRejection.UNAUTHORIZED_ISSUER)
    try:
        issuer_key.public_key().verify(
            grant.signature, _GRANT_SIG_PREFIX + bytes.fromhex(digest_hex(canonical_body))
        )
    except (InvalidSignature, ValueError, TypeError):
        return fail(GrantRejection.INVALID_SIGNATURE)

    attestation_key = pinned_issuers.get(attestation.attestor_peer_id)
    if attestation_key is None:
        return fail(GrantRejection.UNAUTHORIZED_ISSUER)
    attestation_digest = grant.attestation_digest()
    if attestation_digest is None:
        return fail(GrantRejection.MISSING_ATTESTATION)
    try:
        attestation_key.public_key().verify(
            attestation.signature,
            _ATTEST_SIG_PREFIX + bytes.fromhex(attestation_digest),
        )
    except (InvalidSignature, ValueError, TypeError):
        return fail(GrantRejection.INVALID_SIGNATURE)

    if grant.membership_epoch != expected_epoch or grant.membership_root != expected_root:
        return fail(GrantRejection.WRONG_EPOCH)
    if grant.expires_at_ms - grant.issued_at_ms > MAX_GRANT_LIFETIME_MS:
        return fail(GrantRejection.INVALID_EXPIRY)
    if bft_time_ms >= grant.expires_at_ms:
        return fail(GrantRejection.EXPIRED)
    if grant.nonce in used_nonces:
        return fail(GrantRejection.REPLAY)
    if grant.digest in revoked_digests:
        return fail(GrantRejection.REVOKED)
    return GrantVerification(True, None, grant.digest)


def revoke_grant(
    revoker: PeerIdentity, grant: ExecutionGrant, *, reason_digest: str
) -> GrantRevocation:
    """Sign a revocation for ``grant``. No state change is performed here."""

    provisional = GrantRevocation(
        grant.digest, revoker.peer_id, reason_digest, b"\x00" * _SIG_BYTES
    )
    digest = digest_json(provisional.body())
    signature = revoker.key.sign(_REVOKE_SIG_PREFIX + bytes.fromhex(digest))
    return replace(provisional, signature=signature)


def verify_revocation(
    revocation: GrantRevocation, *, pinned_issuers: Mapping[str, PeerPublicKey]
) -> bool:
    """Fail-closed signature check for a revocation record."""

    key = pinned_issuers.get(revocation.revoker_peer_id)
    if key is None:
        return False
    try:
        key.public_key().verify(
            revocation.signature,
            _REVOKE_SIG_PREFIX + bytes.fromhex(digest_json(revocation.body())),
        )
    except (InvalidSignature, EnvelopeError, SchemaError, TypeError, ValueError):
        return False
    return True


class GrantLedger:
    """In-memory, deterministic replay/revocation tracker; no clock or network."""

    def __init__(self, *, membership: Membership) -> None:
        self._membership = membership
        self._pinned: dict[str, PeerPublicKey] = {
            member.peer_id: PeerPublicKey.from_fingerprint(member.public_key)
            for member in membership.members
        }
        self._used_nonces: set[str] = set()
        self._revoked_digests: set[str] = set()

    @property
    def pinned_issuers(self) -> Mapping[str, PeerPublicKey]:
        return dict(self._pinned)

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
        return issue_grant(
            issuer,
            membership=self._membership,
            proposal_id=proposal_id,
            task_id=task_id,
            issued_at_ms=issued_at_ms,
            expires_at_ms=expires_at_ms,
            affected_consumers=affected_consumers,
            nonce=nonce,
        )

    def verify(self, grant: ExecutionGrant, *, bft_time_ms: int) -> GrantVerification:
        return verify_grant(
            grant,
            expected_epoch=self._membership.epoch,
            expected_root=self._membership.root,
            bft_time_ms=bft_time_ms,
            pinned_issuers=self._pinned,
            used_nonces=self._used_nonces,
            revoked_digests=self._revoked_digests,
        )

    def record_used(self, grant: ExecutionGrant) -> None:
        """Record nonce consumption after a successful verification (no side effect)."""

        self._used_nonces.add(grant.nonce)

    def revoke(
        self, revoker: PeerIdentity, grant: ExecutionGrant, *, reason_digest: str
    ) -> GrantRevocation:
        """Sign and, if valid, record a revocation of ``grant``."""

        revocation = revoke_grant(revoker, grant, reason_digest=reason_digest)
        if verify_revocation(revocation, pinned_issuers=self._pinned):
            self._revoked_digests.add(grant.digest)
        return revocation


def verify_at_actuator(
    grant: ExecutionGrant, *, ledger: GrantLedger, bft_time_ms: int
) -> GrantVerification:
    """Verification-only actuator boundary seam; performs no actuation at all.

    Boundary/evidence note: this is protocol foundation, not peer actuation or
    release authorization. A real actuator must additionally implement
    resource-side fencing/idempotency (§12.3), durable intent/outcome records,
    and the independent human D-03 attestation gate; none of that exists here.
    ``bft_time_ms`` is a pinned committed-header value, never a wall-clock read.
    """

    return ledger.verify(grant, bft_time_ms=bft_time_ms)
