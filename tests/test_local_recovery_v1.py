"""Tests for minimal durable local recovery v1 (SPEC_LOCAL_RECOVERY_V1)."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agency.workflows.executor import WorkflowExecutor
from agency.workflows.recovery import RecoverableLock, definition_digest, input_digest
from agency.workflows.registry import WorkflowDefinition, WorkflowRegistry, WorkflowStep

OPS_V = {"op_safe": "1", "op_other": "1", "block": "1"}


def make_registry(tmp_path: Path) -> WorkflowRegistry:
    return WorkflowRegistry(str(tmp_path / "rec.db"))


def make_definition(*ops: str) -> WorkflowDefinition:
    steps = tuple(WorkflowStep(f"s{i}", op) for i, op in enumerate(ops))
    return WorkflowDefinition(workflow_id="wf", version="1.0.0", steps=steps)


def make_executor(
    registry: WorkflowRegistry,
    operations: dict,
    replay=frozenset({"op_safe"}),
    versions: dict | None = None,
    authorizer=None,
) -> WorkflowExecutor:
    auth = (lambda _a, _p: True) if authorizer is None else authorizer
    return WorkflowExecutor(
        registry, operations, authorizer=auth,
        replay_safe=frozenset(replay),
        operation_versions=dict(versions) if versions is not None else dict(OPS_V),
    )


def test_durable_run_and_reopen(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    registry.register(make_definition("op_safe"))
    executor = make_executor(registry, {"op_safe": lambda i, p: {"echo": i.get("x")}})
    run = executor.run_recoverable("wf", "1.0.0", {"x": 1}, "alice")
    assert run.status == "COMPLETED"
    assert run.step_results["s0"]["output"] == {"echo": 1}
    run_id = run.run_id
    registry.close()
    reopened = WorkflowRegistry(str(tmp_path / "rec.db"))
    kept = reopened.get_recoverable_run(run_id)
    assert kept.status == "COMPLETED"
    assert kept.step_results["s0"]["output"] == {"echo": 1}
    reopened.close()
