"""Deterministic gate composition for the autonomous loop (Phase 4).

The loop driver owns the while-loop; this module supplies the default
harness-run gate. :class:`ForgeGateRunner` composes the Forge pipeline
(inspection + security + optional tests + optional heuristic review), an
optional holdout run, and an independent verifier into one frozen
:class:`~agency.autonomy.loop_driver.GateResult`.

Independence: the default :class:`AcceptanceVerifier` re-runs the item's
acceptance tests in a fresh isolated copy, separate from the pipeline's own
test step, so a pipeline bug, stale cache, or bypass cannot mask a failing
acceptance test. A future LLM verifier can implement the same ``Verifier``
protocol (``verify(worktree, item, changed_paths) -> Verdict``) and be
injected with ``ForgeGateRunner(verifier=...)``.

Holdout declaration format: ``item.description`` may contain one or more
lines of the form::

    holdout: <name> :: <shell command>

for example::

    holdout: smoke :: python -c "import sys; sys.exit(0)"

The command string is split with :func:`shlex.split` and executed in an
isolated temporary copy of the worktree by
:func:`agency.forge.holdout.run_holdout`.

This module never authorizes release. A PASS gate is evidence only.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path
from typing import Literal, Protocol

import structlog
from pydantic import BaseModel, ConfigDict

from agency.autonomy.loop_driver import GateResult
from agency.autonomy.loop_state import LoopItem
from agency.forge.holdout import MAX_HOLDOUT_TIMEOUT_S, HoldoutReport, run_holdout
from agency.forge.pipeline import ForgePipelineReport, run_pipeline
from agency.forge.test_runner import MAX_TEST_TIMEOUT_S, TestReport, run_tests

logger = structlog.get_logger(__name__)

_MAX_FEEDBACK_CHARS = 8 * 1024
_MAX_DETAIL_CHARS = 4 * 1024
_HOLDOUT_PREFIX = "holdout:"
_VOLATILE_HEADER_PREFIXES = (
    "platform ",
    "plugins:",
    "cachedir:",
    "rootdir:",
    "configfile:",
)
_DURATION_RE = re.compile(r"\b\d+(?:\.\d+)?s\b")

Gate = Literal["PASS", "FAIL"]


class Verdict(BaseModel):
    """Immutable verifier outcome. Never authorizes release."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gate: Gate
    detail: str = ""
    cost_usd: float = 0.0


class Verifier(Protocol):
    """Independent acceptance check injected into the gate."""

    def verify(self, worktree: Path, item: LoopItem, changed_paths: list[str]) -> Verdict: ...


class NoopVerifier:
    """Always PASS; for tests or explicit opt-out of independent verification."""

    def verify(self, worktree: Path, item: LoopItem, changed_paths: list[str]) -> Verdict:
        return Verdict(gate="PASS")


class AcceptanceVerifier:
    """Default deterministic independent verifier.

    Re-runs ``item.acceptance`` in a fresh isolated copy, separate from the
    pipeline's own test step. Fail-closed: only PASS/SKIP are accepted.
    """

    def __init__(self, *, test_timeout_s: int = 120) -> None:
        if test_timeout_s <= 0 or test_timeout_s > MAX_TEST_TIMEOUT_S:
            raise ValueError(f"test_timeout_s must be in (0, {MAX_TEST_TIMEOUT_S}]")
        self._test_timeout_s = test_timeout_s

    def verify(self, worktree: Path, item: LoopItem, changed_paths: list[str]) -> Verdict:
        if not item.acceptance:
            return Verdict(gate="PASS", detail="no_acceptance_criteria")
        report = run_tests(Path(worktree), list(item.acceptance), timeout_s=self._test_timeout_s)
        if report.gate in ("PASS", "SKIP"):
            return Verdict(gate="PASS", detail=f"acceptance gate={report.gate}")
        return Verdict(
            gate="FAIL",
            detail=f"acceptance gate={report.gate}: {_summarize_test_output(report)}",
        )


class ForgeGateRunner:
    """Compose pipeline + holdout + independent verifier into one GateResult."""

    def __init__(
        self,
        *,
        run_tests: bool = True,
        run_review: bool = True,
        run_holdout: bool = True,
        verifier: Verifier | None = None,
        test_timeout_s: int = 120,
        holdout_timeout_s: int = 120,
    ) -> None:
        if test_timeout_s <= 0 or test_timeout_s > MAX_TEST_TIMEOUT_S:
            raise ValueError(f"test_timeout_s must be in (0, {MAX_TEST_TIMEOUT_S}]")
        if holdout_timeout_s <= 0 or holdout_timeout_s > MAX_HOLDOUT_TIMEOUT_S:
            raise ValueError(f"holdout_timeout_s must be in (0, {MAX_HOLDOUT_TIMEOUT_S}]")
        self.run_tests = run_tests
        self.run_review = run_review
        self.run_holdout = run_holdout
        self.verifier = verifier
        self.test_timeout_s = test_timeout_s
        self.holdout_timeout_s = holdout_timeout_s

    def run(self, worktree: Path, item: LoopItem, changed_paths: list[str]) -> GateResult:
        root = Path(worktree)
        pipeline = run_pipeline(
            root,
            list(changed_paths),
            run_tests_flag=self.run_tests,
            test_paths=list(item.acceptance) or None,
            test_timeout_s=self.test_timeout_s,
            run_review_flag=self.run_review,
        )

        holdout: HoldoutReport | None = None
        scenarios = _parse_holdout_scenarios(item.description)
        if self.run_holdout and scenarios:
            holdout = run_holdout(root, scenarios, timeout_s=self.holdout_timeout_s)

        verifier = self.verifier or AcceptanceVerifier(test_timeout_s=self.test_timeout_s)
        try:
            verdict = verifier.verify(root, item, list(changed_paths))
        except Exception as exc:  # noqa: BLE001
            logger.warning("loop.gate_verifier_error", error=type(exc).__name__)
            verdict = Verdict(gate="FAIL", detail=f"verifier error: {type(exc).__name__}: {exc}")

        gate: Gate = "PASS"
        if (
            pipeline.gate != "PASS"
            or (holdout is not None and holdout.gate != "PASS")
            or verdict.gate != "PASS"
        ):
            gate = "FAIL"

        feedback = _build_feedback(pipeline, holdout, verdict)
        detail = _normalize_detail(feedback, root)[:_MAX_DETAIL_CHARS]
        signature = (
            "" if gate == "PASS" else hashlib.sha256(detail.encode("utf-8")).hexdigest()[:16]
        )
        reports = (
            (pipeline.report_id,) if holdout is None else (pipeline.report_id, holdout.report_id)
        )
        logger.info("loop.gate", item=item.id, gate=gate, reports=len(reports))
        return GateResult(
            gate=gate,
            feedback=feedback,
            reports=reports,
            error_signature=signature,
            detail=detail,
            cost_usd=verdict.cost_usd,
        )


def _parse_holdout_scenarios(description: str) -> list[dict[str, str]]:
    scenarios: list[dict[str, str]] = []
    for line in description.splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith(_HOLDOUT_PREFIX):
            continue
        body = stripped[len(_HOLDOUT_PREFIX) :].strip()
        if "::" not in body:
            continue
        name, command = body.split("::", 1)
        name = name.strip()
        command = command.strip()
        if name and command:
            scenarios.append({"name": name, "command": command})
    return scenarios


def _summarize_test_output(report: TestReport) -> str:
    parts = [f"gate={report.gate}"]
    if report.returncode is not None:
        parts.append(f"rc={report.returncode}")
    stderr = (report.stderr or "").strip()
    stdout = (report.stdout or "").strip()
    if stderr:
        parts.append("stderr:\n" + stderr[-4000:])
    if stdout:
        parts.append("stdout:\n" + stdout[-4000:])
    return "\n".join(parts)


def _build_feedback(
    pipeline: ForgePipelineReport,
    holdout: HoldoutReport | None,
    verdict: Verdict,
) -> str:
    parts: list[str] = [f"pipeline gate={pipeline.gate}"]
    if pipeline.caveats:
        parts.append("pipeline caveats: " + ", ".join(pipeline.caveats))
    if pipeline.tests is not None:
        parts.append("pipeline tests " + _summarize_test_output(pipeline.tests))
    if pipeline.review is not None:
        for finding in pipeline.review.findings:
            parts.append(
                f"review {finding.path}:{finding.line} {finding.rule_id}: {finding.message}"
            )
    if holdout is not None:
        parts.append(f"holdout gate={holdout.gate}")
        for scenario in holdout.scenarios:
            if scenario.gate == "PASS":
                continue
            output = (scenario.stderr or "").strip() or (scenario.stdout or "").strip()
            parts.append(
                f"holdout {scenario.name} gate={scenario.gate} rc={scenario.returncode}: {output}"
            )
    if verdict.detail:
        parts.append(f"verifier gate={verdict.gate}: {verdict.detail}")
    else:
        parts.append(f"verifier gate={verdict.gate}")
    return "\n".join(parts)[:_MAX_FEEDBACK_CHARS]


def _normalize_detail(text: str, root: Path) -> str:
    tmp = tempfile.gettempdir()
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.replace(str(root), "<root>").replace(tmp, "<tmp>")
        if line.strip().startswith(_VOLATILE_HEADER_PREFIXES):
            continue
        line = _DURATION_RE.sub("<t>", line).rstrip()
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


__all__ = [
    "AcceptanceVerifier",
    "ForgeGateRunner",
    "NoopVerifier",
    "Verdict",
    "Verifier",
]
