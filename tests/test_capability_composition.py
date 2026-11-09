from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from agency.capability.composition import (
    AgentCompositionRequest,
    CompositionEngine,
    CompositionError,
)
from agency.capability.contract import (
    CapabilityContract,
    CapabilityNotFoundError,
    CapabilityRef,
    CapabilityType,
    ProvenanceRef,
    compute_content_digest,
)
from agency.capability.resolver import CapabilityResolver, ResolverError
from agency.factory.service import AgentFactory, BlueprintStatus


def _contract(cid: str, ctype: CapabilityType, lifecycle: str = "PUBLISHED") -> CapabilityContract:
    base = CapabilityContract(
        1,
        CapabilityRef(ctype, cid, "1.0.0"),
        cid,
        "desc",
        lifecycle,
        lifecycle,
        None,
        None,
        (),
        None,
        (),
        (),
        ProvenanceRef("agency_native", source_id="src-1"),
        "sha256:" + "0" * 64,
        None,
    )
    return dataclasses.replace(base, content_digest=compute_content_digest(base))


class FakeAdapter:
    def __init__(self, ctype: CapabilityType, store: dict[tuple[str, str], CapabilityContract]):
        self.capability_type = ctype
        self._store = store

    def get(self, capability_id: str, version: str) -> CapabilityContract:
        try:
            return self._store[(capability_id, version)]
        except KeyError:
            raise CapabilityNotFoundError(f"unknown: {(capability_id, version)!r}") from None

    def list_refs(self) -> list[CapabilityRef]:
        return [CapabilityRef(self.capability_type, cid, ver) for cid, ver in self._store]


def _engine(
    store: dict[tuple[str, str], CapabilityContract] | None = None,
    lifecycle: str = "PUBLISHED",
) -> CompositionEngine:
    store = (
        store
        if store is not None
        else {("s1", "1.0.0"): _contract("s1", CapabilityType.SKILL, lifecycle)}
    )
    skill = FakeAdapter(CapabilityType.SKILL, store)
    workflow = FakeAdapter(CapabilityType.WORKFLOW, {})
    prompt = FakeAdapter(CapabilityType.PROMPT, {})
    factory = AgentFactory(
        ":memory:",
        authorizer=lambda *a: True,
        is_skill_published=lambda *a: True,
        is_workflow_published=lambda *a: True,
        allowed_capabilities={"inspect", "summarize"},
    )
    return CompositionEngine(CapabilityResolver([skill, workflow, prompt]), factory)


def _req(**over: Any) -> AgentCompositionRequest:
    kwargs: dict[str, Any] = {
        "blueprint_id": "analyst",
        "version": "1.0.0",
        "name": "Analyst",
        "domain": "research",
        "creator": "creator",
        "skill_refs": (CapabilityRef(CapabilityType.SKILL, "s1", "1.0.0"),),
        "capabilities": ("inspect",),
    }
    kwargs.update(over)
    return AgentCompositionRequest(**kwargs)


def test_happy_compose_draft() -> None:
    draft = _engine().compose_agent(_req())
    assert draft.blueprint.status is BlueprintStatus.DRAFT
    assert draft.blueprint.runtime_agent_id is None
    assert draft.blueprint.skill_refs == (("s1", "1.0.0"),)
    assert draft.requested_permissions == ("inspect",)
    assert draft.requested_only is True
    assert len(draft.graphs) == 1
    assert draft.graphs[0].root.capability_id == "s1"


def test_wrong_type_ref_rejected() -> None:
    eng = _engine()
    bad = CapabilityRef(CapabilityType.WORKFLOW, "s1", "1.0.0")
    with pytest.raises(CompositionError):
        eng.compose_agent(_req(skill_refs=(bad,)))


def test_too_many_refs_rejected() -> None:
    eng = _engine()
    refs = tuple(CapabilityRef(CapabilityType.SKILL, "s1", "1.0.0") for _ in range(33))
    with pytest.raises(CompositionError):
        eng.compose_agent(_req(skill_refs=refs))


def test_unattested_component_rejected() -> None:
    eng = _engine(lifecycle="PUBLISHED_UNATTESTED")
    with pytest.raises(ResolverError):
        eng.compose_agent(_req())
