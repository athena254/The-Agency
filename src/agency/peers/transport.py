"""Authenticated envelope delivery between isolated peer runtimes.

A4/P2 foundation: the loopback channel proves the sending peer's identity with a
signed attestation bound to the receiver, pinned membership epoch/root and a
caller-supplied nonce. It then verifies the signed envelope against the pinned
validator set before returning it. No network, no wall clock, no randomness and
no ambient authority: freshness and replay state stay with the caller.

This is transport authentication only. It is not admission, not peer actuation,
not a durable task journal and not release authorization.
"""

from __future__ import annotations

from collections.abc import Container
from dataclasses import dataclass
from typing import Any, Final

from cryptography.exceptions import InvalidSignature

from agency.peer_envelope import PREFIX, EnvelopeError, decode_envelope, peer_id
from agency.peers.canonical import (
    SchemaError,
    digest_json,
    validate_digest,
    validate_integer,
    validate_peer_id,
    validate_string,
    validate_u64,
)
from agency.peers.envelope import PeerMessage, verify_message
from agency.peers.identities import PeerIdentity, PeerPublicKey
from agency.peers.membership import Membership

__all__ = [
    "Attestation",
    "EnvelopePacket",
    "TransportError",
    "attest",
    "attestation_challenge",
    "receive",
    "send",
    "verify_attestation",
]

_ATTEST_SIG_PREFIX: Final = PREFIX + b"peer-attest-v1|"
_SIG_BYTES: Final = 64
_MAX_WIRE_BYTES: Final = 64 * 1024


class TransportError(ValueError):
    """A packet failed transport authentication or envelope verification."""


@dataclass(frozen=True, slots=True)
class Attestation:
    """Proof that a named sender addressed one named receiver under a pinned set."""

    sender_peer_id: str
    receiver_peer_id: str
    membership_epoch: int
    membership_root: str
    nonce: str
    signature: bytes


@dataclass(frozen=True, slots=True)
class EnvelopePacket:
    """One attested wire envelope ready for receiver-side verification."""

    attestation: Attestation
    wire: bytes


def attestation_challenge(
    *,
    sender_peer_id: str,
    receiver_peer_id: str,
    membership: Membership,
    nonce: str,
) -> str:
    """Derive the exact canonical challenge an attestation signs."""

    return digest_json(
        {
            "domain": "peer-attest-v1",
            "sender_peer_id": sender_peer_id,
            "receiver_peer_id": receiver_peer_id,
            "membership_epoch": membership.epoch,
            "membership_root": membership.root,
            "nonce": nonce,
        }
    )


def attest(
    identity: PeerIdentity,
    *,
    receiver_peer_id: str,
    membership: Membership,
    nonce: str,
) -> Attestation:
    """Sign a receiver-bound attestation with the sender's private key."""

    validate_peer_id(receiver_peer_id, "receiver_peer_id")
    validate_string(nonce, "nonce", maximum=64)
    validate_u64(membership.epoch, "membership_epoch")
    validate_digest(membership.root, "membership_root")
    challenge = attestation_challenge(
        sender_peer_id=identity.peer_id,
        receiver_peer_id=receiver_peer_id,
        membership=membership,
        nonce=nonce,
    )
    signature = identity.key.sign(_ATTEST_SIG_PREFIX + bytes.fromhex(challenge))
    return Attestation(
        sender_peer_id=identity.peer_id,
        receiver_peer_id=receiver_peer_id,
        membership_epoch=membership.epoch,
        membership_root=membership.root,
        nonce=nonce,
        signature=signature,
    )


def _pinned_key(membership: Membership, peer_id_value: str) -> PeerPublicKey | None:
    for member in membership.members:
        if member.peer_id == peer_id_value:
            return PeerPublicKey.from_fingerprint(member.public_key)
    return None


def verify_attestation(
    attestation: Attestation,
    *,
    receiver_peer_id: str,
    membership: Membership,
    nonce: str,
) -> bool:
    """Fail-closed check of an attestation against the receiver's pinned view."""

    try:
        validate_peer_id(attestation.sender_peer_id, "sender_peer_id")
        validate_peer_id(attestation.receiver_peer_id, "receiver_peer_id")
        validate_u64(attestation.membership_epoch, "membership_epoch")
        validate_digest(attestation.membership_root, "membership_root")
        validate_string(attestation.nonce, "nonce", maximum=64)
        if type(attestation.signature) is not bytes or len(attestation.signature) != _SIG_BYTES:
            return False
        if attestation.receiver_peer_id != receiver_peer_id or attestation.nonce != nonce:
            return False
        if (
            attestation.membership_epoch != membership.epoch
            or attestation.membership_root != membership.root
        ):
            return False
        key = _pinned_key(membership, attestation.sender_peer_id)
        if key is None or not key.verify_identity(attestation.sender_peer_id):
            return False
        challenge = attestation_challenge(
            sender_peer_id=attestation.sender_peer_id,
            receiver_peer_id=attestation.receiver_peer_id,
            membership=membership,
            nonce=attestation.nonce,
        )
        key.public_key().verify(
            attestation.signature, _ATTEST_SIG_PREFIX + bytes.fromhex(challenge)
        )
    except (EnvelopeError, SchemaError, InvalidSignature, ValueError, TypeError):
        return False
    return True


def send(
    identity: PeerIdentity,
    *,
    receiver_peer_id: str,
    membership: Membership,
    wire: bytes,
    nonce: str,
) -> EnvelopePacket:
    """Attest one signed wire envelope to a receiver under a pinned membership."""

    if type(wire) is not bytes or not wire or len(wire) > _MAX_WIRE_BYTES:
        raise TransportError("invalid wire envelope")
    return EnvelopePacket(
        attestation=attest(
            identity,
            receiver_peer_id=receiver_peer_id,
            membership=membership,
            nonce=nonce,
        ),
        wire=wire,
    )


def receive(
    packet: EnvelopePacket,
    *,
    receiver: PeerIdentity,
    membership: Membership,
    nonce: str,
    bft_time_ms: int,
    replay_ids: Container[str],
) -> PeerMessage:
    """Verify a packet's attestation and envelope; return the trusted wire message."""

    if not isinstance(packet, EnvelopePacket):
        raise TransportError("invalid packet")
    if not verify_attestation(
        packet.attestation,
        receiver_peer_id=receiver.peer_id,
        membership=membership,
        nonce=nonce,
    ):
        raise TransportError("attestation rejected")
    try:
        envelope: dict[str, Any] = decode_envelope(packet.wire)
        sender = str(envelope["sender_peer_id"])
        if sender != packet.attestation.sender_peer_id:
            raise TransportError("wire sender does not match attestation")
        key = _pinned_key(membership, sender)
        if key is None:
            raise TransportError("wire sender is not a pinned peer")
        validate_integer(bft_time_ms, 0, (1 << 63) - 1, "bft_time_ms")
        message = verify_message(
            packet.wire,
            key.public_key(),
            transport_check=lambda env: str(env["sender_peer_id"]) == sender,
            membership_check=lambda env, public: (
                int(env["membership_epoch"]) == membership.epoch
                and str(env["membership_root"]) == membership.root
                and peer_id(public) == sender
            ),
            replay_check=lambda env: str(env["msg_id"]) not in replay_ids,
            expiry_check=lambda env: bft_time_ms < int(env["expires_at_ms"]),
        )
    except TransportError:
        raise
    except (EnvelopeError, SchemaError, ValueError, TypeError) as exc:
        raise TransportError("envelope verification failed") from exc
    return message
