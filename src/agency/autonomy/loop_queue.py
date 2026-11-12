"""Normalize goal plans, GitHub issues, and PRDs into loop work items.

Phase 2 of the autonomous loop: three work sources (a GOAL.md + fix_plan.md
pair, GitHub issues, a PRD document) are normalized into the frozen
``LoopItem`` queue consumed by the loop driver. Parsing is deterministic and
side-effect free; the GitHub source fails soft to an empty queue when the
``gh`` CLI is unavailable.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from agency.autonomy.loop_state import LoopItem
from agency.forge.backlog import slice_prd

logger = structlog.get_logger(__name__)

_TASK_RE = re.compile(r"^[-*+]\s+\[([ xX])\]\s+(.+?)\s*$")
_SUB_RE = re.compile(r"^\s+[-*+]\s+(.+?)\s*$")
_SUB_KEY_RE = re.compile(r"^([^:]+?)\s*:\s*(.*)$")
_ACCEPT_RE = re.compile(r"(?i)\b(?:test|acceptance)\s*:\s*(.+?)\s*$")


@dataclass
class _TaskDraft:
    title: str
    acceptance: list[str] = field(default_factory=list)
    deps: list[str] = field(default_factory=list)


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "item"


def _unique_slug(slug: str, used: set[str]) -> str:
    if slug not in used:
        used.add(slug)
        return slug
    suffix = 2
    while f"{slug}-{suffix}" in used:
        suffix += 1
    unique = f"{slug}-{suffix}"
    used.add(unique)
    return unique


def _extract_acceptance(text: str) -> tuple[str, ...]:
    values: list[str] = []
    for line in text.splitlines():
        match = _ACCEPT_RE.search(line)
        if match is None:
            continue
        value = match.group(1).strip()
        if value and value not in values:
            values.append(value)
    return tuple(values)


def _apply_sub_bullet(draft: _TaskDraft, text: str) -> _TaskDraft:
    match = _SUB_KEY_RE.match(text)
    if match is None:
        return draft
    key = match.group(1).strip().lower()
    value = match.group(2).strip()
    if not value:
        return draft
    if key in ("test", "acceptance"):
        draft.acceptance.append(value)
    elif key in ("depends on", "depends_on", "depends-on", "deps", "dep"):
        draft.deps.extend(part.strip() for part in value.split(",") if part.strip())
    return draft


def _parse_plan(plan_text: str) -> list[_TaskDraft]:
    drafts: list[_TaskDraft] = []
    current: _TaskDraft | None = None
    for raw in plan_text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        task = _TASK_RE.match(line)
        if task is not None:
            current = None
            if task.group(1).lower() == "x":
                continue
            title = task.group(2).strip()
            if not title:
                continue
            current = _TaskDraft(title=title)
            drafts.append(current)
            continue
        sub = _SUB_RE.match(line)
        if sub is not None and current is not None:
            _apply_sub_bullet(current, sub.group(1))
    return drafts


def parse_goal_plan(goal_path: str | Path, plan_path: str | Path) -> list[LoopItem]:
    """Parse a GOAL.md + fix_plan.md pair into ordered loop items.

    The goal text is attached to every item's ``description`` as shared
    context; the goal itself is not an item. Unchecked plan tasks become
    items in list order; checked tasks are skipped. Indented sub-bullets
    populate ``acceptance`` (``test:`` / ``acceptance:``) and ``deps``
    (``depends on:`` / ``deps:``). Priorities decrease with list order and
    ids are unique slugs of the task title.

    Raises ``FileNotFoundError`` when either file is missing.
    """
    goal_file = Path(goal_path)
    plan_file = Path(plan_path)
    if not goal_file.is_file():
        raise FileNotFoundError(f"goal file not found: {goal_file}")
    if not plan_file.is_file():
        raise FileNotFoundError(f"plan file not found: {plan_file}")

    goal_text = goal_file.read_text(encoding="utf-8").strip()
    drafts = _parse_plan(plan_file.read_text(encoding="utf-8"))

    items: list[LoopItem] = []
    used: set[str] = set()
    for index, draft in enumerate(drafts):
        items.append(
            LoopItem(
                id=_unique_slug(_slugify(draft.title), used),
                title=draft.title,
                description=goal_text,
                acceptance=tuple(dict.fromkeys(draft.acceptance)),
                priority=len(drafts) - index,
                deps=tuple(dict.fromkeys(draft.deps)),
            )
        )
    return items


def parse_github_issues(repo: str, *, label: str | None = None, limit: int = 64) -> list[LoopItem]:
    """Parse GitHub issues into loop items.

    Runs the ``gh`` CLI and maps each issue to an item with id
    ``issue-{number}``. Acceptance entries are read from body lines that
    contain ``test:`` or ``acceptance:``. Fails soft: a missing ``gh``,
    a non-zero exit, or an unparseable payload returns ``[]``.
    """
    command = [
        "gh",
        "issue",
        "list",
        "--repo",
        repo,
        "--json",
        "number,title,body,labels",
        "--limit",
        str(limit),
    ]
    if label:
        command += ["--label", label]

    try:
        proc = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        logger.warning("loop_queue.gh_unavailable", repo=repo, error=str(exc))
        return []
    if proc.returncode != 0:
        logger.warning(
            "loop_queue.gh_failed",
            repo=repo,
            returncode=proc.returncode,
            stderr=proc.stderr.strip()[:200],
        )
        return []
    try:
        issues = json.loads(proc.stdout)
    except json.JSONDecodeError:
        logger.warning("loop_queue.gh_bad_json", repo=repo)
        return []
    if not isinstance(issues, list):
        logger.warning("loop_queue.gh_unexpected_payload", repo=repo)
        return []

    raw: list[tuple[int, str, str]] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        number = issue.get("number")
        title = (issue.get("title") or "").strip()
        if not isinstance(number, int) or not title:
            continue
        raw.append((number, title, (issue.get("body") or "").strip()))

    return [
        LoopItem(
            id=f"issue-{number}",
            title=title,
            description=body,
            acceptance=_extract_acceptance(body),
            priority=len(raw) - index,
        )
        for index, (number, title, body) in enumerate(raw)
    ]


def from_backlog(prd_text: str) -> list[LoopItem]:
    """Map a PRD's backlog sections into loop items via ``slice_prd``."""
    report = slice_prd(prd_text)
    items: list[LoopItem] = []
    used: set[str] = set()
    for index, backlog_item in enumerate(report.items):
        items.append(
            LoopItem(
                id=_unique_slug(_slugify(backlog_item.title), used),
                title=backlog_item.title,
                description=backlog_item.body,
                acceptance=_extract_acceptance(backlog_item.body),
                priority=len(report.items) - index,
            )
        )
    return items


def load_queue(
    source: str | Path, *, repo: str | None = None, label: str | None = None
) -> list[LoopItem]:
    """Normalize one work source into a loop item queue.

    Dispatch rules, in order:

    1. an explicit ``repo`` selects the GitHub source;
    2. a ``source`` starting with ``gh:`` selects the GitHub source, with
       the rest of the string as the repo;
    3. a directory, or a path named ``GOAL.md`` / ``fix_plan.md``, selects
       the goal-plan source (the sibling file is the partner);
    4. anything else is read as a PRD text file.
    """
    if repo is not None:
        return parse_github_issues(repo, label=label)
    text = str(source)
    if text.startswith("gh:"):
        return parse_github_issues(text[3:], label=label)
    path = Path(source)
    if path.is_dir():
        return parse_goal_plan(path / "GOAL.md", path / "fix_plan.md")
    if path.name == "GOAL.md":
        return parse_goal_plan(path, path.with_name("fix_plan.md"))
    if path.name == "fix_plan.md":
        return parse_goal_plan(path.with_name("GOAL.md"), path)
    return from_backlog(path.read_text(encoding="utf-8"))


__all__ = [
    "from_backlog",
    "load_queue",
    "parse_github_issues",
    "parse_goal_plan",
]
