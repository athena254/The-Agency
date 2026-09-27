"""Hermetic cryptographic envelope tests; no membership or authority is implied."""

import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.peer_envelope import (
    EnvelopeError,
    canonical_json,
    decode_envelope,
    peer_fingerprint,
    peer_id,
    sign_envelope,
    verify_envelope,
)


def keys():
    first = Ed25519PrivateKey.generate()
    second = Ed25519PrivateKey.generate()
    return first, second


def header(key):
    public = key.public_key()
    sender = peer_id(public)
    return {
        "proto": "agency-known-peer/1",
        "kind": "heartbeat",
        "sender_peer_id": sender,
        "membership_epoch": 1,
        "membership_root": "a" * 64,
        "msg_id": f"{sender}:1",
        "monotonic_seq": 1,
        "issued_at_ms": 1000,
        "expires_at_ms": 2000,
        "correlation_id": f"{sender}:1",
    }


def checked(wire, public):
    return verify_envelope(
        wire,
        public,
        transport_check=lambda envelope: True,
        membership_check=lambda envelope, key: True,
        replay_check=lambda envelope: True,
        expiry_check=lambda envelope: True,
    )


def test_two_independent_signers_roundtrip_and_exact_digest_bytes():
    first, second = keys()
    assert peer_id(first.public_key()) != peer_id(second.public_key())
    for signer in (first, second):
        wire = sign_envelope(header(signer), {"count": 3, "label": "é"}, signer)
        envelope = checked(wire, signer.public_key())
        assert envelope["sender_peer_id"] == peer_id(signer.public_key())
        assert envelope["signature"]["key_id"] == peer_fingerprint(signer.public_key())
        assert (
            envelope["payload_digest"]
            == hashlib.sha256(b'{"count":3,"label":"\xc3\xa9"}').hexdigest()
        )
        projection = {
            k: v
            for k, v in envelope.items()
            if k
            not in (
                "payload",
                "signature",
                "envelope_digest",
            )
        }
        digest = hashlib.sha256(canonical_json(projection)).digest()
        assert envelope["envelope_digest"] == digest.hex()
        signer.public_key().verify(
            base64.b64decode(envelope["signature"]["sig"]),
            b"agency-known-peer/1|sig-v1|" + digest,
        )


def test_prerequisites_are_required_and_fail_closed():
    signer, _ = keys()
    wire = sign_envelope(header(signer), {}, signer)
    with pytest.raises(TypeError):
        verify_envelope(wire, signer.public_key())
    for name in ("transport_check", "membership_check", "replay_check", "expiry_check"):
        checks = {
            "transport_check": lambda envelope: True,
            "membership_check": lambda envelope, key: True,
            "replay_check": lambda envelope: True,
            "expiry_check": lambda envelope: True,
        }
        checks[name] = lambda *args: False
        with pytest.raises(EnvelopeError):
            verify_envelope(wire, signer.public_key(), **checks)


def test_cross_key_and_tampering_rejected():
    first, second = keys()
    wire = sign_envelope(header(first), {"count": 3}, first)
    with pytest.raises(EnvelopeError):
        checked(wire, second.public_key())
    original = json.loads(wire)
    for field, value in (
        ("payload", {"count": 4}),
        ("monotonic_seq", 2),
        ("msg_id", f"{peer_id(first.public_key())}:2"),
        ("membership_root", "b" * 64),
        ("expires_at_ms", 3000),
    ):
        modified = dict(original)
        modified[field] = value
        with pytest.raises(EnvelopeError):
            checked(canonical_json(modified), first.public_key())
    modified = dict(original)
    modified["monotonic_seq"] = 2
    modified["msg_id"] = f"{peer_id(first.public_key())}:2"
    with pytest.raises(EnvelopeError):
        checked(canonical_json(modified), first.public_key())
    modified = dict(original)
    modified["signature"] = {
        **original["signature"],
        "sig": base64.b64encode(b"\x00" * 64).decode(),
    }
    with pytest.raises(EnvelopeError):
        checked(canonical_json(modified), first.public_key())


def test_strict_canonical_json_and_wire_encodings():
    signer, _ = keys()
    wire = sign_envelope(header(signer), {}, signer)
    original = json.loads(wire)
    malformed = [
        wire + b" ",
        b'{"proto":"a","proto":"b"}',
        wire.replace(b'"membership_epoch":1', b'"membership_epoch":1.0'),
        wire.replace(b'"membership_epoch":1', b'"membership_epoch":NaN'),
        wire.replace(b'"membership_epoch":1', b'"membership_epoch":true'),
        wire.replace(b'"membership_epoch":1', b'"membership_epoch":-1'),
        wire.replace(b'"membership_epoch":1', b'"membership_epoch":18446744073709551616'),
        wire.replace(b'"membership_epoch":1', b'"membership_epoch":01'),
        wire.replace(b'"kind":"heartbeat"', b'"kind":"unknown"'),
        wire.replace(b'"proto":"agency-known-peer/1"', b'"proto":"agency-known-peer/2"'),
    ]
    changed = dict(original)
    changed["extra"] = 1
    malformed.append(canonical_json(changed))
    changed = dict(original)
    changed["signature"] = {**original["signature"], "extra": 1}
    malformed.append(canonical_json(changed))
    changed = dict(original)
    changed["signature"] = {**original["signature"], "sig": "%%%"}
    malformed.append(canonical_json(changed))
    changed = dict(original)
    changed["kind"] = []
    malformed.append(canonical_json(changed))
    changed = dict(original)
    changed["sender_peer_id"] = None
    malformed.append(canonical_json(changed))
    changed = dict(original)
    changed["signature"] = {**original["signature"], "alg": []}
    malformed.append(canonical_json(changed))
    for data in malformed:
        with pytest.raises(EnvelopeError):
            decode_envelope(data)


def test_validator_cannot_mutate_authenticated_result():
    signer, _ = keys()
    wire = sign_envelope(header(signer), {"count": 1}, signer)

    def malicious_expiry(envelope):
        envelope["payload"]["count"] = 999
        return True

    with pytest.raises(EnvelopeError):
        verify_envelope(
            wire,
            signer.public_key(),
            transport_check=lambda envelope: True,
            membership_check=lambda envelope, public: True,
            replay_check=lambda envelope: True,
            expiry_check=malicious_expiry,
        )


def test_limits_nfc_integer_only_and_unknown_payload_schema():
    signer, _ = keys()
    with pytest.raises(EnvelopeError):
        canonical_json({"x": 1.5})
    with pytest.raises(EnvelopeError):
        canonical_json({"x": "\ud800"})
    with pytest.raises(EnvelopeError):
        canonical_json({"e\u0301": 1, "é": 2})
    assert canonical_json({"z": True, "e\u0301": "e\u0301"}) == b'{"z":true,"\xc3\xa9":"\xc3\xa9"}'
    wire = sign_envelope(header(signer), {"opaque": 1}, signer)
    with pytest.raises(EnvelopeError):
        decode_envelope(wire, max_wire_bytes=len(wire) - 1)
    with pytest.raises(EnvelopeError):
        decode_envelope(wire, max_depth=1)
    # No kind-specific payload validation here: cryptographic verification is not acceptance.
    assert checked(wire, signer.public_key())["payload"] == {"opaque": 1}
