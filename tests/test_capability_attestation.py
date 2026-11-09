from __future__ import annotations

import dataclasses

import pytest

from agency.capability.attestation import (
    AttestationError,
    AttestationStore,
    inspect_impact,
    requested_permissions,
)
from agency.capability.contract import (
    CapabilityContract,
    CapabilityRef,
    CapabilityType,
    ProvenanceRef,
    compute_content_digest,
)
from agency.capability.resolver import (
    DependencyEdge,
    DependencyRelation,
    ResolvedCapabilityGraph,
    ResolvedNode,
)

DIGEST = "sha256:" + "c" * 64
DIGEST2 = "sha256:" + "d" * 64


def _ref(cid: str = "a", digest: str | None = None) -> CapabilityRef:
    return CapabilityRef(CapabilityType.SKILL, cid, "1.0.0", digest)


def _auth(approver: str, permission: str) -> bool:
    return approver == "alice" and permission == "capability.publish"


def _contract(cid: str, perms: tuple[str, ...] = ()) -> CapabilityContract:
    base = CapabilityContract(
        1,
        _ref(cid),
        cid,
        "desc",
        "PUBLISHED",
        "PUBLISHED",
        None,
        None,
        perms,
        None,
        (),
        (),
        ProvenanceRef("agency_native", source_id="src"),
        "sha256:" + "0" * 64,
        None,
    )
    return dataclasses.replace(base, content_digest=compute_content_digest(base))


def _graph(
    root_id: str,
    node_ids: tuple[str, ...],
    target_id: str | None = None,
) -> ResolvedCapabilityGraph:
    nodes = tuple(ResolvedNode(_ref(nid, DIGEST), "PUBLISHED") for nid in node_ids)
    edges = (
        (DependencyEdge(DependencyRelation.REQUIRES, _ref(target_id)),)
        if target_id is not None
        else ()
    )
    root = _ref(root_id, DIGEST)
    return ResolvedCapabilityGraph(root, nodes, edges, "sha256:" + "0" * 64)


def test_issue_happy() -> None:
    store = AttestationStore(_auth)
    att = store.issue(_ref("a"), DIGEST, "alice", "dec-1", "ev-1", "2026-01-01")
    assert att.content_digest == DIGEST
    assert att.approver == "alice"
    assert att.decided_at == "2026-01-01"
    # normalized (digest-less) ref for adapter _attested equality
    assert att.ref == _ref("a")


def test_issue_denied() -> None:
    store = AttestationStore(_auth)
    with pytest.raises(AttestationError):
        store.issue(_ref("a"), DIGEST, "bob", "dec-1", "ev-1")


def test_issue_no_authorizer() -> None:
    store = AttestationStore()
    with pytest.raises(AttestationError):
        store.issue(_ref("a"), DIGEST, "alice", "dec-1", "ev-1")


@pytest.mark.parametrize("field", ["approver", "decision_ref", "evidence_ref"])
def test_issue_blank_fields(field: str) -> None:
    store = AttestationStore(_auth)
    kwargs = {"approver": "alice", "decision_ref": "d", "evidence_ref": "e"}
    kwargs[field] = "  "
    with pytest.raises(AttestationError):
        store.issue(_ref("a"), DIGEST, **kwargs)  # type: ignore[arg-type]


def test_issue_bad_digest() -> None:
    store = AttestationStore(_auth)
    with pytest.raises(AttestationError):
        store.issue(_ref("a"), "sha256:xyz", "alice", "d", "e")


def test_issue_duplicate() -> None:
    store = AttestationStore(_auth)
    store.issue(_ref("a"), DIGEST, "alice", "d", "e")
    with pytest.raises(AttestationError):
        store.issue(_ref("a"), DIGEST, "alice", "d2", "e2")


def test_lookup_roundtrip() -> None:
    store = AttestationStore(_auth)
    store.issue(_ref("a", DIGEST2), DIGEST, "alice", "d", "e")
    found = store.get(_ref("a"), DIGEST)
    assert found is not None
    assert store.lookup(_ref("a"), DIGEST) == found
    assert store.get(_ref("a"), DIGEST2) is None
    assert store.list_all() == (found,)


def test_requested_permissions_union_sorted() -> None:
    contracts = [_contract("a", ("b", "a", "a")), _contract("b", ("c", "a"))]
    assert requested_permissions(contracts) == ("a", "b", "c")
    assert requested_permissions([]) == ()


def test_inspect_impact_direct_transitive_count() -> None:
    # consumer (root) -> target via edge; other node is transitive
    g1 = _graph("consumer", ("consumer", "target", "other"), "target")
    g2 = _graph("consumer2", ("consumer2", "target"), "target")
    g3 = _graph("unrelated", ("unrelated", "far"), None)
    report = inspect_impact(_ref("target"), [g1, g2, g3])
    assert report.affected_graphs == 2
    assert [str(r) for r in report.direct_dependents] == [
        "skill:consumer@1.0.0",
        "skill:consumer2@1.0.0",
    ]
    assert [str(r) for r in report.transitive_dependents] == ["skill:other@1.0.0"]
    d = report.to_dict()
    assert d["affected_graphs"] == 2
    assert d["ref"]["capability_id"] == "target"


def test_inspect_impact_empty() -> None:
    g = _graph("x", ("x", "y"), "y")
    report = inspect_impact(_ref("missing"), [g])
    assert report.affected_graphs == 0
    assert report.direct_dependents == ()
    assert report.transitive_dependents == ()
