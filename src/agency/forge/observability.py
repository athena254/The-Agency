"""Small, honest job traces: phase timings and reported-only cost signals."""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field


class PhaseTiming(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    duration_s: float = Field(ge=0)


class JobTrace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str = Field(min_length=1)
    phases: tuple[PhaseTiming, ...] = ()
    # Both are None unless the worker actually reported a value. Forge never
    # derives or invents token counts, durations or prices.
    agent_duration_s: float | None = Field(default=None, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    caveats: tuple[str, ...] = ()

    @property
    def total_s(self) -> float:
        return round(sum(p.duration_s for p in self.phases), 3)


def _reported_number(value: object, *, places: int) -> float | None:
    """Coerce a *reported* non-negative number, or None if absent/invalid."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return round(number, places)


def make_trace(
    job_id: str,
    timings: dict[str, float],
    caveats: tuple[str, ...] = (),
    *,
    agent_duration_s: float | None = None,
    estimated_cost_usd: float | None = None,
) -> JobTrace:
    """Build a trace; optional cost/duration are carried only when reported."""
    phases = tuple(
        PhaseTiming(name=name, duration_s=round(max(0.0, float(value)), 3))
        for name, value in sorted(timings.items())
    )
    reported_agent = _reported_number(agent_duration_s, places=3)
    reported_cost = _reported_number(estimated_cost_usd, places=6)
    trace_caveats = list(caveats)
    if reported_agent is None:
        trace_caveats.append("agent_duration_not_reported")
    if reported_cost is None:
        trace_caveats.append("estimated_cost_not_reported")
    return JobTrace(
        job_id=job_id,
        phases=phases,
        agent_duration_s=reported_agent,
        estimated_cost_usd=reported_cost,
        caveats=tuple(trace_caveats),
    )
