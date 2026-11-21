"""Offline tests for governed self-improvement skill distillation and retrieval."""

from __future__ import annotations

from dataclasses import replace

import pytest

from agency.selfimprove.distiller import TaskTrace, distill_skill
from agency.selfimprove.loop import SelfImprovementLoop
from agency.skills.executor import (
    DOCUMENTATION_ONLY_IMPLEMENTATION,
    OperationRegistry,
    SkillExecutor,
)
from agency.skills.registry import SkillRegistry, SkillSpec, SkillStatus


def _registry(tmp_path: object) -> SkillRegistry:
    return SkillRegistry(str(tmp_path / "skills.db"))  # type: ignore[operator]


def _spec(skill_id: str, version: str, *, status: SkillStatus = SkillStatus.DRAFT) -> SkillSpec:
    return SkillSpec(
        skill_id=skill_id,
        version=version,
        name="Document search",
        description="Search documents by keyword.",
        implementation="search.documents",
        provenance="test",
        status=status,
    )


def _publish(registry: SkillRegistry, skill_id: str, version: str) -> None:
    registry.transition(skill_id, version, SkillStatus.TESTING)
    registry.transition(skill_id, version, SkillStatus.APPROVED, "offline test")
    registry.publish(skill_id, version, "offline test")


def test_distillation_creates_only_a_draft_and_versions_repeat_title(tmp_path) -> None:
    registry = _registry(tmp_path)
    try:
        trace = TaskTrace(
            task_id="task-1",
            title="Analyze Release Notes",
            description="Extract breaking changes.",
        )
        first = distill_skill(trace, registry)
        second = distill_skill(trace, registry)

        assert first is not None
        assert first.skill_id == "learned-analyze-release-notes"
        assert first.version == "1.0.0"
        assert first.status is SkillStatus.DRAFT
        assert second is not None
        assert second.version == "1.0.1"
        assert [spec.status for spec in registry.list_versions(first.skill_id)] == [
            SkillStatus.DRAFT,
            SkillStatus.DRAFT,
        ]
    finally:
        registry.close()


@pytest.mark.parametrize(
    "trace",
    [
        TaskTrace(task_id="failed", title="Usable title", status="failed"),
        TaskTrace(task_id="blank", title="   "),
    ],
)
def test_distillation_rejects_non_completed_or_invalid_traces(tmp_path, trace: TaskTrace) -> None:
    registry = _registry(tmp_path)
    try:
        assert distill_skill(trace, registry) is None
        assert registry._conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0] == 0
    finally:
        registry.close()


class _FailingRegistry:
    def list_versions(self, skill_id: str) -> list[SkillSpec]:
        raise RuntimeError(f"database unavailable for {skill_id}")

    def register(self, spec: SkillSpec) -> SkillSpec:
        raise AssertionError("registration must not be attempted")


def test_distillation_returns_none_when_version_lookup_fails() -> None:
    trace = TaskTrace(task_id="task-1", title="Recover documentation")

    assert distill_skill(trace, _FailingRegistry()) is None  # type: ignore[arg-type]


def test_relevant_skills_returns_only_highest_published_version(tmp_path) -> None:
    registry = _registry(tmp_path)
    try:
        registry.register(_spec("document-search", "1.0.0"))
        registry.register(_spec("document-search", "2.0.0"))
        registry.register(_spec("draft-document-search", "9.0.0"))
        _publish(registry, "document-search", "1.0.0")
        _publish(registry, "document-search", "2.0.0")

        matches = SelfImprovementLoop(registry).relevant_skills("document search")

        assert [(spec.skill_id, spec.version, spec.status) for spec in matches] == [
            ("document-search", "2.0.0", SkillStatus.PUBLISHED)
        ]
    finally:
        registry.close()


@pytest.mark.parametrize("limit", [0, -1])
def test_relevant_skills_returns_no_results_for_nonpositive_limit(tmp_path, limit: int) -> None:
    registry = _registry(tmp_path)
    try:
        registry.register(_spec("document-search", "1.0.0"))
        _publish(registry, "document-search", "1.0.0")

        assert SelfImprovementLoop(registry).relevant_skills("document search", limit=limit) == []
    finally:
        registry.close()


def test_relevant_skills_rejects_boolean_limit(tmp_path) -> None:
    registry = _registry(tmp_path)
    try:
        with pytest.raises(TypeError, match="limit"):
            SelfImprovementLoop(registry).relevant_skills("document search", limit=True)
    finally:
        registry.close()


def test_evolve_creates_immutable_successor_draft_with_new_evidence(tmp_path) -> None:
    registry = _registry(tmp_path)
    try:
        registry.register(_spec("document-search", "1.0.0"))
        _publish(registry, "document-search", "1.0.0")

        evolved = SelfImprovementLoop(registry).evolve(
            "document-search",
            TaskTrace(
                task_id="task-42",
                title="Search release documents",
                description="Include archived release notes when searching.",
                subtask_outputs=["Archive query returned the missing migration note."],
            ),
        )

        assert evolved is not None
        assert evolved.version == "1.0.1"
        assert evolved.status is SkillStatus.DRAFT
        assert "archived release notes" in evolved.description
        assert evolved.implementation == "search.documents"
        assert registry.get("document-search", "1.0.0").status is SkillStatus.PUBLISHED
    finally:
        registry.close()


def test_distilled_skills_are_documentation_only_and_executor_reports_it(tmp_path) -> None:
    registry = _registry(tmp_path)
    try:
        trace = TaskTrace(
            task_id="task-1",
            title="Analyze Release Notes",
            description="Extract breaking changes.",
        )
        spec = distill_skill(trace, registry)
        assert spec is not None
        assert spec.implementation == DOCUMENTATION_ONLY_IMPLEMENTATION
        _publish(registry, spec.skill_id, spec.version)

        result = SkillExecutor(OperationRegistry()).run(registry.get(spec.skill_id, spec.version))

        assert result.success is False
        assert result.error == "skill is documentation-only; no executable operation"
    finally:
        registry.close()


def test_evolved_successor_of_documentation_only_skill_keeps_the_sentinel(tmp_path) -> None:
    registry = _registry(tmp_path)
    try:
        trace = TaskTrace(
            task_id="task-1",
            title="Analyze Release Notes",
            description="Extract breaking changes.",
        )
        spec = distill_skill(trace, registry)
        assert spec is not None
        _publish(registry, spec.skill_id, spec.version)

        evolved = SelfImprovementLoop(registry).evolve(
            spec.skill_id,
            TaskTrace(
                task_id="task-2",
                title="Analyze Release Notes",
                description="Also flag deprecations.",
            ),
        )

        assert evolved is not None
        assert evolved.implementation == DOCUMENTATION_ONLY_IMPLEMENTATION
        result = SkillExecutor(OperationRegistry()).run(
            replace(evolved, status=SkillStatus.PUBLISHED)
        )

        assert result.success is False
        assert result.error == "skill is documentation-only; no executable operation"
    finally:
        registry.close()


def test_unregistered_learned_names_keep_unknown_operation_behavior(tmp_path) -> None:
    registry = _registry(tmp_path)
    try:
        registry.register(
            SkillSpec(
                skill_id="legacy-learned",
                version="1.0.0",
                name="Legacy learned skill",
                description="Persisted before the documentation-only sentinel existed.",
                implementation="learned.legacy_name",
                provenance="test",
                status=SkillStatus.DRAFT,
            )
        )
        _publish(registry, "legacy-learned", "1.0.0")

        result = SkillExecutor(OperationRegistry()).run(registry.get("legacy-learned", "1.0.0"))

        assert result.success is False
        assert result.error == "unknown operation: 'learned.legacy_name'"
    finally:
        registry.close()
