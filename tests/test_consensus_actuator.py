"""Hermetic actuator-side grant enforcement tests."""

from __future__ import annotations

from agency.consensus.actuator import Actuator
from tests.test_consensus_grants import BFT_TIME_MS, consumers, setup4


def _make_grant():
    ids, _mem, ledger = setup4()
    grant = ledger.issue(
        ids[0],
        proposal_id="prop_" + "a" * 32,
        task_id="task-1",
        issued_at_ms=1000,
        expires_at_ms=BFT_TIME_MS + 1000,
        affected_consumers=consumers(),
    )
    return ledger, grant


def test_actuator_runs_executor_on_valid_grant() -> None:
    ledger, grant = _make_grant()
    actuator = Actuator(ledger=ledger, bft_time_ms=BFT_TIME_MS)
    called = {"n": 0}

    def exec_fn():
        called["n"] += 1
        return "done"

    result = actuator.run(grant, exec_fn)
    assert result.ok is True
    assert result.value == "done"
    assert called["n"] == 1


def test_actuator_rejects_invalid_grant_without_running() -> None:
    ledger, grant = _make_grant()
    actuator = Actuator(ledger=ledger, bft_time_ms=grant.expires_at_ms + 1)
    called = {"n": 0}

    def exec_fn():
        called["n"] += 1
        return "done"

    result = actuator.run(grant, exec_fn)
    assert result.ok is False
    assert called["n"] == 0


def test_actuator_replay_rejected_after_first_run() -> None:
    ledger, grant = _make_grant()
    actuator = Actuator(ledger=ledger, bft_time_ms=BFT_TIME_MS)
    actuator.run(grant, lambda: "first")
    result = actuator.run(grant, lambda: "second")
    assert result.ok is False
