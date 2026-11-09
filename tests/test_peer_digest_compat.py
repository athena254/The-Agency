"""Byte-compatibility proof for the derived digests (§5.2/§5.3, §18 step 3).

Independent implementations must agree byte-for-byte on canonical JSON and on
the digests derived from it. This module re-implements the derivation rule from
the spec text with a separate code path — a hand-written recursive serializer,
not ``agency.peer_envelope.canonical_json`` — and asserts equality over a corpus
that includes Unicode normalization, nesting, optional fields, and integer
boundaries.

The reference lives in the test on purpose: it is a second implementation, not
a second copy of the production code.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.peer_envelope import canonical_json, peer_fingerprint, peer_id
from agency.peers.canonical import digest_hex, digest_json
from agency.peers.derive import derive_eligible_set_digest, derive_proposal_id
from agency.peers.log import derive_record_digest
from agency.peers.membership import (
    derive_genesis_membership,
    derive_membership_root,
    derive_next_membership,
)

POLICY = "peer-policy/1"
ROOT = "a" * 64


# --------------------------------------------------------------------- #
# Independent reference implementation (test-local, from the spec text)
# --------------------------------------------------------------------- #


def _ref_string(value: str) -> bytes:
    normalized = unicodedata.normalize("NFC", value)
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _ref_canonical(value: Any) -> bytes:
    if value is None:
        return b"null"
    if value is True:
        return b"true"
    if value is False:
        return b"false"
    if type(value) is int:
        return str(value).encode("ascii")
    if type(value) is str:
        return _ref_string(value)
    if type(value) is list:
        return b"[" + b",".join(_ref_canonical(item) for item in value) + b"]"
    if type(value) is dict:
        items = sorted(
            value.items(), key=lambda item: unicodedata.normalize("NFC", item[0]).encode("utf-8")
        )
        return (
            b"{"
            + b",".join(_ref_string(key) + b":" + _ref_canonical(item) for key, item in items)
            + b"}"
        )
    raise AssertionError(f"reference canonicalizer got {type(value).__name__}")


def _ref_digest_json(value: Any) -> str:
    return hashlib.sha256(_ref_canonical(value)).hexdigest()


def _ref_proposal_id(body: dict[str, Any]) -> str:
    """§5.3: hash every immutable field except the self-referential id."""

    without_id = {key: item for key, item in body.items() if key != "proposal_id"}
    return "prop_" + hashlib.sha256(_ref_canonical(without_id)).hexdigest()[:32]


# --------------------------------------------------------------------- #
# Corpus
# --------------------------------------------------------------------- #


CANONICAL_CORPUS: tuple[Any, ...] = (
    {"b": 1, "a": "é"},
    {"nested": {"z": [1, 2, {"k": None}], "a": True}},
    {"unicode": "e\u0301", "emoji": "\U0001f680", "cjk": "機関"},
    {"int": 0, "neg": -1, "big": (1 << 63) - 1},
    {"empty_object": {}, "empty_list": []},
    {"quote": 'he said "hi"', "backslash": "a\\b", "newline": "a\nb"},
    {"controls": "\u2028\u2029\u0001"},
    {"key_order": {"z": 1, "a": 2, "m": 3}},
)


def make_proposal_body(proposer: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "proposer_peer_id": proposer,
        "proposal_version": 1,
        "membership_epoch": 1,
        "membership_root": ROOT,
        "action": {
            "action_type": "spawn_agent",
            "resource": "agent:x",
            "params_digest": "b" * 64,
            "params_schema_version": "params/1",
        },
        "affected_snapshot": {
            "graph_root_digest": "c" * 64,
            "epoch": 1,
            "membership_root": ROOT,
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
    return body


def proposal_corpus() -> list[dict[str, Any]]:
    proposer = "peer_" + "a" * 32
    bodies = [
        make_proposal_body(proposer),
        make_proposal_body(proposer, proposal_version=2),
        make_proposal_body(proposer, fencing_base=None),
        make_proposal_body(proposer, supersedes="prop_" + "b" * 32),
        make_proposal_body(proposer, idempotency_key="café:spawn:uuid"),
        make_proposal_body(proposer, evidence_manifest=[]),
        make_proposal_body(
            proposer,
            action={
                "action_type": "revoke_grant",
                "resource": "grant:🚀",
                "params_digest": "f" * 64,
                "params_schema_version": "params/2",
            },
        ),
    ]
    return bodies


def make_genesis():
    keys = [Ed25519PrivateKey.generate() for _ in range(4)]
    members = [
        {
            "peer_id": peer_id(key.public_key()),
            "public_key": peer_fingerprint(key.public_key()),
            "roles": ["validator"],
            "joined_epoch": 1,
        }
        for key in keys
    ]
    return derive_genesis_membership(members, policy_version=POLICY)


# --------------------------------------------------------------------- #
# Byte compatibility
# --------------------------------------------------------------------- #


def test_reference_canonical_bytes_match_the_library() -> None:
    for value in CANONICAL_CORPUS:
        assert _ref_canonical(value) == canonical_json(value)
    for body in proposal_corpus():
        assert _ref_canonical(body) == canonical_json(body)


def test_proposal_id_matches_across_implementations() -> None:
    for body in proposal_corpus():
        expected = "prop_" + hashlib.sha256(canonical_json(body)).hexdigest()[:32]
        assert derive_proposal_id(body) == expected
        assert _ref_proposal_id(body) == expected


def test_proposal_id_ignores_key_order_and_the_self_referential_id() -> None:
    body = proposal_corpus()[0]
    reversed_body = dict(reversed(list(body.items())))
    assert _ref_proposal_id(reversed_body) == _ref_proposal_id(body)
    assert _ref_proposal_id({**body, "proposal_id": "prop_" + "0" * 32}) == _ref_proposal_id(body)
    assert derive_proposal_id({**body, "proposal_id": "prop_" + "0" * 32}) == derive_proposal_id(
        body
    )


def test_membership_root_matches_across_implementations() -> None:
    genesis = make_genesis()
    projections = [genesis.to_projection()]
    projections.append(
        derive_next_membership(
            genesis,
            members=[
                {
                    "peer_id": member.peer_id,
                    "public_key": member.public_key,
                    "roles": list(member.roles),
                    "joined_epoch": member.joined_epoch,
                }
                for member in genesis.members
            ],
        ).to_projection()
    )
    for projection in projections:
        assert derive_membership_root(projection) == _ref_digest_json(projection)
        assert digest_json(projection) == _ref_digest_json(projection)


def test_record_digest_matches_across_implementations() -> None:
    genesis = make_genesis()
    records = [
        {
            "seq": 0,
            "prev": "0" * 64,
            "kind": "genesis",
            "membership_epoch": genesis.epoch,
            "membership_root": genesis.root,
            "payload_digest": digest_json({"epoch": genesis.epoch}),
            "envelope_digest": None,
            "raw_digest": None,
            "ts": 1000,
        },
        {
            "seq": 1,
            "prev": "a" * 64,
            "kind": "ballot",
            "membership_epoch": 2,
            "membership_root": "b" * 64,
            "payload_digest": digest_json({"voter": "peer_" + "c" * 32}),
            "envelope_digest": "d" * 64,
            "raw_digest": digest_hex(b"signed-wire"),
            "ts": 2000,
        },
    ]
    for projection in records:
        assert derive_record_digest(projection) == _ref_digest_json(projection)


def test_eligible_set_digest_matches_across_implementations() -> None:
    eligible = ["peer_" + "a" * 32, "peer_" + "b" * 32]
    members = [
        {
            "peer_id": None,
            "consumer_id": "consumer_" + "c" * 32,
            "kind": "human-consumer",
            "role": "operator",
        },
        {"peer_id": "peer_" + "a" * 32, "consumer_id": None, "kind": "peer", "role": "provider"},
    ]
    expected = _ref_digest_json(
        {
            "eligible": sorted(eligible),
            "affected": sorted(
                [
                    {
                        "member_id": "consumer_" + "c" * 32,
                        "kind": "human-consumer",
                        "role": "operator",
                    },
                    {"member_id": "peer_" + "a" * 32, "kind": "peer", "role": "provider"},
                ],
                key=lambda entry: entry["member_id"],
            ),
        }
    )
    assert derive_eligible_set_digest(eligible, members) == expected
