"""Forge metrics: the forge measures itself (metrics-as-product).

Read-only operational picture computed from the BlacksmithOps job database:
state mix, first-pass rate, rework rate, blocked escalations, average
attempts, and the verdict mix. A forge without these numbers is flying
blind; the numbers come from the stores that already exist — no new
instrumentation, no dashboards.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ForgeMetrics(BaseModel):
    """Immutable read-only metrics snapshot over one job database."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total_jobs: int = Field(ge=0)
    by_state: dict[str, int] = Field(default_factory=dict)
    passed: int = Field(ge=0)
    first_pass: int = Field(ge=0)
    rework: int = Field(ge=0)
    blocked: int = Field(ge=0)
    first_pass_rate: float = Field(ge=0.0, le=1.0)
    rework_rate: float = Field(ge=0.0, le=1.0)
    avg_attempts: float = Field(ge=0.0)
    verdict_mix: dict[str, int] = Field(default_factory=dict)


_SCHEMA_TABLE = "forge_jobs"


def compute_metrics(db_path: str | Path) -> ForgeMetrics:
    """Compute :class:`ForgeMetrics` from a BlacksmithOps database (read-only)."""
    path = Path(db_path)
    if not path.is_file():
        return ForgeMetrics(
            total_jobs=0,
            by_state={},
            passed=0,
            first_pass=0,
            rework=0,
            blocked=0,
            first_pass_rate=0.0,
            rework_rate=0.0,
            avg_attempts=0.0,
            verdict_mix={},
        )
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(f"SELECT state, attempts, document FROM {_SCHEMA_TABLE}").fetchall()
    except sqlite3.OperationalError:
        # Fresh/foreign database without the jobs table: honest zeros.
        return ForgeMetrics(
            total_jobs=0,
            by_state={},
            passed=0,
            first_pass=0,
            rework=0,
            blocked=0,
            first_pass_rate=0.0,
            rework_rate=0.0,
            avg_attempts=0.0,
            verdict_mix={},
        )
    finally:
        conn.close()

    by_state: dict[str, int] = {}
    verdict_mix: dict[str, int] = {}
    passed = first_pass = rework = blocked = 0
    attempts_sum = 0
    for state, attempts, document in rows:
        by_state[state] = by_state.get(state, 0) + 1
        attempts_sum += int(attempts or 0)
        # A PASSED job immediately transitions to AWAITING_HUMAN, so both
        # states mean "the gates passed it".
        if state in ("PASSED", "AWAITING_HUMAN"):
            passed += 1
            if int(attempts or 0) <= 1:
                first_pass += 1
            else:
                rework += 1
        if state == "BLOCKED":
            blocked += 1
        try:
            gate = json.loads(document).get("gate", "") or "-"
        except (TypeError, ValueError):
            gate = "-"
        verdict_mix[gate] = verdict_mix.get(gate, 0) + 1

    total = len(rows)
    return ForgeMetrics(
        total_jobs=total,
        by_state=by_state,
        passed=passed,
        first_pass=first_pass,
        rework=rework,
        blocked=blocked,
        first_pass_rate=(first_pass / passed) if passed else 0.0,
        rework_rate=(rework / passed) if passed else 0.0,
        avg_attempts=(attempts_sum / total) if total else 0.0,
        verdict_mix=verdict_mix,
    )


def format_metrics(metrics: ForgeMetrics) -> str:
    """Render the human-readable metrics table."""
    lines = [f"Forge jobs: {metrics.total_jobs}"]
    for state, count in sorted(metrics.by_state.items()):
        lines.append(f"  {state}: {count}")
    if metrics.passed:
        lines.append(
            f"  first-pass rate: {metrics.first_pass_rate:.0%}"
            f" ({metrics.first_pass}/{metrics.passed} passed)"
        )
        lines.append(f"  rework rate: {metrics.rework_rate:.0%}")
    lines.append(f"  avg attempts: {metrics.avg_attempts:.2f}")
    if metrics.blocked:
        lines.append(f"  blocked (escalated to human): {metrics.blocked}")
    if metrics.verdict_mix:
        mix = ", ".join(f"{gate}: {n}" for gate, n in sorted(metrics.verdict_mix.items()))
        lines.append(f"  verdict mix: {mix}")
    return "\n".join(lines)


__all__ = ["ForgeMetrics", "compute_metrics", "format_metrics"]
