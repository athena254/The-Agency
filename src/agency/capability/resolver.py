from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from agency.capability.adapters import CapabilityAdapter
from agency.capability.contract import (
    CapabilityContract,
    CapabilityError,
    CapabilityRef,
    CapabilityType,
    DependencyEdge,
    DependencyRelation,
    DigestMismatchError,
    UnsupportedCapabilityTypeError,
    canonical_sha256,
    verify_digest,
)

_SUPPORTED_PURPOSES = frozenset({"governed_composition", "exploratory"})
_SUPPORTED_TYPES = frozenset(
    {
        CapabilityType.AGENT,
        CapabilityType.SKILL,
        CapabilityType.WORKFLOW,
        CapabilityType.PROMPT,
    }
)
_GOVERNED_ALLOWED = frozenset({"PUBLISHED", "APPROVED", "ACTIVE"})
_EXPLORATORY_REJECTED = frozenset({"RETIRED", "REVOKED"})
_TRAVERSAL_RELATIONS = frozenset({DependencyRelation.REQUIRES, DependencyRelation.COMPOSES})
_MAX_NODES = 64
_MAX_DEPTH = 16


class ResolverError(CapabilityError):
    pass


class CompatibilityOutcome(str, Enum):
    COMPATIBLE = "COMPATIBLE"
    INCOMPATIBLE = "INCOMPATIBLE"
    UNKNOWN = "UNKNOWN"
    NOT_CHECKED = "NOT_CHECKED"


@dataclass(frozen=True)
class EdgeOutcome:
    dependency: DependencyEdge
    outcome: CompatibilityOutcome
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "dependency": self.dependency.to_dict(),
            "outcome": self.outcome.value,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class CompatibilityReport:
    graph_digest: str
    outcomes: tuple[EdgeOutcome, ...]
    overall: CompatibilityOutcome

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_digest": self.graph_digest,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "overall": self.overall.value,
        }


@dataclass(frozen=True)
class ResolvedNode:
    ref: CapabilityRef
    native_state: str
    adapter_schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref.to_dict(),
            "native_state": self.native_state,
            "adapter_schema_version": self.adapter_schema_version,
        }


def _ref_key(ref: CapabilityRef) -> tuple[str, str, str]:
    return (ref.capability_type.value, ref.capability_id, ref.version)


def _edge_key(edge: DependencyEdge) -> tuple[str, str, str, str, str]:
    return (
        edge.relation.value,
        edge.target.capability_type.value,
        edge.target.capability_id,
        edge.target.version,
        edge.label,
    )


def _edge_payload(edge: DependencyEdge) -> dict[str, Any]:
    return {
        "relation": edge.relation.value,
        "target": edge.target.to_dict(),
        "label": edge.label,
    }


@dataclass(frozen=True)
class ResolvedCapabilityGraph:
    root: CapabilityRef
    nodes: tuple[ResolvedNode, ...]
    edges: tuple[DependencyEdge, ...]
    graph_digest: str
    resolver_version: str = "1.0.0"
    resolved_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root.to_dict(),
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [_edge_payload(edge) for edge in self.edges],
            "graph_digest": self.graph_digest,
            "resolver_version": self.resolver_version,
            "resolved_at": self.resolved_at,
        }


def compute_graph_digest(
    root: CapabilityRef,
    nodes: Iterable[ResolvedNode],
    edges: Iterable[DependencyEdge],
) -> str:
    node_refs = sorted(
        (node.ref.to_dict() for node in nodes),
        key=lambda d: (str(d["capability_type"]), str(d["capability_id"]), str(d["version"])),
    )
    edge_payloads = sorted(
        (_edge_payload(edge) for edge in edges),
        key=lambda d: (
            str(d["relation"]),
            str(d["target"]["capability_type"]),
            str(d["target"]["capability_id"]),
            str(d["target"]["version"]),
            str(d["label"]),
        ),
    )
    return canonical_sha256({"root": root.to_dict(), "nodes": node_refs, "edges": edge_payloads})


def validate_graph(graph: ResolvedCapabilityGraph) -> CompatibilityReport:
    node_keys = {_ref_key(node.ref) for node in graph.nodes}
    outcomes: list[EdgeOutcome] = []
    for edge in graph.edges:
        target_in_graph = _ref_key(edge.target) in node_keys
        if edge.relation == DependencyRelation.CONFLICTS_WITH:
            if target_in_graph:
                outcomes.append(
                    EdgeOutcome(
                        edge, CompatibilityOutcome.INCOMPATIBLE, "conflicts_with target in graph"
                    )
                )
            else:
                outcomes.append(
                    EdgeOutcome(
                        edge, CompatibilityOutcome.UNKNOWN, "conflicts_with target not in graph"
                    )
                )
        elif edge.relation in _TRAVERSAL_RELATIONS:
            if target_in_graph:
                outcomes.append(
                    EdgeOutcome(
                        edge, CompatibilityOutcome.COMPATIBLE, "exact pin resolved in graph"
                    )
                )
            else:
                outcomes.append(
                    EdgeOutcome(edge, CompatibilityOutcome.UNKNOWN, "target not in graph")
                )
        else:
            outcomes.append(
                EdgeOutcome(edge, CompatibilityOutcome.NOT_CHECKED, "relation not checked")
            )
    if any(o.outcome == CompatibilityOutcome.INCOMPATIBLE for o in outcomes):
        overall = CompatibilityOutcome.INCOMPATIBLE
    elif any(o.outcome == CompatibilityOutcome.UNKNOWN for o in outcomes):
        overall = CompatibilityOutcome.UNKNOWN
    elif outcomes and all(o.outcome == CompatibilityOutcome.NOT_CHECKED for o in outcomes):
        overall = CompatibilityOutcome.NOT_CHECKED
    else:
        overall = CompatibilityOutcome.COMPATIBLE
    return CompatibilityReport(graph.graph_digest, tuple(outcomes), overall)


class CompatibilityChecker:
    def check(self, graph: ResolvedCapabilityGraph) -> CompatibilityReport:
        return validate_graph(graph)


class CapabilityResolver:
    def __init__(self, adapters: Iterable[CapabilityAdapter]) -> None:
        self._adapters: dict[CapabilityType, CapabilityAdapter] = {}
        for adapter in adapters:
            self._adapters[adapter.capability_type] = adapter

    @classmethod
    def from_adapters(
        cls, adapters: Mapping[CapabilityType | str, CapabilityAdapter]
    ) -> CapabilityResolver:
        for key in adapters:
            if isinstance(key, str):
                CapabilityType.from_value(key)
        return cls(list(adapters.values()))

    def _adapter_for(self, ref: CapabilityRef) -> CapabilityAdapter:
        if ref.capability_type not in _SUPPORTED_TYPES:
            raise UnsupportedCapabilityTypeError(
                f"unsupported capability type: {ref.capability_type.value!r}"
            )
        adapter = self._adapters.get(ref.capability_type)
        if adapter is None:
            raise UnsupportedCapabilityTypeError(
                f"unsupported capability type: {ref.capability_type.value!r}"
            )
        return adapter

    def _check_eligibility(self, contract: CapabilityContract, purpose: str) -> None:
        lifecycle = contract.lifecycle_state
        if purpose == "governed_composition":
            if lifecycle not in _GOVERNED_ALLOWED:
                raise ResolverError(
                    f"capability not eligible for governed_composition: {contract.ref} lifecycle_state={lifecycle!r}"
                )
        elif lifecycle in _EXPLORATORY_REJECTED:
            raise ResolverError(
                f"capability not eligible for exploratory: {contract.ref} lifecycle_state={lifecycle!r}"
            )

    def resolve(
        self, root: CapabilityRef, *, purpose: str = "governed_composition"
    ) -> ResolvedCapabilityGraph:
        if not isinstance(purpose, str) or not purpose.strip():
            raise ResolverError(f"invalid purpose: {purpose!r}")
        if purpose not in _SUPPORTED_PURPOSES:
            raise ResolverError(f"unsupported purpose: {purpose!r}")
        if not isinstance(root, CapabilityRef):
            raise ResolverError(f"invalid root: {root!r}")

        contracts: dict[tuple[str, str, str], CapabilityContract] = {}
        visiting: list[tuple[str, str, str]] = []
        visiting_set: set[tuple[str, str, str]] = set()
        visited: set[tuple[str, str, str]] = set()

        def visit(ref: CapabilityRef, depth: int) -> None:
            key = _ref_key(ref)
            if key in visiting_set:
                raise ResolverError(f"cycle detected at {ref}")
            if key in visited:
                return
            if depth > _MAX_DEPTH:
                raise ResolverError("max depth exceeded")
            if len(contracts) >= _MAX_NODES and key not in contracts:
                raise ResolverError("max nodes exceeded")
            adapter = self._adapter_for(ref)
            contract = adapter.get(ref.capability_id, ref.version)
            verify_digest(contract)
            if ref.digest is not None and ref.digest != contract.content_digest:
                raise DigestMismatchError(f"INTEGRITY_FAILURE: digest mismatch for {ref}")
            self._check_eligibility(contract, purpose)
            contracts[key] = contract
            visiting.append(key)
            visiting_set.add(key)
            try:
                for edge in contract.dependencies:
                    if edge.relation not in _TRAVERSAL_RELATIONS:
                        continue
                    if _ref_key(edge.target) == key:
                        raise ResolverError(f"cycle detected at {ref}")
                    visit(edge.target, depth + 1)
            finally:
                visiting.pop()
                visiting_set.discard(key)
            visited.add(key)

        visit(root, 0)

        for contract in contracts.values():
            for edge in contract.dependencies:
                if (
                    edge.relation == DependencyRelation.CONFLICTS_WITH
                    and _ref_key(edge.target) in contracts
                ):
                    raise ResolverError(f"conflict: {contract.ref} conflicts with {edge.target}")

        nodes = tuple(
            sorted(
                (
                    ResolvedNode(
                        CapabilityRef(
                            contract.ref.capability_type,
                            contract.ref.capability_id,
                            contract.ref.version,
                            digest=contract.content_digest,
                        ),
                        contract.native_state,
                        contract.schema_version,
                    )
                    for contract in contracts.values()
                ),
                key=lambda node: _ref_key(node.ref),
            )
        )
        edge_set: set[DependencyEdge] = set()
        for contract in contracts.values():
            for edge in contract.dependencies:
                if edge.relation in _TRAVERSAL_RELATIONS and _ref_key(edge.target) in contracts:
                    edge_set.add(edge)
        edges = tuple(sorted(edge_set, key=_edge_key))
        root_contract = contracts[_ref_key(root)]
        root_ref = CapabilityRef(
            root_contract.ref.capability_type,
            root_contract.ref.capability_id,
            root_contract.ref.version,
            digest=root_contract.content_digest,
        )
        graph_digest = compute_graph_digest(root_ref, nodes, edges)
        return ResolvedCapabilityGraph(root_ref, nodes, edges, graph_digest)

    def validate_graph(self, graph: ResolvedCapabilityGraph) -> CompatibilityReport:
        return validate_graph(graph)


__all__ = [
    "CapabilityResolver",
    "CompatibilityChecker",
    "CompatibilityOutcome",
    "CompatibilityReport",
    "EdgeOutcome",
    "ResolvedCapabilityGraph",
    "ResolvedNode",
    "ResolverError",
    "compute_graph_digest",
    "validate_graph",
]
