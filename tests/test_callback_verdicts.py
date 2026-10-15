"""Only literal True permits existing factory/workflow callback boundaries."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest

from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.factory.service import APPROVE_PERMISSION, AgentFactory, BlueprintStatus
from agency.kernel.registry import AgentRegistry as KernelRegistry
from agency.workflows.executor import WorkflowExecutor
from agency.workflows.registry import WorkflowDefinition, WorkflowRegistry, WorkflowStep


class TruthyVerdict:
    def __init__(self) -> None:
        self.bool_calls = 0

    def __bool__(self) -> bool:
        self.bool_calls += 1
        return True


class RaisingBoolVerdict(TruthyVerdict):
    def __bool__(self) -> bool:
        self.bool_calls += 1
        raise RuntimeError("verdict truthiness must not be evaluated")


class VerdictProbe:
    def __init__(self, case: str) -> None:
        self.case = case
        self.calls: list[tuple[str, str]] = []
        self.value: object = {
            "true": True,
            "false": False,
            "none": None,
            "zero": 0,
            "one": 1,
            "negative": -1,
            "float-one": 1.0,
            "empty-string": "",
            "string": "approved",
            "empty-list": [],
            "list": [True],
            "empty-dict": {},
            "dict": {"approved": True},
            "tuple": (True,),
            "set": {1},
            "truthy-object": TruthyVerdict(),
            "raising-bool": RaisingBoolVerdict(),
        }.get(case)

    @property
    def callback(self) -> Callable[[str, str], bool] | None:
        return None if self.case == "missing" else self

    def __call__(self, first: str, second: str) -> bool:
        self.calls.append((first, second))
        if self.case == "raising":
            raise RuntimeError("callback failed")
        if self.case == "permission-error":
            raise PermissionError("callback denied")
        # Deliberately violate the injected callback's declared return type.
        return cast(bool, self.value)

    def assert_not_coerced(self) -> None:
        if isinstance(self.value, TruthyVerdict):
            assert self.value.bool_calls == 0


@pytest.fixture(
    params=[
        "true",
        "false",
        "none",
        "zero",
        "one",
        "negative",
        "float-one",
        "empty-string",
        "string",
        "empty-list",
        "list",
        "empty-dict",
        "dict",
        "tuple",
        "set",
        "truthy-object",
        "raising-bool",
        "raising",
        "permission-error",
        "missing",
    ]
)
def verdict(request: pytest.FixtureRequest) -> VerdictProbe:
    return VerdictProbe(request.param)


def test_factory_authorizer_requires_literal_true(tmp_path: Path, verdict: VerdictProbe) -> None:
    kernels, runtimes = KernelRegistry(), RuntimeRegistry()
    publication_calls: list[tuple[str, str]] = []

    def published(ref: str, version: str) -> bool:
        publication_calls.append((ref, version))
        return True

    factory = AgentFactory(
        str(tmp_path / "factory.db"),
        authorizer=verdict.callback,
        is_skill_published=published,
        kernel_registry=kernels,
        runtime_registry=runtimes,
    )
    try:
        before = factory.create(
            "analyst", "1.0.0", "Analyst", creator="creator", skill_refs=[("skill", "1.0.0")]
        )
        events = factory.list_events("analyst", "1.0.0")
        if verdict.case == "true":
            approved = factory.approve(
                "analyst", "1.0.0", approver="approver", evidence="synthetic-review"
            )
            assert approved.status is BlueprintStatus.APPROVED
            assert publication_calls == [("skill", "1.0.0")]
        else:
            with pytest.raises(PermissionError):
                factory.approve(
                    "analyst", "1.0.0", approver="approver", evidence="synthetic-review"
                )
            assert factory.get("analyst", "1.0.0") == before
            assert factory.list_events("analyst", "1.0.0") == events
            assert publication_calls == []
        assert verdict.calls == (
            [] if verdict.case == "missing" else [("approver", APPROVE_PERMISSION)]
        )
        verdict.assert_not_coerced()
        assert kernels.count(include_revoked=True) == 0
        assert runtimes.list_agents() == []
    finally:
        factory.close()


@pytest.mark.parametrize("kind", ["skill", "workflow"])
@pytest.mark.parametrize("entrypoint", ["validate", "approve", "activate"])
def test_factory_publication_requires_literal_true(
    tmp_path: Path, verdict: VerdictProbe, kind: str, entrypoint: str
) -> None:
    kernels, runtimes = KernelRegistry(), RuntimeRegistry()
    db = str(tmp_path / "factory.db")
    refs = {f"{kind}_refs": [(kind, "1.0.0")]}
    seed = AgentFactory(
        db,
        authorizer=lambda _actor, _permission: True,
        is_skill_published=lambda _ref, _version: True,
        is_workflow_published=lambda _ref, _version: True,
    )
    try:
        seed.create("analyst", "1.0.0", "Analyst", creator="creator", **refs)
        if entrypoint == "activate":
            seed.approve("analyst", "1.0.0", approver="approver", evidence="synthetic-review")
    finally:
        seed.close()

    factory = AgentFactory(
        db,
        authorizer=lambda _actor, _permission: True,
        is_skill_published=verdict.callback if kind == "skill" else None,
        is_workflow_published=verdict.callback if kind == "workflow" else None,
        kernel_registry=kernels,
        runtime_registry=runtimes,
    )
    kernel_register = Mock(wraps=kernels.register)
    runtime_register = Mock(wraps=runtimes.register)
    kernels.register = kernel_register
    runtimes.register = runtime_register
    try:
        before = factory.get("analyst", "1.0.0")
        events = factory.list_events("analyst", "1.0.0")

        def invoke() -> object:
            if entrypoint == "approve":
                return factory.approve(
                    "analyst", "1.0.0", approver="approver", evidence="synthetic-review"
                )
            return getattr(factory, entrypoint)("analyst", "1.0.0")

        if verdict.case == "true":
            invoke()
            expected = {
                "validate": BlueprintStatus.DRAFT,
                "approve": BlueprintStatus.APPROVED,
                "activate": BlueprintStatus.ACTIVE,
            }[entrypoint]
            assert factory.get("analyst", "1.0.0").status is expected
            if entrypoint == "activate":
                kernel_register.assert_called_once()
                runtime_register.assert_called_once()
        else:
            with pytest.raises(ValueError, match=f"{kind} ref"):
                invoke()
            assert factory.get("analyst", "1.0.0") == before
            assert factory.list_events("analyst", "1.0.0") == events
        assert verdict.calls == ([] if verdict.case == "missing" else [(kind, "1.0.0")])
        verdict.assert_not_coerced()
        if verdict.case != "true" or entrypoint != "activate":
            kernel_register.assert_not_called()
            runtime_register.assert_not_called()
            assert kernels.count(include_revoked=True) == 0
            assert runtimes.list_agents() == []
    finally:
        factory.close()


@pytest.mark.parametrize("mode", ["ordinary", "recoverable"])
def test_workflow_authorizer_requires_literal_true(
    tmp_path: Path, verdict: VerdictProbe, mode: str
) -> None:
    registry = WorkflowRegistry(str(tmp_path / "workflow.db"))
    operation = Mock(return_value={"ok": True})
    later = Mock(return_value={"later": True})
    definition = WorkflowDefinition(
        "workflow",
        "1.0.0",
        (
            WorkflowStep("guarded", "operation", permissions=("data.read",)),
            WorkflowStep("later", "later", depends_on=("guarded",)),
        ),
    )
    try:
        registry.register(definition)
        executor = WorkflowExecutor(
            registry,
            {"operation": operation, "later": later},
            authorizer=verdict.callback,
            replay_safe=frozenset({"operation", "later"}),
            operation_versions={"operation": "1", "later": "1"},
        )
        inputs = {"synthetic": "input"}
        if mode == "ordinary":
            run = executor.run("workflow", "1.0.0", inputs, "actor")
            stored = registry.get_run(run.run_id)
        else:
            run = executor.run_recoverable("workflow", "1.0.0", inputs, "actor")
            stored = registry.get_recoverable_run(run.run_id)
        if verdict.case == "true":
            assert run.status == "COMPLETED"
            operation.assert_called_once_with(inputs, {})
            later.assert_called_once_with(inputs, {"guarded": {"ok": True}})
        else:
            assert run.status == "FAILED"
            assert run.step_results == {
                "guarded": {
                    "status": "failed",
                    "output": None,
                    "error": "permission denied: data.read",
                }
            }
            operation.assert_not_called()
            later.assert_not_called()
        assert stored.status == run.status
        assert stored.step_results == run.step_results
        assert registry.get("workflow", "1.0.0") == definition
        assert inputs == {"synthetic": "input"}
        assert verdict.calls == ([] if verdict.case == "missing" else [("actor", "data.read")])
        verdict.assert_not_coerced()
    finally:
        registry.close()
