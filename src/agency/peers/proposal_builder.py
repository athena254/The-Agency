"""Build signed proposal and ballot envelopes (Tier 3 peer-layer wiring).

Hermetic: all time and sequence values are pinned caller inputs. No transport.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agency.peers.authority import PeerAuthority
from agency.peers.canonical import digest_json
from agency.peers.derive import derive_proposal_id
from agency.peers.envelope import build_envelope
from agency.peers.identities import PeerIdentity
from agency.peers.membership import Membership


def _zero_digest() -> str:
    return "0" * 64


def build_proposal_envelope(
    issuer: PeerIdentity,
    membership: Membership,
    *,
    action_type: str,
    resource: str,
    params: Mapping[str, Any],
    affected_snapshot: Mapping[str, Any] | None = None,
    policy_version: str = "peer-policy/1",
    monotonic_seq: int = 1,
    issued_at_ms: int = 1000,
    expires_at_ms: int = 9999999999999,
    supersedes: str | None = None,
    fencing_base: int = 0,
) -> bytes:
    """Build one signed ``proposal`` wire envelope with a derived proposal_id."""
    body: dict[str, Any] = {
        "proposer_peer_id": issuer.peer_id,
        "proposal_version": 1,
        "membership_epoch": membership.epoch,
        "membership_root": membership.root,
        "action": {
            "action_type": action_type,
            "resource": resource,
            "params_digest": digest_json(dict(params)),
            "params_schema_version": "v1",
        },
        "affected_snapshot": affected_snapshot
        or {
            "graph_root_digest": _zero_digest(),
            "epoch": membership.epoch,
            "membership_root": membership.root,
            "members": [],
            "derivation": "empty-affected-set",
        },
        "policy_version": policy_version,
        "evidence_manifest": [],
        "idempotency_key": f"{issuer.peer_id}:{monotonic_seq}",
        "fencing_base": fencing_base,
        "expires_at_ms": expires_at_ms,
        "supersedes": supersedes,
    }
    proposal_id = derive_proposal_id(body)
    return build_envelope(
        kind="proposal",
        sender=issuer,
        membership_epoch=membership.epoch,
        membership_root=membership.root,
        monotonic_seq=monotonic_seq,
        issued_at_ms=issued_at_ms,
        expires_at_ms=expires_at_ms,
        correlation_id=proposal_id,
        payload={"proposal_id": proposal_id, **body},
    )


def build_ballot_wire(
    authority: PeerAuthority,
    issuer: PeerIdentity,
    *,
    proposal_id: str,
    decision: str,
    monotonic_seq: int,
    ballot_seq: int | None = 1,
    issued_at_ms: int = 1000,
    expires_at_ms: int = 9999999999999,
) -> bytes:
    """Build one signed ``ballot`` wire envelope via the pinned PeerAuthority."""
    return authority.make_ballot(
        issuer,
        proposal_id,
        decision,
        proposal_version=1,
        ballot_seq=ballot_seq,
        monotonic_seq=monotonic_seq,
        issued_at_ms=issued_at_ms,
        expires_at_ms=expires_at_ms,
    )
