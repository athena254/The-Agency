"""Deterministic decomposition strategies for the autonomy engine.

``forge_build_strategy`` tags build-type subtasks with the ``forge``
capability so the engine spawns/assigns the Blacksmith (the forge manager)
instead of a generic worker. The default planner strategy is unchanged;
this one is opt-in via ``AgentPlanner(strategy=forge_build_strategy)``.
"""

from __future__ import annotations

import re

from agency.agents.planner import SubtaskSpec

_BUILD_RE = re.compile(r"\b(build|create|implement|make|write|add|develop)\b", re.IGNORECASE)
_OBJECT_RE = re.compile(
    r"\b(tool|module|function|component|script|library|package|endpoint|service)\b",
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
MAX_TITLE_CHARS = 120


def forge_build_strategy(task: str) -> list[SubtaskSpec]:
    """Split a task into clauses; tag build clauses with ``forge``.

    A clause is a build clause when it contains both a build verb and a
    buildable object noun. Everything else stays capability-free (the
    engine's generic fallback handles it). Deterministic, no LLM.
    """
    parts = [p.strip() for p in _SPLIT_RE.split(task.strip()) if p.strip()]
    if not parts:
        parts = [task.strip()] if task.strip() else []
    specs: list[SubtaskSpec] = []
    for part in parts:
        is_build = bool(_BUILD_RE.search(part) and _OBJECT_RE.search(part))
        specs.append(
            SubtaskSpec(
                title=part[:MAX_TITLE_CHARS],
                description=part,
                required_capabilities=["forge"] if is_build else [],
            )
        )
    return specs


__all__ = ["forge_build_strategy"]
