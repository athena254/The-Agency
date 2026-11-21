"""Import reviewers: SkillSpector adapter + deterministic static fallback.

Phase 4a of PLAN_TOOL_PROVISIONING §3.6 with owner decision §8.2: every
imported package is scanned by NVIDIA SkillSpector
(``skillspector scan <path> --no-llm --format json``) and the verdict gates
registration. The reviewer never executes the scanned code — it only runs the
scanner binary in a capture subprocess and parses its JSON report.

Fail closed: scanner missing from PATH, nonzero exit, unreadable/malformed
report, or timeout all yield verdict ``UNAVAILABLE``; callers (see
:mod:`agency.tools.importing`) must refuse to register on anything but a
``PASS``. A ``PASS`` requires score >= 80 and zero critical/high findings.

``StaticFallbackReviewer`` implements the same :class:`ToolReviewer` protocol
with pure text checks and no subprocess. The default import flow never uses
it (fail closed); it exists for tests and explicitly owner-audited offline
imports.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

SCANNER = "skillspector"
DEFAULT_SCAN_TIMEOUT_S = 300
# SkillSpector's `risk_assessment.recommendation` is its own verdict vocabulary
# (e.g. OK / CAUTION / BLOCK); BLOCK is the scanner's hard refusal and always
# fails the review. The numeric score is a *risk* score (lower = safer) and is
# recorded as evidence, never used as a gate on its own.
BLOCKING_RECOMMENDATION = "BLOCK"
CRITICAL_SEVERITY = "CRITICAL"
HIGH_SEVERITY = "HIGH"
_FALLBACK_SCANNER = "static-fallback"

Verdict = Literal["PASS", "FAIL", "UNAVAILABLE"]


class ToolReviewVerdict(BaseModel):
    """Immutable scanner verdict for one package scan."""

    model_config = ConfigDict(frozen=True)

    scanner: str
    score: int
    verdict: Verdict
    findings_summary: str
    report_json: dict[str, Any]


@runtime_checkable
class ToolReviewer(Protocol):
    """Anything that can pass/fail a package directory without executing it."""

    def is_available(self) -> bool: ...

    def review(self, path: Path) -> ToolReviewVerdict: ...


def _unavailable(detail: str) -> ToolReviewVerdict:
    return ToolReviewVerdict(
        scanner=SCANNER,
        score=0,
        verdict="UNAVAILABLE",
        findings_summary=detail,
        report_json={},
    )


def _map_report(data: Any) -> ToolReviewVerdict:
    """Map a parsed scanner report to a verdict; missing shapes fail closed.

    Real report shape (SkillSpector v2): ``risk_assessment`` carries
    ``score`` (risk, lower = safer), ``severity``, ``recommendation`` and
    ``max_issue_severity``; ``issues[]`` are per-finding (``severity``,
    ``category``, ``location.file``); ``components[]`` mark each file
    ``executable``. Policy:

    - the scanner's own ``BLOCK`` recommendation always fails the review;
    - any CRITICAL finding fails the review;
    - HIGH-or-above findings located in *executable* components fail the
      review (code must be clean);
    - HIGH findings in advisory-only markdown stay PASS — they are recorded
      verbatim as evidence (persona text routinely trips jailbreak-pattern
      heuristics; the scan report rides along with every promotion).
    """
    if not isinstance(data, dict):
        return _unavailable(f"{SCANNER} report is not a JSON object (fail closed)")
    risk = data.get("risk_assessment")
    if not isinstance(risk, dict):
        return _unavailable(f"{SCANNER} report has no risk_assessment (fail closed)")
    score = risk.get("score")
    if not isinstance(score, int) or isinstance(score, bool):
        return _unavailable(f"{SCANNER} risk score is missing or not an integer (fail closed)")
    recommendation = str(risk.get("recommendation", "")).upper()
    issues = data.get("issues")
    if not isinstance(issues, list):
        issues = []
    components = data.get("components")
    if not isinstance(components, list):
        components = []
    executable_paths = {
        str(c.get("path"))
        for c in components
        if isinstance(c, dict) and c.get("executable") and c.get("path")
    }

    counts: dict[str, int] = {}
    blocking: list[str] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        severity = str(issue.get("severity", "unknown")).upper()
        counts[severity] = counts.get(severity, 0) + 1
        category = str(issue.get("category", "unknown"))
        raw_location = issue.get("location")
        location: dict[Any, Any] = raw_location if isinstance(raw_location, dict) else {}
        file_path = str(location.get("file", "?"))
        if severity == CRITICAL_SEVERITY:
            blocking.append(f"critical {category} in {file_path}")
        elif severity == HIGH_SEVERITY and file_path in executable_paths:
            blocking.append(f"high {category} in executable {file_path}")

    if recommendation == BLOCKING_RECOMMENDATION:
        blocking.append("scanner recommendation is BLOCK")
    summary = f"risk score {score} ({recommendation}); " + (
        ", ".join(f"{name}={count}" for name, count in sorted(counts.items())) or "no findings"
    )
    if blocking:
        summary += "; blocking: " + "; ".join(blocking[:3])
    verdict: Verdict = "FAIL" if blocking else "PASS"
    return ToolReviewVerdict(
        scanner=SCANNER,
        score=score,
        verdict=verdict,
        findings_summary=summary,
        report_json=data,
    )


class SkillSpectorReviewer:
    """Reviewer backed by the NVIDIA SkillSpector CLI (no LLM, no network).

    The executable is resolved on every call (never cached), so a scanner
    installed later is picked up and a removed one fails closed. The JSON
    report is written to a private temp file, never stdout parsing.
    """

    def __init__(self, *, scan_timeout_s: int = DEFAULT_SCAN_TIMEOUT_S) -> None:
        self._timeout_s = scan_timeout_s

    @staticmethod
    def _find_scanner() -> str | None:
        """Resolve the scanner: PATH first, then the active venv's Scripts/bin.

        SkillSpector is a declared Agency dependency (pyproject), installed
        into the venv; a bare `shutil.which` misses venv shims when the
        Scripts dir is not on the ambient PATH (Windows hosts).
        """
        found = shutil.which(SCANNER)
        if found:
            return found
        for candidate in (
            Path(sys.prefix) / "Scripts" / f"{SCANNER}.exe",
            Path(sys.prefix) / "Scripts" / SCANNER,
            Path(sys.prefix) / "bin" / SCANNER,
        ):
            if candidate.is_file():
                return str(candidate)
        return None

    def is_available(self) -> bool:
        return self._find_scanner() is not None

    def review(self, path: Path) -> ToolReviewVerdict:
        exe = self._find_scanner()
        if exe is None:
            return _unavailable(
                f"{SCANNER} executable not found on PATH or in the venv; import must stay quarantined (fail closed)"
            )
        command = [exe, "scan", str(path), "--no-llm", "--format", "json"]
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                timeout=self._timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return _unavailable(f"{SCANNER} scan timed out after {self._timeout_s}s (fail closed)")
        except OSError as exc:
            return _unavailable(f"{SCANNER} could not be executed: {exc} (fail closed)")
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            detail = f"{SCANNER} exited {proc.returncode}"
            if stderr:
                detail += f": {stderr[:200]}"
            return _unavailable(detail + " (fail closed)")
        try:
            # The scanner writes the JSON report to stdout; `--output` is
            # explicitly unsupported on some platforms (Windows) and its own
            # guidance is to redirect stdout to a file outside the scan target.
            raw = proc.stdout.decode("utf-8", errors="replace")
            data = json.loads(raw)
        except ValueError:
            return _unavailable(
                f"{SCANNER} report is missing, unreadable, or not valid JSON (fail closed)"
            )
        return _map_report(data)


_CODE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsubprocess\b"), "subprocess"),
    (re.compile(r"\bos\.system\b"), "os.system"),
    (re.compile(r"\beval\s*\("), "eval"),
    (re.compile(r"\bexec\s*\("), "exec"),
    (re.compile(r"\b__import__\b"), "__import__"),
    (re.compile(r"\bsocket\b"), "socket"),
    (re.compile(r"\bsocketserver\b"), "socketserver"),
    (re.compile(r"\brequests\b"), "requests"),
    (re.compile(r"\bhttpx\b"), "httpx"),
)
# Applies to every file, tests included: destruction and credential paths.
_UNIVERSAL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"rm\s+-rf"), "rm -rf"),
    (re.compile(r"\.env\b"), ".env"),
    (re.compile(r"\bid_rsa\b"), "id_rsa"),
)


class StaticFallbackReviewer:
    """Deterministic text-pattern reviewer (no subprocess, no execution).

    Only for tests and explicitly owner-audited offline imports — the default
    import flow refuses to rely on it. HTTP clients (``requests``/``httpx``)
    are forbidden in tool code but tolerated in the test file, mirroring the
    Forge trust boundary: tests run in the isolated copy, tool code runs in
    the agent's process.
    """

    def is_available(self) -> bool:
        return True

    def review(self, path: Path) -> ToolReviewVerdict:
        findings: list[str] = []
        for file_path in sorted(Path(path).rglob("*.py")):
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                findings.append(f"{file_path.name}: unreadable file")
                continue
            code = () if file_path.name.startswith("test_") else _CODE_PATTERNS
            for pattern, label in (*code, *_UNIVERSAL_PATTERNS):
                if pattern.search(text):
                    findings.append(f"{file_path.name}: forbidden pattern {label!r}")
        if findings:
            return ToolReviewVerdict(
                scanner=_FALLBACK_SCANNER,
                score=0,
                verdict="FAIL",
                findings_summary="; ".join(findings),
                report_json={"findings": findings},
            )
        return ToolReviewVerdict(
            scanner=_FALLBACK_SCANNER,
            score=100,
            verdict="PASS",
            findings_summary="no forbidden patterns found",
            report_json={},
        )


__all__ = [
    "DEFAULT_SCAN_TIMEOUT_S",
    "SCANNER",
    "SkillSpectorReviewer",
    "StaticFallbackReviewer",
    "ToolReviewVerdict",
    "ToolReviewer",
]
