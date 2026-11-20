"""Actuator-side grant enforcement seam (Slice C, Wave 1).

Consumes :func:`verify_at_actuator` so a task-scoped grant is actually checked
at the point of execution. Persists intent/outcome/executed-grant records to a
PeerLog (WAL + synchronous=FULL) so a crash mid-action leaves an
UNCERTAIN/PENDING record rather than a silent success. Still protocol
foundation: no network, no wall clock, no release authorization.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from agency.consensus.grants import (
    ExecutionGrant,
    GrantLedger,
    GrantRejection,
    verify_at_actuator,
)
from agency.peers.log import PeerLog


@dataclass(frozen=True, slots=True)
class FinalityProof:
    chain_id: str
    height: int
    app_hash: str


@dataclass(frozen=True, slots=True)
class ActuationResult:
    ok: bool
    reason: GrantRejection | None
    value: Any = None
    grant_digest: str = ""
    intent_seq: int | None = None
    outcome_seq: int | None = None


class Actuator:
    """Runs an executor only after a grant verifies at the actuator boundary."""

    def __init__(
        self,
        *,
        ledger: GrantLedger,
        bft_time_ms: int,
        log: PeerLog | None = None,
        finality: FinalityProof | None = None,
        membership_epoch: int | None = None,
        membership_root: str | None = None,
    ) -> None:
        self._ledger = ledger
        self._bft_time_ms = bft_time_ms
        self._log = log
        self._finality = finality
        self._epoch = membership_epoch
        self._root = membership_root

    def run(self, grant: ExecutionGrant, executor: Callable[[], Any]) -> ActuationResult:
        # Durable idempotency: an already-executed grant is not re-run.
        if self._log is not None:
            for rec in self._log.records():
                payload = getattr(rec, "payload", None)
                if (
                    getattr(rec, "kind", None) == "executed_grant"
                    and isinstance(payload, Mapping)
                    and payload.get("grant_digest") == getattr(grant, "digest", None)
                ):
                    return ActuationResult(True, None, None, grant.digest)
        verification = verify_at_actuator(grant, ledger=self._ledger, bft_time_ms=self._bft_time_ms)
        if not verification.ok:
            return ActuationResult(False, verification.reason, None, verification.grant_digest)

        if (
            self._finality is not None
            and self._epoch is not None
            and self._root is not None
            and (grant.membership_epoch != self._epoch or grant.membership_root != self._root)
        ):
            return ActuationResult(False, GrantRejection.WRONG_EPOCH, None, verification.grant_digest)

        intent_seq: int | None = None
        if self._log is not None:
            epoch = self._epoch if self._epoch is not None else grant.membership_epoch
            root = self._root if self._root is not None else grant.membership_root
            rec = self._log.append(
                "intent",
                {
                    "grant_digest": verification.grant_digest,
                    "status": "PENDING",
                    "finality_height": self._finality.height if self._finality else None,
                },
                membership_epoch=epoch,
                membership_root=root,
                ts=self._bft_time_ms,
            )
            intent_seq = rec.seq

        try:
            value = executor()
        except Exception:  # noqa: BLE001 - fail closed
            if self._log is not None and intent_seq is not None:
                epoch = self._epoch if self._epoch is not None else grant.membership_epoch
                root = self._root if self._root is not None else grant.membership_root
                self._log.append(
                    "outcome",
                    {"grant_digest": verification.grant_digest, "status": "FAILED"},
                    membership_epoch=epoch,
                    membership_root=root,
                    ts=self._bft_time_ms,
                )
            return ActuationResult(False, None, None, verification.grant_digest, intent_seq)

        outcome_seq: int | None = None
        if self._log is not None:
            epoch = self._epoch if self._epoch is not None else grant.membership_epoch
            root = self._root if self._root is not None else grant.membership_root
            rec = self._log.append(
                "outcome",
                {"grant_digest": verification.grant_digest, "status": "EXECUTED"},
                membership_epoch=epoch,
                membership_root=root,
                ts=self._bft_time_ms,
            )
            outcome_seq = rec.seq
            self._log.append(
                "executed_grant",
                {"grant_digest": verification.grant_digest},
                membership_epoch=epoch,
                membership_root=root,
                ts=self._bft_time_ms,
            )

        self._ledger.record_used(grant)
        return ActuationResult(True, None, value, verification.grant_digest, intent_seq, outcome_seq)
