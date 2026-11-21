from __future__ import annotations

from dataclasses import dataclass

from agency.capability.contract import CapabilityError, CapabilityRef, CapabilityType
from agency.capability.resolver import CapabilityResolver, ResolvedCapabilityGraph
from agency.factory.service import AgentFactory, Blueprint

_MAX_REFS = 32


class CompositionError(CapabilityError):
    pass


@dataclass(frozen=True)
class AgentCompositionRequest:
    blueprint_id: str
    version: str
    name: str
    domain: str
    creator: str
    skill_refs: tuple[CapabilityRef, ...] = ()
    workflow_refs: tuple[CapabilityRef, ...] = ()
    prompt_refs: tuple[CapabilityRef, ...] = ()
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompositionDraft:
    blueprint: Blueprint
    graphs: tuple[ResolvedCapabilityGraph, ...]
    requested_permissions: tuple[str, ...]
    requested_only: bool = True


_TYPE_MAP = (
    ("skill_refs", CapabilityType.SKILL),
    ("workflow_refs", CapabilityType.WORKFLOW),
    ("prompt_refs", CapabilityType.PROMPT),
)


class CompositionEngine:
    def __init__(self, resolver: CapabilityResolver, factory: AgentFactory) -> None:
        self._resolver = resolver
        self._factory = factory

    def compose_agent(self, request: AgentCompositionRequest) -> CompositionDraft:
        groups = [(name, getattr(request, name), expected) for name, expected in _TYPE_MAP]
        all_refs = [ref for _, refs, _ in groups for ref in refs]
        if len(all_refs) > _MAX_REFS:
            raise CompositionError(f"too many refs: {len(all_refs)} > {_MAX_REFS}")
        for name, refs, expected in groups:
            for ref in refs:
                if ref.capability_type is not expected:
                    raise CompositionError(
                        f"{name} ref {ref} has wrong type {ref.capability_type.value!r}, "
                        f"expected {expected.value!r}"
                    )
        graphs = tuple(
            self._resolver.resolve(ref, purpose="governed_composition") for ref in all_refs
        )
        blueprint = self._factory.create(
            request.blueprint_id,
            request.version,
            request.name,
            domain=request.domain,
            capabilities=list(request.capabilities),
            skill_refs=[(r.capability_id, r.version) for r in request.skill_refs],
            workflow_refs=[(r.capability_id, r.version) for r in request.workflow_refs],
            prompt_refs=[(r.capability_id, r.version) for r in request.prompt_refs],
            creator=request.creator,
        )
        return CompositionDraft(
            blueprint=blueprint,
            graphs=graphs,
            requested_permissions=tuple(sorted(request.capabilities)),
            requested_only=True,
        )


__all__ = [
    "AgentCompositionRequest",
    "CompositionDraft",
    "CompositionEngine",
    "CompositionError",
]
