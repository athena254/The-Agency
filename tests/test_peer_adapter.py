"""Per-peer log adapter tests (§18 steps 2–3).

A passing test means the adapter wrote a verified message into the local log
and refused everything else. It never means the message was admitted, counted,
or authorized.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.peer_envelope import peer_fingerprint, peer_id
from agency.peers.adapter import MESSAGE_KIND_RECORD_KIND, PeerLogAdapter, PeerLogAdapterError
from agency.peers.canonical import digest_hex, digest_json
from agency.peers.envelope import (
    EnvelopeBindingError,
    PeerMessage,
    build_envelope,
    envelope_digest,
    verify_message,
)
from agency.peers.identities import PeerIdentity
from agency.peers.log import LogIntegrityError, PeerLog
from agency.peers.membership import derive_genesis_membership
from agency.peers.redaction import REDACTED

POLICY = "peer-policy/1"
ROOT = "a" * 64
TABLE = "peer_log"


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


def make_genesis():
    return derive_genesis_membership(make_members(make_keys()), policy_version=POLICY)


def genesis_payload(genesis) -> dict[str, Any]:
    return {
        "epoch": genesis.epoch,
        "membership_root": genesis.root,
        "policy_version": genesis.policy_version,
    }


def open_log(path: Path, genesis, peer_id: str) -> PeerLog:
    log = PeerLog(
        path,
        peer_id=peer_id,
        genesis=genesis_payload(genesis),
        expected_genesis_root=genesis.root,
    )
    log.open()
    return log


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
                }
            ],
            "derivation": "graph:DEPENDS_ON@epoch",
        },
        "policy_version": POLICY,
        "evidence_manifest": [],
        "idempotency_key": "spawn_agent:agent:x:uuid",
        "fencing_base": 0,
        "expires_at_ms": 2000,
        "supersedes": None,
    }
    body.update(overrides)
    return body


def make_ballot_body(voter: str, proposal_id: str) -> dict[str, Any]:
    return {
        "proposal_id": proposal_id,
        "proposal_version": 1,
        "voter_peer_id": voter,
        "decision": "approve",
        "affected_role": "peer-voter",
        "evidence_cited": [],
        "human_input_ref": None,
        "ballot_seq": 1,
        "expires_at_ms": 2000,
    }


def make_checks() -> dict[str, Any]:
    return {
        "transport_check": lambda envelope: True,
        "membership_check": lambda envelope, key: True,
        "replay_check": lambda envelope: True,
        "expiry_check": lambda envelope: True,
    }


def sign(
    sender: PeerIdentity,
    kind: str,
    payload: dict[str, Any],
    *,
    monotonic_seq: int = 1,
    correlation_id: str | None = None,
) -> bytes:
    return build_envelope(
        kind=kind,
        sender=sender,
        membership_epoch=1,
        membership_root=ROOT,
        monotonic_seq=monotonic_seq,
        issued_at_ms=1000,
        expires_at_ms=2000,
        correlation_id=correlation_id or f"{sender.peer_id}:{monotonic_seq}",
        payload=payload,
    )


def verified(wire: bytes, key: Ed25519PrivateKey) -> PeerMessage:
    return verify_message(wire, key.public_key(), **make_checks())


def update_row(path: Path, seq: int, **changes: Any) -> None:
    assignments = ", ".join(f"{key} = ?" for key in changes)
    conn = sqlite3.connect(path)
    try:
        conn.execute(f"UPDATE {TABLE} SET {assignments} WHERE seq = ?", (*changes.values(), seq))
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------- #
# Binding
# --------------------------------------------------------------------- #


def test_adapter_binds_identity_to_a_log_owned_by_that_peer(tmp_path: Path) -> None:
    keys = make_keys(2)
    genesis = make_genesis()
    owner = PeerIdentity.from_private_key(keys[0])
    log = open_log(tmp_path / "peer.db", genesis, owner.peer_id)
    try:
        adapter = PeerLogAdapter(owner, log)
        assert adapter.identity is owner
        assert adapter.log is log

        other = PeerIdentity.from_private_key(keys[1])
        with pytest.raises(PeerLogAdapterError):
            PeerLogAdapter(other, log)
        assert MESSAGE_KIND_RECORD_KIND["ballot"] == "ballot"
    finally:
        log.close()


# --------------------------------------------------------------------- #
# Verified writes
# --------------------------------------------------------------------- #


def test_append_verified_writes_a_record_bound_to_the_signed_message(tmp_path: Path) -> None:
    keys = make_keys(2)
    genesis = make_genesis()
    owner = PeerIdentity.from_private_key(keys[0])
    sender = PeerIdentity.from_private_key(keys[1])
    log = open_log(tmp_path / "peer.db", genesis, owner.peer_id)
    try:
        adapter = PeerLogAdapter(owner, log)
        body = make_proposal_body(sender.peer_id)
        body["proposal_id"] = "prop_" + "0" * 32
        message = verified(sign(sender, "proposal", body), keys[1])

        record = adapter.append_verified(message, ts=5000)
        assert record.seq == 1
        assert record.kind == "proposal"
        assert record.prev  # chained to genesis
        assert record.prev != "0" * 64
        assert record.membership_epoch == 1
        assert record.membership_root == ROOT
        assert record.envelope_digest == message.envelope_digest
        assert record.raw_digest == digest_hex(message.wire)
        assert record.payload_digest == digest_json(dict(message.payload))
        assert record.payload["proposal_id"] == body["proposal_id"]
        assert record.ts == 5000
        assert log.verify().ok
        assert log.head() == record
    finally:
        log.close()


def test_append_verified_redacts_content_but_keeps_the_signed_binding(tmp_path: Path) -> None:
    keys = make_keys(2)
    genesis = make_genesis()
    owner = PeerIdentity.from_private_key(keys[0])
    sender = PeerIdentity.from_private_key(keys[1])
    log = open_log(tmp_path / "peer.db", genesis, owner.peer_id)
    try:
        adapter = PeerLogAdapter(owner, log)
        body = make_proposal_body(sender.peer_id, idempotency_key="sk-abcdefghijklmnop1234")
        body["proposal_id"] = "prop_" + "0" * 32
        message = verified(sign(sender, "proposal", body), keys[1])

        record = adapter.append_verified(message)
        # The stored payload is redacted; the signed binding is untouched.
        assert record.payload["idempotency_key"] == REDACTED
        assert record.payload["proposal_id"] == body["proposal_id"]
        assert record.envelope_digest == message.envelope_digest
        assert record.raw_digest == digest_hex(message.wire)
        assert record.payload_digest == digest_json(dict(record.payload))
        assert record.payload_digest != digest_json(dict(message.payload))
        assert "sk-abcdefghijklmnop1234" not in repr(record.payload)
    finally:
        log.close()


def test_append_verified_refuses_heartbeats(tmp_path: Path) -> None:
    keys = make_keys(2)
    genesis = make_genesis()
    owner = PeerIdentity.from_private_key(keys[0])
    sender = PeerIdentity.from_private_key(keys[1])
    log = open_log(tmp_path / "peer.db", genesis, owner.peer_id)
    try:
        adapter = PeerLogAdapter(owner, log)
        message = verified(sign(sender, "heartbeat", {"note": "alive"}), keys[1])
        with pytest.raises(PeerLogAdapterError):
            adapter.append_verified(message)
        head = log.head()
        assert head is not None
        assert head.seq == 0
        assert "heartbeat" not in MESSAGE_KIND_RECORD_KIND
    finally:
        log.close()


def test_append_verified_refuses_a_hand_built_unverified_message(tmp_path: Path) -> None:
    keys = make_keys(1)
    genesis = make_genesis()
    owner = PeerIdentity.from_private_key(keys[0])
    log = open_log(tmp_path / "peer.db", genesis, owner.peer_id)
    try:
        adapter = PeerLogAdapter(owner, log)
        forged = PeerMessage(envelope={"kind": "proposal"}, payload={}, wire=b"not-signed")
        with pytest.raises(PeerLogAdapterError):
            adapter.append_verified(forged)
        head = log.head()
        assert head is not None
        assert head.seq == 0
    finally:
        log.close()


def test_append_verified_refuses_a_self_consistent_message_with_a_bad_root(
    tmp_path: Path,
) -> None:
    keys = make_keys(2)
    genesis = make_genesis()
    owner = PeerIdentity.from_private_key(keys[0])
    sender = PeerIdentity.from_private_key(keys[1])
    log = open_log(tmp_path / "peer.db", genesis, owner.peer_id)
    try:
        adapter = PeerLogAdapter(owner, log)
        body = make_proposal_body(sender.peer_id)
        body["proposal_id"] = "prop_" + "0" * 32
        message = verified(sign(sender, "proposal", body), keys[1])

        # Recompute the envelope digest so the tampered message is internally
        # consistent; only the adapter's own field validation can refuse it.
        tampered = dict(message.envelope)
        tampered["membership_root"] = "not-a-digest"
        tampered["envelope_digest"] = envelope_digest(tampered)
        forged = PeerMessage(envelope=tampered, payload=dict(message.payload), wire=message.wire)
        with pytest.raises(PeerLogAdapterError):
            adapter.append_verified(forged)
        head = log.head()
        assert head is not None
        assert head.seq == 0
    finally:
        log.close()


def test_verify_and_append_is_the_single_live_entry_point(tmp_path: Path) -> None:
    keys = make_keys(2)
    genesis = make_genesis()
    owner = PeerIdentity.from_private_key(keys[0])
    sender = PeerIdentity.from_private_key(keys[1])
    log = open_log(tmp_path / "peer.db", genesis, owner.peer_id)
    try:
        adapter = PeerLogAdapter(owner, log)
        body = make_proposal_body(sender.peer_id)
        body["proposal_id"] = "prop_" + "0" * 32
        wire = sign(sender, "proposal", body)

        record = adapter.verify_and_append(wire, keys[1].public_key(), **make_checks())
        assert record.envelope_digest == verified(wire, keys[1]).envelope_digest
        assert log.verify().ok

        failing = make_checks()
        failing["replay_check"] = lambda envelope: False
        with pytest.raises(EnvelopeBindingError):
            adapter.verify_and_append(wire, keys[1].public_key(), **failing)
        head = log.head()
        assert head is not None
        assert head.seq == 1
    finally:
        log.close()


def test_membership_message_is_logged_as_a_proposal_record(tmp_path: Path) -> None:
    keys = make_keys(2)
    genesis = make_genesis()
    owner = PeerIdentity.from_private_key(keys[0])
    sender = PeerIdentity.from_private_key(keys[1])
    log = open_log(tmp_path / "peer.db", genesis, owner.peer_id)
    try:
        adapter = PeerLogAdapter(owner, log)
        message = verified(sign(sender, "membership", genesis.to_payload()), keys[1])
        record = adapter.append_verified(message)
        assert record.kind == MESSAGE_KIND_RECORD_KIND["membership"] == "proposal"
        assert record.payload["epoch"] == genesis.epoch
        assert "proposal_id" not in record.payload
        assert log.verify().ok
    finally:
        log.close()


# --------------------------------------------------------------------- #
# Durability and fail-closed behavior
# --------------------------------------------------------------------- #


def test_adapter_records_survive_reopen_and_verify(tmp_path: Path) -> None:
    keys = make_keys(2)
    genesis = make_genesis()
    owner = PeerIdentity.from_private_key(keys[0])
    sender = PeerIdentity.from_private_key(keys[1])
    path = tmp_path / "peer.db"
    log = open_log(path, genesis, owner.peer_id)
    adapter = PeerLogAdapter(owner, log)
    body = make_proposal_body(sender.peer_id)
    body["proposal_id"] = "prop_" + "0" * 32
    adapter.append_verified(verified(sign(sender, "proposal", body), keys[1]))
    log.close()

    reopened = open_log(path, genesis, owner.peer_id)
    try:
        assert reopened.verify().ok
        assert [record.seq for record in reopened.records()] == [0, 1]
        assert reopened.records()[1].payload["proposal_id"] == body["proposal_id"]
    finally:
        reopened.close()


def test_adapter_refuses_to_append_to_an_unhealthy_log(tmp_path: Path) -> None:
    keys = make_keys(1)
    genesis = make_genesis()
    owner = PeerIdentity.from_private_key(keys[0])
    path = tmp_path / "peer.db"
    log = open_log(path, genesis, owner.peer_id)
    adapter = PeerLogAdapter(owner, log)
    body = make_proposal_body(owner.peer_id)
    body["proposal_id"] = "prop_" + "0" * 32
    adapter.append_verified(verified(sign(owner, "proposal", body), keys[0]))
    log.close()

    update_row(path, 1, payload='{"proposal_id": "prop_' + "0" * 32 + '"}')
    log = open_log(path, genesis, owner.peer_id)
    try:
        assert not log.is_healthy
        ballot = make_ballot_body(owner.peer_id, "prop_" + "0" * 32)
        with pytest.raises(LogIntegrityError):
            adapter.append_verified(verified(sign(owner, "ballot", ballot), keys[0]))
    finally:
        log.close()
