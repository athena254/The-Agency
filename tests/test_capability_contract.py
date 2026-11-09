from __future__ import annotations

import dataclasses

import pytest

from agency.capability import contract as contract_module
from agency.capability.contract import (
    CapabilityContract,
    CapabilityError,
    CapabilityLifecycleState,
    CapabilityNotFoundError,
    CapabilityRef,
    CapabilityType,
    CorruptCapabilityError,
    DependencyEdge,
    DependencyRelation,
    DigestMismatchError,
    ProvenanceRef,
    PublicationAttestation,
    UnsupportedCapabilityTypeError,
    canonical_sha256,
    compute_content_digest,
    content_digest,
    verify_digest,
)

_PLACEHOLDER = "sha256:" + "0" * 64
_GOLDEN = "sha256:68022c22939de4e5842d8aa78d7e2e7d8512dc5fdabe8b8e6fc70509406dc37b"


def _provenance(**overrides: object) -> ProvenanceRef:
    kwargs: dict[str, object] = {
        "source_kind": "agency_native",
        "source_id": "src-1",
        "actor_id": "actor-1",
        "created_at": "2026-01-01T00:00:00Z",
    }
    kwargs.update(overrides)
    return ProvenanceRef(**kwargs)  # type: ignore[arg-type]


def _contract(**overrides: object) -> CapabilityContract:
    kwargs: dict[str, object] = {
        "schema_version": 1,
        "ref": CapabilityRef(CapabilityType.SKILL, "demo-skill", "1.0.0"),
        "name": "Demo",
        "description": "demo description",
        "lifecycle_state": "PUBLISHED",
        "native_state": "PUBLISHED",
        "inputs": {"q": "str"},
        "outputs": {"a": "str"},
        "requested_permissions": ("read",),
        "implementation_ref": "op.demo",
        "dependencies": (),
        "tags": ("demo",),
        "provenance": _provenance(),
        "content_digest": _PLACEHOLDER,
    }
    kwargs.update(overrides)
    return CapabilityContract(**kwargs)  # type: ignore[arg-type]


def _sealed(**overrides: object) -> CapabilityContract:
    base = _contract(**overrides)
    return dataclasses.replace(base, content_digest=compute_content_digest(base))


def test_error_hierarchy() -> None:
    assert issubclass(CapabilityError, ValueError)
    assert issubclass(UnsupportedCapabilityTypeError, CapabilityError)
    assert issubclass(CapabilityNotFoundError, CapabilityError)
    assert issubclass(CapabilityNotFoundError, KeyError)
    assert issubclass(DigestMismatchError, CapabilityError)
    assert issubclass(CorruptCapabilityError, CapabilityError)


def test_lifecycle_values_equal_names() -> None:
    assert {state.name for state in CapabilityLifecycleState} == {
        state.value for state in CapabilityLifecycleState
    }
    assert len(CapabilityLifecycleState) == 10


def test_dependency_relation_values() -> None:
    assert DependencyRelation.REQUIRES.value == "requires"
    assert DependencyRelation.COMPOSES.value == "composes"
    assert DependencyRelation.CONFLICTS_WITH.value == "conflicts_with"
    assert DependencyRelation.SUPERSEDES.value == "supersedes"
    assert DependencyRelation.COMPATIBLE_WITH.value == "compatible_with"


@pytest.mark.parametrize("capability_type", list(CapabilityType))
def test_valid_per_type_round_trip(capability_type: CapabilityType) -> None:
    ref = CapabilityRef(capability_type, "item-1", "2.3.4")
    contract = _sealed(ref=ref)
    data = contract.to_dict()
    assert data["ref"] == {
        "capability_type": capability_type.value,
        "capability_id": "item-1",
        "version": "2.3.4",
        "digest": None,
    }
    assert data["schema_version"] == 1
    assert data["name"] == "Demo"
    assert data["requested_permissions"] == ["read"]
    assert data["tags"] == ["demo"]
    assert data["provenance"]["source_kind"] == "agency_native"
    assert data["content_digest"] == compute_content_digest(contract)
    assert str(ref) == f"{capability_type.value}:item-1@2.3.4"
    verify_digest(contract)


def test_capability_type_from_value() -> None:
    assert CapabilityType.from_value("skill") is CapabilityType.SKILL
    assert CapabilityType.from_value("knowledge_pack") is CapabilityType.KNOWLEDGE_PACK


def test_unsupported_type_rejected() -> None:
    with pytest.raises(UnsupportedCapabilityTypeError):
        CapabilityType.from_value("unknown_thing")
    with pytest.raises(UnsupportedCapabilityTypeError):
        CapabilityType.from_value("")
    with pytest.raises(UnsupportedCapabilityTypeError):
        CapabilityRef("unknown_thing", "item-1", "1.0.0")  # type: ignore[arg-type]


def test_ref_accepts_str_type() -> None:
    ref = CapabilityRef("prompt", "item-1", "1.0.0")
    assert ref.capability_type is CapabilityType.PROMPT


@pytest.mark.parametrize("bad_id", ["", " ", "-bad", ".bad", "has space", "a" * 129, "a/b"])
def test_invalid_id_rejected(bad_id: str) -> None:
    with pytest.raises(ValueError):
        CapabilityRef(CapabilityType.SKILL, bad_id, "1.0.0")


@pytest.mark.parametrize("bad_version", ["", "1.0", "v1.0.0", "latest", "1.0.0.0", "1..0"])
def test_invalid_version_rejected(bad_version: str) -> None:
    with pytest.raises(ValueError):
        CapabilityRef(CapabilityType.SKILL, "item-1", bad_version)


@pytest.mark.parametrize(
    "bad_digest",
    ["", "sha256:xyz", "sha256:" + "A" * 64, "sha256:" + "a" * 63, "md5:" + "a" * 64],
)
def test_invalid_digest_rejected(bad_digest: str) -> None:
    with pytest.raises(ValueError):
        CapabilityRef(CapabilityType.SKILL, "item-1", "1.0.0", digest=bad_digest)


def test_valid_digest_accepted() -> None:
    ref = CapabilityRef(CapabilityType.SKILL, "item-1", "1.0.0", digest=_PLACEHOLDER)
    assert ref.digest == _PLACEHOLDER


def test_edge_accepts_str_relation_and_validates_label() -> None:
    target = CapabilityRef(CapabilityType.PROMPT, "p-1", "1.0.0")
    edge = DependencyEdge("requires", target, label="role")
    assert edge.relation is DependencyRelation.REQUIRES
    assert edge.to_dict()["relation"] == "requires"
    with pytest.raises(ValueError):
        DependencyEdge("bogus", target)
    with pytest.raises(ValueError):
        DependencyEdge(DependencyRelation.REQUIRES, target, label="x" * 129)


def test_provenance_validation() -> None:
    with pytest.raises(ValueError):
        ProvenanceRef(source_kind="bogus")
    for kind in ("agency_native", "human_authored", "generated_candidate", "reviewed_import"):
        assert ProvenanceRef(source_kind=kind).source_kind == kind
    prov = _provenance(parents=(CapabilityRef(CapabilityType.SKILL, "parent-1", "1.0.0"),))
    assert prov.to_dict()["parents"][0]["capability_id"] == "parent-1"


@pytest.mark.parametrize("bad_name", ["", " ", "x" * 201])
def test_invalid_name_rejected(bad_name: str) -> None:
    with pytest.raises(ValueError):
        _contract(name=bad_name)


@pytest.mark.parametrize("bad_schema", [0, 2, -1])
def test_invalid_schema_version_rejected(bad_schema: int) -> None:
    with pytest.raises(ValueError):
        _contract(schema_version=bad_schema)


@pytest.mark.parametrize("field_name", ["lifecycle_state", "native_state"])
def test_blank_lifecycle_rejected(field_name: str) -> None:
    with pytest.raises(ValueError):
        _contract(**{field_name: " "})


def test_canonical_sha256_format() -> None:
    digest = canonical_sha256({"a": 1})
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


def test_canonical_rejects_oversized() -> None:
    with pytest.raises(ValueError):
        canonical_sha256("x" * 70000)
    with pytest.raises(ValueError):
        canonical_sha256({"k": "x" * 40000, "j": "y" * 40000})


def test_canonical_rejects_excessive_nesting() -> None:
    value: object = 1
    for _ in range(20):
        value = [value]
    with pytest.raises(ValueError):
        canonical_sha256(value)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_canonical_rejects_nonfinite(bad: float) -> None:
    with pytest.raises(ValueError):
        canonical_sha256({"x": bad})


def test_canonical_rejects_bytes_and_unsupported() -> None:
    with pytest.raises(ValueError):
        canonical_sha256(b"bytes")
    with pytest.raises(ValueError):
        canonical_sha256({"x": b"bytes"})
    with pytest.raises(ValueError):
        canonical_sha256((1, 2))
    with pytest.raises(ValueError):
        canonical_sha256({1: "non-str key"})
    with pytest.raises(ValueError):
        canonical_sha256(object())


def test_compute_rejects_nonfinite_in_inputs() -> None:
    contract = _contract(inputs={"x": float("nan")})
    with pytest.raises(ValueError):
        compute_content_digest(contract)


def test_digest_stable_across_dict_order() -> None:
    first = _contract(inputs={"a": 1, "b": {"x": 1, "y": 2}}, extra={"m": 1, "n": 2})
    second = _contract(inputs={"b": {"y": 2, "x": 1}, "a": 1}, extra={"n": 2, "m": 1})
    assert compute_content_digest(first) == compute_content_digest(second)
    assert canonical_sha256({"a": 1, "b": 2}) == canonical_sha256({"b": 2, "a": 1})


def test_digest_changes_per_covered_field() -> None:
    base = _sealed()
    mutations: list[CapabilityContract] = [
        dataclasses.replace(base, name="Other"),
        dataclasses.replace(base, description="other"),
        dataclasses.replace(base, inputs={"q": "int"}),
        dataclasses.replace(base, outputs={"a": "int"}),
        dataclasses.replace(base, requested_permissions=("write",)),
        dataclasses.replace(base, implementation_ref="op.other"),
        dataclasses.replace(base, tags=("other",)),
        dataclasses.replace(base, extra={"k": "v"}),
        dataclasses.replace(
            base,
            dependencies=(
                DependencyEdge(
                    DependencyRelation.REQUIRES,
                    CapabilityRef(CapabilityType.PROMPT, "p-1", "1.0.0"),
                ),
            ),
        ),
        dataclasses.replace(base, provenance=_provenance(source_kind="human_authored")),
        dataclasses.replace(
            base, ref=CapabilityRef(CapabilityType.WORKFLOW, "demo-skill", "1.0.0")
        ),
        dataclasses.replace(base, ref=CapabilityRef(CapabilityType.SKILL, "other-skill", "1.0.0")),
        dataclasses.replace(base, ref=CapabilityRef(CapabilityType.SKILL, "demo-skill", "1.0.1")),
    ]
    for mutated in mutations:
        assert compute_content_digest(mutated) != compute_content_digest(base)


def test_lifecycle_change_does_not_change_digest() -> None:
    base = _sealed()
    moved = dataclasses.replace(base, lifecycle_state="DEPRECATED", native_state="DEPRECATED")
    assert compute_content_digest(moved) == compute_content_digest(base)
    redigested = dataclasses.replace(base, content_digest=_PLACEHOLDER)
    assert compute_content_digest(redigested) == compute_content_digest(base)


def test_content_digest_alias() -> None:
    assert contract_module.content_digest is contract_module.compute_content_digest
    assert content_digest(_contract()) == compute_content_digest(_contract())


def test_verify_digest_passes_and_mismatch_fails() -> None:
    sealed = _sealed()
    verify_digest(sealed)
    tampered = dataclasses.replace(sealed, name="Tampered")
    with pytest.raises(DigestMismatchError) as exc_info:
        verify_digest(tampered)
    assert "INTEGRITY_FAILURE" in str(exc_info.value)


def test_golden_vector() -> None:
    contract = _sealed()
    assert contract.content_digest == _GOLDEN
    assert compute_content_digest(contract) == _GOLDEN
    verify_digest(contract)


def test_contract_defensive_copies() -> None:
    inputs = {"q": "str", "nested": {"n": 1}}
    contract = _contract(inputs=inputs, extra={"k": ["v"]})
    inputs["nested"]["n"] = 999
    assert contract.inputs == {"q": "str", "nested": {"n": 1}}
    data = contract.to_dict()
    data["inputs"]["nested"]["n"] = 999
    data["extra"]["k"].append("x")
    assert contract.inputs == {"q": "str", "nested": {"n": 1}}
    assert contract.extra == {"k": ["v"]}


def test_contract_list_inputs_normalised_to_tuples() -> None:
    contract = _contract(requested_permissions=["read"], tags=["a"], dependencies=[])  # type: ignore[arg-type]
    assert contract.requested_permissions == ("read",)
    assert contract.tags == ("a",)
    assert contract.dependencies == ()


def test_frozen_contract() -> None:
    contract = _sealed()
    with pytest.raises(dataclasses.FrozenInstanceError):
        contract.name = "nope"  # type: ignore[misc]


def test_publication_attestation() -> None:
    ref = CapabilityRef(CapabilityType.SKILL, "demo-skill", "1.0.0")
    attestation = PublicationAttestation(
        ref=ref,
        content_digest=_GOLDEN,
        approver="approver-1",
        decision_ref="decision-1",
        evidence_ref="evidence-1",
    )
    assert attestation.schema_version == 1
    assert attestation.decided_at == ""


def test_projection_excludes_lifecycle_and_digest() -> None:
    projection = contract_module._projection_dict(_contract())
    assert "lifecycle_state" not in projection
    assert "native_state" not in projection
    assert "content_digest" not in projection
    assert set(projection) == {
        "schema_version",
        "ref",
        "name",
        "description",
        "inputs",
        "outputs",
        "requested_permissions",
        "implementation_ref",
        "dependencies",
        "tags",
        "provenance",
        "extra",
    }
