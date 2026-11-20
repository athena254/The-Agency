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
    validate_closed,
    validate_consumer_id,
    validate_digest,
    validate_peer_id,
    validate_proposal_id_format,
    validate_string,
    validate_timestamp,
    validate_u64,
)
from agency.peers.identities import PeerIdentity, PeerPublicKey
from agency.peers.membership import (
    APPROVAL_DENOMINATOR,
    APPROVAL_NUMERATOR,
    Membership,
    find_member,
)

__all__ = [
    "GRANT_SIGNER_THRESHOLD",
    "GRANT_VALIDATOR_COUNT",
    "MAX_GRANT_LIFETIME_MS",
    "NONCE_HEX_CHARS",
    "AffectedSetAttestation",
    "ExecutionGrant",
    "GrantAction",
    "GrantError",
    "GrantLedger",
    "GrantRejection",
    "GrantRevocation",
    "GrantSigner",
    "GrantVerification",
    "VoterCertificate",
    "VoterInputRef",
    "grant_from_payload",
    "issue_format_grant",
    "issue_grant",
    "revoke_grant",
    "verify_at_actuator",
    "verify_grant",
    "verify_revocation",
]

MAX_GRANT_LIFETIME_MS: Final = 60 * 60 * 1000
NONCE_HEX_CHARS: Final = 32
GRANT_SIGNER_THRESHOLD: Final = APPROVAL_NUMERATOR
GRANT_VALIDATOR_COUNT: Final = APPROVAL_DENOMINATOR

_NONCE_RE: Final = re.compile(r"[0-9a-f]{32}\Z")
_SIG_BYTES: Final = 64
_ZERO_DIGEST: Final = "0" * 64
_GRANT_SIG_PREFIX: Final = PREFIX + b"task-grant-v1|"
_FORMAT_SIG_PREFIX: Final = PREFIX + b"task-grant-format-v1|"
_FORMAT_DOMAIN: Final = "task-grant-format-v1"
_FORMAT_NONCE_DOMAIN: Final = "grant-format-nonce-v1"
_ATTEST_SIG_PREFIX: Final = PREFIX + b"affected-set-v1|"
_REVOKE_SIG_PREFIX: Final = PREFIX + b"grant-revoke-v1|"
_ACTION_KEYS: Final = frozenset(
    {"action_type", "resource", "params_digest", "params_schema_version"}
)
_VOTER_CERT_KEYS: Final = frozenset({"decision_certificate_digest", "input_refs"})
_INPUT_REF_KEYS: Final = frozenset({"consumer_id", "input_digest"})


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
    INSUFFICIENT_SIGNERS = "insufficient_signers"
    MISSING_COVERAGE = "missing_coverage"


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
class GrantAction:
    """Bounded §12.1 action descriptor: operation, resource and content-addressed params."""

    action_type: str
    resource: str
    params_digest: str
    params_schema_version: str

    def __post_init__(self) -> None:
        validate_string(self.action_type, "action_type", maximum=64)
        validate_string(self.resource, "resource", maximum=256)
        validate_digest(self.params_digest, "params_digest")
        validate_string(self.params_schema_version, "params_schema_version", maximum=64)

    def body(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "resource": self.resource,
            "params_digest": self.params_digest,
            "params_schema_version": self.params_schema_version,
        }


@dataclass(frozen=True, slots=True)
class VoterInputRef:
    """Reference to one externally-produced affected-consumer input digest."""

    consumer_id: str
    input_digest: str

    def __post_init__(self) -> None:
        validate_consumer_id(self.consumer_id, "consumer_id")
        validate_digest(self.input_digest, "input_digest")

    def body(self) -> dict[str, Any]:
        return {"consumer_id": self.consumer_id, "input_digest": self.input_digest}


@dataclass(frozen=True, slots=True)
class VoterCertificate:
    """Closed §12.2 coverage certificate by digest only; no votes or attestations embedded."""

    decision_certificate_digest: str
    input_refs: tuple[VoterInputRef, ...]

    def __post_init__(self) -> None:
        validate_digest(self.decision_certificate_digest, "decision_certificate_digest")
        if type(self.input_refs) is not tuple:
            raise SchemaError("input_refs must be a tuple")
        if len(self.input_refs) > MAX_MEMBERS:
            raise SchemaError("input_refs exceeds its bound")
        seen: set[str] = set()
        for index, ref in enumerate(self.input_refs):
            if not isinstance(ref, VoterInputRef):
                raise SchemaError(f"input_refs[{index}] is not a VoterInputRef")
            if ref.consumer_id in seen:
                raise SchemaError("duplicate input_refs consumer")
            seen.add(ref.consumer_id)

    @property
    def consumer_ids(self) -> tuple[str, ...]:
        return tuple(ref.consumer_id for ref in self.input_refs)

    def body(self) -> dict[str, Any]:
        return {
            "decision_certificate_digest": self.decision_certificate_digest,
            "input_refs": [ref.body() for ref in self.input_refs],
        }


@dataclass(frozen=True, slots=True)
class GrantSigner:
    """One pinned validator's signature over the canonical §12 grant body."""

    peer_id: str
    signature: bytes

    def __post_init__(self) -> None:
        validate_peer_id(self.peer_id, "peer_id")
        if type(self.signature) is not bytes or len(self.signature) != _SIG_BYTES:
            raise SchemaError("invalid grant signer signature")


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
    action: GrantAction | None = None
    idempotency_key: str | None = None
    expected_current: int | None = None
    next_token: int | None = None
    grant_signers: tuple[GrantSigner, ...] | None = None
    voter_certificate: VoterCertificate | None = None

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

    @property
    def is_format(self) -> bool:
        return any(
            field is not None
            for field in (
                self.action,
                self.idempotency_key,
                self.expected_current,
                self.next_token,
                self.grant_signers,
                self.voter_certificate,
            )
        )

    def body(self) -> dict[str, Any]:
        """Canonical signed projection; excludes signatures, includes §12 fields."""

        result: dict[str, Any] = {
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
        if self.is_format:
            result["format"] = _FORMAT_DOMAIN
            result["action"] = (
                self.action.body() if isinstance(self.action, GrantAction) else self.action
            )
            result["idempotency_key"] = self.idempotency_key
            result["expected_current"] = self.expected_current
            result["next_token"] = self.next_token
            result["grant_signers"] = (
                [signer.peer_id for signer in self.grant_signers]
                if self.grant_signers is not None
                else None
            )
            result["voter_certificate"] = (
                self.voter_certificate.body()
                if isinstance(self.voter_certificate, VoterCertificate)
                else self.voter_certificate
            )
        return result

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


def _validate_format_fields(grant: ExecutionGrant) -> None:
    values = (
        grant.action,
        grant.idempotency_key,
        grant.expected_current,
        grant.next_token,
        grant.grant_signers,
        grant.voter_certificate,
    )
    present = [value is not None for value in values]
    if any(present) and not all(present):
        raise SchemaError("partial §12 grant format")
    if not any(present):
        return
    action = grant.action
    if not isinstance(action, GrantAction):
        raise SchemaError("invalid action")
    validate_closed(action.body(), _ACTION_KEYS, "action")
    validate_string(grant.idempotency_key, "idempotency_key", maximum=256)
    expected_current = validate_u64(grant.expected_current, "expected_current")
    next_token = validate_u64(grant.next_token, "next_token")
    if next_token != expected_current + 1:
        raise SchemaError("next_token must be expected_current + 1")
    signers = grant.grant_signers
    if signers is None or type(signers) is not tuple:
        raise SchemaError("grant_signers must be a tuple")
    if not GRANT_SIGNER_THRESHOLD <= len(signers) <= GRANT_VALIDATOR_COUNT:
        raise SchemaError("grant_signers exceeds the pinned threshold bounds")
    seen: set[str] = set()
    for index, signer in enumerate(signers):
        if not isinstance(signer, GrantSigner):
            raise SchemaError(f"grant_signers[{index}] is not a GrantSigner")
        if signer.peer_id in seen:
            raise SchemaError("duplicate grant signer")
        seen.add(signer.peer_id)
    certificate = grant.voter_certificate
    if not isinstance(certificate, VoterCertificate):
        raise SchemaError("invalid voter_certificate")
    body = validate_closed(certificate.body(), _VOTER_CERT_KEYS, "voter_certificate")
    for index, ref in enumerate(body["input_refs"]):
        validate_closed(ref, _INPUT_REF_KEYS, f"voter_certificate.input_refs[{index}]")


def _derive_format_nonce(
    *,
    issuer_peer_id: str,
    proposal_id: str,
    task_id: str,
    issued_at_ms: int,
    format_digest: str,
) -> str:
    return digest_json(
        {
            "domain": _FORMAT_NONCE_DOMAIN,
            "issuer_peer_id": issuer_peer_id,
            "proposal_id": proposal_id,
            "task_id": task_id,
            "issued_at_ms": issued_at_ms,
            "format_digest": format_digest,
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


def issue_format_grant(
    issuer: PeerIdentity,
    *,
    membership: Membership,
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
    """Build and sign one §12-format grant with a pinned-validator threshold."""

    if not membership.has_member(issuer.peer_id):
        raise GrantError("issuer is not a member of the pinned epoch")
    consumers = tuple(sorted(affected_consumers))
    current = validate_u64(expected_current, "expected_current")
    next_token = validate_u64(current + 1, "next_token")
    if set(voter_certificate.consumer_ids) != set(consumers):
        raise GrantError("voter_certificate does not cover the affected consumers")
    unique: dict[str, PeerIdentity] = {}
    for signer in signers:
        member = find_member(membership, signer.peer_id)
        if member is None or "validator" not in member.roles:
            raise GrantError("grant signer is not a pinned validator")
        unique[signer.peer_id] = signer
    ordered = tuple(unique[peer_id] for peer_id in sorted(unique))
    if not GRANT_SIGNER_THRESHOLD <= len(ordered) <= GRANT_VALIDATOR_COUNT:
        raise GrantError("grant signer count violates the pinned threshold")
    format_digest = digest_json(
        {
            "action": action.body(),
            "idempotency_key": idempotency_key,
            "expected_current": current,
            "next_token": next_token,
            "grant_signers": [signer.peer_id for signer in ordered],
            "voter_certificate": voter_certificate.body(),
        }
    )
    nonce_value = nonce or _derive_format_nonce(
        issuer_peer_id=issuer.peer_id,
        proposal_id=proposal_id,
        task_id=task_id,
        issued_at_ms=issued_at_ms,
        format_digest=format_digest,
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
        action=action,
        idempotency_key=idempotency_key,
        expected_current=current,
        next_token=next_token,
        grant_signers=tuple(
            GrantSigner(signer.peer_id, b"\x00" * _SIG_BYTES) for signer in ordered
        ),
        voter_certificate=voter_certificate,
    )
    _validate_format_fields(provisional)
    signing_bytes = _FORMAT_SIG_PREFIX + bytes.fromhex(provisional.digest)
    signed = replace(
        provisional,
        grant_signers=tuple(
            replace(entry, signature=signer.key.sign(signing_bytes))
            for entry, signer in zip(provisional.grant_signers or (), ordered, strict=True)
        ),
    )
    return replace(signed, signature=issuer.key.sign(signed._signing_bytes()))


def _safe_digest(grant: ExecutionGrant) -> str:
    try:
        return grant.digest
    except (AttributeError, EnvelopeError, SchemaError, TypeError, ValueError):
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
    pinned_validators: Mapping[str, PeerPublicKey] | None = None,
) -> GrantVerification:
    """Verify a grant against pinned inputs. Fail closed; never raise on bad data."""

    def fail(reason: GrantRejection) -> GrantVerification:
        return GrantVerification(False, reason, _safe_digest(grant))

    if not isinstance(grant, ExecutionGrant):
        return fail(GrantRejection.MALFORMED)
    attestation = grant.attestation
    if attestation is None or attestation.attestor_peer_id != grant.issuer_peer_id:
        return fail(GrantRejection.MISSING_ATTESTATION)
    if grant.is_format:
        try:
            _validate_format_fields(grant)
        except (AttributeError, EnvelopeError, SchemaError, TypeError, ValueError):
            return fail(GrantRejection.MALFORMED)
    try:
        canonical_body = canonical_json(grant.body())
    except (AttributeError, EnvelopeError, SchemaError, TypeError, ValueError):
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

    if grant.is_format:
        validators = pinned_validators if pinned_validators is not None else pinned_issuers
        if len(validators) < GRANT_VALIDATOR_COUNT:
            return fail(GrantRejection.MALFORMED)
        signing_bytes = _FORMAT_SIG_PREFIX + bytes.fromhex(digest_hex(canonical_body))
        valid_signers: set[str] = set()
        for signer in grant.grant_signers or ():
            key = validators.get(signer.peer_id)
            if key is None:
                return fail(GrantRejection.INSUFFICIENT_SIGNERS)
            try:
                key.public_key().verify(signer.signature, signing_bytes)
            except (InvalidSignature, ValueError, TypeError):
                return fail(GrantRejection.INVALID_SIGNATURE)
            valid_signers.add(signer.peer_id)
        if len(valid_signers) < GRANT_SIGNER_THRESHOLD:
            return fail(GrantRejection.INSUFFICIENT_SIGNERS)
        certificate = grant.voter_certificate
        if certificate is None or set(certificate.consumer_ids) != set(grant.affected_consumers):
            return fail(GrantRejection.MISSING_COVERAGE)

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
        self._validators: dict[str, PeerPublicKey] = {
            member.peer_id: PeerPublicKey.from_fingerprint(member.public_key)
            for member in membership.members
            if "validator" in member.roles
        }
        self._used_nonces: set[str] = set()
        self._revoked_digests: set[str] = set()

    @property
    def pinned_issuers(self) -> Mapping[str, PeerPublicKey]:
        return dict(self._pinned)

    @property
    def pinned_validators(self) -> Mapping[str, PeerPublicKey]:
        return dict(self._validators)

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
        return issue_format_grant(
            issuer,
            membership=self._membership,
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

    def verify(self, grant: ExecutionGrant, *, bft_time_ms: int) -> GrantVerification:
        return verify_grant(
            grant,
            expected_epoch=self._membership.epoch,
            expected_root=self._membership.root,
            bft_time_ms=bft_time_ms,
            pinned_issuers=self._pinned,
            used_nonces=self._used_nonces,
            revoked_digests=self._revoked_digests,
            pinned_validators=self._validators,
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


def grant_from_payload(payload: Any) -> ExecutionGrant:
    """Reconstruct an ExecutionGrant from a canonical wire payload.

    Fail-closed: only the exact pinned field set is accepted; all validation
    runs through ExecutionGrant.__post_init__.
    """
    if not isinstance(payload, Mapping):
        raise GrantError("grant payload must be an object")
    expected = {
        "proposal_id", "task_id", "issuer_peer_id", "membership_epoch", "membership_root",
        "nonce", "issued_at_ms", "expires_at_ms", "affected_consumers", "attestation", "signature",
    }
    if set(payload) != expected:
        raise GrantError("grant payload field set does not match the pinned schema")
    attestation_payload = payload.get("attestation")
    attestation: AffectedSetAttestation | None = None
    if attestation_payload is not None:
        if not isinstance(attestation_payload, Mapping) or set(attestation_payload) != {
            "attestor_peer_id", "signature"
        }:
            raise GrantError("attestation payload field set does not match the pinned schema")
        signature = attestation_payload["signature"]
        if isinstance(signature, str):
            try:
                signature = bytes.fromhex(signature)
            except ValueError as exc:
                raise GrantError("invalid attestation signature encoding") from exc
        attestation = AffectedSetAttestation(
            str(attestation_payload["attestor_peer_id"]), signature
        )
    signature = payload["signature"]
    if isinstance(signature, str):
        try:
            signature = bytes.fromhex(signature)
        except ValueError as exc:
            raise GrantError("invalid grant signature encoding") from exc
    return ExecutionGrant(
        proposal_id=str(payload["proposal_id"]),
        task_id=str(payload["task_id"]),
        issuer_peer_id=str(payload["issuer_peer_id"]),
        membership_epoch=int(payload["membership_epoch"]),
        membership_root=str(payload["membership_root"]),
        nonce=str(payload["nonce"]),
        issued_at_ms=int(payload["issued_at_ms"]),
        expires_at_ms=int(payload["expires_at_ms"]),
        affected_consumers=tuple(str(c) for c in payload["affected_consumers"]),
        attestation=attestation,
        signature=signature,
    )

