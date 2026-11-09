"""Minimal peer authority: valid ballot, replay, forgery, 3-of-4 quorum."""

import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.peer_envelope import canonical_json
from agency.peers.authority import AuthorityError, PeerAuthority, PeerRegistry
from agency.peers.identities import PeerIdentity
from agency.peers.membership import derive_genesis_membership, derive_next_membership

POLICY = "peer-policy/1"


def setup4():
    keys = [Ed25519PrivateKey.generate() for _ in range(4)]
    ids = [PeerIdentity.from_private_key(k) for k in keys]
    members = [
        {
            "peer_id": i.peer_id,
            "public_key": i.fingerprint,
            "roles": ["validator"],
            "joined_epoch": 1,
        }
        for i in ids
    ]
    mem = derive_genesis_membership(members, policy_version=POLICY)
    reg = PeerRegistry()
    for i in ids:
        reg.register(i)
    return ids, PeerAuthority(mem, reg)


def ballot(auth, ident, pid, decision, seq, ballot_seq=1):
    return auth.make_ballot(ident, pid, decision, monotonic_seq=seq, ballot_seq=ballot_seq)


def test_valid_ballot_passes():
    ids, auth = setup4()
    pid = "prop_" + "a" * 32
    wire = ballot(auth, ids[0], pid, "approve", 1)
    msg = auth.submit_ballot(wire)
    assert msg.payload["proposal_id"] == pid
    assert msg.payload["voter_peer_id"] == ids[0].peer_id
    assert msg.payload["decision"] == "approve"
    assert auth.tally(pid)["approve"] == 1
    assert auth.decide(pid) == "open"


def test_duplicate_seq_rejected():
    ids, auth = setup4()
    pid = "prop_" + "b" * 32
    auth.submit_ballot(ballot(auth, ids[0], pid, "approve", 1))
    with pytest.raises(AuthorityError):
        auth.submit_ballot(ballot(auth, ids[0], pid, "approve", 1, ballot_seq=2))
    # Duplicate ballot_seq also rejected
    with pytest.raises(AuthorityError):
        auth.submit_ballot(ballot(auth, ids[0], pid, "approve", 2, ballot_seq=1))


def test_forged_ballot_rejected():
    ids, auth = setup4()
    pid = "prop_" + "c" * 32
    wire = ballot(auth, ids[0], pid, "approve", 1)
    forged = json.loads(wire)
    forged["payload"]["decision"] = "approve"
    forged["payload"]["voter_peer_id"] = ids[1].peer_id
    with pytest.raises(AuthorityError):
        auth.submit_ballot(canonical_json(forged))
    # Wrong key / tampered signature
    bad = json.loads(wire)
    bad["signature"] = {**bad["signature"], "sig": bad["signature"]["sig"][:-4] + "AAAA"}
    with pytest.raises(AuthorityError):
        auth.submit_ballot(canonical_json(bad))
    # Nonexistent peer
    fake = PeerIdentity.generate()
    with pytest.raises(AuthorityError):
        auth.submit_ballot(auth.make_ballot(fake, pid, "approve", monotonic_seq=1))
        # fake not registered in membership either; submit before register check
    # (make_ballot registers; authority must still reject non-member)
    assert auth.decide(pid) == "open"


def test_quorum_requires_3_of_4():
    ids, auth = setup4()
    pid = "prop_" + "d" * 32
    for i in (0, 1):
        auth.submit_ballot(ballot(auth, ids[i], pid, "approve", 1))
    assert not auth.quorum_met(pid)
    assert auth.decide(pid) == "open"
    auth.submit_ballot(ballot(auth, ids[2], pid, "approve", 1))
    assert auth.quorum_met(pid)
    assert auth.decide(pid) == "passed"
    # Deny quorum path stays fail-closed for 2-of-4
    pid2 = "prop_" + "e" * 32
    for i in (0, 1):
        auth.submit_ballot(ballot(auth, ids[i], pid2, "deny", 2))
    assert auth.decide(pid2) == "open"
    auth.submit_ballot(ballot(auth, ids[2], pid2, "deny", 2))
    assert auth.decide(pid2) == "denied"


def test_epoch_bump_invalidates_old_epoch_ballots():
    ids, auth = setup4()
    mem2 = derive_next_membership(
        auth.membership,
        members=[
            {
                "peer_id": i.peer_id,
                "public_key": i.fingerprint,
                "roles": ["validator"],
                "joined_epoch": 1,
            }
            for i in ids
        ],
    )
    old_wire = ballot(auth, ids[0], "prop_" + "f" * 32, "approve", 1)
    auth.set_membership(mem2)
    assert auth.epoch == 2
    with pytest.raises(AuthorityError):
        auth.submit_ballot(old_wire)


def test_registry_sqlite_roundtrip(tmp_path):
    ids, _ = setup4()
    db = tmp_path / "registry.db"
    reg = PeerRegistry(db)
    reg.register(ids[0])
    reg.bump_seq(ids[0].peer_id, 5)
    reg.close()
    reg2 = PeerRegistry(db)
    assert reg2.get(ids[0].peer_id).fingerprint == ids[0].fingerprint
    assert reg2.last_seq(ids[0].peer_id) == 5
    reg2.close()
