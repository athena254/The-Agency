from __future__ import annotations

import threading
from datetime import UTC, datetime
from enum import Enum

import structlog
from pydantic import BaseModel, Field

from agency.security.blue.defender import BlueTeam, Defense, DefenseStatus
from agency.security.red.executor import (
    Evidence,
    RedTeamExecutor,
    RedTeamExecutorError,
    TestResult,
    TestStatus,
)

logger = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Verdict(str, Enum):
    CONFIRMED = "confirmed"
    NOT_VULNERABLE = "not_vulnerable"
    INCONCLUSIVE = "inconclusive"


class ValidationResult(BaseModel):
    finding_id: str
    attack_result: TestResult
    defense_result: Defense | None = None
    verdict: Verdict
    evidence: list[Evidence] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=_utcnow)


class PurpleReport(BaseModel):
    generated_at: datetime = Field(default_factory=_utcnow)
    total_findings: int = 0
    confirmed: int = 0
    not_vulnerable: int = 0
    inconclusive: int = 0
    findings: list[ValidationResult] = Field(default_factory=list)


class PurpleTeamError(Exception):
    pass


class PurpleTeam:
    def __init__(self, executor: RedTeamExecutor, blue: BlueTeam) -> None:
        self._executor = executor
        self._blue = blue
        self._results: dict[str, ValidationResult] = {}
        self._defense_map: dict[str, str] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _verdict(attack: TestResult, defense: Defense | None) -> Verdict:
        if attack.status is TestStatus.PASSED:
            return Verdict.CONFIRMED
        if (
            defense is not None
            and defense.status in (DefenseStatus.VALIDATED, DefenseStatus.ACTIVE)
            and attack.status in (TestStatus.BLOCKED, TestStatus.FAILED, TestStatus.ERROR)
        ):
            return Verdict.NOT_VULNERABLE
        if attack.status is TestStatus.BLOCKED:
            return Verdict.NOT_VULNERABLE
        return Verdict.INCONCLUSIVE

    def _store(self, attack: TestResult, defense: Defense | None) -> ValidationResult:
        evidence = [item.model_copy(deep=True) for item in attack.evidence]
        result = ValidationResult(
            finding_id=attack.id,
            attack_result=attack,
            defense_result=defense,
            verdict=self._verdict(attack, defense),
            evidence=evidence,
        )
        with self._lock:
            self._results[attack.id] = result
        return result

    def run_attack_defense_test(self, attack_id: str, defense_id: str) -> ValidationResult:
        defense = self._blue.get_defense(defense_id)
        try:
            attack = self._executor.get_test(attack_id)
        except RedTeamExecutorError as exc:
            raise PurpleTeamError(str(exc)) from exc
        with self._lock:
            self._defense_map[attack_id] = defense_id
        result = self._store(attack, defense)
        logger.info(
            "purple_attack_defense_tested",
            attack_id=attack_id,
            defense_id=defense_id,
            verdict=result.verdict.value,
        )
        return result

    def validate_finding(self, finding_id: str) -> ValidationResult:
        try:
            attack = self._executor.get_test(finding_id)
        except RedTeamExecutorError as exc:
            raise PurpleTeamError(str(exc)) from exc
        with self._lock:
            defense_id = self._defense_map.get(finding_id)
        defense = self._blue.get_defense(defense_id) if defense_id else None
        result = self._store(attack, defense)
        logger.info(
            "purple_finding_validated",
            finding_id=finding_id,
            verdict=result.verdict.value,
        )
        return result

    def generate_report(self) -> PurpleReport:
        with self._lock:
            findings = list(self._results.values())
        confirmed = sum(1 for item in findings if item.verdict is Verdict.CONFIRMED)
        not_vulnerable = sum(
            1 for item in findings if item.verdict is Verdict.NOT_VULNERABLE
        )
        inconclusive = sum(
            1 for item in findings if item.verdict is Verdict.INCONCLUSIVE
        )
        return PurpleReport(
            total_findings=len(findings),
            confirmed=confirmed,
            not_vulnerable=not_vulnerable,
            inconclusive=inconclusive,
            findings=sorted(findings, key=lambda item: item.timestamp, reverse=True),
        )