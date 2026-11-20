"""Authenticated peer envelope delivery over an in-process loopback channel."""

from __future__ import annotations

from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.peers.envelope import build_envelope
from agency.peers.identities import PeerIdentity
from agency.peers.membership import Membership, derive_genesis_membership
from agency.peers.transport import (
    TransportError,
    attest,
    attestation_challenge,
    receive,
    send,
    verify_attestation,
)

T0 = 1000
T1 = 2000
NONCE = "nonce-1"
POLICY = "peer-policy/1"


def make_membership(identities: list[PeerIdentity]) -> Membership:
    members = [
        {
            "peer_id": identity.peer_id,
            "public_key": identity.fingerprint,
            "roles": ["validator"],
            "joined_epoch": 1,
        }
        for identity in identities
    ]
    return derive_genesis_membership(members, policy_version=POLICY)


def heartbeat(
    sender: PeerIdentity,
    membership: Membership,
    *,
    seq: int = 1,
    expires: int = T1,
    note: str = "alive",
) -> bytes:
    return build_envelope(
        kind="heartbeat",
        sender=sender,
        membership_epoch=membership.epoch,
        membership_root=membership.root,
        monotonic_seq=seq,
        issued_at_ms=T0,
        expires_at_ms=expires,
        correlation_id="corr-1",
        payload={"note": note},
    )


@pytest.fixture
def identities() -> list[PeerIdentity]:
    return [PeerIdentity.from_private_key(Ed25519PrivateKey.generate()) for _ in range(3)]


def test_attestation_challenge_is_stable(identities: list[PeerIdentity]) -> None:
    membership = make_membership(identities)
    first = attestation_challenge(
        sender_peer_id=identities[0].peer_id,
        receiver_peer_id=identities[1].peer_id,
        membership=membership,
        nonce=NONCE,
    )
    second = attestation_challenge(
        sender_peer_id=identities[0].peer_id,
        receiver_peer_id=identities[1].peer_id,
        membership=membership,
        nonce=NONCE,
    )
    assert first == second
    assert len(first) == 64


def test_round_trip_delivery(identities: list[PeerIdentity]) -> None:
    membership = make_membership(identities)
    wire = heartbeat(identities[0], membership)
    packet = send(
        identities[0],
        receiver_peer_id=identities[1].peer_id,
        membership=membership,
        wire=wire,
        nonce=NONCE,
    )
    message = receive(
        packet,
        receiver=identities[1],
        membership=membership,
        nonce=NONCE,
        bft_time_ms=1500,
        replay_ids=set(),
    )
    assert message.wire == wire
    assert message.kind == "heartbeat"
    assert message.sender_peer_id == identities[0].peer_id


def test_forged_attestation_signature_rejected(identities: list[PeerIdentity]) -> None:
    membership = make_membership(identities)
    wire = heartbeat(identities[0], membership)
    packet = send(
        identities[0],
        receiver_peer_id=identities[1].peer_id,
        membership=membership,
        wire=wire,
        nonce=NONCE,
    )
    forged = attest(
        identities[2],
        receiver_peer_id=identities[1].peer_id,
        membership=membership,
        nonce=NONCE,
    )
    packet = replace(packet, attestation=replace(forged, sender_peer_id=identities[0].peer_id))
    with pytest.raises(TransportError):
        receive(
            packet,
            receiver=identities[1],
            membership=membership,
            nonce=NONCE,
            bft_time_ms=1500,
            replay_ids=set(),
        )


def test_attestation_bound_to_receiver(identities: list[PeerIdentity]) -> None:
    membership = make_membership(identities)
    packet = send(
        identities[0],
        receiver_peer_id=identities[2].peer_id,
        membership=membership,
        wire=heartbeat(identities[0], membership),
        nonce=NONCE,
    )
    assert (
        verify_attestation(
            packet.attestation,
            receiver_peer_id=identities[1].peer_id,
            membership=membership,
            nonce=NONCE,
        )
        is False
    )
    with pytest.raises(TransportError):
        receive(
            packet,
            receiver=identities[1],
            membership=membership,
            nonce=NONCE,
            bft_time_ms=1500,
            replay_ids=set(),
        )


def test_attestation_pins_membership_and_nonce(identities: list[PeerIdentity]) -> None:
    membership = make_membership(identities)
    other = derive_genesis_membership(
        [
            {
                "peer_id": identity.peer_id,
                "public_key": identity.fingerprint,
                "roles": ["validator"],
                "joined_epoch": 1,
            }
            for identity in identities
        ],
        policy_version="peer-policy/2",
    )
    packet = send(
        identities[0],
        receiver_peer_id=identities[1].peer_id,
        membership=membership,
        wire=heartbeat(identities[0], membership),
        nonce=NONCE,
    )
    assert (
        verify_attestation(
            packet.attestation,
            receiver_peer_id=identities[1].peer_id,
            membership=other,
            nonce=NONCE,
        )
        is False
    )
    assert (
        verify_attestation(
            packet.attestation,
            receiver_peer_id=identities[1].peer_id,
            membership=membership,
            nonce="nonce-2",
        )
        is False
    )


def test_unknown_sender_rejected(identities: list[PeerIdentity]) -> None:
    membership = make_membership(identities)
    stranger = PeerIdentity.generate()
    packet = send(
        stranger,
        receiver_peer_id=identities[1].peer_id,
        membership=make_membership([*identities, stranger]),
        wire=heartbeat(stranger, make_membership([*identities, stranger])),
        nonce=NONCE,
    )
    with pytest.raises(TransportError):
        receive(
            packet,
            receiver=identities[1],
            membership=membership,
            nonce=NONCE,
            bft_time_ms=1500,
            replay_ids=set(),
        )


def test_wire_sender_must_match_attestation(identities: list[PeerIdentity]) -> None:
    membership = make_membership(identities)
    packet = send(
        identities[0],
        receiver_peer_id=identities[1].peer_id,
        membership=membership,
        wire=heartbeat(identities[1], membership),
        nonce=NONCE,
    )
    with pytest.raises(TransportError):
        receive(
            packet,
            receiver=identities[1],
            membership=membership,
            nonce=NONCE,
            bft_time_ms=1500,
            replay_ids=set(),
        )


def test_tampered_wire_rejected(identities: list[PeerIdentity]) -> None:
    membership = make_membership(identities)
    wire = heartbeat(identities[0], membership)
    tampered = wire[:-1] + bytes([wire[-1] ^ 0x01])
    packet = send(
        identities[0],
        receiver_peer_id=identities[1].peer_id,
        membership=membership,
        wire=tampered,
        nonce=NONCE,
    )
    with pytest.raises(TransportError):
        receive(
            packet,
            receiver=identities[1],
            membership=membership,
            nonce=NONCE,
            bft_time_ms=1500,
            replay_ids=set(),
        )


def test_replay_and_expiry_rejected(identities: list[PeerIdentity]) -> None:
    membership = make_membership(identities)
    wire = heartbeat(identities[0], membership)
    packet = send(
        identities[0],
        receiver_peer_id=identities[1].peer_id,
        membership=membership,
        wire=wire,
        nonce=NONCE,
    )
    used = {f"{identities[0].peer_id}:1"}
    with pytest.raises(TransportError):
        receive(
            packet,
            receiver=identities[1],
            membership=membership,
            nonce=NONCE,
            bft_time_ms=T0 + 1,
            replay_ids=used,
        )
    with pytest.raises(TransportError):
        receive(
            packet,
            receiver=identities[1],
            membership=membership,
            nonce=NONCE,
            bft_time_ms=T1,
            replay_ids=set(),
        )
