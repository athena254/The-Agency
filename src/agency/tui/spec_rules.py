"""Deterministic spec-minimum rules. LLM proposes, this code disposes.

Dynamic per system type: the same request needs different evidence
depending on what is being built. Rules are versioned, fail-closed data
+ predicates; no LLM call is made here.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

_MARKER_KEYS = (
    "goal",
    "scope",
    "acceptance",
    "test_plan",
    "non_goals",
    "repo",
    "stack",
    "journeys",
)
_MARKER_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")


class SystemType(str, Enum):
    WEB_APP = "web-app"
    CLI = "cli"
    LIBRARY = "library"
    SERVICE = "service"
    AGENCY_INTERNAL = "agency-internal"
    UNKNOWN = "unknown"


class SpecDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = ""
    system_type: SystemType = SystemType.UNKNOWN
    scope: str = ""
    non_goals: str = ""
    acceptance: str = ""
    test_plan: str = ""
    repo: str = ""
    stack: str = ""
    journeys: str = ""


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gate: str = "FAIL"
    system_type: SystemType = SystemType.UNKNOWN
    missing: tuple[str, ...] = ()
    ambiguous: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.gate == "PASS"


REQUIRED: dict[SystemType, tuple[str, ...]] = {
    SystemType.WEB_APP: ("goal", "scope", "acceptance", "test_plan", "repo", "journeys"),
    SystemType.CLI: ("goal", "scope", "acceptance", "test_plan", "repo"),
    SystemType.LIBRARY: ("goal", "scope", "acceptance", "test_plan", "repo"),
    SystemType.SERVICE: ("goal", "scope", "acceptance", "test_plan", "repo"),
    SystemType.AGENCY_INTERNAL: ("goal", "scope", "non_goals", "acceptance", "test_plan", "repo"),
    SystemType.UNKNOWN: ("goal", "scope", "acceptance", "test_plan", "repo"),
}

QUESTION_FOR_FIELD: dict[str, str] = {
    "goal": "What is the goal in one sentence?",
    "scope": "What is in scope (and what is explicitly out)?",
    "non_goals": "What is explicitly out of scope?",
    "acceptance": "What observable outcome proves it works (acceptance criteria)?",
    "test_plan": "How will it be tested (command / scenario)?",
    "repo": "Which repository / directory should Forge build in?",
    "journeys": "List 1-3 user journeys (start -> action -> expected result).",
    "stack": "Which stack / language / framework?",
}

AMBIGUOUS_PHRASES = ("something", "stuff", "etc", "asap", "fix it", "make it better", "whatever")


def classify_system(repo: str = "", description: str = "") -> SystemType:
    text = (description + " " + repo).lower()
    if "agency" in text or "forge" in text:
        return SystemType.AGENCY_INTERNAL
    if any(
        k in text for k in ("web app", "website", "fastapi", "react", "http", "endpoint", "page")
    ):
        return SystemType.WEB_APP
    if "cli" in text or "command line" in text or "terminal" in text:
        return SystemType.CLI
    if "library" in text or "package" in text or "sdk" in text:
        return SystemType.LIBRARY
    if "service" in text or "daemon" in text or "server" in text:
        return SystemType.SERVICE
    root = Path(repo) if repo else Path(".")
    if root.is_dir():
        try:
            names = {p.name.lower() for p in root.iterdir()}
        except OSError:
            names = set()
        if "pyproject.toml" in names and ("app.py" in names or "main.py" in names):
            return SystemType.SERVICE
        if "package.json" in names or "vite.config.ts" in names:
            return SystemType.WEB_APP
    return SystemType.UNKNOWN


def check_spec(draft: SpecDraft) -> CheckResult:
    missing: list[str] = []
    values = draft.model_dump()
    for field_name in REQUIRED.get(draft.system_type, REQUIRED[SystemType.UNKNOWN]):
        if not str(values.get(field_name, "")).strip():
            missing.append(field_name)
    ambiguous: list[str] = []
    blob = " ".join(str(v) for v in values.values()).lower()
    for phrase in AMBIGUOUS_PHRASES:
        if phrase in blob:
            ambiguous.append(phrase)
    if draft.goal.strip() and len(draft.goal.strip().split()) < 3:
        ambiguous.append("goal_too_short")
    questions = tuple(QUESTION_FOR_FIELD[f] for f in missing)
    if ambiguous:
        questions += ("Clarify ambiguous wording: " + ", ".join(ambiguous),)
    gate = "PASS" if not missing and not ambiguous else "FAIL"
    return CheckResult(
        gate=gate,
        system_type=draft.system_type,
        missing=tuple(missing),
        ambiguous=tuple(ambiguous),
        questions=questions,
    )


def _parse_markers(text: str) -> tuple[dict[str, list[str]], list[str]]:
    """Split intake text into marker values and free prose (the goal)."""
    markers: dict[str, list[str]] = {}
    prose: list[str] = []
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _MARKER_RE.match(raw)
        key = match.group(1).lower() if match else ""
        if match and key in _MARKER_KEYS:
            current = key
            markers.setdefault(key, []).append(match.group(2).strip())
        elif current is not None and (raw[:1].isspace() or line.startswith(("- ", "* "))):
            markers[current].append(line)
        else:
            prose.append(line)
            current = None
    return markers, prose


def _marker_value(markers: dict[str, list[str]], key: str) -> str:
    return "\n".join(part for part in markers.get(key, []) if part).strip()


def draft_from_text(
    text: str, *, repo: str = "", system_type: SystemType | None = None
) -> SpecDraft:
    """Parse intake text into a spec draft.

    Marker syntax: a line starting with ``key: value`` (leading whitespace is
    allowed) sets one of goal, scope, acceptance, test_plan, non_goals, repo,
    stack or journeys. The value is the rest of that line; following indented
    lines and lines beginning with ``- `` or ``* `` are appended to it, one
    per line, so multi-line acceptance/test plans accumulate. Any other
    non-blank line is goal prose. An explicit ``goal:`` marker wins over prose;
    with neither, the whole stripped text is the goal (capped at 500 chars).
    Absent fields keep their fallbacks: scope from the whole text when it has
    at least 8 words, repo from the ``repo`` argument.
    """
    st = system_type or classify_system(repo, text)
    markers, prose = _parse_markers(text)
    goal = _marker_value(markers, "goal")
    if not goal:
        goal = " ".join(prose)[:500] if prose else text.strip()[:500]
    scope = _marker_value(markers, "scope") or (
        text.strip()[:500] if len(text.split()) >= 8 else ""
    )
    return SpecDraft(
        goal=goal,
        system_type=st,
        scope=scope,
        non_goals=_marker_value(markers, "non_goals"),
        acceptance=_marker_value(markers, "acceptance"),
        test_plan=_marker_value(markers, "test_plan"),
        repo=_marker_value(markers, "repo") or repo,
        stack=_marker_value(markers, "stack"),
        journeys=_marker_value(markers, "journeys"),
    )


def render_spec_md(draft: SpecDraft) -> str:
    return (
        f"# spec.md — {draft.system_type.value}\n\n## Goal\n{draft.goal}\n\n"
        f"## Scope\n{draft.scope}\n\n## Non-goals\n{draft.non_goals}\n\n"
        f"## Acceptance\n{draft.acceptance}\n\n## Test plan\n{draft.test_plan}\n\n"
        f"## Repo\n{draft.repo}\n\n## Stack\n{draft.stack}\n\n## Journeys\n{draft.journeys}\n"
    )
