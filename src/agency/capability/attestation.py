from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from agency.capability.contract import (
    CapabilityContract,
    CapabilityError,
    CapabilityRef,
    PublicationAttestation,
)
from agency.capability.resolver import ResolvedCapabilityGraph

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class AttestationError(CapabilityError):
    pass


def _identity(ref: CapabilityRef) -> tuple[str, str, str]:
    return (ref.capability_type.value, ref.capability_id, ref.version)


def _sort_key(ref: CapabilityRef) -> tuple[str, str, str, str]:
    return (*_identity(ref), ref.digest or "")


def _nonblank(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AttestationError(f"invalid {name}: {value!r}")
    return value


class AttestationStore:
    """In-memory attestation store (no DB)."""

    def __init__(self, authorizer: Callable[[str, str], bool] | None = None) -> None:
        self._authorizer = authorizer
        self._by_key: dict[tuple[str, str, str, str], PublicationAttestation] = {}

    def issue(
        self,
        ref: CapabilityRef,
        content_digest: str,
        approver: str,
        decision_ref: str,
        evidence_ref: str,
        decided_at: str = "",
    ) -> PublicationAttestation:
        if not isinstance(ref, CapabilityRef):
            raise AttestationError(f"invalid ref: {ref!r}")
        if ref.digest is not None:
            try:
                CapabilityRef(ref.capability_type, ref.capability_id, ref.version, ref.digest)
            except (CapabilityError, ValueError, TypeError) as exc:
                raise AttestationError(f"invalid ref digest: {ref.digest!r}") from exc
            if _DIGEST_RE.match(ref.digest) is None:
                raise AttestationError(f"invalid ref digest: {ref.digest!r}")
        try:
            CapabilityRef(ref.capability_type, ref.capability_id, ref.version)
        except (CapabilityError, ValueError, TypeError) as exc:
            raise AttestationError(f"invalid ref: {exc}") from exc
        if not isinstance(content_digest, str) or _DIGEST_RE.match(content_digest) is None:
            raise AttestationError(f"invalid content_digest: {content_digest!r}")
        _nonblank(approver, "approver")
        _nonblank(decision_ref, "decision_ref")
        _nonblank(evidence_ref, "evidence_ref")
        if not isinstance(decided_at, str):
            raise AttestationError(f"invalid decided_at: {decided_at!r}")
        if self._authorizer is None:
            raise AttestationError("authorizer is required")
        try:
            allowed = self._authorizer(approver, "capability.publish")
        except Exception as exc:
            raise AttestationError(f"authorizer failed: {exc}") from exc
        if allowed is not True:
            raise AttestationError(f"approver not authorized: {approver!r}")
        key = (*_identity(ref), content_digest)
        if key in self._by_key:
            raise AttestationError(f"duplicate attestation for {ref} {content_digest}")
        normalized = CapabilityRef(ref.capability_type, ref.capability_id, ref.version)
        attestation = PublicationAttestation(
            normalized, content_digest, approver, decision_ref, evidence_ref, 1, decided_at
        )
        self._by_key[key] = attestation
        return attestation

    def get(self, ref: CapabilityRef, content_digest: str) -> PublicationAttestation | None:
        if not isinstance(ref, CapabilityRef) or not isinstance(content_digest, str):
            return None
        return self._by_key.get((*_identity(ref), content_digest))

    def lookup(self, ref: CapabilityRef, content_digest: str) -> PublicationAttestation | None:
        return self.get(ref, content_digest)

    def list_all(self) -> tuple[PublicationAttestation, ...]:
        return tuple(
            sorted(self._by_key.values(), key=lambda a: (_identity(a.ref), a.content_digest))
        )


def requested_permissions(contracts: Iterable[CapabilityContract]) -> tuple[str, ...]:
    return tuple(sorted({p for contract in contracts for p in contract.requested_permissions}))


@dataclass(frozen=True)
class DependencyImpactReport:
    ref: CapabilityRef
    direct_dependents: tuple[CapabilityRef, ...]
    transitive_dependents: tuple[CapabilityRef, ...]
    affected_graphs: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref.to_dict(),
            "direct_dependents": [r.to_dict() for r in self.direct_dependents],
            "transitive_dependents": [r.to_dict() for r in self.transitive_dependents],
            "affected_graphs": self.affected_graphs,
        }


def inspect_impact(
    ref: CapabilityRef, graphs: Iterable[ResolvedCapabilityGraph]
) -> DependencyImpactReport:
    """Impact of changing ref across resolved graphs.

    A graph is affected if any node ref has the same (type,id,version) identity.
    Edges carry only a target (no source), so the graph root is treated as the
    dependent holding the edge: if the graph has an edge targeting ref, its root
    (when distinct from ref) is a direct dependent. All other nodes in affected
    graphs (excluding direct dependents and ref itself) are transitive dependents.
    """
    target = _identity(ref)
    affected = [g for g in graphs if any(_identity(node.ref) == target for node in g.nodes)]
    observed: dict[tuple[str, str, str], CapabilityRef] = {}
    direct: set[tuple[str, str, str]] = set()
    for graph in affected:
        for node in graph.nodes:
            observed.setdefault(_identity(node.ref), node.ref)
        has_edge = any(_identity(edge.target) == target for edge in graph.edges)
        if has_edge:
            root_key = _identity(graph.root)
            if root_key != target:
                direct.add(root_key)
                observed.setdefault(root_key, graph.root)
    direct_refs = tuple(sorted((observed[k] for k in direct), key=_sort_key))
    transitive_refs = tuple(
        sorted((r for k, r in observed.items() if k != target and k not in direct), key=_sort_key)
    )
    return DependencyImpactReport(ref, direct_refs, transitive_refs, len(affected))


__all__ = [
    "AttestationError",
    "AttestationStore",
    "DependencyImpactReport",
    "inspect_impact",
    "requested_permissions",
]
