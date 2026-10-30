"""Offline tests for native skill operation resolution and execution guards."""

from __future__ import annotations

import pytest

from agency.skills.executor import OperationRegistry, SkillExecutor
from agency.skills.registry import SkillSpec, SkillStatus


def _skill(
    *,
    skill_id: str = "document-search",
    version: str = "2.3.4",
    status: SkillStatus = SkillStatus.PUBLISHED,
    permissions: tuple[str, ...] = (),
    implementation: str = "search.documents",
) -> SkillSpec:
    return SkillSpec(
        skill_id=skill_id,
        version=version,
        name="Document search",
        description="Search documents by keyword.",
        permissions=permissions,
        implementation=implementation,
        provenance="test",
        status=status,
    )


def test_operation_registry_rejects_blank_names_and_lists_names() -> None:
    operations = OperationRegistry()

    with pytest.raises(ValueError, match="nonblank"):
        operations.register("  ", lambda payload: payload)

    operations.register("z.operation", lambda payload: payload)
    operations.register("a.operation", lambda payload: payload)

    assert operations.names() == ["a.operation", "z.operation"]
    assert operations.get(" z.operation ") is not None


def test_published_skill_runs_operation_and_preserves_identity_and_version() -> None:
    operations = OperationRegistry()
    received: list[dict[str, object]] = []

    def operation(payload: dict[str, object]) -> dict[str, object]:
        received.append(payload)
        return {"count": len(payload)}

    operations.register("search.documents", operation)
    result = SkillExecutor(operations).run(_skill(), {"query": "release notes"})

    assert result.skill_id == "document-search"
    assert result.version == "2.3.4"
    assert result.success is True
    assert result.output == {"count": 1}
    assert result.error is None
    assert received == [{"query": "release notes"}]


def test_execution_requires_explicitly_published_skill_before_calling_operation() -> None:
    operations = OperationRegistry()
    calls: list[dict[str, object]] = []
    operations.register("search.documents", lambda payload: calls.append(payload))

    result = SkillExecutor(operations).run(_skill(status=SkillStatus.DRAFT), {"query": "x"})

    assert result.success is False
    assert result.error == "skill not published"
    assert result.skill_id == "document-search"
    assert result.version == "2.3.4"
    assert calls == []


def test_execution_enforces_permissions_without_calling_operation() -> None:
    operations = OperationRegistry()
    calls: list[dict[str, object]] = []
    operations.register("search.documents", lambda payload: calls.append(payload))
    skill = _skill(permissions=("documents.read",))

    denied = SkillExecutor(operations).run(skill, {"query": "x"})
    allowed = SkillExecutor(operations, frozenset({"documents.read"})).run(skill, {"query": "x"})

    assert denied.success is False
    assert denied.error == "permission denied"
    assert allowed.success is True
    assert calls == [{"query": "x"}]


def test_execution_reports_unknown_operation_and_operation_exceptions() -> None:
    operations = OperationRegistry()
    missing = SkillExecutor(operations).run(_skill(implementation="missing.operation"))
    assert missing.success is False
    assert missing.error == "unknown operation: 'missing.operation'"

    def broken(payload: dict[str, object]) -> None:
        raise RuntimeError(f"broken: {payload['query']}")

    operations.register("search.documents", broken)
    failed = SkillExecutor(operations).run(_skill(), {"query": "offline"})

    assert failed.success is False
    assert failed.error == "RuntimeError: broken: offline"


@pytest.mark.parametrize("invalid_inputs", [[], ["not", "a", "mapping"], "not a mapping"])
def test_execution_rejects_non_mapping_inputs_without_calling_operation(
    invalid_inputs: object,
) -> None:
    operations = OperationRegistry()
    calls: list[dict[str, object]] = []
    operations.register("search.documents", lambda payload: calls.append(payload))

    result = SkillExecutor(operations).run(_skill(), invalid_inputs)  # type: ignore[arg-type]

    assert result.success is False
    assert result.error == "inputs must be a dictionary"
    assert calls == []
