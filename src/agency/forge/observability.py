"""Small, honest job traces: phase timings and states."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PhaseTiming(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    duration_s: float = Field(ge=0)


class JobTrace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str = Field(min_length=1)
    phases: tuple[PhaseTiming, ...] = ()
    caveats: tuple[str, ...] = ()

    @property
    def total_s(self) -> float:
        return round(sum(p.duration_s for p in self.phases), 3)


def make_trace(job_id: str, timings: dict[str, float], caveats: tuple[str, ...] = ()) -> JobTrace:
    """Build a trace, rounding each phase and dropping negative values."""
    phases = tuple(
        PhaseTiming(name=name, duration_s=round(max(0.0, float(value)), 3))
        for name, value in sorted(timings.items())
    )
    return JobTrace(job_id=job_id, phases=phases, caveats=caveats)
