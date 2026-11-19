"""Point-of-use actuator guard for task-scoped execution grants (D6).

Thin enforcement seam over the existing grant verifier: a grant is accepted
once at the actuator, against the pinned membership/issuer set and a
caller-supplied committed time. No clock, network, or side-effecting resource
is touched. Resource-side fencing/idempotency, the finality proof and the
independent human attestation gate remain separate checks.
"""

from __future__ import annotations

from agency.consensus.grants import (
    ExecutionGrant,
    GrantLedger,
    GrantVerification,
    verify_at_actuator,
)

__all__ = ["enforce_grant"]


def enforce_grant(
    grant: ExecutionGrant,
    *,
    ledger: GrantLedger,
    bft_time_ms: int,
) -> GrantVerification:
    """Verify ``grant`` at the actuator and consume its nonce on success.

    All validation (attestation, signatures, authority, epoch/root, lifetime,
    expiry, replay, revocation) is delegated to :func:`verify_at_actuator` and
    the real grant types; no parallel grant model is introduced. The first
    successful call records nonce use in ``ledger``, so any later presentation
    of that nonce is rejected as a replay. A rejected grant consumes nothing.

    ``bft_time_ms`` must come from a pinned committed header, never the wall
    clock. This guard enforces verification at the point of use only: the
    downstream resource still owns atomic fencing and idempotency.
    """

    result = verify_at_actuator(grant, ledger=ledger, bft_time_ms=bft_time_ms)
    if result.ok:
        ledger.record_used(grant)
    return result
