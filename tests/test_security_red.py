"""Tests for the red team: planner approvals and sandboxed execution."""

import pytest

from agency.security.red.executor import RedTeamExecutor, RedTeamExecutorError, TestStatus
from agency.security.red.planner import PlanStatus, RedTeamPlanner, RedTeamPlannerError


def test_create_plan_pending_approval(test_red_planner: RedTeamPlanner):
    plan = test_red_planner.create_plan(
        target="staging/api", hypothesis="SSRF via webhook", scope=["staging/*"])
    assert plan.target == "staging/api"
    assert plan.status is PlanStatus.PENDING_APPROVAL
    assert plan.scope == ["staging/*"]
    assert plan.created_by == "system"


def test_approve_plan(test_red_planner: RedTeamPlanner):
    plan = test_red_planner.create_plan(target="t", hypothesis="h")
    approved = test_red_planner.approve_plan(plan.id, approved_by="admin")
    assert approved.status is PlanStatus.APPROVED
    assert approved.approved_by == "admin"
    with pytest.raises(RedTeamPlannerError):
        test_red_planner.approve_plan(plan.id)  # already approved


def test_approve_unknown_plan(test_red_planner: RedTeamPlanner):
    with pytest.raises(RedTeamPlannerError):
        test_red_planner.approve_plan("plan-missing")


def test_get_and_list_plans(test_red_planner: RedTeamPlanner):
    p1 = test_red_planner.create_plan(target="a", hypothesis="h1")
    test_red_planner.create_plan(target="b", hypothesis="h2")
    test_red_planner.approve_plan(p1.id)
    assert test_red_planner.get_plan(p1.id).id == p1.id
    assert len(test_red_planner.list_plans()) == 2
    assert len(test_red_planner.list_plans(PlanStatus.APPROVED)) == 1
    with pytest.raises(RedTeamPlannerError):
        test_red_planner.get_plan("missing")


def test_execute_approved_plan_collects_evidence(
    test_red_planner: RedTeamPlanner, test_red_executor: RedTeamExecutor,
    test_sandbox_manager, test_sandbox,
):
    plan = test_red_planner.create_plan(target="staging/api", hypothesis="h")
    test_red_planner.approve_plan(plan.id)
    result = test_red_executor.execute_test(plan.id, test_sandbox.id, ["echo", "probe"])
    assert result.status is TestStatus.PASSED
    assert len(result.evidence) == 5
    kinds = {e.kind for e in result.evidence}
    assert {"command", "stdout", "stderr", "exit_code", "execution"} <= kinds
    assert result.findings  # success finding recorded
    # evidence collection returns deep copies
    evidence = test_red_executor.collect_evidence(result.id)
    assert len(evidence) == 5
    evidence[0].content = "mutated"
    assert test_red_executor.collect_evidence(result.id)[0].content != "mutated"


def test_execute_unapproved_plan_blocked(
    test_red_planner: RedTeamPlanner, test_red_executor: RedTeamExecutor, test_sandbox,
):
    plan = test_red_planner.create_plan(target="t", hypothesis="h")
    result = test_red_executor.execute_test(plan.id, test_sandbox.id)
    assert result.status is TestStatus.BLOCKED
    assert any("not approved" in f for f in result.findings)


def test_execute_unknown_plan_blocked(test_red_executor: RedTeamExecutor, test_sandbox):
    result = test_red_executor.execute_test("plan-nope", test_sandbox.id)
    assert result.status is TestStatus.BLOCKED


def test_execute_missing_sandbox_error(
    test_red_planner: RedTeamPlanner, test_red_executor: RedTeamExecutor,
):
    plan = test_red_planner.create_plan(target="t", hypothesis="h")
    test_red_planner.approve_plan(plan.id)
    result = test_red_executor.execute_test(plan.id, "sbx-missing")
    assert result.status is TestStatus.ERROR


def test_execute_failing_command_marks_failed(
    test_red_planner: RedTeamPlanner, test_sandbox_manager, fake_backend,
):
    fake_backend.exit_code = 2
    fake_backend.stdout = ""
    executor = RedTeamExecutor(test_red_planner, test_sandbox_manager)
    plan = test_red_planner.create_plan(target="t", hypothesis="h")
    test_red_planner.approve_plan(plan.id)
    sbx = test_sandbox_manager.create_sandbox("agent-1")
    result = executor.execute_test(plan.id, sbx.id, ["false"])
    assert result.status is TestStatus.FAILED


def test_get_and_list_tests(
    test_red_planner: RedTeamPlanner, test_red_executor: RedTeamExecutor, test_sandbox,
):
    plan = test_red_planner.create_plan(target="t", hypothesis="h")
    test_red_planner.approve_plan(plan.id)
    result = test_red_executor.execute_test(plan.id, test_sandbox.id)
    assert test_red_executor.get_test(result.id).id == result.id
    assert len(test_red_executor.list_tests()) == 1
    assert len(test_red_executor.list_tests(TestStatus.PASSED)) == 1
    assert test_red_executor.list_tests(TestStatus.FAILED) == []
    with pytest.raises(RedTeamExecutorError):
        test_red_executor.get_test("test-missing")
    with pytest.raises(RedTeamExecutorError):
        test_red_executor.collect_evidence("test-missing")
