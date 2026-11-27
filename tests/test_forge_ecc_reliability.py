"""Tests for ECC reliability slice: escalation SLA and loop-health regression.

These tests exercise the two new deep modules only. They never touch the
orchestrator, metrics module or CLI — wiring belongs to the parent wave.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from agency.forge.escalation import (
    DEFAULT_POLICY as DEFAULT_ESCALATION_POLICY,
)
from agency.forge.escalation import (
    MAX_SLA_SECONDS,
    EscalationPolicy,
    evaluate_escalation,
)
from agency.forge.loop_metrics import (
    DEFAULT_POLICY as DEFAULT_LOOP_POLICY,
)
from agency.forge.loop_metrics import (
    LoopHealthPolicy,
    assess_loop_health,
    compute_loop_health,
)
from agency.forge.metrics import ForgeMetrics

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class _Job:
    """Minimal AWAITING_HUMAN-shaped subject; no dependency on orchestration."""

    job_id: str
    state: str
    updated_at: str


def _ts(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


def test_missing_updated_at_is_unknown_and_never_escalates_or_approves() -> None:
    job = _Job("j1", "AWAITING_HUMAN", "")
    result = evaluate_escalation(job, now=NOW)
    assert result.age_seconds is None
    assert result.within_sla is None
    assert result.should_escalate is False
    assert "missing_updated_at" in result.caveats
    assert "never_auto_approves" in result.caveats
    low = result.suggestion.lower()
    assert "escalat" in low
    assert "do not auto-approve" in low
    assert not hasattr(result, "approve")
    assert not hasattr(result, "state_after")


def test_within_sla_no_escalation() -> None:
    job = _Job("j2", "AWAITING_HUMAN", _ts(10))
    result = evaluate_escalation(job, now=NOW)
    assert result.within_sla is True
    assert result.should_escalate is False
    assert result.age_seconds == pytest.approx(600.0)


def test_past_sla_escalates_but_cannot_approve() -> None:
    job = _Job("j3", "AWAITING_HUMAN", _ts(1500))  # 25h > 24h SLA
    result = evaluate_escalation(job, now=NOW)
    assert result.within_sla is False
    assert result.should_escalate is True
    assert "escalat" in result.suggestion.lower()
    assert "do not auto-approve" in result.suggestion.lower()
    assert "never_auto_approves" in result.caveats


def test_future_timestamp_is_unknown_not_escalated() -> None:
    job = _Job("j4", "AWAITING_HUMAN", _ts(-10))
    result = evaluate_escalation(job, now=NOW)
    assert result.age_seconds is None
    assert result.should_escalate is False
    assert "invalid_timestamp" in result.caveats


def test_non_awaiting_state_never_escalates() -> None:
    job = _Job("j5", "FAILED", _ts(5000))
    result = evaluate_escalation(job, now=NOW)
    assert result.should_escalate is False
    assert "not_awaiting_human" in result.caveats


def test_custom_policy_changes_boundary() -> None:
    policy = EscalationPolicy(sla_seconds=60)
    job = _Job("j6", "AWAITING_HUMAN", _ts(2))
    assert evaluate_escalation(job, policy=policy, now=NOW).should_escalate is True
    assert evaluate_escalation(job, now=NOW).should_escalate is False


def test_policy_sla_is_bounded() -> None:
    with pytest.raises(ValidationError):
        EscalationPolicy(sla_seconds=0)
    with pytest.raises(ValidationError):
        EscalationPolicy(sla_seconds=MAX_SLA_SECONDS + 1)
    assert DEFAULT_ESCALATION_POLICY.sla_seconds > 0


def test_render_is_human_readable_and_carries_caveats() -> None:
    rendered = evaluate_escalation(_Job("j7", "AWAITING_HUMAN", ""), now=NOW).render()
    assert "j7" in rendered
    assert "caveats=" in rendered
    assert "never_auto_approves" in rendered


def _metrics(
    *,
    first_pass: float,
    rework: float,
    blocked: int,
    attempts: float,
    total: int = 10,
) -> ForgeMetrics:
    return ForgeMetrics(
        total_jobs=total,
        by_state={},
        passed=total,
        first_pass=int(first_pass * total),
        rework=int(rework * total),
        blocked=blocked,
        first_pass_rate=first_pass,
        rework_rate=rework,
        avg_attempts=attempts,
        verdict_mix={},
    )


def test_missing_db_is_honest_no_halt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "agency.forge.loop_metrics.compute_metrics",
        lambda *_a, **_k: _metrics(first_pass=0.0, rework=0.0, blocked=0, attempts=0.0, total=0),
    )
    health = compute_loop_health(tmp_path / "nope.db")
    assert health.total_jobs == 0
    assert health.halt_recommended is False
    assert health.halt_reasons == ()
    assert "no_jobs" in health.caveats
    assert "no_baseline" in health.caveats


def test_carries_rates_blocked_and_attempts() -> None:
    current = _metrics(first_pass=0.8, rework=0.2, blocked=3, attempts=1.6)
    health = assess_loop_health(current, baseline=None)
    assert health.first_pass_rate == 0.8
    assert health.rework_rate == 0.2
    assert health.blocked_escalations == 3
    assert health.avg_attempts == 1.6
    assert health.halt_recommended is False
    assert "no_baseline" in health.caveats


def test_regression_on_first_pass_rework_blocked_and_attempts_halts() -> None:
    baseline = _metrics(first_pass=0.95, rework=0.05, blocked=0, attempts=1.1)
    current = _metrics(first_pass=0.75, rework=0.25, blocked=3, attempts=1.8)
    health = assess_loop_health(current, baseline=baseline)
    assert health.halt_recommended is True
    assert set(health.halt_reasons) == {
        "first_pass_rate_dropped",
        "rework_rate_rose",
        "blocked_escalations_increased",
        "avg_attempts_rose",
    }


def test_improvement_does_not_halt() -> None:
    baseline = _metrics(first_pass=0.6, rework=0.4, blocked=5, attempts=2.5)
    current = _metrics(first_pass=0.9, rework=0.1, blocked=1, attempts=1.2)
    health = assess_loop_health(current, baseline=baseline)
    assert health.halt_recommended is False
    assert health.halt_reasons == ()


def test_custom_policy_thresholds() -> None:
    baseline = _metrics(first_pass=0.8, rework=0.2, blocked=0, attempts=1.0)
    current = _metrics(first_pass=0.79, rework=0.21, blocked=1, attempts=1.05)
    strict = LoopHealthPolicy(
        max_first_pass_drop=0.005,
        max_rework_rise=0.005,
        max_blocked_increase=0,
        max_attempts_rise=0.01,
    )
    health = assess_loop_health(current, baseline=baseline, policy=strict)
    assert health.halt_recommended is True
    assert DEFAULT_LOOP_POLICY.max_first_pass_drop > strict.max_first_pass_drop


def test_health_is_frozen_and_renderable() -> None:
    current = _metrics(first_pass=0.8, rework=0.2, blocked=0, attempts=1.0)
    health = assess_loop_health(current, baseline=current)
    rendered = health.render()
    assert "halt_recommended=false" in rendered
    assert "first_pass_rate=0.80" in rendered
    with pytest.raises(ValidationError):
        health.total_jobs = 99
