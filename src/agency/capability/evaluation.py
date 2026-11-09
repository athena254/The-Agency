from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agency.capability.contract import CapabilityRef
from agency.capability.resolver import CapabilityResolver

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_ALLOWED_GATES = frozenset({"digest", "lifecycle", "references", "permissions"})
_STATUSES = frozenset({"PASSED", "FAILED"})


@dataclass(frozen=True)
class EvaluationSuite:
    suite_id: str
    version: str
    description: str = ""
    gates: tuple[str, ...] = ("digest", "lifecycle", "references", "permissions")

    def __post_init__(self) -> None:
        if (
            not isinstance(self.suite_id, str)
            or not self.suite_id
            or len(self.suite_id) > 128
            or _ID_RE.match(self.suite_id) is None
        ):
            raise ValueError(f"invalid suite_id: {self.suite_id!r}")
        if not isinstance(self.version, str) or _VERSION_RE.match(self.version) is None:
            raise ValueError(f"invalid version: {self.version!r}")
        if not isinstance(self.description, str):
            raise TypeError(f"invalid description: {self.description!r}")
        gates = tuple(self.gates) if self.gates is not None else ()
        for gate in gates:
            if gate not in _ALLOWED_GATES:
                raise ValueError(f"unknown gate: {gate!r}")
        object.__setattr__(self, "gates", gates)


@dataclass(frozen=True)
class EvaluationResult:
    case_id: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"case_id": self.case_id, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class EvaluationRun:
    run_id: str
    target: CapabilityRef
    suite_id: str
    suite_version: str
    status: str
    results: tuple[EvaluationResult, ...]
    eligible_for_review: bool

    def __post_init__(self) -> None:
        if self.status not in _STATUSES:
            raise ValueError(f"invalid status: {self.status!r}")
        if not isinstance(self.target, CapabilityRef):
            raise TypeError(f"invalid target: {self.target!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "target": self.target.to_dict(),
            "suite_id": self.suite_id,
            "suite_version": self.suite_version,
            "status": self.status,
            "results": [result.to_dict() for result in self.results],
            "eligible_for_review": self.eligible_for_review,
        }


class EvaluationRunner:
    def __init__(self, resolver: CapabilityResolver) -> None:
        self._resolver = resolver

    def run(
        self, target: CapabilityRef, suite: EvaluationSuite, actor: str, run_id: str = "run-1"
    ) -> EvaluationRun:
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError(f"invalid actor: {actor!r}")
        try:
            self._resolver.resolve(target)
        except Exception as exc:  # noqa: BLE001
            detail = str(exc)[:200]
            results = tuple(EvaluationResult(gate, False, detail) for gate in suite.gates)
            return EvaluationRun(
                run_id, target, suite.suite_id, suite.version, "FAILED", results, False
            )
        results = tuple(
            EvaluationResult(gate, True, "requested_only" if gate == "permissions" else "")
            for gate in suite.gates
        )
        return EvaluationRun(run_id, target, suite.suite_id, suite.version, "PASSED", results, True)


__all__ = ["EvaluationResult", "EvaluationRun", "EvaluationRunner", "EvaluationSuite"]
