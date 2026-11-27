"""Loop health: regression-aware reading of the forge's own metrics.

``metrics.py`` already reports the raw numbers (first-pass rate, rework rate,
blocked jobs, average attempts). This module turns those numbers into a
decision-ready *health* result: it carries the key rates forward and, when a
baseline is supplied, recommends halting a loop that is regressing beyond
bounded tolerances. It is pure — no state changes, no instrumention, no new
data source. Missing data yields honest caveats and no halt recommendation.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agency.forge.metrics import ForgeMetrics, compute_metrics


class LoopHealthPolicy(BaseModel):
    """Bounded tolerances for what counts as a regression."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_first_pass_drop: float = Field(default=0.10, ge=0.0, le=1.0)
    max_rework_rise: float = Field(default=0.10, ge=0.0, le=1.0)
    max_blocked_increase: int = Field(default=1, ge=0)
    max_attempts_rise: float = Field(default=0.5, ge=0.0)


DEFAULT_POLICY = LoopHealthPolicy()


class LoopHealth(BaseModel):
    """Read-only health snapshot with an optional halt recommendation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total_jobs: int
    first_pass_rate: float
    rework_rate: float
    blocked_escalations: int
    avg_attempts: float
    halt_recommended: bool
    halt_reasons: tuple[str, ...]
    caveats: tuple[str, ...]

    def render(self) -> str:
        """One-line, decision-ready summary for ``forge metrics``."""
        halt = "HALT" if self.halt_recommended else "continue"
        reasons = ",".join(self.halt_reasons) or "-"
        caveats = ",".join(self.caveats) or "-"
        return (
            f"loop_health: halt_recommended={str(self.halt_recommended).lower()} "
            f"({halt}) reasons={reasons} first_pass_rate={self.first_pass_rate:.2f} "
            f"rework_rate={self.rework_rate:.2f} blocked_escalations={self.blocked_escalations} "
            f"avg_attempts={self.avg_attempts:.2f} caveats={caveats}"
        )


def assess_loop_health(
    current: ForgeMetrics,
    baseline: ForgeMetrics | None = None,
    policy: LoopHealthPolicy = DEFAULT_POLICY,
) -> LoopHealth:
    """Derive :class:`LoopHealth` from two read-only metric snapshots.

    A halt is recommended only when there is data *and* a baseline to regress
    against. Without either, the result carries a caveat and no recommendation
    rather than guessing. ``blocked_escalations`` is the count of jobs in the
    BLOCKED state (retries/escalations that exhausted their budget).
    """
    caveats: list[str] = ["blocked_escalations=BLOCKED_state_jobs"]
    if current.total_jobs == 0:
        caveats.append("no_jobs")
    if baseline is None:
        caveats.append("no_baseline")
        caveats.append("regression_not_evaluated")

    reasons: list[str] = []
    if baseline is not None and current.total_jobs > 0:
        if baseline.first_pass_rate - current.first_pass_rate > policy.max_first_pass_drop:
            reasons.append("first_pass_rate_dropped")
        if current.rework_rate - baseline.rework_rate > policy.max_rework_rise:
            reasons.append("rework_rate_rose")
        if current.blocked - baseline.blocked > policy.max_blocked_increase:
            reasons.append("blocked_escalations_increased")
        if current.avg_attempts - baseline.avg_attempts > policy.max_attempts_rise:
            reasons.append("avg_attempts_rose")

    return LoopHealth(
        total_jobs=current.total_jobs,
        first_pass_rate=current.first_pass_rate,
        rework_rate=current.rework_rate,
        blocked_escalations=current.blocked,
        avg_attempts=current.avg_attempts,
        halt_recommended=bool(reasons),
        halt_reasons=tuple(reasons),
        caveats=tuple(caveats),
    )


def compute_loop_health(
    db_path: str | Path,
    *,
    baseline: ForgeMetrics | None = None,
    policy: LoopHealthPolicy = DEFAULT_POLICY,
) -> LoopHealth:
    """Read a job database and return loop health (query only, no writes).

    An optional ``baseline`` snapshot enables regression detection; callers
    that have no previous snapshot still get an honest, caveat-bearing result.
    """
    return assess_loop_health(compute_metrics(db_path), baseline=baseline, policy=policy)
