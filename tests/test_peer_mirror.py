"""Read-only legacy mirror tests (§18 step 3).

A passing test means a legacy proposal/ballot was copied into the new log as an
explicitly unauthorized observation. It never means the artifact was admitted,
counted as a ballot, bound to a membership, or allowed to authorize anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.lattice.models import ConsensusProposal, Vote
from agency.peer_envelope import peer_fingerprint, peer_id
from agency.peers.adapter import MESSAGE_KIND_RECORD_KIND
from agency.peers.canonical import SchemaError, digest_json, validate_peer_id
from agency.peers.log import ZERO_DIGEST, PeerLog
from agency.peers.membership import derive_genesis_membership
from agency.peers.mirror import (
    MIRROR_AUTHORITY,
    MIRROR_MEMBERSHIP_EPOCH,
    MIRROR_RECORD_KIND,
    MIRROR_SOURCE,
    LegacyMirror,
    MirrorError,
)
from agency.peers.redaction import REDACTED

POLICY = "peer-policy/1"
PEER = "peer_" + "a" * 32


def make_keys(count: int = 4) -> list[Ed25519PrivateKey]:
    return [Ed25519PrivateKey.generate() for _ in range(count)]


def make_genesis():
    keys = make_keys()
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


def open_mirror_log(path: Path, genesis) -> PeerLog:
    log = PeerLog(
        path,
        peer_id=PEER,
        genesis={
            "epoch": genesis.epoch,
            "membership_root": genesis.root,
            "policy_version": genesis.policy_version,
        },
        expected_genesis_root=genesis.root,
    )
    log.open()
    return log


def legacy_proposal(**overrides: Any) -> dict[str, Any]:
    proposal: dict[str, Any] = {
        "proposal_id": "legacy-1",
        "proposer_id": "user",
        "proposal_type": "spawn_agent",
        "quorum_required": 0.5,
        "votes": [],
        "status": "open",
        "expires_at": "2026-01-01T00:00:00+00:00",
    }
    proposal.update(overrides)
    return proposal


def legacy_vote(**overrides: Any) -> dict[str, Any]:
    vote: dict[str, Any] = {
        "voter_id": "user",
        "proposal_id": "legacy-1",
        "decision": "approve",
        "evidence": [],
        "weight": 1.0,
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    vote.update(overrides)
    return vote


def test_mirror_proposal_writes_an_unauthorized_observation(tmp_path: Path) -> None:
    genesis = make_genesis()
    log = open_mirror_log(tmp_path / "peer.db", genesis)
    try:
        mirror = LegacyMirror(log)
        record = mirror.mirror_proposal(legacy_proposal(), ts=5000)
        assert record.kind == MIRROR_RECORD_KIND == "mirror"
        assert record.seq == 1
        assert record.ts == 5000
        assert record.envelope_digest is None
        assert record.raw_digest is None
        payload = record.payload
        assert payload["source"] == MIRROR_SOURCE == "legacy-governance"
        assert payload["mirror"] is True
        assert payload["authority"] == MIRROR_AUTHORITY == "none"
        assert payload["artifact_type"] == "proposal"
        assert payload["membership_bound"] is False
        assert payload["artifact"]["proposal_id"] == "legacy-1"
        assert payload["legacy_digest"] == digest_json(dict(payload["artifact"]))
        assert log.verify().ok
    finally:
        log.close()


def test_mirror_ballot_writes_an_unauthorized_observation(tmp_path: Path) -> None:
    genesis = make_genesis()
    log = open_mirror_log(tmp_path / "peer.db", genesis)
    try:
        mirror = LegacyMirror(log)
        record = mirror.mirror_ballot(legacy_vote())
        payload = record.payload
        assert record.kind == "mirror"
        assert payload["artifact_type"] == "ballot"
        assert payload["authority"] == "none"
        assert payload["artifact"]["voter_id"] == "user"
        assert payload["artifact"]["decision"] == "approve"
        assert payload["legacy_digest"] == digest_json(dict(payload["artifact"]))
        assert log.verify().ok
    finally:
        log.close()


def test_mirror_accepts_legacy_dataclass_instances(tmp_path: Path) -> None:
    genesis = make_genesis()
    log = open_mirror_log(tmp_path / "peer.db", genesis)
    try:
        mirror = LegacyMirror(log)
        vote = Vote(
            voter_id="user",
            proposal_id="legacy-1",
            decision="deny",
            evidence=["looked wrong"],
        )
        proposal = ConsensusProposal(
            proposal_id="legacy-1",
            proposer_id="user",
            proposal_type="spawn_agent",
            votes=[vote],
        )
        record = mirror.mirror_proposal(proposal)
        artifact = record.payload["artifact"]
        assert artifact["proposal_id"] == "legacy-1"
        # Floats become deterministic text; canonical JSON is integer-only.
        assert artifact["quorum_required"] == "0.5"
        assert artifact["votes"][0]["weight"] == "1.0"
        assert artifact["expires_at"].endswith("+00:00")

        ballot = mirror.mirror_ballot(vote)
        assert ballot.payload["artifact"]["evidence"] == ["looked wrong"]
        assert ballot.payload["legacy_digest"] == digest_json(dict(ballot.payload["artifact"]))
        assert log.verify().ok
    finally:
        log.close()


def test_mirror_records_are_deterministic_citations(tmp_path: Path) -> None:
    genesis = make_genesis()
    log = open_mirror_log(tmp_path / "peer.db", genesis)
    try:
        mirror = LegacyMirror(log)
        first = mirror.mirror_proposal(legacy_proposal(), ts=1000)
        second = mirror.mirror_proposal(legacy_proposal(), ts=1000)
        assert first.payload["legacy_digest"] == second.payload["legacy_digest"]
        assert first.seq == 1 and second.seq == 2
        assert first.digest != second.digest
        assert log.verify().ok
    finally:
        log.close()


def test_mirror_redacts_legacy_content(tmp_path: Path) -> None:
    genesis = make_genesis()
    log = open_mirror_log(tmp_path / "peer.db", genesis)
    try:
        mirror = LegacyMirror(log)
        record = mirror.mirror_ballot(
            legacy_vote(evidence=["contact user@example.com before acting"])
        )
        evidence = record.payload["artifact"]["evidence"]
        assert evidence == ["contact " + REDACTED + " before acting"]
        assert "user@example.com" not in repr(record.payload)
    finally:
        log.close()


def test_mirror_never_claims_a_membership_binding(tmp_path: Path) -> None:
    genesis = make_genesis()
    log = open_mirror_log(tmp_path / "peer.db", genesis)
    try:
        mirror = LegacyMirror(log)
        record = mirror.mirror_proposal(legacy_proposal())
        assert record.membership_epoch == MIRROR_MEMBERSHIP_EPOCH == 0
        assert record.membership_root == ZERO_DIGEST
        assert genesis.root != ZERO_DIGEST
        assert record.payload["membership_bound"] is False
        assert record.payload["authority"] == "none"
    finally:
        log.close()


def test_mirror_refuses_unknown_artifacts(tmp_path: Path) -> None:
    genesis = make_genesis()
    log = open_mirror_log(tmp_path / "peer.db", genesis)
    try:
        mirror = LegacyMirror(log)
        with pytest.raises(MirrorError):
            mirror.mirror_proposal({"foo": "bar"})
        with pytest.raises(MirrorError):
            mirror.mirror_ballot({"proposal_id": "legacy-1"})
        with pytest.raises(MirrorError):
            mirror.mirror_proposal(object())
        with pytest.raises(MirrorError):
            mirror.mirror_proposal(legacy_proposal(quorum_required=float("nan")))
        with pytest.raises(MirrorError):
            mirror.mirror_proposal(legacy_proposal(status={"unexpected", "set"}))
        head = log.head()
        assert head is not None
        assert head.seq == 0
    finally:
        log.close()


def test_mirrored_records_are_not_protocol_records(tmp_path: Path) -> None:
    genesis = make_genesis()
    log = open_mirror_log(tmp_path / "peer.db", genesis)
    try:
        mirror = LegacyMirror(log)
        proposal_record = mirror.mirror_proposal(legacy_proposal())
        ballot_record = mirror.mirror_ballot(legacy_vote())

        kinds = [record.kind for record in log.records()]
        assert kinds == ["genesis", "mirror", "mirror"]
        assert "proposal" not in kinds and "ballot" not in kinds
        # The adapter can never produce a mirror record, and the mirror can
        # never produce a protocol record.
        assert "mirror" not in MESSAGE_KIND_RECORD_KIND
        # Legacy labels are not peer identities; the mirror does not mint them.
        with pytest.raises(SchemaError):
            validate_peer_id(proposal_record.payload["artifact"]["proposer_id"])
        with pytest.raises(SchemaError):
            validate_peer_id(ballot_record.payload["artifact"]["voter_id"])
        assert log.verify().ok
    finally:
        log.close()
