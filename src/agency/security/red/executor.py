from __future__ import annotations

import threading
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import Enum

import structlog
from pydantic import BaseModel, Field

from agency.security.red.planner import (
    PlanStatus,
    RedTeamPlanner,
    RedTeamPlannerError,
)
from agency.security.sandbox.manager import SandboxError, SandboxManager

logger = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TestStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    ERROR = "error"


class Evidence(BaseModel):
    id: str
    kind: str
    content: str
    captured_at: datetime = Field(default_factory=_utcnow)


class TestResult(BaseModel):
    id: str
    plan_id: str
    status: TestStatus = TestStatus.PENDING
    evidence: list[Evidence] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=_utcnow)


class RedTeamExecutorError(Exception):
    pass


class RedTeamExecutor:
    def __init__(self, planner: RedTeamPlanner, sandboxes: SandboxManager) -> None:
        self._planner = planner
        self._sandboxes = sandboxes
        self._tests: dict[str, TestResult] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _new_id() -> str:
        return f"test-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _new_evidence_id() -> str:
        return f"ev-{uuid.uuid4().hex[:12]}"

    def _register(
        self,
        plan_id: str,
        test_id: str,
        status: TestStatus,
        findings: list[str],
        evidence: list[Evidence],
    ) -> TestResult:
        result = TestResult(
            id=test_id,
            plan_id=plan_id,
            status=status,
            findings=findings,
            evidence=evidence,
        )
        with self._lock:
            self._tests[test_id] = result
        return result

    def execute_test(
        self,
        plan_id: str,
        sandbox_id: str,
        command: Sequence[str] | None = None,
    ) -> TestResult:
        test_id = self._new_id()

        try:
            plan = self._planner.get_plan(plan_id)
        except RedTeamPlannerError as exc:
            logger.warning(
                "red_test_blocked",
                test_id=test_id,
                plan_id=plan_id,
                reason="unknown plan",
            )
            return self._register(
                plan_id,
                test_id,
                TestStatus.BLOCKED,
                [f"plan {plan_id} not found: {exc}"],
                [],
            )

        if plan.status is not PlanStatus.APPROVED:
            reason = f"plan {plan_id} is not approved (status={plan.status.value})"
            logger.warning("red_test_blocked", test_id=test_id, plan_id=plan_id, reason=reason)
            return self._register(plan_id, test_id, TestStatus.BLOCKED, [reason], [])

        sandbox = self._sandboxes.get_sandbox(sandbox_id)
        if sandbox is None:
            reason = f"sandbox {sandbox_id} does not exist"
            return self._register(plan_id, test_id, TestStatus.ERROR, [reason], [])

        cmd = list(command) if command else ["/bin/sh", "-c", "echo security-probe"]
        logger.info(
            "red_test_running",
            test_id=test_id,
            plan_id=plan_id,
            sandbox_id=sandbox_id,
            command=cmd[:64],
        )
        with self._lock:
            self._tests[test_id] = TestResult(
                id=test_id, plan_id=plan_id, status=TestStatus.RUNNING
            )

        try:
            execution = self._sandboxes.execute(sandbox_id, cmd)
        except SandboxError as exc:
            logger.error(
                "red_test_execution_failed",
                test_id=test_id,
                plan_id=plan_id,
                sandbox_id=sandbox_id,
                error=str(exc),
            )
            return self._register(
                plan_id,
                test_id,
                TestStatus.ERROR,
                [f"sandbox execution failed: {exc}"],
                [],
            )

        if execution.timed_out or execution.error is not None:
            status = TestStatus.ERROR
        elif execution.exit_code == 0:
            status = TestStatus.PASSED
        else:
            status = TestStatus.FAILED

        findings: list[str] = []
        if execution.timed_out:
            findings.append(
                f"command timed out after {sandbox.config.timeout}s"
            )
        if execution.error is not None:
            findings.append(execution.error)
        if status is TestStatus.PASSED:
            findings.append("attack sequence completed successfully within sandbox")

        evidence = [
            Evidence(
                id=self._new_evidence_id(),
                kind="command",
                content=" ".join(cmd),
            ),
            Evidence(
                id=self._new_evidence_id(),
                kind="stdout",
                content=execution.stdout,
            ),
            Evidence(
                id=self._new_evidence_id(),
                kind="stderr",
                content=execution.stderr,
            ),
            Evidence(
                id=self._new_evidence_id(),
                kind="exit_code",
                content=str(execution.exit_code),
            ),
            Evidence(
                id=self._new_evidence_id(),
                kind="execution",
                content=f"duration_ms={execution.duration_ms:.2f} timed_out={execution.timed_out}",
            ),
        ]

        logger.info(
            "red_test_completed",
            test_id=test_id,
            plan_id=plan_id,
            status=status.value,
            exit_code=execution.exit_code,
        )
        return self._register(plan_id, test_id, status, findings, evidence)

    def collect_evidence(self, test_id: str) -> list[Evidence]:
        test = self.get_test(test_id)
        return [item.model_copy(deep=True) for item in test.evidence]

    def list_tests(self, status: TestStatus | None = None) -> list[TestResult]:
        with self._lock:
            tests = [
                test
                for test in self._tests.values()
                if status is None or test.status is status
            ]
        return sorted(tests, key=lambda test: test.timestamp, reverse=True)

    def get_test(self, test_id: str) -> TestResult:
        with self._lock:
            test = self._tests.get(test_id)
        if test is None:
            raise RedTeamExecutorError(f"unknown test {test_id}")
        return test