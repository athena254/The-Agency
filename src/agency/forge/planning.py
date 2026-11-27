"""Enforced pre-code planning gate for factory workers.

A plan is a claim set. This module checks that the claim exists and is shaped
well enough to execute, before any code gate is trusted.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict

REQUIRED_SECTIONS = ("Goal", "Approach", "Files", "Tests", "Risks")
MAX_PLAN_CHARS = 8000


class PlanReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gate: str
    plan_path: str
    missing_sections: tuple[str, ...] = ()
    allowed_files: tuple[str, ...] = ()
    findings: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ("plan_is_a_claim_not_a_guarantee",)


def check_plan(root: str | Path, plan_path: str = "PLAN.md") -> PlanReport:
    """Check that a bounded plan exists with all required sections."""
    base = Path(root).resolve()
    candidate = base / plan_path
    findings: list[str] = []
    if not candidate.is_file():
        return PlanReport(gate="FAIL", plan_path=plan_path, findings=("plan_file_missing",))
    try:
        text = candidate.read_text(encoding="utf-8")
    except OSError:
        return PlanReport(gate="FAIL", plan_path=plan_path, findings=("plan_unreadable",))
    if len(text) > MAX_PLAN_CHARS:
        findings.append("plan_too_long")
    lowered = text.lower()
    missing = tuple(section for section in REQUIRED_SECTIONS if section.lower() not in lowered)
    if missing:
        findings.append("missing_sections")

    files_section = ""
    match = re.search(r"files\s*:?(.*?)(?:\n\s*tests\s*:|\Z)", text, re.IGNORECASE | re.DOTALL)
    if match:
        files_section = match.group(1)
    allowed = tuple(
        sorted({p.replace("\\", "/").strip("./") for p in re.findall(r"`([^`]+)`", files_section)})
    )
    caveats = ["plan_is_a_claim_not_a_guarantee"]
    if not allowed:
        caveats.append("no_file_allowlist")

    gate = "PASS" if not findings else "FAIL"
    return PlanReport(
        gate=gate,
        plan_path=plan_path,
        missing_sections=missing,
        allowed_files=allowed,
        findings=tuple(findings),
        caveats=tuple(caveats),
    )


def path_allowed(path: str, allowed_files: tuple[str, ...]) -> bool:
    """A path is allowed if it is listed or sits under a listed directory."""
    if not allowed_files:
        return True
    normal = path.replace("\\", "/").strip("./")
    return any(
        normal == item or normal.startswith(item.rstrip("/") + "/") for item in allowed_files
    )
