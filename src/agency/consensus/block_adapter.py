"""Wave 1 block pipeline adapter: decision -> grant -> actuator -> durable result.

Orchestrates outside the deterministic consensus core. Keeps actuation out of
apply_block/commit; reads BlockResult.decisions, enforces consumer-input
coverage, issues grants, runs the actuator, persists PeerLog records.

The minimal single-grant actuator seam lives in
:mod:`agency.consensus.engine_adapter` (``EngineAdapter``).

Optional durable dependencies (all default to ``None`` so existing callers keep
the original in-memory behaviour):
- ``fencing``: a :class:`FencingStateStore` that turns execution into a
  hash-chained :class:`FinalityReceipt` with resource-side fencing.
- ``journal``: an :class:`AssignmentJournal` that records the assignment
  lifecycle with atomic state+outbox commits and UNCERTAIN recovery.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agency.autonomy.assignment_journal import AssignmentJournal
from agency.consensus.actions import ActionOutcome, ActionRegistry, ActionSpec
from agency.consensus.actuator import ActuationResult, Actuator
from agency.consensus.app import AgencyApplication
from agency.consensus.consumer_input import ConsumerInput, check_coverage
from agency.consensus.fencing import (
    FencedActuator,
    FencingStateStore,
    fencing_from_state_token,
)
from agency.consensus.grants import ExecutionGrant, GrantLedger
from agency.peers.canonical import digest_hex, digest_json
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
        fencing: FencingStateStore | None = None,
        journal: AssignmentJournal | None = None,
    ) -> None:
        self._app = app
        self._ledger = ledger
        self._issuer = issuer
        self._membership = membership
        self._logs = logs
        self._registry = registry
        self._consumers = tuple(affected_consumers)
        self._bft_time_ms = bft_time_ms
        self._fencing = fencing
        self._journal = journal

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

            log = next(iter(self._logs.values())) if self._logs else None
            actuation: ActuationResult | None
            action_outcome: ActionOutcome | None = None
            receipt: Any | None = None
            outcome_value: Any = None

            if self._fencing is not None:
                actuation, outcome_value, receipt = self._execute_fenced(grant, spec, log)
            else:
                actuator = Actuator(
                    ledger=self._ledger,
                    bft_time_ms=self._bft_time_ms,
                    log=log,
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
                outcome_value = actuation.value

            if actuation.ok and spec is not None:
                action_outcome = ActionOutcome(True, outcome_value)

            finality_token = getattr(receipt, "fencing_token", None)
            receipt_digest = receipt.digest() if receipt is not None else None
            canonical_outcome = (
                receipt.outcome
                if receipt is not None
                else json.dumps(outcome_value, sort_keys=True, separators=(",", ":"))
            )
            result_digest = digest_json({"value": canonical_outcome})
            if log is not None:
                epoch = self._membership.epoch
                root = self._membership.root
                log.append(
                    "result",
                    {
                        "proposal_id": decision.proposal_id,
                        "grant_digest": grant.digest,
                        "task_id": (spec.task_id if spec is not None else decision.proposal_id),
                        "affected_consumers": list(affected),
                        "result_digest": result_digest,
                        "fencing_token": finality_token,
                        "receipt_digest": receipt_digest,
                    },
                    membership_epoch=epoch,
                    membership_root=root,
                    ts=self._bft_time_ms,
                )

            if self._journal is not None:
                self._record_assignment(
                    proposal_id=decision.proposal_id,
                    task_id=(spec.task_id if spec is not None else decision.proposal_id),
                    grant_digest=grant.digest,
                    result_digest=result_digest,
                    at=issued_at,
                    ok=actuation.ok,
                )

            self._app.commit()
            reports.append(
                ExecutionReport(
                    decision.proposal_id,
                    decision.outcome,
                    grant,
                    actuation,
                    set(),
                    action_outcome,
                )
            )
        return reports

    def _execute_fenced(
        self,
        grant: ExecutionGrant,
        spec: ActionSpec | None,
        log: PeerLog | None,
    ) -> tuple[ActuationResult, Any, Any | None]:
        assert self._fencing is not None
        holder: dict[str, Any] = {}

        def _fenced_executor(_grant: ExecutionGrant) -> str:
            if spec is None or not self._registry.has(spec.name):
                holder["value"] = None
                return json.dumps(None, sort_keys=True, separators=(",", ":"))
            outcome = self._registry.run(spec)
            holder["value"] = outcome.value if outcome.ok else None
            if not outcome.ok:
                return json.dumps({"error": outcome.error}, sort_keys=True, separators=(",", ":"))
            return json.dumps(outcome.value, sort_keys=True, separators=(",", ":"))

        fenced = FencedActuator(self._ledger, actuator=_fenced_executor, store=self._fencing)
        resource_id = (spec.task_id if spec is not None else grant.task_id)[:128] or grant.task_id
        current = self._fencing.token(resource_id)
        attempt = fenced.execute(
            grant,
            bft_time_ms=self._bft_time_ms,
            resource_id=resource_id,
            fencing=fencing_from_state_token(current),
        )
        receipt = attempt.receipt
        if receipt is None:
            return (
                ActuationResult(False, None, None, grant.digest),
                None,
                None,
            )
        value = holder.get("value")
        if "value" not in holder:
            try:
                value = json.loads(receipt.outcome)
            except (TypeError, ValueError):
                value = receipt.outcome
        return ActuationResult(True, None, value, grant.digest), value, receipt

    def _record_assignment(
        self,
        *,
        proposal_id: str,
        task_id: str,
        grant_digest: str,
        result_digest: str,
        at: int,
        ok: bool,
    ) -> None:
        assert self._journal is not None
        assignment_id = "asg_" + digest_hex(proposal_id.encode())[:32]
        if any(entry.assignment_id == assignment_id for entry in self._journal.outbox()):
            return
        journal_task = "task_" + digest_hex(task_id.encode())[:32]
        generations = [
            entry.body["generation"]
            for entry in self._journal.outbox()
            if entry.body.get("task_id") == journal_task
            and isinstance(entry.body.get("generation"), int)
        ]
        generation = max(generations) + 1 if generations else 0
        self._journal.assign(
            assignment_id=assignment_id,
            task_id=journal_task,
            worker_id=self._issuer.peer_id,
            generation=generation,
            at=at,
            grant_digest=grant_digest,
        )
        self._journal.transition(
            assignment_id=assignment_id,
            generation=generation,
            state="RUNNING",
            at=at,
            grant_digest=grant_digest,
        )
        if ok:
            self._journal.transition(
                assignment_id=assignment_id,
                generation=generation,
                state="COMPLETED",
                at=at,
                grant_digest=grant_digest,
                result_digest=result_digest,
            )
        else:
            self._journal.transition(
                assignment_id=assignment_id,
                generation=generation,
                state="FAILED",
                at=at,
                grant_digest=grant_digest,
            )

    def process_block(
        self,
        txs: list[bytes],
        ctx: Any,
        actions: Mapping[str, ActionSpec],
        consumer_inputs: Sequence[ConsumerInput],
    ) -> list[ExecutionReport]:
        result = self._app.finalize_block(txs, ctx)
        return self.process_result(result, ctx, actions, consumer_inputs)

    def _result_records(self) -> list[Mapping[str, Any]]:
        out: list[Mapping[str, Any]] = []
        seen: set[str] = set()
        for log in self._logs.values():
            for rec in log.records():
                if rec.kind != "result":
                    continue
                payload = rec.payload
                digest = payload.get("grant_digest") if isinstance(payload, Mapping) else None
                if not isinstance(digest, str) or digest in seen:
                    continue
                seen.add(digest)
                out.append(payload)
        return out

    def results_for(self, proposal_id: str) -> list[Mapping[str, Any]]:
        return [
            payload
            for payload in self._result_records()
            if payload.get("proposal_id") == proposal_id
        ]

    def result_for_grant(self, grant_digest: str) -> Any | None:
        if self._fencing is not None:
            return self._fencing.receipt(grant_digest)
        for payload in self._result_records():
            if payload.get("grant_digest") == grant_digest:
                return payload
        return None

    def results_for_consumer(self, consumer_id: str) -> list[Mapping[str, Any]]:
        return [
            payload
            for payload in self._result_records()
            if isinstance(payload.get("affected_consumers"), (list, tuple))
            and consumer_id in payload["affected_consumers"]
        ]
