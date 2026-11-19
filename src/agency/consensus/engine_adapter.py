"""Engine adapter: accepted grant -> one actuator execution -> durable result.

Boundary/evidence note: this is the minimal v1 actuation seam
(human -> Butler -> grant -> execution -> durable result). Verification is fully
delegated to :mod:`agency.consensus.grants` (:class:`~agency.consensus.grants.GrantLedger`
and :func:`~agency.consensus.grants.verify_at_actuator`); this adapter adds exactly
two things: a single-shot actuator call and an append-only, keyed result record the
caller can read back.

Deterministic and local: no network, no CometBFT, no config, no background work and
no wall clock. Committed time is supplied by the caller as ``bft_time_ms``. Result
durability is in-process (lifetime of the adapter); a SQLite-backed store is a
follow-up, consistent with the boundary note in :mod:`agency.consensus.grants`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from agency.consensus.grants import (
    ExecutionGrant,
    GrantLedger,
    GrantVerification,
    verify_at_actuator,
)

__all__ = ["EngineAdapter", "ExecutionAttempt", "ExecutionResult"]


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Immutable durable outcome record for one executed grant."""

    grant_digest: str
    proposal_id: str
    task_id: str
    outcome: str


@dataclass(frozen=True, slots=True)
class ExecutionAttempt:
    """What the adapter did with one grant, including its verification."""

    executed: bool
    verification: GrantVerification
    result: ExecutionResult | None


class EngineAdapter:
    """Turn one accepted grant into one actuator call and a durable result."""

    def __init__(self, ledger: GrantLedger, *, actuator: Callable[[ExecutionGrant], str]) -> None:
        self._ledger = ledger
        self._actuator = actuator
        self._results: dict[str, ExecutionResult] = {}

    @property
    def results(self) -> Mapping[str, ExecutionResult]:
        """Read-only copy of the keyed durable results."""

        return dict(self._results)

    def result(self, grant_digest: str) -> ExecutionResult | None:
        """Look up one durable result by grant digest."""

        return self._results.get(grant_digest)

    def execute(self, grant: ExecutionGrant, *, bft_time_ms: int) -> ExecutionAttempt:
        """Verify, then execute at most once and persist the outcome."""

        verification = verify_at_actuator(grant, ledger=self._ledger, bft_time_ms=bft_time_ms)
        if not verification.accepted:
            return ExecutionAttempt(False, verification, None)

        existing = self._results.get(verification.grant_digest)
        if existing is not None:
            return ExecutionAttempt(False, verification, existing)

        outcome = self._actuator(grant)
        result = ExecutionResult(
            grant_digest=verification.grant_digest,
            proposal_id=grant.proposal_id,
            task_id=grant.task_id,
            outcome=outcome,
        )
        self._results[verification.grant_digest] = result
        self._ledger.record_used(grant)
        return ExecutionAttempt(True, verification, result)
