"""Distill a completed task trace into a candidate SkillSpec (DRAFT).

The distillation is deliberately heuristic and deterministic so it works
offline (no LLM dependency) and is fully testable. It never executes or
imports anything; it only proposes metadata for the versioned registry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agency.skills.registry import SkillRegistry, SkillSpec, SkillStatus

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_IMPL_RE_SAFE = re.compile(r"[^a-z0-9_.-]")


@dataclass
class TaskTrace:
    """Minimal record of one completed task execution."""

    task_id: str
    title: str
    description: str = ""
    status: str = "completed"
    agent_id: str | None = None
    subtask_outputs: list[str] = field(default_factory=list)
    tool_steps: list[dict[str, object]] = field(default_factory=list)


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    if not slug:
        slug = "learned-skill"
    if slug[0].isdigit():
        slug = f"skill-{slug}"
    return slug[:48]


def _impl_name(slug: str) -> str:
    base = slug.replace("-", "_")
    base = _IMPL_RE_SAFE.sub("", base)
    if not base or base[0].isdigit():
        base = f"skill_{base}" if base else "skill_learned"
    return f"learned.{base}"


def _next_version(registry: SkillRegistry, skill_id: str) -> str:
    versions = registry.list_versions(skill_id)
    if not versions:
        return "1.0.0"
    last = versions[-1].version
    major, minor, patch = last.split(".")
    return f"{major}.{minor}.{int(patch) + 1}"


def _description_from_trace(trace: TaskTrace) -> str:
    if trace.description.strip():
        return trace.description.strip()[:500]
    if trace.subtask_outputs:
        return str(trace.subtask_outputs[0])[:500]
    return f"Learned from task {trace.task_id}."


def distill_skill(
    trace: TaskTrace,
    registry: SkillRegistry,
    *,
    allowed_permissions: frozenset[str] | None = None,
) -> SkillSpec | None:
    """Propose and register a DRAFT skill from a completed task trace.

    Returns None when the trace is not distillable (failed task, empty title,
    or a registry error). Never raises for normal distillation failures.
    """
    if trace.status.strip().lower() != "completed":
        return None
    if not trace.title.strip():
        return None

    slug = _slugify(trace.title)
    skill_id = f"learned-{slug}"
    # Keep skill_id within a sane, non-path-like shape.
    skill_id = skill_id[:64]

    version = _next_version(registry, skill_id)
    impl = _impl_name(slug)

    spec = SkillSpec(
        skill_id=skill_id,
        version=version,
        name=trace.title.strip()[:120],
        description=_description_from_trace(trace),
        inputs={"task": {"type": "string"}},
        outputs={"result": {"type": "string"}},
        permissions=(),
        implementation=impl,
        provenance=f"task:{trace.task_id}",
        status=SkillStatus.DRAFT,
    )

    try:
        return registry.register(spec)
    except (ValueError, KeyError):
        return None
    except Exception:  # noqa: BLE001 - distillation must never break a task
        return None
