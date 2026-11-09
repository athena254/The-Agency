from __future__ import annotations

import dataclasses

import pytest

from agency.capability.contract import (
    CapabilityContract,
    CapabilityNotFoundError,
    CapabilityRef,
    CapabilityType,
    DependencyEdge,
    DependencyRelation,
    DigestMismatchError,
    ProvenanceRef,
    UnsupportedCapabilityTypeError,
    compute_content_digest,
)
from agency.capability.resolver import (
    CapabilityResolver,
    CompatibilityChecker,
    CompatibilityOutcome,
    ResolvedCapabilityGraph,
    ResolverError,
)


def _prov() -> ProvenanceRef:
    return ProvenanceRef("agency_native", source_id="src-1")


def _ref(
    capability_id: str,
    version: str = "1.0.0",
    capability_type: CapabilityType = CapabilityType.SKILL,
    digest: str | None = None,
) -> CapabilityRef:
    return CapabilityRef(capability_type, capability_id, version, digest)


def _contract(
    capability_id: str,
    version: str = "1.0.0",
    lifecycle: str = "PUBLISHED",
    capability_type: CapabilityType = CapabilityType.SKILL,
    deps: tuple[DependencyEdge, ...] = (),
    name: str | None = None,
) -> CapabilityContract:
    base = CapabilityContract(
        1,
        _ref(capability_id, version, capability_type),
        name or capability_id,
        "desc",
        lifecycle,
        lifecycle,
        None,
        None,
        (),
        None,
        deps,
        (),
        _prov(),
        "sha256:" + "0" * 64,
        None,
    )
    return dataclasses.replace(base, content_digest=compute_content_digest(base))


def _edge(
    target: CapabilityRef, relation: DependencyRelation = DependencyRelation.REQUIRES
) -> DependencyEdge:
    return DependencyEdge(relation, target)


class FakeAdapter:
    def __init__(
        self,
        capability_type: CapabilityType,
        contracts: dict[tuple[str, str], CapabilityContract],
    ) -> None:
        self.capability_type = capability_type
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


def _resolver(
    store: dict[tuple[str, str], CapabilityContract],
    capability_type: CapabilityType = CapabilityType.SKILL,
) -> CapabilityResolver:
    return CapabilityResolver([FakeAdapter(capability_type, store)])


def _chain_store(n: int, lifecycle: str = "PUBLISHED") -> dict[tuple[str, str], CapabilityContract]:
    store: dict[tuple[str, str], CapabilityContract] = {}
    for i in range(n):
        cid = f"node-{i}"
        deps = (_edge(_ref(f"node-{i + 1}")),) if i + 1 < n else ()
        store[(cid, "1.0.0")] = _contract(cid, deps=deps, lifecycle=lifecycle)
    return store


def test_linear_lock_deterministic() -> None:
    store = _chain_store(3)
    resolver = _resolver(store)
    root = _ref("node-0")
    g1 = resolver.resolve(root)
    g2 = resolver.resolve(root)
    assert g1.graph_digest == g2.graph_digest
    assert g1.nodes == g2.nodes
    assert g1.edges == g2.edges
    assert len(g1.nodes) == 3
    assert len(g1.edges) == 2
    assert g1.root.digest is not None
    assert g1.to_dict()["graph_digest"] == g1.graph_digest


def test_missing_dep() -> None:
    store = {("a", "1.0.0"): _contract("a", deps=(_edge(_ref("missing")),))}
    with pytest.raises(CapabilityNotFoundError):
        _resolver(store).resolve(_ref("a"))


def test_cycle() -> None:
    a = _contract("a", deps=(_edge(_ref("b")),))
    b = _contract("b", deps=(_edge(_ref("a")),))
    store = {("a", "1.0.0"): a, ("b", "1.0.0"): b}
    with pytest.raises(ResolverError, match="cycle"):
        _resolver(store).resolve(_ref("a"))


def test_self_edge() -> None:
    store = {("a", "1.0.0"): _contract("a", deps=(_edge(_ref("a")),))}
    with pytest.raises(ResolverError, match="cycle"):
        _resolver(store).resolve(_ref("a"))


def test_digest_mismatch_ref() -> None:
    store = {("a", "1.0.0"): _contract("a")}
    root = _ref("a", digest="sha256:" + "f" * 64)
    with pytest.raises(DigestMismatchError):
        _resolver(store).resolve(root)


def test_tampered_contract() -> None:
    sealed = _contract("a")
    tampered = dataclasses.replace(sealed, name="tampered")
    store = {("a", "1.0.0"): tampered}
    with pytest.raises(DigestMismatchError):
        _resolver(store).resolve(_ref("a"))


@pytest.mark.parametrize("lifecycle", ["PUBLISHED_UNATTESTED", "REGISTERED_UNVERIFIED", "DRAFT"])
def test_governed_rejects(lifecycle: str) -> None:
    store = {("a", "1.0.0"): _contract("a", lifecycle=lifecycle)}
    with pytest.raises(ResolverError):
        _resolver(store).resolve(_ref("a"))


def test_exploratory_allows_draft() -> None:
    store = {("a", "1.0.0"): _contract("a", lifecycle="DRAFT")}
    g = _resolver(store).resolve(_ref("a"), purpose="exploratory")
    assert len(g.nodes) == 1


def test_exploratory_rejects_retired() -> None:
    store = {("a", "1.0.0"): _contract("a", lifecycle="RETIRED")}
    with pytest.raises(ResolverError):
        _resolver(store).resolve(_ref("a"), purpose="exploratory")


def test_conflict_pair() -> None:
    b_ref = _ref("b")
    c_ref = _ref("c")
    a = _contract("a", deps=(_edge(b_ref), _edge(c_ref)))
    b = _contract("b", deps=(_edge(c_ref, DependencyRelation.CONFLICTS_WITH),))
    c = _contract("c")
    store = {("a", "1.0.0"): a, ("b", "1.0.0"): b, ("c", "1.0.0"): c}
    with pytest.raises(ResolverError, match="conflict"):
        _resolver(store).resolve(_ref("a"))


def test_unsupported_type() -> None:
    tool_ref = _ref("t", capability_type=CapabilityType.TOOL_ADAPTER)
    store = {("t", "1.0.0"): _contract("t", capability_type=CapabilityType.TOOL_ADAPTER)}
    resolver = CapabilityResolver([FakeAdapter(CapabilityType.TOOL_ADAPTER, store)])
    with pytest.raises(UnsupportedCapabilityTypeError):
        resolver.resolve(tool_ref)


def test_max_depth_bound() -> None:
    store = _chain_store(20)
    with pytest.raises(ResolverError):
        _resolver(store).resolve(_ref("node-0"))


def test_max_nodes_bound() -> None:
    store = _chain_store(65)
    with pytest.raises(ResolverError):
        _resolver(store).resolve(_ref("node-0"))


def test_validate_graph_compatible() -> None:
    store = _chain_store(3)
    resolver = _resolver(store)
    graph = resolver.resolve(_ref("node-0"))
    report = resolver.validate_graph(graph)
    assert report.overall == CompatibilityOutcome.COMPATIBLE
    assert report.graph_digest == graph.graph_digest
    assert all(o.outcome == CompatibilityOutcome.COMPATIBLE for o in report.outcomes)
    assert CompatibilityChecker().check(graph).overall == CompatibilityOutcome.COMPATIBLE


def test_validate_graph_unknown() -> None:
    store = _chain_store(2)
    graph = _resolver(store).resolve(_ref("node-0"))
    edge = _edge(_ref("ghost"))
    manual = ResolvedCapabilityGraph(graph.root, graph.nodes, (edge,), graph.graph_digest)
    report = CapabilityResolver([]).validate_graph(manual)
    assert report.overall == CompatibilityOutcome.UNKNOWN


def test_validate_graph_incompatible() -> None:
    store = _chain_store(2)
    graph = _resolver(store).resolve(_ref("node-0"))
    edge = _edge(_ref("node-1"), DependencyRelation.CONFLICTS_WITH)
    manual = ResolvedCapabilityGraph(graph.root, graph.nodes, (edge,), graph.graph_digest)
    report = CapabilityResolver([]).validate_graph(manual)
    assert report.overall == CompatibilityOutcome.INCOMPATIBLE


def test_graph_digest_stable_across_adapter_order() -> None:
    store = _chain_store(3)
    a1 = FakeAdapter(CapabilityType.SKILL, store)
    a2 = FakeAdapter(CapabilityType.PROMPT, {})
    g1 = CapabilityResolver([a1, a2]).resolve(_ref("node-0"))
    g2 = CapabilityResolver([a2, a1]).resolve(_ref("node-0"))
    assert g1.graph_digest == g2.graph_digest
    assert g1.nodes == g2.nodes


def test_invalid_purpose() -> None:
    store = {("a", "1.0.0"): _contract("a")}
    with pytest.raises(ResolverError):
        _resolver(store).resolve(_ref("a"), purpose="bogus")
    with pytest.raises(ResolverError):
        _resolver(store).resolve(_ref("a"), purpose="  ")
