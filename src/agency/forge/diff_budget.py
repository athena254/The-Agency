"""Bounded change-size budget for the Forge review gate.

``check_diff_budget`` is a pure function over caller-supplied per-file line
counts: it applies hard ceilings to the number of changed files and changed
lines, and returns an auditable verdict. An over-budget path is excused only
by an explicit :class:`BudgetException` carrying a reason and a human
approver; applied exceptions are recorded in the report and surfaced as a
caveat, so the gate can never turn an oversized change into a silent pass.
No git, filesystem, subprocess or network access happens here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

HARD_MAX_FILES = 64
HARD_MAX_LINES = 5_000
DEFAULT_MAX_FILES = 20
DEFAULT_MAX_LINES = 400

BudgetGate = Literal["PASS", "FAIL"]


class FileChange(BaseModel):
    """One path's added/deleted line counts. Counts are caller-supplied."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1)
    added: int = Field(ge=0)
    deleted: int = Field(ge=0)

    @property
    def changed_lines(self) -> int:
        return self.added + self.deleted


class BudgetException(BaseModel):
    """An explicit, attributable waiver for one path. Not a silent override."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    approved_by: str = Field(min_length=1)


class BudgetLimits(BaseModel):
    """Effective (clamped) ceilings applied by the check."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_files: int = Field(ge=1)
    max_changed_lines: int = Field(ge=1)


class DiffBudgetReport(BaseModel):
    """Immutable budget verdict with counts, reasons and audited waivers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gate: BudgetGate
    limits: BudgetLimits
    file_count: int = Field(ge=0)
    charged_files: int = Field(ge=0)
    changed_lines: int = Field(ge=0)
    charged_lines: int = Field(ge=0)
    reasons: tuple[str, ...] = ()
    exceptions_applied: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ("does_not_authorize_merge",)


def _normalize_path(path: str) -> str:
    return "/".join(part for part in path.replace("\\", "/").split("/") if part not in ("", "."))


def _is_safe_relative(path: str) -> bool:
    """True for a plain relative path with no traversal or drive prefix."""
    if not path or path.startswith("/") or ".." in path.split("/"):
        return False
    first = path.split("/", 1)[0]
    return not (len(first) == 2 and first[1] == ":")


def check_diff_budget(
    changes: list[FileChange],
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_changed_lines: int = DEFAULT_MAX_LINES,
    exceptions: list[BudgetException] | None = None,
) -> DiffBudgetReport:
    """Check *changes* against bounded file/line ceilings.

    Requested ceilings above the hard ceilings are clamped (and the clamp is
    reported as a caveat), so a caller cannot disable the budget by inflating
    limits. Duplicate or unsafe paths and malformed exceptions are denials,
    never ignored. Waivers apply to a path's file and line charges and must
    carry a non-blank ``reason`` and ``approved_by``.
    """
    if max_files < 1 or max_changed_lines < 1:
        raise ValueError("budget limits must be >= 1")

    caveats: list[str] = []
    effective_files = min(max_files, HARD_MAX_FILES)
    effective_lines = min(max_changed_lines, HARD_MAX_LINES)
    if effective_files != max_files or effective_lines != max_changed_lines:
        caveats.append("limits_clamped_to_hard_ceiling")
    limits = BudgetLimits(max_files=effective_files, max_changed_lines=effective_lines)

    reasons: list[str] = []
    if not changes:
        reasons.append("empty_change_set")

    valid: dict[str, BudgetException] = {}
    for waiver in exceptions or ():
        key = _normalize_path(waiver.path)
        if not key or not waiver.reason.strip() or not waiver.approved_by.strip():
            reasons.append(f"invalid_exception:{waiver.path[:80]}")
            continue
        valid[key] = waiver

    seen: set[str] = set()
    file_count = 0
    changed_lines = 0
    charged_files = 0
    charged_lines = 0
    for change in changes:
        key = _normalize_path(change.path)
        if not _is_safe_relative(key):
            reasons.append(f"unsafe_path:{change.path[:80]}")
            continue
        if key in seen:
            reasons.append(f"duplicate_path:{key[:80]}")
            continue
        seen.add(key)
        file_count += 1
        lines = change.changed_lines
        changed_lines += lines
        if key in valid:
            continue  # explicitly waived: still counted, just not charged
        charged_files += 1
        charged_lines += lines

    applied = tuple(sorted(set(valid) & seen))
    unused = sorted(set(valid) - seen)
    if applied:
        caveats.append("exceptions_applied_requires_human_review")
    if unused:
        caveats.append("unused_exceptions:" + ",".join(unused))
    if charged_files > limits.max_files:
        reasons.append(f"file_count_exceeded:{charged_files}>{limits.max_files}")
    if charged_lines > limits.max_changed_lines:
        reasons.append(f"changed_lines_exceeded:{charged_lines}>{limits.max_changed_lines}")

    gate: BudgetGate = "FAIL" if reasons else "PASS"
    return DiffBudgetReport(
        gate=gate,
        limits=limits,
        file_count=file_count,
        charged_files=charged_files,
        changed_lines=changed_lines,
        charged_lines=charged_lines,
        reasons=tuple(reasons),
        exceptions_applied=applied,
        caveats=tuple(caveats),
    )


__all__ = [
    "DEFAULT_MAX_FILES",
    "DEFAULT_MAX_LINES",
    "HARD_MAX_FILES",
    "HARD_MAX_LINES",
    "BudgetException",
    "BudgetGate",
    "BudgetLimits",
    "DiffBudgetReport",
    "FileChange",
    "check_diff_budget",
]
