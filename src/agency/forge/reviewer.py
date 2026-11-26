"""Forge Phase 5 AI reviewer loop (local heuristic by default).

Bounded, deterministic reviewer that scores a change set 0-5 using the
security scan, lint-ish checks, and (optionally) the test gate. Never
performs a human review; caveats preserve that honestly.

Fail-closed: any gate failure or high finding blocks PASS.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

from agency.forge.diff import build_diff_bundle, delta_paths, head_sha
from agency.forge.inspection import (
    MAX_FILE_BYTES,
    _resolve_root,
    _symlink_or_escape,
)
from agency.forge.security_scan import scan_changes
from agency.forge.test_runner import run_tests

logger = structlog.get_logger(__name__)

MAX_ROUNDS = 5
MAX_LINT_LINE_LENGTH = 120
REVIEW_RETRY_DELAY_S = 5.0
REVIEW_MAX_PROVIDER_RETRIES = 2

# Which provider the review gate prefers. ``auto`` (default) keeps the historic
# behaviour: Google serious models when Google credentials exist, else the
# configured Agency LLM. ``agency`` skips the Google preference and always uses
# the configured Agency LLM — the escape hatch for a rate-limited Google key,
# which otherwise fails the whole review gate closed (observed live with
# ``gemini-4-hydrogen`` returning 429).
REVIEW_PROVIDER_ENV = "AGENCY_FORGE_REVIEW_PROVIDER"
_REVIEW_PROVIDER_MODES = ("auto", "google", "agency")


def review_provider_mode() -> str:
    """Resolve the configured review-provider preference; unknown values warn and
    fall back to ``auto`` rather than silently disabling the gate."""
    raw = os.environ.get(REVIEW_PROVIDER_ENV, "auto").strip().lower()
    if raw in _REVIEW_PROVIDER_MODES:
        return raw
    logger.warning("forge.review_provider_unknown", value=raw or "<empty>")
    return "auto"


def build_review_llm() -> Any | None:
    """Build the configured Agency LLM for the review gate.

    The serious-work models (:mod:`agency.llm.serious_models`) are Google Gemini
    models, so when Google credentials exist the gate runs them on the Google
    provider even when the Agency's task LLM uses a different backend —
    ``load_named_provider`` keeps the two providers' credentials and endpoints
    apart. Without Google credentials the gate falls back to the configured
    Agency LLM (``AGENCY_LLM_*`` / ``.env``). The echo/offline fallback is
    treated as "no provider configured" and returns ``None`` so hermetic runs
    never attempt a network call.

    ``AGENCY_FORGE_REVIEW_PROVIDER=agency`` skips the Google preference entirely
    and judges with the configured Agency LLM. Set it when the Google key is
    rate-limited: the default path would otherwise fail every review closed.
    """
    try:
        from agency.llm.adapter import LLMAdapter
        from agency.llm.config import ProviderKind, load_config, load_named_provider
        from agency.llm.serious_models import serious_model

        mode = review_provider_mode()
        google_config: Any = None
        if mode != "agency":
            try:
                candidate = load_named_provider("google")
            except ValueError:  # unknown preset — defensive; the name is hardcoded
                candidate = None
            if candidate is not None and candidate.api_key is not None:
                google_config = candidate
        if google_config is not None and mode != "agency":
            return LLMAdapter(config=google_config.model_copy(update={"model": serious_model()}))
        config = load_config()
        if config.provider is ProviderKind.ECHO:
            return None
        if config.provider is ProviderKind.GOOGLE and mode != "agency":
            # Task LLM already on Google: point it at the serious model.
            config = config.model_copy(update={"model": serious_model()})
        return LLMAdapter(config=config)
    except Exception:  # noqa: BLE001
        logger.warning("forge.review_llm_unavailable")
        return None


def build_hermes_judge() -> Any | None:
    """A cross-family fallback judge: the hermes CLI, if installed.

    Returned object quacks like the LLM adapter (async ``generate``), so the
    ordinary review machinery parses its verdict. ``None`` when hermes is not
    on PATH — callers then fail closed as usual.
    """
    exe = shutil.which("hermes") or ""
    if not exe:
        return None

    class _HermesJudge:
        async def generate(self, prompt: str, context: Any = None) -> str:
            proc = await asyncio.create_subprocess_exec(
                exe,
                "-z",
                prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=900)
            except TimeoutError:
                proc.kill()
                raise RuntimeError("hermes judge timed out") from None
            if proc.returncode != 0:
                raise RuntimeError(f"hermes judge exited {proc.returncode}")
            return out.decode("utf-8", errors="replace")

    return _HermesJudge()


ReviewGate = Literal["PASS", "FAIL"]
Severity = Literal["low", "medium", "high", "critical"]

CAVEATS: tuple[str, ...] = (
    "not_a_human_review",
    "heuristic_review",
    "tests_not_run",
)

_LLM_FAILURE_RULE = "LLM_REVIEW_FAILED"
_LLM_FAILURE_MESSAGE = "LLM reviewer did not produce a usable verdict; gate failed closed."
_LLM_FINDING_MESSAGE = "LLM reviewer finding."
_REFUSAL_MARKERS: tuple[str, ...] = (
    "i cannot",
    "i can't",
    "i am unable",
    "i'm unable",
    "i refuse",
    "cannot comply",
    "can't comply",
    "as an ai",
    "content policy",
)


class _LLMReviewError(Exception):
    """The configured reviewer could not produce a usable verdict.

    ``retryable`` marks provider-side failures (timeouts, 503/429 bursts) that
    a patient wait may cure; refusals and malformed answers are never retried.
    """

    def __init__(self, message: str = "", *, retryable: bool = False) -> None:
        super().__init__(message or _LLM_FAILURE_MESSAGE)
        self.retryable = retryable


class ReviewFinding(BaseModel):
    """One finding raised by the (heuristic) reviewer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = ""
    line: int = Field(default=1, ge=1)
    rule_id: str = Field(min_length=1)
    severity: Severity = "medium"
    message: str = Field(min_length=1)
    resolved: bool = False


class ReviewReport(BaseModel):
    """Immutable bounded review outcome. Never authorizes merge/deploy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(min_length=1)
    created_at: datetime
    root: str
    gate: ReviewGate
    score: int = Field(ge=0, le=5)
    threshold: int = Field(default=4, ge=0, le=5)
    rounds: int = Field(default=1, ge=1, le=MAX_ROUNDS)
    max_rounds: int = Field(default=MAX_ROUNDS, ge=1, le=MAX_ROUNDS)
    findings: tuple[ReviewFinding, ...] = ()
    unresolved_high: int = Field(default=0, ge=0)
    reviewed_sha: str = ""
    delta_base_sha: str = ""
    shadow_score: int | None = Field(default=None, ge=0, le=5)
    shadow_gate: str = ""
    shadow_findings: tuple[ReviewFinding, ...] = ()
    caveats: tuple[str, ...] = CAVEATS
    note: str = "Heuristic review; not a human review; does not authorize merge or deploy."


_SCHEMA = """
CREATE TABLE IF NOT EXISTS forge_review_reports (
    report_id  TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    gate       TEXT NOT NULL,
    document   TEXT NOT NULL
);
"""


class ReviewStore:
    def __init__(self, db_path: str | Path = "forge_review.db") -> None:
        self._db_path = Path(db_path)
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def save(self, report: ReviewReport) -> str:
        self._conn.execute(
            "INSERT INTO forge_review_reports (report_id, created_at, gate, document) VALUES (?, ?, ?, ?)",
            (
                report.report_id,
                report.created_at.isoformat(),
                report.gate,
                report.model_dump_json(),
            ),
        )
        self._conn.commit()
        logger.info("forge.review_saved", report_id=report.report_id, gate=report.gate)
        return report.report_id

    def load(self, report_id: str) -> ReviewReport | None:
        cursor = self._conn.execute(
            "SELECT document FROM forge_review_reports WHERE report_id = ?", (report_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return ReviewReport.model_validate_json(row[0])

    def list_reports(self) -> list[ReviewReport]:
        cursor = self._conn.execute("SELECT document FROM forge_review_reports ORDER BY created_at")
        return [ReviewReport.model_validate_json(row[0]) for row in cursor.fetchall()]

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


_LINTABLE_SUFFIXES = frozenset(
    {
        ".py",
        ".go",
        ".rs",
        ".ts",
        ".tsx",
        ".mts",
        ".cts",
        ".js",
        ".mjs",
        ".cjs",
        ".java",
        ".kt",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".rb",
        ".php",
        ".swift",
        ".cs",
    }
)


def _lint_check(root: Path, path: str) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    normalized = path.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if not parts:
        return findings
    candidate = root.joinpath(*parts)
    escape = _symlink_or_escape(root, candidate)
    if escape is not None:
        return findings
    try:
        st = os.lstat(candidate)
    except OSError:
        return findings
    if not os.path.isfile(candidate) or st.st_size > MAX_FILE_BYTES:
        return findings
    suffix = candidate.suffix.lower()
    if suffix not in _LINTABLE_SUFFIXES:
        return findings
    try:
        text = candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return findings
    display = "/".join(parts)
    for lineno, line in enumerate(text.splitlines(), start=1):
        if len(line) > MAX_LINT_LINE_LENGTH:
            findings.append(
                ReviewFinding(
                    path=display,
                    line=lineno,
                    rule_id="LINT001",
                    severity="medium" if suffix == ".py" else "low",
                    message=f"LINT001 line too long ({len(line)} > {MAX_LINT_LINE_LENGTH})",
                )
            )
        if suffix == ".py":
            stripped = line.strip()
            if stripped == "except:" or stripped.startswith("except :"):
                findings.append(
                    ReviewFinding(
                        path=display,
                        line=lineno,
                        rule_id="LINT002",
                        severity="medium",
                        message="LINT002 bare except",
                    )
                )
        if line != line.rstrip(" \t"):
            findings.append(
                ReviewFinding(
                    path=display,
                    line=lineno,
                    rule_id="LINT003",
                    severity="low",
                    message="LINT003 trailing whitespace",
                )
            )
    return findings


def _one_round(
    root: Path,
    changed_paths: list[str],
    *,
    test_paths: list[str] | None,
    test_timeout_s: int,
    run_tests_flag: bool,
) -> tuple[int, list[ReviewFinding], str]:
    findings: list[ReviewFinding] = []

    # Security scan
    try:
        sec = scan_changes(root, changed_paths)
    except Exception:  # noqa: BLE001
        sec = None
    if sec is not None:
        for f in sec.findings:
            sev = f.severity
            if sev not in ("low", "medium", "high", "critical"):
                sev = "medium"
            findings.append(
                ReviewFinding(
                    path=f.path,
                    line=f.line,
                    rule_id=f.rule_id,
                    severity=sev,
                    message=f.message,
                )
            )

    # Inspection-derived syntax failures
    try:
        from agency.forge.inspection import inspect_changes

        insp = inspect_changes(root, changed_paths)
    except Exception:  # noqa: BLE001
        insp = None
    if insp is not None:
        for r in insp.rejected:
            findings.append(
                ReviewFinding(
                    path=r.path,
                    line=1,
                    rule_id="INSP",
                    severity="high",
                    message=f"INSP rejected path: {r.reason}",
                )
            )
        for file_record in insp.files:
            if file_record.kind == "python" and file_record.syntax_ok is False:
                findings.append(
                    ReviewFinding(
                        path=file_record.path,
                        line=1,
                        rule_id="SYNTAX",
                        severity="high",
                        message=f"SYNTAX python syntax error: {file_record.syntax_error}",
                    )
                )

    # Lint-ish
    for p in changed_paths:
        findings.extend(_lint_check(root, p))

    # Test gate
    tests_ran = False
    tests_passed = True
    if run_tests_flag and test_paths:
        try:
            tr = run_tests(root, test_paths, timeout_s=test_timeout_s)
            tests_ran = True
            if tr.gate not in ("PASS", "SKIP"):
                tests_passed = False
                findings.append(
                    ReviewFinding(
                        path="<tests>",
                        line=1,
                        rule_id="TEST",
                        severity="high",
                        message=f"TEST gate {tr.gate}",
                    )
                )
        except Exception as exc:  # noqa: BLE001
            tests_ran = True
            tests_passed = False
            findings.append(
                ReviewFinding(
                    path="<tests>",
                    line=1,
                    rule_id="TEST",
                    severity="high",
                    message=f"TEST error: {type(exc).__name__}",
                )
            )

    # Score
    score = 5
    high_count = sum(1 for f in findings if f.severity in ("high", "critical"))
    medium_count = sum(1 for f in findings if f.severity == "medium")
    if high_count:
        score -= min(3, high_count + 1)
    if medium_count:
        score -= min(2, (medium_count + 2) // 3)
    if not tests_passed:
        score = min(score, 2)
    if not tests_ran:
        # no tests provided: slight penalty but keep pass possible
        pass
    score = max(0, min(5, score))

    tests_note = "tests_run" if tests_ran else "tests_not_run"
    return score, findings, tests_note


_REVIEW_BODY_LIMIT = 6000
_REVIEW_TRUNCATION_MARK = "...[change set truncated for review]"

_REVIEW_PROMPT_HEADER = (
    "You are a code reviewer for a software factory. Given the change set, "
    "the actual diff content, and the automated findings below, produce a "
    "JSON object with keys: "
    '"score" (integer 0-5), "findings" (list of objects with keys path, line, '
    "rule_id, severity (low/medium/high/critical), message, resolved (bool)), "
    '"summary" (short string). Score 5 means clean. High/critical findings '
    "block. Respond with JSON only.\n\n"
    "Severity rubric: low = style or naming only; medium = missing validation "
    "or error handling; high = security or correctness bug in the changed "
    "paths; critical = secret leak, injection, or protected-path edit. Every "
    "finding must cite the exact path and line it applies to, and every cited "
    "path must appear in the CHANGE SET below. If you are "
    "uncertain about a piece of the change, lower the score and name what is "
    "unverified in the summary. Do not invent files, lines, or findings that "
    "are not supported by the change set and DIFF below. When the diff is "
    "truncated or paths are listed as not shown, review what is visible and "
    "name the omission in the summary.\n\n"
)


def _review_prompt(findings_summary: dict[str, Any], diff_text: str = "") -> str:
    """Render the deterministic review LLM prompt from one findings snapshot."""
    body = json.dumps(findings_summary, indent=2)
    if len(body) > _REVIEW_BODY_LIMIT:
        body = body[:_REVIEW_BODY_LIMIT] + "\n" + _REVIEW_TRUNCATION_MARK
    prompt = f"{_REVIEW_PROMPT_HEADER}CHANGE SET:\n{body}"
    if diff_text:
        prompt += "\n\nDIFF (actual change content; truncated paths are marked):\n" + diff_text
    return prompt


def _llm_review_one_round(
    llm: Any,
    root: Path,
    changed_paths: list[str],
    test_paths: list[str] | None,
    test_timeout_s: int,
    run_tests_flag: bool,
    tests_report: Any = None,
    delta_base_sha: str = "",
) -> tuple[int, list[ReviewFinding], str]:
    """Run one LLM review round. Raise :class:`_LLMReviewError` on any failure.

    Errors, refusals and malformed responses are all failures: the caller must
    fail the gate closed rather than fall back to a passing heuristic verdict.
    The prompt carries the actual diff content (bounded, risk-ranked), not
    just metadata. Findings citing a path outside the change set are kept but
    demoted to low-severity ``LLM_UNVERIFIED`` citations — never silently
    trusted, never dropped.
    """
    from agency.forge.inspection import inspect_changes
    from agency.forge.security_scan import scan_changes

    try:
        reviewed_paths = changed_paths
        if delta_base_sha:
            delta = delta_paths(root, delta_base_sha)
            if delta is not None:
                reviewed_paths = list(delta)
        insp = inspect_changes(root, reviewed_paths)
        sec = scan_changes(root, reviewed_paths)
        bundle = build_diff_bundle(root, reviewed_paths)
        findings_summary: dict[str, Any] = {
            "changed_paths": reviewed_paths,
            "review_scope": "delta" if delta_base_sha else "full",
            "inspection_rejected": [r.reason for r in insp.rejected],
            "security_findings": [
                {
                    "path": f.path,
                    "line": f.line,
                    "rule": f.rule_id,
                    "sev": f.severity,
                    "msg": f.message,
                }
                for f in sec.findings
            ],
            "lint": [],
        }
        for p in changed_paths:
            for lf in _lint_check(root, p):
                findings_summary["lint"].append(
                    {
                        "path": lf.path,
                        "line": lf.line,
                        "rule": lf.rule_id,
                        "sev": lf.severity,
                        "msg": lf.message,
                    }
                )
        tests_info: Any = "not_run"
        if tests_report is not None:
            # Real results from the pipeline's tests sub-gate, not a re-run.
            tests_info = {
                "gate": tests_report.gate,
                "returncode": tests_report.returncode,
                "summary": (tests_report.stdout or "")[-500:],
            }
        elif run_tests_flag and test_paths:
            try:
                tr = run_tests(root, test_paths, timeout_s=test_timeout_s)
                tests_info = tr.gate
            except Exception:  # noqa: BLE001
                tests_info = "error"
        findings_summary["tests"] = tests_info

        prompt = _review_prompt(findings_summary, diff_text=bundle.text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("forge.review_llm_evidence_error", error_class=type(exc).__name__)
        raise _LLMReviewError from None

    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            response = asyncio.run(llm.generate(prompt))
        else:
            with ThreadPoolExecutor(max_workers=1) as executor:
                response = executor.submit(lambda: asyncio.run(llm.generate(prompt))).result()
    except _LLMReviewError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("forge.review_llm_call_failed", error_class=type(exc).__name__)
        raise _LLMReviewError(f"provider error: {type(exc).__name__}", retryable=True) from None

    text = response.strip() if isinstance(response, str) else ""
    lowered = text.lower()
    if not text or any(marker in lowered for marker in _REFUSAL_MARKERS):
        raise _LLMReviewError
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise _LLMReviewError
    try:
        data = json.loads(text[start : end + 1])
    except (TypeError, ValueError):
        raise _LLMReviewError from None
    if not isinstance(data, dict) or "score" not in data:
        raise _LLMReviewError
    try:
        score = max(0, min(5, int(data["score"])))
    except (TypeError, ValueError):
        raise _LLMReviewError from None

    llm_findings: list[ReviewFinding] = []
    raw_findings = data.get("findings") or []
    if not isinstance(raw_findings, list):
        raise _LLMReviewError
    cited_paths = {p.replace("\\", "/") for p in reviewed_paths}
    for raw in raw_findings:
        if not isinstance(raw, dict):
            continue
        sev = raw.get("severity", "medium")
        if sev not in ("low", "medium", "high", "critical"):
            sev = "medium"
        try:
            line = int(raw.get("line", 1) or 1)
        except (TypeError, ValueError):
            line = 1
        path = str(raw.get("path", ""))[:512]
        rule_id = str(raw.get("rule_id", "LLM")).strip()[:64] or "LLM"
        # Hallucination guard: a finding citing a path outside the change set
        # is kept for visibility but demoted so it can never block on its own.
        if path and path not in cited_paths:
            rule_id = "LLM_UNVERIFIED"
            sev = "low"
        # No LLM free text is ever persisted (test-enforced invariant): the
        # finding stays actionable through its structured citation instead.
        llm_findings.append(
            ReviewFinding(
                path=path,
                line=max(1, line),
                rule_id=rule_id,
                severity=sev,
                message=_LLM_FINDING_MESSAGE,
                resolved=bool(raw.get("resolved", False)),
            )
        )

    tests_ran = tests_report is not None or bool(run_tests_flag and test_paths)
    if tests_ran:
        # The LLM may not claim tests were skipped when real results were supplied.
        llm_findings = [
            f for f in llm_findings if f.rule_id.lower() not in {"tests-not-run", "tests_not_run"}
        ]
    tests_note = "tests_run" if tests_ran else "tests_not_run"
    return score, llm_findings, tests_note


def run_review(
    root: str | Path,
    changed_paths: list[str] | None = None,
    *,
    paths: list[str] | None = None,
    threshold: int = 4,
    max_rounds: int = MAX_ROUNDS,
    test_paths: list[str] | None = None,
    test_timeout_s: int = 120,
    run_tests_flag: bool = False,
    llm: Any = None,
    use_llm: bool = False,
    tests_report: Any = None,
    delta_base_sha: str = "",
    shadow_llm: Any = None,
    fallback_llm: Any = None,
    **kwargs: Any,
) -> ReviewReport:
    real_root = _resolve_root(root)
    if changed_paths is None:
        changed_paths = paths
    if changed_paths is None:
        # maybe kwargs
        changed_paths = kwargs.get("changed_paths") or kwargs.get("files") or []
    if not isinstance(changed_paths, list) or any(not isinstance(p, str) for p in changed_paths):
        raise ValueError("changed_paths must be a list of strings")
    if max_rounds <= 0 or max_rounds > MAX_ROUNDS:
        raise ValueError(f"max_rounds must be in (0, {MAX_ROUNDS}]")
    if threshold < 0 or threshold > 5:
        raise ValueError("threshold must be 0-5")

    # normalize test paths
    if test_paths is None:
        test_paths = kwargs.get("test_paths")
    if isinstance(test_paths, (tuple,)):
        test_paths = list(test_paths)

    # run tests only if explicitly requested or test_paths provided
    run_tests_flag = bool(run_tests_flag or test_paths)

    report_id = str(uuid4())
    created_at = datetime.now(UTC)

    if use_llm and llm is None:
        llm = build_review_llm()
    llm_requested = bool(use_llm and llm is not None)

    # Delta re-review scope (frozen-tree follow-up): re-review only what
    # changed since the recorded base sha. An unresolvable base falls back to
    # the full change set and says so.
    delta_scope = ""
    delta_unresolved = False
    if delta_base_sha:
        if delta_paths(real_root, delta_base_sha) is None:
            delta_unresolved = True
        else:
            delta_scope = delta_base_sha

    used_llm = False
    fallback_used = False
    rounds_done = 0
    final_score = 0
    final_findings: list[ReviewFinding] = []
    for _ in range(max_rounds):
        rounds_done += 1
        if llm_requested:
            try:
                # Provider failures get a patient retry: a down judge must not
                # burn the builder's attempts when a short wait cures it.
                for attempt in range(REVIEW_MAX_PROVIDER_RETRIES + 1):
                    try:
                        score, findings, _ = _llm_review_one_round(
                            llm,
                            real_root,
                            changed_paths,
                            test_paths,
                            test_timeout_s,
                            run_tests_flag,
                            tests_report=tests_report,
                            delta_base_sha=delta_scope,
                        )
                        used_llm = True
                        break
                    except _LLMReviewError as exc:
                        if not exc.retryable or attempt == REVIEW_MAX_PROVIDER_RETRIES:
                            raise
                        logger.warning("forge.review_provider_retry", attempt=attempt + 1)
                        time.sleep(REVIEW_RETRY_DELAY_S)
            except _LLMReviewError:
                # Cross-family fallback judge: when the primary reviewer's
                # provider is down, an independent second family (e.g. the
                # hermes CLI) may judge instead — maker stays != checker.
                fallback_used = False
                if fallback_llm is not None:
                    try:
                        score, findings, _ = _llm_review_one_round(
                            fallback_llm,
                            real_root,
                            changed_paths,
                            test_paths,
                            test_timeout_s,
                            run_tests_flag,
                            tests_report=tests_report,
                            delta_base_sha=delta_scope,
                        )
                        used_llm = True
                        fallback_used = True
                        logger.warning("forge.review_fallback_judge_used")
                    except _LLMReviewError:
                        fallback_used = False
                if not fallback_used:
                    # Heuristic remains the scorer of last resort, but the failed
                    # LLM gate keeps an unresolved high finding so the gate fails.
                    logger.warning("forge.review_llm_failed_closed")
                    score, heuristic_findings, _ = _one_round(
                        real_root,
                        changed_paths,
                        test_paths=test_paths,
                        test_timeout_s=test_timeout_s,
                        run_tests_flag=run_tests_flag,
                    )
                    findings = [
                        *heuristic_findings,
                        ReviewFinding(
                            path="",
                            line=1,
                            rule_id=_LLM_FAILURE_RULE,
                            severity="high",
                            message=_LLM_FAILURE_MESSAGE,
                        ),
                    ]
            # The prompt is identical each round; a second call only adds cost.
            break
        score, findings, _ = _one_round(
            real_root,
            changed_paths,
            test_paths=test_paths,
            test_timeout_s=test_timeout_s,
            run_tests_flag=run_tests_flag,
        )
        unresolved_high = sum(
            1 for f in findings if (not f.resolved) and f.severity in ("high", "critical")
        )
        if score >= threshold and unresolved_high == 0:
            break

    final_score = score
    final_findings = findings
    unresolved_high = sum(
        1 for f in final_findings if (not f.resolved) and f.severity in ("high", "critical")
    )
    gate: ReviewGate = "PASS" if (final_score >= threshold and unresolved_high == 0) else "FAIL"

    # Shadow reviewer (omniscient VERIFIER-CONTRACT pattern): same rubric on a
    # second model, recorded as SHADOW_* fields, never gating. Divergence is
    # the drift alarm that feeds the gate-calibration loop; a shadow failure
    # is recorded as absent, never as a pass.
    shadow_score: int | None = None
    shadow_gate = ""
    shadow_findings: tuple[ReviewFinding, ...] = ()
    if llm_requested and shadow_llm is not None:
        try:
            s_score, s_findings, _ = _llm_review_one_round(
                shadow_llm,
                real_root,
                changed_paths,
                test_paths,
                test_timeout_s,
                run_tests_flag,
                tests_report=tests_report,
                delta_base_sha=delta_scope,
            )
            shadow_score = s_score
            shadow_findings = tuple(s_findings)
            s_unresolved = sum(
                1
                for f in shadow_findings
                if (not f.resolved) and f.severity in ("high", "critical")
            )
            shadow_gate = "PASS" if (s_score >= threshold and s_unresolved == 0) else "FAIL"
            if shadow_gate != gate:
                logger.info("forge.review_shadow_divergence", gate=gate, shadow_gate=shadow_gate)
        except _LLMReviewError:
            logger.warning("forge.review_shadow_unavailable")

    caveats = list(CAVEATS)
    if used_llm:
        caveats = [c for c in caveats if c != "heuristic_review"]
    if fallback_used:
        caveats.append("review_fallback_judge")
    if run_tests_flag or tests_report is not None:
        caveats = [c for c in caveats if c != "tests_not_run"]
    if delta_unresolved:
        caveats.append("delta_base_unresolved")

    note = (
        "LLM-assisted review; not a human review; does not authorize merge or deploy."
        if used_llm
        else "Heuristic review; not a human review; does not authorize merge or deploy."
    )
    report = ReviewReport(
        report_id=report_id,
        created_at=created_at,
        root=str(real_root),
        gate=gate,
        score=final_score,
        threshold=threshold,
        rounds=rounds_done,
        max_rounds=max_rounds,
        findings=tuple(final_findings),
        unresolved_high=unresolved_high,
        reviewed_sha=head_sha(real_root),
        delta_base_sha=delta_scope,
        shadow_score=shadow_score,
        shadow_gate=shadow_gate,
        shadow_findings=shadow_findings,
        caveats=tuple(caveats),
        note=note,
    )
    logger.info(
        "forge.review", root=str(real_root), gate=gate, score=final_score, rounds=rounds_done
    )
    return report


__all__ = [
    "CAVEATS",
    "MAX_ROUNDS",
    "ReviewFinding",
    "ReviewReport",
    "ReviewStore",
    "build_review_llm",
    "review_provider_mode",
    "run_review",
]
