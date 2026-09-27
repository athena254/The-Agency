"""Unauthoritative, in-memory known-peer envelope cryptographic primitives.

A successful verification is NOT admission, consensus, a ballot, or an execution grant.
Callers must provide independent authenticated-transport, pinned-membership,
durable replay, and expiry checks. Kind-specific payload schemas are NOT implemented.
Do not connect this module to an actuator or treat its result as authority.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


class EnvelopeError(ValueError):
    """Noncanonical, invalid, or untrusted envelope."""


PROTO = "agency-known-peer/1"
PREFIX = b"agency-known-peer/1|sig-v1|"
KINDS = frozenset(
    {
        "proposal",
        "ballot",
        "membership",
        "decision-certificate",
        "grant",
        "heartbeat",
        "recovery-request",
        "recovery-response",
    }
)
HEADER = frozenset(
    {
        "proto",
        "kind",
        "sender_peer_id",
        "membership_epoch",
        "membership_root",
        "msg_id",
        "monotonic_seq",
        "issued_at_ms",
        "expires_at_ms",
        "correlation_id",
        "payload_digest",
    }
)
_INPUT_HEADER = HEADER - {"payload_digest"}
ENVELOPE = HEADER | {"envelope_digest", "payload", "signature"}
_SIGNATURE = frozenset({"alg", "key_id", "sig"})
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_PEER = re.compile(r"peer_[0-9a-f]{32}\Z")
_MAX_U64 = (1 << 64) - 1
_MIN_I64 = -(1 << 63)
_MAX_I64 = (1 << 63) - 1
MAX_WIRE_BYTES = 65536
MAX_DEPTH = 32


def _canonical_value(value: Any, depth: int, max_depth: int) -> Any:
    if depth > max_depth:
        raise EnvelopeError("JSON nesting limit exceeded")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = unicodedata.normalize("NFC", value)
        try:
            normalized.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise EnvelopeError("invalid Unicode") from exc
        return normalized
    if type(value) is int:
        return value
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item, depth + 1, max_depth) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise EnvelopeError("non-string JSON key")
            normalized = _canonical_value(key, depth + 1, max_depth)
            if normalized in result:
                raise EnvelopeError("duplicate normalized JSON key")
            result[normalized] = _canonical_value(item, depth + 1, max_depth)
        return dict(sorted(result.items(), key=lambda entry: entry[0].encode("utf-8")))
    raise EnvelopeError("unsupported JSON value (floats are forbidden)")


def canonical_json(value: Any, *, max_depth: int = MAX_DEPTH) -> bytes:
    """NFC, UTF-8, lexicographic byte-key order, integer-only canonical JSON."""
    if type(max_depth) is not int or max_depth < 0 or max_depth > MAX_DEPTH:
        raise EnvelopeError("invalid nesting limit")
    normalized = _canonical_value(value, 0, max_depth)
    try:
        return json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, OverflowError, RecursionError) as exc:
        raise EnvelopeError("JSON encoding failed") from exc


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EnvelopeError("duplicate JSON key")
        result[key] = value
    return result


def _reject_number(value: str) -> Any:
    raise EnvelopeError("non-integer JSON number")


def _closed(value: Any, keys: frozenset[str], name: str) -> None:
    if not isinstance(value, dict) or value.keys() != keys:
        raise EnvelopeError(f"invalid {name} fields")


def _integer(value: Any, minimum: int, maximum: int, field: str) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise EnvelopeError(f"invalid {field}")


def _digest(value: Any, field: str) -> None:
    if not isinstance(value, str) or _HEX.fullmatch(value) is None:
        raise EnvelopeError(f"invalid {field}")


def _validate_header(value: Any, *, signing: bool = False) -> None:
    _closed(value, _INPUT_HEADER if signing else HEADER, "header")
    if value["proto"] != PROTO or not isinstance(value["kind"], str) or value["kind"] not in KINDS:
        raise EnvelopeError("unsupported protocol or kind")
    sender = value["sender_peer_id"]
    if not isinstance(sender, str) or _PEER.fullmatch(sender) is None:
        raise EnvelopeError("invalid sender_peer_id")
    _integer(value["membership_epoch"], 0, _MAX_U64, "membership_epoch")
    _digest(value["membership_root"], "membership_root")
    _integer(value["monotonic_seq"], 0, _MAX_U64, "monotonic_seq")
    if value["msg_id"] != f"{sender}:{value['monotonic_seq']}":
        raise EnvelopeError("msg_id does not bind sender and sequence")
    for field in ("issued_at_ms", "expires_at_ms"):
        _integer(value[field], _MIN_I64, _MAX_I64, field)
    if value["expires_at_ms"] <= value["issued_at_ms"]:
        raise EnvelopeError("expiry is not after issue")
    if not isinstance(value["correlation_id"], str) or not value["correlation_id"]:
        raise EnvelopeError("invalid correlation_id")
    if not signing:
        _digest(value["payload_digest"], "payload_digest")


def peer_fingerprint(public_key: Ed25519PublicKey) -> str:
    """Full hex of the raw 32-byte Ed25519 public key (§5.4)."""
    return public_key.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def peer_id(public_key: Ed25519PublicKey) -> str:
    """Stable, truncated SHA-256 public-key identity (§5.4)."""
    raw = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return "peer_" + hashlib.sha256(raw).hexdigest()[:32]


def decode_envelope(
    wire: bytes,
    *,
    max_wire_bytes: int = MAX_WIRE_BYTES,
    max_depth: int = MAX_DEPTH,
) -> dict[str, Any]:
    """Only structural decoding; return is UNTRUSTED and has no authority."""
    if (
        type(wire) is not bytes
        or type(max_wire_bytes) is not int
        or not 0 < max_wire_bytes <= MAX_WIRE_BYTES
    ):
        raise EnvelopeError("invalid wire or size bound")
    if not wire or len(wire) > max_wire_bytes:
        raise EnvelopeError("wire size limit exceeded")
    try:
        value = json.loads(
            wire.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_pairs,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
        if canonical_json(value, max_depth=max_depth) != wire:
            raise EnvelopeError("noncanonical wire encoding")
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise EnvelopeError("invalid canonical JSON wire encoding") from exc
    _closed(value, ENVELOPE, "envelope")
    header = {key: value[key] for key in HEADER}
    _validate_header(header)
    _digest(value["envelope_digest"], "envelope_digest")
    if not isinstance(value["payload"], dict):
        raise EnvelopeError("payload must be an object")
    signature = value["signature"]
    _closed(signature, _SIGNATURE, "signature")
    if signature["alg"] != "Ed25519":
        raise EnvelopeError("unsupported signature algorithm")
    _digest(signature["key_id"], "key_id")
    _signature_bytes(signature["sig"])
    return cast(dict[str, Any], value)


def _signature_bytes(encoded: Any) -> bytes:
    if not isinstance(encoded, str):
        raise EnvelopeError("invalid signature encoding")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise EnvelopeError("invalid signature encoding") from exc
    if len(raw) != 64 or base64.b64encode(raw).decode("ascii") != encoded:
        raise EnvelopeError("invalid signature length or noncanonical base64")
    return raw


def _same_decoded_json(original: Any, candidate: Any) -> bool:
    """Compare exact decoded values and container types, without NFC folding."""
    if type(original) is not type(candidate):
        return False
    if type(original) is dict:
        return original.keys() == candidate.keys() and all(
            _same_decoded_json(original[key], candidate[key]) for key in original
        )
    if type(original) is list:
        return len(original) == len(candidate) and all(
            _same_decoded_json(a, b) for a, b in zip(original, candidate)
        )
    return bool(original == candidate)


def sign_envelope(
    header: Mapping[str, Any],
    payload: Mapping[str, Any],
    key: Ed25519PrivateKey,
) -> bytes:
    """Sign a structural envelope; does not create a ballot, membership or grant."""
    fields = dict(header)
    _validate_header(fields, signing=True)
    if fields["sender_peer_id"] != peer_id(key.public_key()):
        raise EnvelopeError("sender does not match signing key")
    if not isinstance(payload, Mapping):
        raise EnvelopeError("payload must be an object")
    fields["payload_digest"] = hashlib.sha256(canonical_json(payload)).hexdigest()
    digest = hashlib.sha256(canonical_json(fields)).digest()
    envelope = {
        **fields,
        "envelope_digest": digest.hex(),
        "payload": payload,
        "signature": {
            "alg": "Ed25519",
            "key_id": peer_fingerprint(key.public_key()),
            "sig": base64.b64encode(key.sign(PREFIX + digest)).decode("ascii"),
        },
    }
    wire = canonical_json(envelope)
    decode_envelope(wire)
    return wire


def verify_envelope(
    wire: bytes,
    public_key: Ed25519PublicKey,
    *,
    transport_check: Callable[[dict[str, Any]], bool],
    membership_check: Callable[[dict[str, Any], Ed25519PublicKey], bool],
    replay_check: Callable[[dict[str, Any]], bool],
    expiry_check: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    """Verify cryptography after explicit external prerequisites; never authorize.

    Caller must independently pin epoch/root and the listed key, durably check
    sequence replay, apply configured expiry/skew, and authenticate transport.
    No built-in policy, kind-specific payload schema, or action validation exists.
    """
    envelope = decode_envelope(wire)
    if not all(
        callable(check)
        for check in (
            transport_check,
            membership_check,
            replay_check,
            expiry_check,
        )
    ):
        raise EnvelopeError("all external validators are required")

    def checked(callback: Callable[..., bool], *args: Any) -> None:
        # Never expose the authenticated result to a validator. Re-encoding
        # alone misses canonically equivalent Unicode or list→tuple mutation.
        candidate = deepcopy(envelope)
        if callback(candidate, *args) is not True:
            raise EnvelopeError("external prerequisite failed")
        if not _same_decoded_json(envelope, candidate):
            raise EnvelopeError("validator mutated signed envelope")

    checked(transport_check)
    checked(membership_check, public_key)
    if envelope["sender_peer_id"] != peer_id(public_key):
        raise EnvelopeError("sender does not match public key")
    if envelope["signature"]["key_id"] != peer_fingerprint(public_key):
        raise EnvelopeError("key fingerprint mismatch")
    digest = bytes.fromhex(envelope["envelope_digest"])
    try:
        public_key.verify(_signature_bytes(envelope["signature"]["sig"]), PREFIX + digest)
    except InvalidSignature as exc:
        raise EnvelopeError("invalid Ed25519 signature") from exc
    expected_payload = hashlib.sha256(canonical_json(envelope["payload"])).hexdigest()
    if envelope["payload_digest"] != expected_payload:
        raise EnvelopeError("payload digest mismatch")
    projection = {field: envelope[field] for field in HEADER}
    if envelope["envelope_digest"] != hashlib.sha256(canonical_json(projection)).hexdigest():
        raise EnvelopeError("envelope digest mismatch")
    checked(replay_check)
    checked(expiry_check)
    return envelope
