"""Chain-head anchoring step (Tier 3 peer-layer wiring).

Hermetic: no clock/network. Uses the log's current head digest.
"""

from __future__ import annotations

from agency.peers.log import PeerLog, chain_head_record
from agency.peers.membership import Membership


def build_chain_head_payload(log: PeerLog, *, peer_id: str) -> dict[str, object] | None:
    """Return the unsigned chain-head payload for the current head."""
    records = log.records()
    if not records:
        return None
    return chain_head_record(peer_id, records[-1])


def anchor_chain_head(
    log: PeerLog,
    *,
    peer_id: str,
    membership: Membership,
    ts: int,
) -> int | None:
    """Append a recovery record anchoring the current chain head; return seq."""
    payload = build_chain_head_payload(log, peer_id=peer_id)
    if payload is None:
        return None
    try:
        rec = log.append(
            "recovery",
            payload,
            membership_epoch=membership.epoch,
            membership_root=membership.root,
            ts=ts,
        )
    except Exception:  # noqa: BLE001
        return None
    return rec.seq
