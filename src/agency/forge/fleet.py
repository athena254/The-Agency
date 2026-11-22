"""Forge fleet: one dispatch, several kinds of worker.

The forge must not depend on a single provider's mood. A FleetRunner walks an
ordered chain of runners (in-process apprentice, opencode striker, hermes
striker, ...) and fails over ONLY on infrastructure failures — a provider
being down, timing out, or rate-limited. Genuine build failures (failing
tests, review findings) are verdicts, not infrastructure: failing over would
just burn a second worker on the same defect. The chain, the failover reason
and the producing runner are recorded honestly.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import structlog

from agency.forge.external_agent import ExternalAgentReport

logger = structlog.get_logger(__name__)

# Substrings that mark an INFRASTRUCTURE failure (provider/capacity), not a
# verdict about the code. Matched case-insensitively against failure_detail.
_INFRA_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"llm call failed", re.IGNORECASE),
    re.compile(r"no apprentice llm configured", re.IGNORECASE),
    re.compile(r"review_llm_call_failed", re.IGNORECASE),
    re.compile(r"\b503\b"),
    re.compile(r"\b429\b"),
    re.compile(r"\b502\b"),
    re.compile(r"econnrefused", re.IGNORECASE),
    re.compile(r"etimedout|timeout(ed)?\b", re.IGNORECASE),
    re.compile(r"free usage limit", re.IGNORECASE),
    re.compile(r"rate.?limit", re.IGNORECASE),
    re.compile(r"failed to launch (agent|cli)", re.IGNORECASE),
)


def is_infrastructure_failure(report: ExternalAgentReport) -> bool:
    """True when the report's failure smells like provider/capacity trouble."""
    if report.gate != "FAIL":
        return False
    haystack = f"{report.failure_detail} {report.stderr[-400:] if report.stderr else ''}"
    return any(marker.search(haystack) for marker in _INFRA_MARKERS)


class FleetRunner:
    """Callable with the striker-runner signature; walks its chain in order."""

    def __init__(self, runners: list[tuple[str, Callable[..., ExternalAgentReport]]]) -> None:
        if not runners:
            raise ValueError("FleetRunner needs at least one runner")
        self.runners = list(runners)
        self.uses: list[str] = []

    def __call__(self, repo_root: str, command: list[str], task: str, **kwargs: Any) -> ExternalAgentReport:
        last: ExternalAgentReport | None = None
        last_error: Exception | None = None
        for name, runner in self.runners:
            try:
                report = runner(repo_root, command, task, **kwargs)
            except Exception as exc:  # noqa: BLE001 - infra failures fail over honestly
                logger.warning("forge.fleet_runner_raised", runner=name, error_class=type(exc).__name__)
                last_error = exc
                continue
            self.uses.append(name)
            if report.gate == "PASS":
                logger.info("forge.fleet_verdict", runner=name, gate="PASS")
                return report
            last = report
            # Fail over on infrastructure trouble, and also when a worker
            # produced NOTHING: an empty change set means the worker could not
            # work (provider down, launch failure), not that the code is bad.
            if is_infrastructure_failure(report) or not report.changed_paths:
                logger.warning(
                    "forge.fleet_failover",
                    runner=name,
                    reason="infra" if is_infrastructure_failure(report) else "no-changes",
                    detail=report.failure_detail[:200],
                )
                continue
            # A genuine verdict: tests failed or the reviewer rejected the code.
            # Another worker would only re-earn the same verdict.
            logger.info("forge.fleet_verdict", runner=name, gate="FAIL", final=True)
            return report
        if last is not None:
            return last
        raise RuntimeError(f"every fleet runner raised: {last_error!r}") from last_error


__all__ = ["FleetRunner", "is_infrastructure_failure"]
