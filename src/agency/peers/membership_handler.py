"""Membership update and revocation record wiring (Tier 3 peer-layer).

Hermetic: no clock/network; all values caller-supplied.
"""

from __future__ import annotations

from typing import Any

from agency.consensus.grants import GrantRevocation, verify_revocation
from agency.peers.authority import PeerAuthority
from agency.peers.log import PeerLog
from agency.peers.membership import Membership


def apply_membership_update(
    authority: PeerAuthority,
    log: PeerLog,
    *,
    new_membership: Membership,
    reason_digest: str,
    epoch: int,
    ts: int,
) -> bool:
    """Apply a signed membership change to authority and append a log record."""
    try:
        authority.set_membership(new_membership)
        log.append(
            "reconciliation",
            {"membership_root": new_membership.root, "reason_digest": reason_digest},
            membership_epoch=epoch,
            membership_root=new_membership.root,
            ts=ts,
        )
    except Exception:  # noqa: BLE001
        return False
    return True


def record_revocation(
    log: PeerLog,
    *,
    grant_digest: str,
    revocation: GrantRevocation,
    pinned_issuers: Any,
    epoch: int,
    membership_root: str,
    ts: int,
) -> bool:
    """Verify a revocation and, if valid, append a REVOKED outcome record."""
    if not verify_revocation(revocation, pinned_issuers=pinned_issuers):
        return False
    try:
        log.append(
            "outcome",
            {"grant_digest": grant_digest, "status": "REVOKED"},
            membership_epoch=epoch,
            membership_root=membership_root,
            ts=ts,
        )
    except Exception:  # noqa: BLE001
        return False
    return True
