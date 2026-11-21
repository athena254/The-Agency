"""Distill a completed task trace into a candidate SkillSpec (DRAFT).

The distillation is deliberately heuristic and deterministic so it works
offline (no LLM dependency) and is fully testable. It never executes or
imports anything; it only proposes metadata for the versioned registry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agency.skills.executor import DOCUMENTATION_ONLY_IMPLEMENTATION
from agency.skills.registry import SkillRegistry, SkillSpec, SkillStatus

_SLUG_RE = re.compile(r"[^a-z0-9]+")


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

    The skill is documentation-only metadata (gaps.md #4): its implementation
    is the documentation-only sentinel, never a registered operation.
    Returns None when the trace is not distillable (failed task, empty title,
    or a registry error). Never raises for normal distillation failures.
    """
    if trace.status.strip().lower() != "completed":
        return None
    if not trace.title.strip():
        return None

    try:
        slug = _slugify(trace.title)
        skill_id = f"learned-{slug}"[:64]
        version = _next_version(registry, skill_id)
        spec = SkillSpec(
            skill_id=skill_id,
            version=version,
            name=trace.title.strip()[:120],
            description=_description_from_trace(trace),
            inputs={"task": {"type": "string"}},
            outputs={"result": {"type": "string"}},
            permissions=(),
            implementation=DOCUMENTATION_ONLY_IMPLEMENTATION,
            provenance=f"task:{trace.task_id}",
            status=SkillStatus.DRAFT,
        )
        return registry.register(spec)
    except (ValueError, KeyError):
        return None
    except Exception:  # noqa: BLE001 - distillation must never break a task
        return None


def evolve_skill(
    skill_id: str,
    trace: TaskTrace,
    registry: SkillRegistry,
) -> SkillSpec | None:
    """Create a DRAFT successor from a published skill and new task evidence.

    The prior version is read-only. The successor retains its logical operation
    and requested permissions; changed authority requires separate review.
    Documentation-only predecessors therefore keep the sentinel (gaps.md #4).
    """
    if trace.status.strip().lower() != "completed" or not trace.description.strip():
        return None
    try:
        published = [
            item
            for item in registry.list_versions(skill_id)
            if item.status is SkillStatus.PUBLISHED
        ]
        if not published:
            return None
        current = max(published, key=lambda item: tuple(int(p) for p in item.version.split(".")))
        version = _next_version(registry, skill_id)
        evidence = _description_from_trace(trace)
        if trace.subtask_outputs:
            evidence = f"{evidence} Observed outcome: {trace.subtask_outputs[0][:300]}"
        return registry.register(
            SkillSpec(
                skill_id=current.skill_id,
                version=version,
                name=current.name,
                description=evidence[:500],
                inputs=current.inputs,
                outputs=current.outputs,
                permissions=current.permissions,
                implementation=current.implementation,
                provenance=f"evolved:{current.skill_id}@{current.version}:task:{trace.task_id}",
                status=SkillStatus.DRAFT,
            )
        )
    except Exception:  # noqa: BLE001 - evolution must not break completed work.
        return None
