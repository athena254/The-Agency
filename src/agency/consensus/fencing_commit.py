"""Grant-bound fencing commit for downstream task execution (gaps.md #18).

Resource-side companion to the intake grant path (gaps.md #18 route swap):
HTTP intake verifies a §12 grant, consumes its nonce
(:func:`agency.consensus.grant_guard.enforce_grant`) and stamps the verified
grant digest server-side on the task record. When the admitted task later
reaches the executor, :class:`GrantFencingCommit` commits the resource-side
fencing token bound to (grant digest, task id) BEFORE the consequential
pipeline runs and records an idempotent finality receipt AFTER it, reusing
:class:`agency.consensus.fencing.FencingStateStore` unchanged.

The grant object is deliberately not re-verified here: the executor holds only
the digest verified at intake, and the nonce was already consumed at the point
of use, so a second cryptographic verification would be a replay by
construction. Fencing therefore binds on the digest — an executed grant digest
replays its recorded receipt exactly once (idempotent re-execution), a digest
revoked after intake refuses the execution, and a concurrent duplicate commit
of the same digest is refused by the store's append-only receipt log (the
stale-epoch refusal). Store errors refuse the execution too.

Fail closed: every non-committed outcome must not execute; the caller surfaces
an honest FAILED outcome. Deterministic and local: no wall clock, no
randomness, no network. A crash between the execution and :meth:`finalize`
leaves the effect without a receipt — external-resource reconciliation remains
a follow-up, exactly as in :mod:`agency.consensus.fencing`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from agency.consensus.fencing import (
    FencingStateStore,
    FencingStoreError,
    FinalityReceipt,
    fencing_from_state_token,
)
from agency.consensus.grants import GrantLedger
from agency.peers.canonical import SchemaError, validate_digest, validate_string

__all__ = [
    "EXECUTED_OUTCOME_COMPLETED",
    "FencingCommit",
    "FencingCommitOutcome",
    "GrantFencingCommit",
]

#: Receipt outcome recorded when the fenced execution completed.
EXECUTED_OUTCOME_COMPLETED: Final = "completed"


class FencingCommitOutcome(str, Enum):
    """Closed set of outcomes for one downstream fencing commit.

    ``COMMITTED`` reserves the fencing token and authorizes exactly this one
    execution attempt; every other outcome is a fail-closed refusal (or the
    idempotent ``REPLAYED`` replay of an existing receipt).
    """

    COMMITTED = "committed"
    REPLAYED = "replayed"
    REVOKED = "revoked"
    STORE_ERROR = "store_error"


@dataclass(frozen=True, slots=True)
class FencingCommit:
    """What the fencing layer decided about one execution attempt.

    ``token`` is the fencing token this attempt holds when the outcome is
    ``COMMITTED``; ``receipt`` is the existing finality receipt when the
    outcome is ``REPLAYED``; ``detail`` carries the honest refusal reason.
    """

    outcome: FencingCommitOutcome
    receipt: FinalityReceipt | None = None
    token: int | None = None
    detail: str | None = None


class GrantFencingCommit:
    """Commit a fencing token per (grant digest, resource) around one execution.

    ``ledger`` supplies the revocation view; ``None`` skips the revocation
    check (the store-level idempotent replay and stale-epoch refusals still
    apply). All state lives in the :class:`FencingStateStore`, so a durable
    store makes both the token and the receipt survive restarts.
    """

    def __init__(self, *, store: FencingStateStore, ledger: GrantLedger | None = None) -> None:
        self._store = store
        self._ledger = ledger

    @property
    def receipts(self) -> tuple[FinalityReceipt, ...]:
        """Append-only receipt log in execution order."""

        return self._store.receipts

    def receipt(self, grant_digest: str) -> FinalityReceipt | None:
        """The recorded finality receipt for ``grant_digest``, if executed."""

        return self._store.receipt(grant_digest)

    async def close(self) -> None:
        """Release the underlying fencing store."""

        self._store.close()

    def begin(self, *, grant_digest: str, resource_id: str) -> FencingCommit:
        """Decide whether this attempt may execute, and reserve its token.

        Order of refusal: an already-executed digest replays its receipt
        (``REPLAYED``); a revoked digest refuses (``REVOKED``); a closed,
        corrupt or mismatched fencing store refuses (``STORE_ERROR``). Only a
        fresh digest against a consistent store commits the next token.
        """

        try:
            digest = validate_digest(grant_digest, "grant_digest")
            validate_string(resource_id, "resource_id", maximum=128)
            existing = self._store.receipt(digest)
            if existing is not None:
                return FencingCommit(FencingCommitOutcome.REPLAYED, receipt=existing)
            if self._ledger is not None and digest in self._ledger.revoked_digests:
                return FencingCommit(FencingCommitOutcome.REVOKED, detail="grant digest is revoked")
            fencing = fencing_from_state_token(self._store.token(resource_id))
            self._store.ensure_token(resource_id, fencing.expected_current)
        except (SchemaError, FencingStoreError) as exc:
            return FencingCommit(FencingCommitOutcome.STORE_ERROR, detail=str(exc))
        return FencingCommit(FencingCommitOutcome.COMMITTED, token=fencing.next_token)

    def finalize(
        self,
        *,
        grant_digest: str,
        resource_id: str,
        token: int,
        outcome: str,
    ) -> FinalityReceipt:
        """Record the idempotent finality receipt for the committed execution.

        Raises :class:`FencingStoreError` (fail closed for the caller) when the
        store is closed or another attempt already committed this grant digest
        or advanced the resource past ``token`` — the stale-epoch refusal.
        """

        receipt = FinalityReceipt(grant_digest, resource_id, token, outcome)
        self._store.commit(resource_id=resource_id, next_token=token, receipt=receipt)
        return receipt
