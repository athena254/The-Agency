"""Striker work orders: the structured task contract for forge workers.

A striker (external coding agent) receives one rendered document instead of a
bare task string. The order carries the four Agency work rules (KISS, YAGNI,
Unix-style modularity, research -> draft plan -> final plan -> execute), the
job's boundaries (protected paths, isolation), verbatim acceptance criteria,
the exact test command, and the honest-failure contract. Pure rendering: no
LLM, no subprocess, no I/O.

Retry feedback: the caller passes the previous attempt's bounded, redacted
failure tail; the renderer includes it as a RETRY NOTE only when present.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal  # noqa: F401

from pydantic import BaseModel, Field, field_validator

from agency.forge.agent_skills import skill_for
from agency.forge.apprentice_policy import (
    PromotionEvidence,
    render_apprentice_policy,
)
from agency.forge.protected_paths import protected_paths

MAX_TASK_CHARS = 8000

_WORK_RULES = (
    "WORK RULES (Agency global rules — binding):\n"
    "1. KISS: the smallest change that fully solves the task wins.\n"
    "2. YAGNI: build only what is needed now; defer the rest.\n"
    "3. Modular and reusable (Unix philosophy): solve a problem once and reuse the\n"
    "   solution everywhere it fits; small pieces, one job each, no copy-paste forks.\n"
    "4. Research first, then draft a plan, refine it into a final plan, then execute\n"
    "   only the final plan. No edits before the final plan."
)


class WorkOrder(BaseModel):
    """One striker job, frozen; rendering is deterministic."""

    model_config = {"frozen": True, "extra": "forbid"}

    title: str = Field(min_length=1, max_length=200)
    task: str = Field(min_length=1, max_length=MAX_TASK_CHARS)
    repo: str = Field(min_length=1)
    acceptance: tuple[str, ...] = ()
    test_paths: tuple[str, ...] = ()
    test_command: str = ""
    attempt: int = Field(default=1, ge=1)
    max_attempts: int = Field(default=3, ge=1)
    failure_tail: str = ""
    role: str = "striker"
    plan_required: bool = False
    plan_path: str = "PLAN.md"
    # Apprentice-policy inputs; defaults keep existing striker orders unchanged.
    job_class: str = ""
    tool_budget: int = 0
    promotion_evidence: PromotionEvidence | None = None

    @field_validator("repo")
    @classmethod
    def _repo_absolute(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("repo must be an absolute path (isolation rule)")
        return value


def render_work_order(order: WorkOrder) -> str:
    """Render the work order document for one striker dispatch."""
    lines: list[str] = [
        "# STRIKER WORK ORDER",
        "",
        "OBJECTIVE:",
        f"- {order.title}: {order.task}",
        "",
        "CONTEXT:",
        f"- repo: {order.repo}",
        "- You work only inside the isolated worktree the dispatcher gives you.",
        "",
        "BOUNDARIES (hard):",
        "- Never touch protected paths: " + ", ".join(protected_paths()),
        (
            "- Also enforced (fail-closed): CI configs (.github/workflows, .gitlab-ci.yml, ...),"
            " agent/skill/hook control surfaces (.claude, .zcode, .mcp.json, .factory), and the"
            " repo-root governance files (AGENTS.md, CLAUDE.md, GEMINI.md, MISSION.md)."
        ),
        "- Change nothing outside this worktree; do not modify sibling repos.",
        "- No network installs, no subprocess use, no edits to CI or gates.",
        "",
        _WORK_RULES,
        "",
    ]
    if order.acceptance:
        lines += ["ACCEPTANCE (each item must hold verbatim):"]
        lines += [f"- {item}" for item in order.acceptance]
        lines.append("")
    lines += [
        "TESTS:",
        (
            f"- run: {order.test_command}"
            if order.test_command
            else "- run the repo's standard test command"
        ),
        (
            "- test paths: " + ", ".join(order.test_paths)
            if order.test_paths
            else "- test paths: none specified"
        ),
        "",
        "DELIVERABLE:",
        "- Leave the changed files in the worktree; list every path you touched.",
        "- Run the tests and report the real result.",
        "",
        "ON_FAILURE:",
        "- If blocked or a test will not pass, stop and return the exact error output.",
        "- Never claim a test or check you did not run; never fabricate success.",
    ]
    skill = skill_for(order.role)
    if skill:
        lines += ["", skill.rstrip()]
    policy = render_apprentice_policy(
        order.role,
        order.job_class,
        order.tool_budget,
        order.promotion_evidence,
    )
    if policy:
        lines += ["", policy]
    if order.plan_required:
        lines += [
            "",
            "PLAN GATE (hard):",
            f"- Write `{order.plan_path}` before editing any code.",
            "- Required headings: Goal, Approach, Files, Tests, Risks.",
            "- Forge refuses the result if that file is missing or incomplete.",
        ]
    if order.failure_tail:
        lines += [
            "",
            "RETRY NOTE:",
            f"- attempt {order.attempt} of {order.max_attempts}; previous attempt failed with:",
            "- " + order.failure_tail.strip().replace("\n", "\n- "),
        ]
    return "\n".join(lines)


__all__ = ["MAX_TASK_CHARS", "WorkOrder", "render_work_order"]
