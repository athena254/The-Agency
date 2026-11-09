"""Protocol/contract checkpoint tests for the peer layer (slice A).

These tests exercise types and pure functions only. A passing test means the
contract holds; it never means a message is admitted, a ballot counts, or an
action is authorized.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.peer_envelope import EnvelopeError, canonical_json, peer_fingerprint, peer_id
from agency.peers.canonical import (
    SchemaError,
    digest_hex,
    validate_closed,
    validate_consumer_id,
    validate_digest,
    validate_peer_id,
)
from agency.peers.derive import (
    derive_eligible_set_digest,
    derive_proposal_id,
    validate_proposal_id,
)
from agency.peers.envelope import (
    EnvelopeBindingError,
    build_envelope,
    validate_payload,
    verify_message,
)
from agency.peers.identities import (
    ConsumerId,
    PeerIdentity,
    consumer_id_from_ingress,
    derive_peer_id,
)
from agency.peers.membership import (
    Membership,
    derive_genesis_membership,
    derive_membership_root,
    derive_next_membership,
    eligible_voter_ids,
    membership_from_payload,
    quorum_approvals_required,
)
from agency.peers.redaction import (
    REDACTED,
    contains_secret,
    is_sensitive_key,
    redact_mapping,
    redact_structure,
    redact_text,
    redacted_finding,
    sanitize_log_payload,
)

ROOT = "a" * 64
POLICY = "peer-policy/1"


def make_keys(count: int = 4) -> list[Ed25519PrivateKey]:
    return [Ed25519PrivateKey.generate() for _ in range(count)]


def make_members(keys: list[Ed25519PrivateKey]) -> list[dict[str, Any]]:
    return [
        {
            "peer_id": peer_id(key.public_key()),
            "public_key": peer_fingerprint(key.public_key()),
            "roles": ["validator"],
            "joined_epoch": 1,
        }
        for key in keys
    ]


def make_genesis() -> Membership:
    return derive_genesis_membership(make_members(make_keys()), policy_version=POLICY)


def checked(wire: bytes, public, *, fail: str | None = None):
    checks = {
        "transport_check": lambda envelope: True,
        "membership_check": lambda envelope, key: True,
        "replay_check": lambda envelope: True,
        "expiry_check": lambda envelope: True,
    }
    if fail is not None:
        checks[fail] = lambda *args: False
    return verify_message(wire, public, **checks)


def make_proposal_body(
    proposer: str,
    *,
    epoch: int = 1,
    root: str = ROOT,
    proposal_id: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "proposer_peer_id": proposer,
        "proposal_version": 1,
        "membership_epoch": epoch,
        "membership_root": root,
        "action": {
            "action_type": "spawn_agent",
            "resource": "agent:x",
            "params_digest": "b" * 64,
            "params_schema_version": "params/1",
        },
        "affected_snapshot": {
            "graph_root_digest": "c" * 64,
            "epoch": epoch,
            "membership_root": root,
            "members": [
                {
                    "peer_id": proposer,
                    "consumer_id": None,
                    "kind": "peer",
                    "role": "provider",
                    "reason": "owns the resource",
                },
                {
                    "peer_id": None,
                    "consumer_id": "consumer_" + "d" * 32,
                    "kind": "human-consumer",
                    "role": "operator",
                    "reason": "operates the resource",
                },
            ],
            "derivation": "graph:DEPENDS_ON@epoch",
        },
        "policy_version": POLICY,
        "evidence_manifest": [
            {
                "name": "finding",
                "sha256": "e" * 64,
                "media_type": "text/plain",
                "provenance": "model-analysis",
            }
        ],
        "idempotency_key": "spawn_agent:agent:x:uuid",
        "fencing_base": 0,
        "expires_at_ms": 2000,
        "supersedes": None,
    }
    body.update(overrides)
    if proposal_id is not None:
        body["proposal_id"] = proposal_id
    return body


# --------------------------------------------------------------------- #
# Identities
# --------------------------------------------------------------------- #


def test_peer_identity_is_derived_from_the_key_and_stable() -> None:
    key = Ed25519PrivateKey.generate()
    identity = PeerIdentity.from_private_key(key)
    assert identity.peer_id == peer_id(key.public_key())
    assert identity.fingerprint == peer_fingerprint(key.public_key())
    assert identity.peer_id == derive_peer_id(key.public_key())
    assert identity.public.verify_identity(identity.peer_id)
    assert identity.public.verify_fingerprint(identity.fingerprint)
    assert not identity.public.verify_identity("peer_" + "0" * 32)
    assert PeerIdentity.from_private_key(key).peer_id == identity.peer_id
    other = Ed25519PrivateKey.generate()
    assert PeerIdentity.from_private_key(other).peer_id != identity.peer_id
    assert identity.to_record() == {
        "peer_id": identity.peer_id,
        "fingerprint": identity.fingerprint,
    }


def test_consumer_identity_is_a_digest_of_the_ingress_binding() -> None:
    first = consumer_id_from_ingress(ingress="telegram", subject="12345")
    second = consumer_id_from_ingress(ingress="telegram", subject="12345")
    other_subject = consumer_id_from_ingress(ingress="telegram", subject="99999")
    other_ingress = consumer_id_from_ingress(ingress="butler", subject="12345")
    assert first == second
    assert first != other_subject != other_ingress
    assert first.startswith("consumer_")
    assert validate_consumer_id(first) == first
    with pytest.raises(SchemaError):
        validate_consumer_id(peer_id(Ed25519PrivateKey.generate().public_key()))
    derived = ConsumerId.derive(ingress="telegram", subject="12345")
    assert derived.consumer_id == first


# --------------------------------------------------------------------- #
# Canonical serialization and closed schemas
# --------------------------------------------------------------------- #


def test_canonical_json_rejects_noncanonical_values() -> None:
    with pytest.raises(SchemaError):
        validate_closed({"a": 1, "b": 2}, frozenset({"a"}), "value")
    with pytest.raises(SchemaError):
        validate_digest("A" * 64)
    with pytest.raises(SchemaError):
        validate_digest("a" * 63)
    with pytest.raises(SchemaError):
        validate_peer_id("user")
    assert canonical_json({"b": 1, "a": "é"}) == '{"a":"é","b":1}'.encode()


# --------------------------------------------------------------------- #
# Membership and epochs
# --------------------------------------------------------------------- #


def test_genesis_membership_root_is_derived_and_self_consistent() -> None:
    keys = make_keys()
    members = make_members(keys)
    genesis = derive_genesis_membership(members, policy_version=POLICY)
    assert genesis.epoch == 1
    assert genesis.prev_epoch_digest == "0" * 64
    assert genesis.root == derive_membership_root(genesis.to_projection())
    assert genesis.member_count == 4
    assert eligible_voter_ids(genesis) == tuple(member["peer_id"] for member in members)
    assert membership_from_payload(genesis.to_payload()) == genesis
    assert genesis.role_of(members[0]["peer_id"]) == ("validator",)
    assert genesis.has_member(members[1]["peer_id"])


def test_membership_rejects_a_root_that_does_not_match_its_contents() -> None:
    genesis = make_genesis()
    with pytest.raises(SchemaError):
        Membership(
            epoch=genesis.epoch,
            members=genesis.members,
            removed=genesis.removed,
            policy_version=genesis.policy_version,
            prev_epoch_digest=genesis.prev_epoch_digest,
            root="0" * 64,
        )


def test_membership_binds_peer_identity_to_the_listed_public_key() -> None:
    keys = make_keys(2)
    members = make_members(keys)
    forged_id = [dict(member) for member in members]
    forged_id[1]["peer_id"] = members[0]["peer_id"]
    with pytest.raises(SchemaError):
        derive_genesis_membership(forged_id, policy_version=POLICY)
    forged_key = [dict(member) for member in members]
    forged_key[1]["public_key"] = members[0]["public_key"]
    with pytest.raises(SchemaError):
        derive_genesis_membership(forged_key, policy_version=POLICY)


def test_membership_enforces_the_four_peer_bound_and_nonempty_set() -> None:
    with pytest.raises(SchemaError):
        derive_genesis_membership(make_members(make_keys(5)), policy_version=POLICY)
    with pytest.raises(SchemaError):
        derive_genesis_membership([], policy_version=POLICY)


def test_epoch_bump_chains_to_the_prior_root() -> None:
    genesis = make_genesis()
    bumped = derive_next_membership(genesis, members=make_members(make_keys()))
    assert bumped.epoch == 2
    assert bumped.prev_epoch_digest == genesis.root
    assert bumped.root != genesis.root
    assert bumped.root == derive_membership_root(bumped.to_projection())
    with pytest.raises(SchemaError):
        Membership(
            epoch=bumped.epoch,
            members=bumped.members,
            removed=bumped.removed,
            policy_version=bumped.policy_version,
            prev_epoch_digest=genesis.root,
            root=genesis.root,
        )


@pytest.mark.parametrize(("count", "expected"), [(1, 1), (2, 2), (3, 3), (4, 3)])
def test_quorum_helper_is_the_approved_three_of_four(count: int, expected: int) -> None:
    assert quorum_approvals_required(count) == expected
    with pytest.raises(SchemaError):
        quorum_approvals_required(5)


# --------------------------------------------------------------------- #
# Envelope binding
# --------------------------------------------------------------------- #


def test_envelope_roundtrip_binds_payload_and_sender() -> None:
    key = Ed25519PrivateKey.generate()
    identity = PeerIdentity.from_private_key(key)
    payload = {"note": "hello"}
    wire = build_envelope(
        kind="heartbeat",
        sender=identity,
        membership_epoch=1,
        membership_root=ROOT,
        monotonic_seq=1,
        issued_at_ms=1000,
        expires_at_ms=2000,
        correlation_id=f"{identity.peer_id}:1",
        payload=payload,
    )
    message = checked(wire, key.public_key())
    assert message.kind == "heartbeat"
    assert message.payload == payload
    assert message.sender_peer_id == identity.peer_id
    assert message.wire == wire
    assert message.msg_id == f"{identity.peer_id}:1"
    assert len(message.envelope_digest) == 64


def test_envelope_rejects_tampering_and_wrong_keys() -> None:
    key = Ed25519PrivateKey.generate()
    identity = PeerIdentity.from_private_key(key)
    wire = build_envelope(
        kind="heartbeat",
        sender=identity,
        membership_epoch=1,
        membership_root=ROOT,
        monotonic_seq=1,
        issued_at_ms=1000,
        expires_at_ms=2000,
        correlation_id=f"{identity.peer_id}:1",
        payload={"note": "hello"},
    )
    original = json.loads(wire)
    for field, value in (
        ("payload", {"note": "evil"}),
        ("membership_root", "b" * 64),
        ("monotonic_seq", 2),
        ("expires_at_ms", 3000),
    ):
        modified = dict(original)
        modified[field] = value
        with pytest.raises(EnvelopeError):
            checked(canonical_json(modified), key.public_key())
    with pytest.raises(EnvelopeError):
        checked(wire, Ed25519PrivateKey.generate().public_key())


def test_envelope_requires_every_external_prerequisite() -> None:
    key = Ed25519PrivateKey.generate()
    identity = PeerIdentity.from_private_key(key)
    wire = build_envelope(
        kind="heartbeat",
        sender=identity,
        membership_epoch=1,
        membership_root=ROOT,
        monotonic_seq=1,
        issued_at_ms=1000,
        expires_at_ms=2000,
        correlation_id=f"{identity.peer_id}:1",
        payload={"note": "hello"},
    )
    for name in ("transport_check", "membership_check", "replay_check", "expiry_check"):
        with pytest.raises(EnvelopeBindingError):
            checked(wire, key.public_key(), fail=name)


def test_payload_schemas_are_closed() -> None:
    with pytest.raises(EnvelopeBindingError):
        validate_payload("heartbeat", {"note": "x", "extra": 1})
    with pytest.raises(EnvelopeBindingError):
        validate_payload("unknown", {})
    with pytest.raises(EnvelopeBindingError):
        validate_payload("heartbeat", {})
    with pytest.raises(EnvelopeBindingError):
        validate_payload("heartbeat", {"note": "x", "extra": None})


def test_proposal_payload_rejects_unknown_fields() -> None:
    body = make_proposal_body("peer_" + "a" * 32)
    with pytest.raises(EnvelopeBindingError):
        validate_payload("proposal", {**body, "surprise": 1})


# --------------------------------------------------------------------- #
# Pure derivation
# --------------------------------------------------------------------- #


def test_proposal_id_matches_the_spec_formula_and_is_field_sensitive() -> None:
    body = make_proposal_body("peer_" + "a" * 32)
    expected = "prop_" + hashlib.sha256(canonical_json(body)).hexdigest()[:32]
    derived = derive_proposal_id(body)
    assert derived == expected
    assert derive_proposal_id(dict(body)) == derived
    assert validate_proposal_id({**body, "proposal_id": derived}) == derived
    with pytest.raises(SchemaError):
        validate_proposal_id({**body, "proposal_id": "prop_" + "0" * 32})
    for change in (
        {"proposal_version": 2},
        {"fencing_base": 1},
        {"policy_version": "peer-policy/2"},
        {"idempotency_key": "spawn_agent:agent:x:other"},
    ):
        assert derive_proposal_id(make_proposal_body("peer_" + "a" * 32, **change)) != derived


def test_proposal_envelope_roundtrip_carries_the_derived_id() -> None:
    key = Ed25519PrivateKey.generate()
    identity = PeerIdentity.from_private_key(key)
    body = make_proposal_body(identity.peer_id)
    body["proposal_id"] = derive_proposal_id(body)
    wire = build_envelope(
        kind="proposal",
        sender=identity,
        membership_epoch=body["membership_epoch"],
        membership_root=body["membership_root"],
        monotonic_seq=1,
        issued_at_ms=1000,
        expires_at_ms=2000,
        correlation_id=body["proposal_id"],
        payload=body,
    )
    message = checked(wire, key.public_key())
    assert message.payload["proposal_id"] == body["proposal_id"]
    assert message.correlation_id == body["proposal_id"]


def test_eligible_set_digest_is_order_independent_and_kind_sensitive() -> None:
    peer_a = "peer_" + "a" * 32
    peer_b = "peer_" + "b" * 32
    consumer = "consumer_" + "c" * 32
    members = [
        {"peer_id": peer_a, "consumer_id": None, "kind": "peer", "role": "provider"},
        {"peer_id": None, "consumer_id": consumer, "kind": "human-consumer", "role": "operator"},
    ]
    first = derive_eligible_set_digest([peer_a, peer_b], members)
    second = derive_eligible_set_digest([peer_b, peer_a], list(reversed(members)))
    assert first == second
    assert derive_eligible_set_digest([peer_a], members) != first
    swapped = [
        {"peer_id": None, "consumer_id": consumer, "kind": "peer", "role": "provider"},
    ]
    with pytest.raises(SchemaError):
        derive_eligible_set_digest([peer_a], swapped)


# --------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------- #


def test_redaction_removes_content_but_keeps_protocol_metadata() -> None:
    payload = {
        "proposal_id": "prop_" + "a" * 32,
        "peer_id": "peer_" + "b" * 32,
        "payload_digest": "c" * 64,
        "prompt": "my secret prompt",
        "search_query": "find me",
        "api_key": "sk-abcdefghijklmnopqrstuvwxyz",
        "note": "contact me at user@example.com",
        "count": 3,
        "nested": {"message": "hello", "ok": True},
    }
    redacted = redact_mapping(payload)
    assert redacted["proposal_id"] == payload["proposal_id"]
    assert redacted["peer_id"] == payload["peer_id"]
    assert redacted["payload_digest"] == payload["payload_digest"]
    assert redacted["prompt"] == REDACTED
    assert redacted["search_query"] == REDACTED
    assert redacted["api_key"] == REDACTED
    assert redacted["nested"]["message"] == REDACTED
    assert redacted["nested"]["ok"] is True
    assert redacted["note"] == "contact me at [REDACTED]"
    assert redacted["count"] == 3


def test_sanitize_log_payload_preserves_digests_and_redacts_nested_content() -> None:
    payload = {
        "proposal_id": "prop_" + "a" * 32,
        "payload_digest": "b" * 64,
        "prompt": "secret",
        "items": [{"text": "hi"}],
    }
    redacted = sanitize_log_payload(payload)
    assert redacted["proposal_id"].startswith("prop_")
    assert redacted["payload_digest"] == "b" * 64
    assert redacted["prompt"] == REDACTED
    assert redacted["items"][0]["text"] == REDACTED


def test_secret_detection_is_specific_enough_to_keep_digests() -> None:
    assert contains_secret("Bearer abcdefghijklmnop")
    assert contains_secret("AKIA1234567890ABCDEF")
    assert contains_secret("telegram token 123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    assert not contains_secret("a" * 64)
    assert not contains_secret("peer_0123456789abcdef0123456789abcdef")
    assert is_sensitive_key("prompt")
    assert is_sensitive_key("search_query")
    assert not is_sensitive_key("proposal_id")
    assert not is_sensitive_key("membership_root")


def test_redacted_finding_cites_a_digest_of_redacted_text_only() -> None:
    raw = "the user email is user@example.com"
    text, digest = redacted_finding(raw)
    assert "user@example.com" not in text
    assert digest == digest_hex(text.encode("utf-8"))
    assert redacted_finding(raw) == (text, digest)
    assert redact_text("nothing sensitive here") == "nothing sensitive here"
    assert redact_structure(["a", {"prompt": "b"}, 1]) == ["a", {"prompt": REDACTED}, 1]
