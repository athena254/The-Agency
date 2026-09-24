"""Tests for workflow registry/executor v1 (brief 4)."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from agency.workflows.executor import WorkflowExecutor
from agency.workflows.registry import (
    MAX_JSON_BYTES,
    WorkflowDefinition,
    WorkflowRegistry,
    WorkflowRun,
    WorkflowStep,
)


def make_registry(tmp_path) -> WorkflowRegistry:
    return WorkflowRegistry(str(tmp_path / "workflows.db"))


def make_step(
    step_id: str,
    operation: str = "op",
    depends_on: tuple[str, ...] = (),
    permissions: tuple[str, ...] = (),
) -> WorkflowStep:
    return WorkflowStep(
        step_id=step_id,
        operation=operation,
        depends_on=depends_on,
        permissions=permissions,
    )


def make_definition(
    workflow_id: str = "demo",
    version: str = "1.0.0",
    steps: tuple[WorkflowStep, ...] | None = None,
) -> WorkflowDefinition:
    if steps is None:
        steps = (make_step("a"),)
    return WorkflowDefinition(workflow_id=workflow_id, version=version, steps=steps)


def test_dataclasses_immutable_and_defensive() -> None:
    step = make_step("a")
    with pytest.raises(FrozenInstanceError):
        step.step_id = "b"  # type: ignore[misc]
    definition = make_definition()
    with pytest.raises(FrozenInstanceError):
        definition.workflow_id = "x"  # type: ignore[misc]
    run = WorkflowRun(
        run_id="r1",
        workflow_id="demo",
        version="1.0.0",
        actor_id="actor",
        status="COMPLETED",
        step_results={"a": {"status": "ok"}},
        created_at="t",
        completed_at=None,
    )
    with pytest.raises(FrozenInstanceError):
        run.status = "FAILED"  # type: ignore[misc]
    # Defensive copy: mutating source dict must not affect the run.
    source: dict[str, dict] = {"a": {"status": "ok"}}
    run2 = WorkflowRun(
        run_id="r2",
        workflow_id="demo",
        version="1.0.0",
        actor_id="actor",
        status="RUNNING",
        step_results=source,
        created_at="t",
        completed_at=None,
    )
    source["a"]["status"] = "tampered"
    assert run2.step_results["a"] == {"status": "ok"}


def test_register_get_and_restart(tmp_path) -> None:
    db = str(tmp_path / "wf.db")
    registry = WorkflowRegistry(db)
    definition = make_definition(
        steps=(make_step("a"), make_step("b", depends_on=("a",))),
    )
    registry.register(definition)
    fetched = registry.get("demo", "1.0.0")
    assert fetched.workflow_id == "demo"
    assert [s.step_id for s in fetched.steps] == ["a", "b"]
    assert registry.list_versions("demo")[0].version == "1.0.0"

    executor = WorkflowExecutor(
        registry, {"op": lambda inputs, prev: {"echo": inputs.get("x")}}
    )
    run = executor.run("demo", "1.0.0", {"x": 1}, "alice")
    assert run.status == "COMPLETED"
    run_id = run.run_id
    registry.close()

    reopened = WorkflowRegistry(db)
    assert reopened.get("demo", "1.0.0").workflow_id == "demo"
    persisted = reopened.get_run(run_id)
    assert persisted.status == "COMPLETED"
    assert persisted.step_results["a"]["status"] == "ok"
    reopened.close()


@pytest.mark.parametrize(
    "bad",
    ["1.0", "v1.0.0", "1.0.0-alpha", "latest", "", "1..0", "1.0.0.0", "a.b.c"],
)
def test_malformed_version_rejected(tmp_path, bad: str) -> None:
    registry = make_registry(tmp_path)
    with pytest.raises(ValueError):
        registry.register(make_definition(version=bad))
    registry.close()
    with pytest.raises(ValueError):
        WorkflowDefinition(workflow_id="demo", version=bad, steps=(make_step("a"),))


def test_duplicate_version_rejected(tmp_path) -> None:
    registry = make_registry(tmp_path)
    registry.register(make_definition())
    with pytest.raises(ValueError):
        registry.register(make_definition())
    registry.close()


def test_duplicate_step_rejected(tmp_path) -> None:
    registry = make_registry(tmp_path)
    definition = make_definition(steps=(make_step("a"), make_step("a")))
    with pytest.raises(ValueError):
        registry.register(definition)
    registry.close()


def test_unknown_dependency_rejected(tmp_path) -> None:
    registry = make_registry(tmp_path)
    definition = make_definition(steps=(make_step("a", depends_on=("ghost",)),))
    with pytest.raises(ValueError):
        registry.register(definition)
    registry.close()


def test_self_dependency_rejected(tmp_path) -> None:
    registry = make_registry(tmp_path)
    definition = make_definition(steps=(make_step("a", depends_on=("a",)),))
    with pytest.raises(ValueError):
        registry.register(definition)
    registry.close()


def test_cycle_rejected(tmp_path) -> None:
    registry = make_registry(tmp_path)
    definition = make_definition(
        steps=(
            make_step("a", depends_on=("c",)),
            make_step("b", depends_on=("a",)),
            make_step("c", depends_on=("b",)),
        )
    )
    with pytest.raises(ValueError):
        registry.register(definition)
    registry.close()


def test_too_many_steps_rejected(tmp_path) -> None:
    registry = make_registry(tmp_path)
    steps = tuple(make_step(f"s{i}") for i in range(65))
    with pytest.raises(ValueError):
        registry.register(make_definition(steps=steps))
    registry.close()


def test_deterministic_ordering_and_output_flow(tmp_path) -> None:
    registry = make_registry(tmp_path)
    # Declaration order is c, b, a but a has no deps so ordering must respect
    # dependencies with declaration-index tie-break for ready nodes.
    definition = make_definition(
        steps=(
            make_step("c", operation="op_c", depends_on=("a", "b")),
            make_step("b", operation="op_b", depends_on=("a",)),
            make_step("a", operation="op_a"),
        )
    )
    registry.register(definition)
    seen: list[str] = []

    def op_a(inputs: dict, prev: dict) -> object:
        seen.append("a")
        return inputs["start"] + 1

    def op_b(inputs: dict, prev: dict) -> object:
        seen.append("b")
        assert prev["a"] == 2
        return prev["a"] * 10

    def op_c(inputs: dict, prev: dict) -> object:
        seen.append("c")
        assert prev["a"] == 2
        assert prev["b"] == 20
        return prev["b"] + 1

    executor = WorkflowExecutor(
        registry, {"op_a": op_a, "op_b": op_b, "op_c": op_c}
    )
    run = executor.run("demo", "1.0.0", {"start": 1}, "actor-1")
    assert run.status == "COMPLETED"
    assert seen == ["a", "b", "c"]
    assert run.step_results["c"]["output"] == 21
    registry.close()


def test_independent_steps_follow_declaration_order(tmp_path) -> None:
    registry = make_registry(tmp_path)
    definition = make_definition(
        steps=(make_step("y", operation="op"), make_step("x", operation="op"))
    )
    registry.register(definition)
    order: list[str] = []

    def op(inputs: dict, prev: dict) -> object:
        order.append("called")
        return 1

    calls: list[dict] = []

    def tracking(inputs: dict, prev: dict) -> object:
        calls.append(dict(prev))
        return len(calls)

    executor = WorkflowExecutor(registry, {"op": tracking})
    run = executor.run("demo", "1.0.0", {}, "actor-1")
    assert run.status == "COMPLETED"
    assert list(run.step_results.keys()) == ["y", "x"]
    assert order == []  # sanity: only `tracking` used
    registry.close()
    assert op({"a": 1}, {}) == 1


def test_old_version_unaffected_by_new_version(tmp_path) -> None:
    registry = make_registry(tmp_path)
    registry.register(
        make_definition(version="1.0.0", steps=(make_step("a", operation="v1"),))
    )
    registry.register(
        make_definition(
            version="1.0.1",
            steps=(make_step("a", operation="v2"), make_step("b", operation="v2")),
        )
    )
    old = registry.get("demo", "1.0.0")
    new = registry.get("demo", "1.0.1")
    assert [s.step_id for s in old.steps] == ["a"]
    assert [s.step_id for s in new.steps] == ["a", "b"]
    executor = WorkflowExecutor(
        registry,
        {"v1": lambda i, p: "old", "v2": lambda i, p: "new"},
    )
    run_old = executor.run("demo", "1.0.0", {}, "actor-1")
    assert run_old.version == "1.0.0"
    assert run_old.step_results["a"]["output"] == "old"
    assert "b" not in run_old.step_results
    registry.close()


def test_unknown_operation_fails_closed(tmp_path) -> None:
    registry = make_registry(tmp_path)
    registry.register(
        make_definition(
            steps=(
                make_step("a", operation="known"),
                make_step("b", operation="missing"),
            )
        )
    )
    called: list[str] = []

    def known(inputs: dict, prev: dict) -> object:
        called.append("a")
        return "ok"

    def should_not_run(inputs: dict, prev: dict) -> object:
        called.append("never")
        return "bad"

    executor = WorkflowExecutor(registry, {"known": known, "other": should_not_run})
    run = executor.run("demo", "1.0.0", {}, "actor-1")
    assert run.status == "FAILED"
    assert called == ["a"]
    assert run.step_results["b"]["error"] is not None
    assert "unknown operation" in run.step_results["b"]["error"]
    persisted = registry.get_run(run.run_id)
    assert persisted.status == "FAILED"
    registry.close()


def test_permission_denial_does_not_invoke(tmp_path) -> None:
    registry = make_registry(tmp_path)
    registry.register(
        make_definition(
            steps=(make_step("a", permissions=("secret.read",)),),
        )
    )
    called: list[str] = []

    def op(inputs: dict, prev: dict) -> object:
        called.append("a")
        return 1

    executor = WorkflowExecutor(registry, {"op": op}, authorizer=lambda a, p: False)
    run = executor.run("demo", "1.0.0", {}, "actor-1")
    assert run.status == "FAILED"
    assert called == []
    assert "denied" in run.step_results["a"]["error"]
    registry.close()


def test_permission_default_deny_without_authorizer(tmp_path) -> None:
    registry = make_registry(tmp_path)
    registry.register(
        make_definition(steps=(make_step("a", permissions=("anything",)),))
    )
    called: list[str] = []

    def op(inputs: dict, prev: dict) -> object:
        called.append("a")
        return 1

    executor = WorkflowExecutor(registry, {"op": op})
    run = executor.run("demo", "1.0.0", {}, "actor-1")
    assert run.status == "FAILED"
    assert called == []
    registry.close()


def test_authorizer_allow_runs_step(tmp_path) -> None:
    registry = make_registry(tmp_path)
    registry.register(
        make_definition(steps=(make_step("a", permissions=("data.read",)),))
    )

    def op(inputs: dict, prev: dict) -> object:
        return "allowed"

    executor = WorkflowExecutor(
        registry, {"op": op}, authorizer=lambda actor, perm: perm == "data.read"
    )
    run = executor.run("demo", "1.0.0", {}, "actor-1")
    assert run.status == "COMPLETED"
    assert run.step_results["a"]["output"] == "allowed"
    registry.close()


def test_failure_stops_later_steps_and_persists(tmp_path) -> None:
    registry = make_registry(tmp_path)
    registry.register(
        make_definition(
            steps=(make_step("a"), make_step("b"), make_step("c")),
        )
    )
    called: list[str] = []

    def ok(inputs: dict, prev: dict) -> object:
        called.append("ok")
        return 1

    def boom(inputs: dict, prev: dict) -> object:
        called.append("boom")
        raise RuntimeError("kaput")

    def never(inputs: dict, prev: dict) -> object:
        called.append("never")
        return 3

    # Map operations distinctly.
    registry2 = registry
    executor = WorkflowExecutor(
        registry2, {"op": ok}, authorizer=None
    )
    # Re-register a workflow with distinct operations for this test.
    registry2.register(
        make_definition(
            workflow_id="failflow",
            steps=(
                WorkflowStep(step_id="a", operation="step_a"),
                WorkflowStep(step_id="b", operation="step_b"),
                WorkflowStep(step_id="c", operation="step_c"),
            ),
        )
    )
    executor2 = WorkflowExecutor(
        registry2,
        {"step_a": ok, "step_b": boom, "step_c": never},
    )
    run = executor2.run("failflow", "1.0.0", {}, "actor-1")
    assert run.status == "FAILED"
    assert "c" not in run.step_results
    assert run.step_results["b"]["status"] == "failed"
    assert run.step_results["b"]["error"] == "RuntimeError"
    assert "kaput" not in run.step_results["b"]["error"]
    persisted = registry2.get_run(run.run_id)
    assert persisted.status == "FAILED"
    assert "c" not in persisted.step_results
    assert called.count("ok") == 1
    assert "never" not in called
    assert executor is not None
    registry.close()


def test_blank_actor_rejected(tmp_path) -> None:
    registry = make_registry(tmp_path)
    registry.register(make_definition())
    executor = WorkflowExecutor(registry, {"op": lambda i, p: 1})
    with pytest.raises(ValueError):
        executor.run("demo", "1.0.0", {}, "   ")
    registry.close()


def test_oversized_input_rejected(tmp_path) -> None:
    registry = make_registry(tmp_path)
    registry.register(make_definition())
    executor = WorkflowExecutor(registry, {"op": lambda i, p: 1})
    big = {"blob": "x" * (MAX_JSON_BYTES + 1)}
    with pytest.raises(ValueError):
        executor.run("demo", "1.0.0", big, "actor-1")
    registry.close()


def test_non_json_input_rejected(tmp_path) -> None:
    registry = make_registry(tmp_path)
    registry.register(make_definition())
    executor = WorkflowExecutor(registry, {"op": lambda i, p: 1})
    with pytest.raises(ValueError):
        executor.run("demo", "1.0.0", {"bad": {1, 2, 3}}, "actor-1")  # type: ignore[dict-item]
    registry.close()


def test_non_json_output_fails_run(tmp_path) -> None:
    registry = make_registry(tmp_path)
    registry.register(make_definition())

    def bad_op(inputs: dict, prev: dict) -> Any:
        return {1, 2, 3}

    executor = WorkflowExecutor(registry, {"op": bad_op})
    run = executor.run("demo", "1.0.0", {}, "actor-1")
    assert run.status == "FAILED"
    assert "JSON" in run.step_results["a"]["error"]
    registry.close()


def test_oversized_output_fails_run(tmp_path) -> None:
    registry = make_registry(tmp_path)
    registry.register(make_definition())

    def big_op(inputs: dict, prev: dict) -> object:
        return "y" * (MAX_JSON_BYTES + 1)

    executor = WorkflowExecutor(registry, {"op": big_op})
    run = executor.run("demo", "1.0.0", {}, "actor-1")
    assert run.status == "FAILED"
    assert "exceeds" in run.step_results["a"]["error"]
    registry.close()


def test_sql_injection_as_data(tmp_path) -> None:
    registry = make_registry(tmp_path)
    registry.register(make_definition(workflow_id="legit"))
    evil = "'; DROP TABLE workflow_definitions; --"
    with pytest.raises(ValueError):
        registry.register(make_definition(workflow_id=evil))
    # Table must still exist and remain usable.
    assert registry.get("legit", "1.0.0").workflow_id == "legit"
    # Injection via actor_id is stored safely with parameterized SQL.
    registry.register(make_definition(workflow_id="injectflow"))
    executor = WorkflowExecutor(registry, {"op": lambda i, p: "ok"})
    nasty_actor = "'; DROP TABLE workflow_runs; --"
    run = executor.run("injectflow", "1.0.0", {}, nasty_actor)
    assert run.status == "COMPLETED"
    fetched = registry.get_run(run.run_id)
    assert fetched.actor_id == nasty_actor
    assert registry.get("injectflow", "1.0.0").workflow_id == "injectflow"
    # Inputs carrying SQL text round-trip as data.
    payload = {"q": "SELECT * FROM workflow_runs; DROP TABLE x;"}
    blob = json.dumps(payload)
    assert "DROP TABLE" in blob
    run2 = executor.run("injectflow", "1.0.0", payload, "actor-1")
    assert run2.status == "COMPLETED"
    registry.close()


def test_unknown_workflow_and_run_raise_keyerror(tmp_path) -> None:
    registry = make_registry(tmp_path)
    with pytest.raises(KeyError):
        registry.get("missing", "1.0.0")
    with pytest.raises(KeyError):
        registry.get_run("nope")
    registry.close()


def test_nonfinite_output_is_failed_not_persisted(tmp_path) -> None:
    registry = make_registry(tmp_path)
    registry.register(WorkflowDefinition("finite", "1.0.0", (WorkflowStep("x", "bad"),)))
    run = WorkflowExecutor(registry, {"bad": lambda _i, _p: float("nan")}).run(
        "finite", "1.0.0", {}, "actor"
    )
    assert run.status == "FAILED"
    assert registry.get_run(run.run_id).status == "FAILED"
    registry.close()


def test_64_valid_outputs_do_not_strand_running_state(tmp_path) -> None:
    registry = make_registry(tmp_path)
    steps = tuple(WorkflowStep(f"s{i}", "large") for i in range(64))
    registry.register(WorkflowDefinition("large", "1.0.0", steps))
    calls = []

    def large(_inputs, _previous):
        calls.append(1)
        return "x" * 65_000

    run = WorkflowExecutor(registry, {"large": large}).run("large", "1.0.0", {}, "actor")
    assert len(calls) == 64
    assert run.status == "COMPLETED"
    assert registry.get_run(run.run_id).status == "COMPLETED"
    registry.close()


def test_multiple_bounded_step_outputs_persist_as_one_run(tmp_path) -> None:
    registry = make_registry(tmp_path)
    registry.register(make_definition(steps=(make_step("a"), make_step("b"))))
    executor = WorkflowExecutor(registry, {"op": lambda _inputs, _prev: "x" * 40_000})
    run = executor.run("demo", "1.0.0", {}, "actor")
    assert run.status == "COMPLETED"
    assert len(registry.get_run(run.run_id).step_results) == 2
    registry.close()
