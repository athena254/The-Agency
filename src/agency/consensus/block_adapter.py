"""Wave 1 block pipeline adapter: decision -> grant -> actuator -> durable result.

Orchestrates outside the deterministic consensus core. Keeps actuation out of
apply_block/commit; reads BlockResult.decisions, enforces consumer-input
coverage, issues grants, runs the actuator, persists PeerLog records.

This is the block-level pipeline. The minimal single-grant actuator seam lives
in :mod:`agency.consensus.engine_adapter` (``EngineAdapter``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agency.consensus.actions import ActionOutcome, ActionRegistry, ActionSpec
from agency.consensus.actuator import ActuationResult, Actuator
from agency.consensus.app import AgencyApplication
from agency.consensus.consumer_input import ConsumerInput, check_coverage
from agency.consensus.grants import ExecutionGrant, GrantLedger
from agency.peers.identities import PeerIdentity
from agency.peers.log import PeerLog
from agency.peers.membership import Membership

__all__ = ["BlockEngineAdapter", "ExecutionReport"]


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    proposal_id: str
    decision_outcome: str
    grant: ExecutionGrant | None
    actuation: ActuationResult | None
    consumer_inputs_missing: set[str]
    action_outcome: ActionOutcome | None


class BlockEngineAdapter:
    def __init__(
        self,
        *,
        app: AgencyApplication,
        ledger: GrantLedger,
        issuer: PeerIdentity,
        membership: Membership,
        logs: Mapping[str, PeerLog],
        registry: ActionRegistry,
        affected_consumers: Sequence[str],
        bft_time_ms: int,
    ) -> None:
        self._app = app
        self._ledger = ledger
        self._issuer = issuer
        self._membership = membership
        self._logs = logs
        self._registry = registry
        self._consumers = tuple(affected_consumers)
        self._bft_time_ms = bft_time_ms

    def process_result(
        self,
        result: Any,
        ctx: Any,
        actions: Mapping[str, ActionSpec],
        consumer_inputs: Sequence[ConsumerInput],
    ) -> list[ExecutionReport]:
        reports: list[ExecutionReport] = []
        for decision in getattr(result, "decisions", ()):
            if decision.outcome != "passed":
                reports.append(
                    ExecutionReport(decision.proposal_id, decision.outcome, None, None, set(), None)
                )
                continue

            spec = actions.get(decision.proposal_id)
            affected = spec.affected_consumers if spec is not None else self._consumers
            snapshot = {"members": [{"consumer_id": c, "kind": "human-consumer"} for c in affected]}
            covered, missing = check_coverage(consumer_inputs, snapshot)
            if not covered:
                reports.append(
                    ExecutionReport(
                        decision.proposal_id, decision.outcome, None, None, missing, None
                    )
                )
                continue

            issued_at = self._bft_time_ms
            expires_at = issued_at + 60_000
            grant = self._ledger.issue(
                self._issuer,
                proposal_id=decision.proposal_id,
                task_id=(spec.task_id if spec is not None else decision.proposal_id),
                issued_at_ms=issued_at,
                expires_at_ms=expires_at,
                affected_consumers=affected,
            )

            actuator = Actuator(
                ledger=self._ledger,
                bft_time_ms=self._bft_time_ms,
                log=next(iter(self._logs.values())) if self._logs else None,
                finality=None,
                membership_epoch=self._membership.epoch,
                membership_root=self._membership.root,
            )

            def executor(_spec: ActionSpec | None = spec) -> Any:
                if _spec is None or not self._registry.has(_spec.name):
                    return None
                outcome = self._registry.run(_spec)
                return outcome.value if outcome.ok else None

            actuation = actuator.run(grant, executor)
            action_outcome = None
            if spec is not None and actuation.ok:
                action_outcome = ActionOutcome(True, actuation.value)

            self._app.commit()
            reports.append(
                ExecutionReport(
                    decision.proposal_id, decision.outcome, grant, actuation, set(), action_outcome
                )
            )
        return reports

    def process_block(
        self,
        txs: list[bytes],
        ctx: Any,
        actions: Mapping[str, ActionSpec],
        consumer_inputs: Sequence[ConsumerInput],
    ) -> list[ExecutionReport]:
        result = self._app.finalize_block(txs, ctx)
        return self.process_result(result, ctx, actions, consumer_inputs)

    def results_for(self, proposal_id: str) -> list[Any]:
        out: list[Any] = []
        for log in self._logs.values():
            for rec in log.records():
                payload = getattr(rec, "payload", None)
                if isinstance(payload, Mapping) and payload.get("grant_digest") is not None:
                    out.append(rec)
        return out
