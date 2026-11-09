from __future__ import annotations

import dataclasses

import pytest

from agency.capability.contract import (
    CapabilityContract,
    CapabilityNotFoundError,
    CapabilityRef,
    CapabilityType,
    ProvenanceRef,
    compute_content_digest,
)
from agency.capability.evaluation import EvaluationRunner, EvaluationSuite
from agency.capability.resolver import CapabilityResolver


def _prov() -> ProvenanceRef:
    return ProvenanceRef("agency_native", source_id="src-1")


def _ref(capability_id: str) -> CapabilityRef:
    return CapabilityRef(CapabilityType.SKILL, capability_id, "1.0.0")


def _contract(capability_id: str, lifecycle: str = "PUBLISHED") -> CapabilityContract:
    base = CapabilityContract(
        1,
        _ref(capability_id),
        capability_id,
        "desc",
        lifecycle,
        lifecycle,
        None,
        None,
        (),
        None,
        (),
        (),
        _prov(),
        "sha256:" + "0" * 64,
        None,
    )
    return dataclasses.replace(base, content_digest=compute_content_digest(base))


class FakeAdapter:
    def __init__(self, contracts: dict[tuple[str, str], CapabilityContract]) -> None:
        self.capability_type = CapabilityType.SKILL
        self._contracts = contracts

    def get(self, capability_id: str, version: str) -> CapabilityContract:
        try:
            return self._contracts[(capability_id, version)]
        except KeyError:
            raise CapabilityNotFoundError(
                f"unknown capability: {(capability_id, version)!r}"
            ) from None

    def list_refs(self) -> list[CapabilityRef]:
        return [CapabilityRef(self.capability_type, cid, ver) for cid, ver in self._contracts]


def _runner(lifecycle: str = "PUBLISHED") -> EvaluationRunner:
    store = {("a", "1.0.0"): _contract("a", lifecycle)}
    return EvaluationRunner(CapabilityResolver([FakeAdapter(store)]))


def test_passing_governed_target_eligible() -> None:
    run = _runner().run(
        _ref("a"), EvaluationSuite("suite-1", "1.0.0"), actor="alice", run_id="run-1"
    )
    assert run.status == "PASSED"
    assert run.eligible_for_review is True
    assert run.run_id == "run-1"
    assert [r.case_id for r in run.results] == ["digest", "lifecycle", "references", "permissions"]
    assert all(r.passed for r in run.results)
    assert run.results[-1].detail == "requested_only"
    assert run.to_dict()["status"] == "PASSED"


def test_failing_unattested_target_not_eligible() -> None:
    run = _runner("PUBLISHED_UNATTESTED").run(
        _ref("a"), EvaluationSuite("suite-1", "1.0.0"), actor="alice"
    )
    assert run.status == "FAILED"
    assert run.eligible_for_review is False
    assert run.results
    assert all(not r.passed for r in run.results)
    assert all(r.detail for r in run.results)


def test_unknown_gate_rejected() -> None:
    with pytest.raises(ValueError):
        EvaluationSuite("suite-1", "1.0.0", gates=("bogus",))


def test_blank_actor_rejected() -> None:
    with pytest.raises(ValueError):
        _runner().run(_ref("a"), EvaluationSuite("suite-1", "1.0.0"), actor="  ")
