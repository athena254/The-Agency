"""Peer and consumer identities for the known-peer protocol.

Identity is cryptographic only. A peer identity is derived from an Ed25519
public key; there are no pet names, Telegram IDs, caller strings, or ``user``
aliases. Human consumers are identified by a stable, non-cryptographic
consumer reference that is bound to their authenticated ingress identity at
attestation time — a human never holds a peer key and never casts a ballot.

Nothing here grants authority. ``peer_id`` is a stable label; treating it as
permission is a caller bug.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agency.peers.canonical import CONSUMER_ID_RE, SchemaError, validate_string

__all__ = [
    "CONSUMER_ID_RE",
    "ConsumerId",
    "PeerIdentity",
    "PeerPublicKey",
    "consumer_id_from_ingress",
    "derive_peer_id",
    "derive_peer_id_from_raw",
    "peer_fingerprint",
    "public_key_from_fingerprint",
]

MAX_CONSUMER_REF_BYTES: Final = 256


def _raw_public_key(public_key: Ed25519PublicKey) -> bytes:
    if not isinstance(public_key, Ed25519PublicKey):
        raise SchemaError("public key is not an Ed25519 public key")
    return public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)


def peer_fingerprint(public_key: Ed25519PublicKey) -> str:
    """Full lowercase hex of the raw 32-byte Ed25519 public key (§5.4)."""

    return _raw_public_key(public_key).hex()


def derive_peer_id(public_key: Ed25519PublicKey) -> str:
    """``peer_`` + first 32 hex of SHA-256(raw public key) (§5.4)."""

    return "peer_" + hashlib.sha256(_raw_public_key(public_key)).hexdigest()[:32]


def derive_peer_id_from_raw(raw_public_key: bytes) -> str:
    """Peer identity from raw public-key bytes, without a key object.

    Used by membership loading, where the key is materialized from a pinned
    genesis record. The bytes are hashed, never trusted as a label.
    """

    if type(raw_public_key) is not bytes or len(raw_public_key) != 32:
        raise SchemaError("raw public key must be exactly 32 bytes")
    return "peer_" + hashlib.sha256(raw_public_key).hexdigest()[:32]


def public_key_from_fingerprint(fingerprint: str) -> Ed25519PublicKey:
    """Rebuild a public key from its full hex fingerprint."""

    if type(fingerprint) is not str or len(fingerprint) != 64:
        raise SchemaError("invalid public-key fingerprint length")
    try:
        raw = bytes.fromhex(fingerprint)
    except ValueError as exc:
        raise SchemaError("invalid public-key fingerprint") from exc
    if len(raw) != 32:
        raise SchemaError("invalid public-key fingerprint length")
    return Ed25519PublicKey.from_public_bytes(raw)


def consumer_id_from_ingress(*, ingress: str, subject: str) -> str:
    """Derive a stable, non-reversible consumer identity from ingress identity.

    The human consumer's identity is a digest of the authenticated ingress
    binding (for example ``telegram`` + sender scope), never the raw sender ID
    and never a peer key. The reference is a label for affected-set accounting:
    it is not proof of humanity and not a signature.
    """

    if type(ingress) is not str or not ingress:
        raise SchemaError("invalid ingress binding")
    if type(subject) is not str or not subject:
        raise SchemaError("invalid consumer subject")
    if len(subject.encode("utf-8")) > MAX_CONSUMER_REF_BYTES:
        raise SchemaError("consumer subject is too long")
    material = f"{ingress}\x1f{subject}".encode()
    return "consumer_" + hashlib.sha256(material).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class PeerPublicKey:
    """A peer's pinned Ed25519 public key and its derived identity.

    ``fingerprint`` is the full hex key; ``peer_id`` is the truncated digest
    used on the wire. Both are derived, never supplied, so a mismatched pair
    cannot be constructed.
    """

    fingerprint: str
    peer_id: str
    _raw: bytes

    @classmethod
    def from_key(cls, public_key: Ed25519PublicKey) -> PeerPublicKey:
        raw = _raw_public_key(public_key)
        return cls(
            fingerprint=raw.hex(),
            peer_id=derive_peer_id_from_raw(raw),
            _raw=raw,
        )

    @classmethod
    def from_fingerprint(cls, fingerprint: str) -> PeerPublicKey:
        key = public_key_from_fingerprint(fingerprint)
        return cls.from_key(key)

    def public_key(self) -> Ed25519PublicKey:
        return Ed25519PublicKey.from_public_bytes(self._raw)

    def verify_fingerprint(self, fingerprint: str) -> bool:
        return self.fingerprint == fingerprint

    def verify_identity(self, peer_id: str) -> bool:
        return self.peer_id == peer_id

    def to_record(self) -> dict[str, str]:
        """Storage form: the full fingerprint is enough to rebuild the key."""

        return {"fingerprint": self.fingerprint, "peer_id": self.peer_id}


@dataclass(frozen=True, slots=True)
class PeerIdentity:
    """A peer's signing identity: private key plus derived public identity.

    The private key never leaves this object in serialized form; ``to_record``
    exposes only the public half. A peer signs its own messages only.
    """

    key: Ed25519PrivateKey
    public: PeerPublicKey

    @classmethod
    def generate(cls) -> PeerIdentity:
        key = Ed25519PrivateKey.generate()
        return cls(key=key, public=PeerPublicKey.from_key(key.public_key()))

    @classmethod
    def from_private_key(cls, key: Ed25519PrivateKey) -> PeerIdentity:
        return cls(key=key, public=PeerPublicKey.from_key(key.public_key()))

    @property
    def peer_id(self) -> str:
        return self.public.peer_id

    @property
    def fingerprint(self) -> str:
        return self.public.fingerprint

    def to_record(self) -> dict[str, str]:
        return self.public.to_record()


@dataclass(frozen=True, slots=True)
class ConsumerId:
    """A human consumer's affected-set identity.

    ``consumer_id`` is the protocol label; ``ingress`` names the authenticated
    adapter; ``subject`` is the non-reversible input to the identity digest.
    The raw ``subject`` is retained only so the record can be re-derived and
    audited by the operator, never so it can be signed as a peer.
    """

    consumer_id: str
    ingress: str
    subject: str

    def __post_init__(self) -> None:
        if type(self.consumer_id) is not str or CONSUMER_ID_RE.fullmatch(self.consumer_id) is None:
            raise SchemaError("invalid consumer_id")
        validate_string(self.ingress, "ingress", minimum=1, maximum=64)
        validate_string(self.subject, "subject", minimum=1, maximum=MAX_CONSUMER_REF_BYTES)

    @classmethod
    def derive(cls, *, ingress: str, subject: str) -> ConsumerId:
        return cls(
            consumer_id=consumer_id_from_ingress(ingress=ingress, subject=subject),
            ingress=ingress,
            subject=subject,
        )
