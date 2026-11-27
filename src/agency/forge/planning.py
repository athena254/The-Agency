"""Enforced pre-code planning gate for factory workers.

A plan is a claim set. This module checks that the claim exists, is shaped
well enough to execute, and — for code jobs — carries the quality signals
that make the claim checkable: a non-empty file allow-list, a named test
command and declared risks. Legacy plans still pass the shape gate, but are
marked with an explicit ``lenient_plan`` caveat instead of passing silently.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict

REQUIRED_SECTIONS = ("Goal", "Approach", "Files", "Tests", "Risks")
MAX_PLAN_CHARS = 8000

# Job classes whose plans must carry the full quality signals.
_CODE_JOB_CLASSES = frozenset({"standard", "risky"})
# Values that mean "the author did not actually fill this in".
_PLACEHOLDER_VALUES = frozenset({"", "none", "n/a", "na", "tbd", "todo", "-", "--", "?", "unknown"})
# Phrase words that do not by themselves constitute a test command.
_FILLER_WORDS = frozenset(
    {
        "run",
        "runs",
        "the",
        "test",
        "tests",
        "testing",
        "command",
        "standard",
        "usual",
        "repo",
        "repos",
        "see",
        "readme",
        "use",
        "uses",
        "using",
        "please",
        "appropriate",
        "it",
        "this",
        "that",
        "or",
        "a",
        "an",
        "with",
        "via",
    }
)
# Tolerate markdown headers ("## Files:") as well as bare "Files:" lines.
_HEADER_PREFIX = r"^\s*#*\s*"
_HEADER_ALTERNATION = "|".join(REQUIRED_SECTIONS)


class PlanReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gate: str
    plan_path: str
    missing_sections: tuple[str, ...] = ()
    allowed_files: tuple[str, ...] = ()
    findings: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ("plan_is_a_claim_not_a_guarantee",)
    # Whether the quality requirements were enforced (code-job mode).
    strict: bool = False
    # Parsed quality signals, exposed so callers can audit the verdict cleanly.
    test_command: str = ""
    declared_risks: str = ""


def _section_body(text: str, name: str) -> str:
    """Text after a ``Name:`` header, up to the next required header or EOF."""
    pattern = (
        rf"{_HEADER_PREFIX}{re.escape(name)}\s*:"
        rf"(.*?)(?={_HEADER_PREFIX}(?:{_HEADER_ALTERNATION})\s*:|\Z)"
    )
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL | re.MULTILINE)
    return match.group(1).strip() if match else ""


def _is_placeholder(value: str) -> bool:
    return value.strip().lower().rstrip(".") in _PLACEHOLDER_VALUES


def _first_meaningful_line(body: str) -> str:
    for line in body.splitlines():
        candidate = line.strip().lstrip("-*# ").strip()
        if candidate and not _is_placeholder(candidate):
            return candidate
    return ""


def _concrete_test_command(body: str) -> str:
    """First test line that is concrete (not a placeholder or filler phrase)."""
    candidate = _first_meaningful_line(body)
    if not candidate:
        return ""
    tokens = re.findall(r"[A-Za-z_][\w./-]*", candidate.lower())
    return candidate if any(token not in _FILLER_WORDS for token in tokens) else ""


def check_plan(
    root: str | Path,
    plan_path: str = "PLAN.md",
    *,
    strict: bool = False,
    job_class: str = "",
) -> PlanReport:
    """Check a plan's shape, and optionally enforce code-job quality signals.

    ``strict=True`` (or a code ``job_class``) turns missing allow-list / test
    command / risks into findings that fail the gate. The default lenient mode
    keeps legacy plans passing but records the same gaps as caveats, led by
    ``lenient_plan``, so a weak plan never passes silently.
    """
    if job_class and not strict:
        strict = job_class.strip().lower() in _CODE_JOB_CLASSES
    base = Path(root).resolve()
    candidate = base / plan_path
    findings: list[str] = []
    if not candidate.is_file():
        return PlanReport(
            gate="FAIL",
            plan_path=plan_path,
            findings=("plan_file_missing",),
            strict=strict,
        )
    try:
        text = candidate.read_text(encoding="utf-8")
    except OSError:
        return PlanReport(
            gate="FAIL",
            plan_path=plan_path,
            findings=("plan_unreadable",),
            strict=strict,
        )
    if len(text) > MAX_PLAN_CHARS:
        findings.append("plan_too_long")
    lowered = text.lower()
    missing = tuple(section for section in REQUIRED_SECTIONS if section.lower() not in lowered)
    if missing:
        findings.append("missing_sections")

    files_body = _section_body(text, "Files")
    allowed = tuple(
        sorted(
            {
                path.strip().replace("\\", "/").strip("./")
                for path in re.findall(r"`([^`]+)`", files_body)
            }
        )
    )
    tests_body = _section_body(text, "Tests")
    test_command = _concrete_test_command(tests_body)
    risks_body = _section_body(text, "Risks")

    gaps: list[str] = []
    if not allowed:
        gaps.append("no_file_allowlist")
    if not test_command:
        gaps.append("no_named_test_command")
    if not risks_body or _is_placeholder(risks_body):
        gaps.append("no_risks_declared")

    caveats: list[str] = ["plan_is_a_claim_not_a_guarantee"]
    if strict:
        findings.extend(gaps)
    else:
        caveats.append("lenient_plan")
        caveats.extend(gaps)

    gate = "PASS" if not findings else "FAIL"
    return PlanReport(
        gate=gate,
        plan_path=plan_path,
        missing_sections=missing,
        allowed_files=allowed,
        findings=tuple(findings),
        caveats=tuple(caveats),
        strict=strict,
        test_command=test_command,
        declared_risks=risks_body,
    )


def check_code_plan(root: str | Path, plan_path: str = "PLAN.md") -> PlanReport:
    """Strict variant for code jobs: all quality signals are required."""
    return check_plan(root, plan_path, strict=True)


def path_allowed(path: str, allowed_files: tuple[str, ...]) -> bool:
    """A path is allowed if it is listed or sits under a listed directory.

    An empty allow-list remains permissive for legacy plans; code-job callers
    should use ``check_code_plan`` so the allow-list is guaranteed non-empty.
    """
    if not allowed_files:
        return True
    normal = path.replace("\\", "/").strip("./")
    return any(
        normal == item or normal.startswith(item.rstrip("/") + "/") for item in allowed_files
    )


__all__ = [
    "MAX_PLAN_CHARS",
    "REQUIRED_SECTIONS",
    "PlanReport",
    "check_code_plan",
    "check_plan",
    "path_allowed",
]
