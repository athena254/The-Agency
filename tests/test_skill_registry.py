"""Tests for agency.skills.registry (pytest tmp_path, no network/docker)."""

from __future__ import annotations

import copy
import json

import pytest

from agency.skills.registry import SkillRegistry, SkillSpec, SkillStatus

ALLOWED = frozenset({"read", "write"})


def _make_spec(**overrides) -> SkillSpec:
    params = {
        "skill_id": "summarize-docs",
        "version": "1.0.0",
        "name": "Summarize docs",
        "description": "Summarize a document deterministically.",
        "inputs": {"doc": {"type": "string"}},
        "outputs": {"summary": {"type": "string"}},
        "permissions": (),
        "implementation": "summarize.docs",
        "provenance": "agency-team",
        "status": SkillStatus.DRAFT,
    }
    params.update(overrides)
    return SkillSpec(**params)


def _registry(tmp_path, allowed=ALLOWED) -> SkillRegistry:
    return SkillRegistry(str(tmp_path / "skills.db"), allowed_permissions=allowed)


@pytest.mark.parametrize(
    "status", [SkillStatus.TESTING, SkillStatus.APPROVED, SkillStatus.PUBLISHED]
)
def test_registration_cannot_skip_lifecycle(tmp_path, status):
    reg = _registry(tmp_path)
    try:
        with pytest.raises(ValueError, match="DRAFT"):
            reg.register(_make_spec(status=status))
        assert reg.list_versions("summarize-docs") == []
    finally:
        reg.close()


def test_lifecycle_evidence_survives_restart(tmp_path):
    reg = _registry(tmp_path)
    reg.register(_make_spec())
    reg.transition("summarize-docs", "1.0.0", SkillStatus.TESTING)
    reg.transition("summarize-docs", "1.0.0", SkillStatus.APPROVED, "tests passed")
    reg.publish("summarize-docs", "1.0.0", "human review")
    reg.close()
    reopened = _registry(tmp_path)
    try:
        events = reopened.list_events("summarize-docs", "1.0.0")
        assert [event["to_status"] for event in events] == ["TESTING", "APPROVED", "PUBLISHED"]
        assert events[-1]["evidence"] == "human review"
        assert events[-1]["at"]
    finally:
        reopened.close()


def test_register_get_roundtrip(tmp_path):
    reg = _registry(tmp_path)
    try:
        spec = _make_spec()
        stored = reg.register(spec)
        assert stored == spec
        fetched = reg.get("summarize-docs", "1.0.0")
        assert fetched == spec
        assert fetched.inputs == {"doc": {"type": "string"}}
        assert fetched.status is SkillStatus.DRAFT
    finally:
        reg.close()


def test_restart_persistence(tmp_path):
    db = str(tmp_path / "skills.db")
    reg = SkillRegistry(db, allowed_permissions=ALLOWED)
    reg.register(_make_spec(permissions=("read",)))
    reg.close()
    reopened = SkillRegistry(db, allowed_permissions=ALLOWED)
    try:
        fetched = reopened.get("summarize-docs", "1.0.0")
        assert fetched.permissions == ("read",)
        assert fetched.implementation == "summarize.docs"
    finally:
        reopened.close()


def test_duplicate_rejected(tmp_path):
    reg = _registry(tmp_path)
    try:
        reg.register(_make_spec())
        with pytest.raises(ValueError):
            reg.register(_make_spec())
        # Same version, different skill_id is fine.
        reg.register(_make_spec(skill_id="other"))
        # Different version, same skill_id is fine.
        reg.register(_make_spec(version="1.0.1"))
        assert len(reg.list_versions("summarize-docs")) == 2
    finally:
        reg.close()


def test_immutability_nested_dicts(tmp_path):
    reg = _registry(tmp_path)
    try:
        inputs = {"cfg": {"nested": [1, 2, 3]}}
        outputs = {"out": {"deep": {"x": 1}}}
        reg.register(_make_spec(inputs=inputs, outputs=outputs))
        inputs["cfg"]["nested"].append(999)
        outputs["out"]["deep"]["x"] = 999
        fetched = reg.get("summarize-docs", "1.0.0")
        assert fetched.inputs == {"cfg": {"nested": [1, 2, 3]}}
        assert fetched.outputs == {"out": {"deep": {"x": 1}}}
        # Mutating a fetched copy must not affect the store.
        fetched.inputs["cfg"]["nested"].append(1000)
        refetched = reg.get("summarize-docs", "1.0.0")
        assert refetched.inputs == {"cfg": {"nested": [1, 2, 3]}}
        # Serialization round-trip is a copy too.
        snap = refetched.to_dict()
        snap["inputs"]["cfg"]["nested"].append(1000)
        assert reg.get("summarize-docs", "1.0.0").inputs == {"cfg": {"nested": [1, 2, 3]}}
    finally:
        reg.close()


def test_returns_do_not_alias(tmp_path):
    reg = _registry(tmp_path)
    try:
        reg.register(_make_spec(version="1.0.0"))
        reg.register(_make_spec(version="2.0.0"))
        first = reg.list_versions("summarize-docs")
        first[0].inputs["injected"] = True
        assert "injected" not in reg.get(first[0].skill_id, first[0].version).inputs
        restored = SkillSpec.from_dict(copy.deepcopy(first[1].to_dict()))
        assert restored == reg.get("summarize-docs", "2.0.0")
    finally:
        reg.close()


@pytest.mark.parametrize("bad", ["1.0", "v1.0.0", "1.0.0.0", "1.0.x", "", "  ", "latest", "1..0"])
def test_invalid_version_tokens_rejected(tmp_path, bad):
    reg = _registry(tmp_path)
    try:
        with pytest.raises(ValueError):
            reg.register(_make_spec(version=bad))
    finally:
        reg.close()


@pytest.mark.parametrize(
    "bad",
    ["../evil", "/bin/run", "http://x/y", "Foo.Bar", "has space",
     "import os", "skill;drop", "", "  ", "UPPER"],
)
def test_invalid_implementation_rejected(tmp_path, bad):
    reg = _registry(tmp_path)
    try:
        with pytest.raises(ValueError):
            reg.register(_make_spec(implementation=bad))
    finally:
        reg.close()


def test_unrecognized_permission_rejected(tmp_path):
    reg = _registry(tmp_path)
    try:
        with pytest.raises(ValueError):
            reg.register(_make_spec(permissions=("admin",)))
    finally:
        reg.close()


def test_default_registry_rejects_any_permission(tmp_path):
    reg = SkillRegistry(str(tmp_path / "skills.db"))
    try:
        with pytest.raises(ValueError):
            reg.register(_make_spec(permissions=("read",)))
        reg.register(_make_spec())  # empty permissions are fine
    finally:
        reg.close()


@pytest.mark.parametrize(
    "field,value",
    [("skill_id", ""), ("skill_id", "  "), ("name", ""), ("implementation", ""),
     ("provenance", ""), ("provenance", "   "), ("version", "")],
)
def test_blank_fields_rejected(tmp_path, field, value):
    reg = _registry(tmp_path)
    try:
        with pytest.raises(ValueError):
            reg.register(_make_spec(**{field: value}))
    finally:
        reg.close()


def test_non_serializable_and_oversize_rejected(tmp_path):
    reg = _registry(tmp_path)
    try:
        with pytest.raises(ValueError):
            reg.register(_make_spec(inputs={"f": object()}))
        with pytest.raises(ValueError):
            reg.register(_make_spec(inputs=["not-a-mapping"]))  # type: ignore[arg-type]
        big = "x" * (64 * 1024)
        with pytest.raises(ValueError):
            reg.register(_make_spec(inputs={"blob": big}))
        with pytest.raises(ValueError):
            reg.register(_make_spec(outputs={"blob": big}))
    finally:
        reg.close()


def test_legal_lifecycle(tmp_path):
    reg = _registry(tmp_path)
    try:
        reg.register(_make_spec())
        assert reg.transition("summarize-docs", "1.0.0", SkillStatus.TESTING).status is (
            SkillStatus.TESTING
        )
        approved = reg.transition("summarize-docs", "1.0.0", SkillStatus.APPROVED, "tests-pass")
        assert approved.status is SkillStatus.APPROVED
        published = reg.publish("summarize-docs", "1.0.0", "review-ok")
        assert published.status is SkillStatus.PUBLISHED
        # Definition fields unchanged by status moves.
        assert published.implementation == "summarize.docs"
        assert published.inputs == {"doc": {"type": "string"}}
        assert reg.transition(
            "summarize-docs", "1.0.0", SkillStatus.DEPRECATED
        ).status is SkillStatus.DEPRECATED
        assert reg.transition(
            "summarize-docs", "1.0.0", SkillStatus.RETIRED
        ).status is SkillStatus.RETIRED
    finally:
        reg.close()


def test_illegal_transitions_rejected(tmp_path):
    reg = _registry(tmp_path)
    try:
        reg.register(_make_spec())
        with pytest.raises(ValueError):  # skip TESTING
            reg.transition("summarize-docs", "1.0.0", SkillStatus.APPROVED, "e")
        with pytest.raises(ValueError):  # backwards target unreachable
            reg.transition("summarize-docs", "1.0.0", SkillStatus.PUBLISHED, "e")
        with pytest.raises(ValueError):  # self-transition
            reg.transition("summarize-docs", "1.0.0", SkillStatus.DRAFT)
        with pytest.raises(ValueError):  # unknown status token
            reg.transition("summarize-docs", "1.0.0", "BOGUS")
        reg.transition("summarize-docs", "1.0.0", SkillStatus.TESTING)
        with pytest.raises(ValueError):  # backwards
            reg.transition("summarize-docs", "1.0.0", SkillStatus.DRAFT)
    finally:
        reg.close()


def test_evidence_required_for_approval_and_publication(tmp_path):
    reg = _registry(tmp_path)
    try:
        reg.register(_make_spec())
        reg.transition("summarize-docs", "1.0.0", SkillStatus.TESTING)
        with pytest.raises(ValueError):
            reg.transition("summarize-docs", "1.0.0", SkillStatus.APPROVED, "")
        with pytest.raises(ValueError):
            reg.transition("summarize-docs", "1.0.0", SkillStatus.APPROVED, "   ")
        reg.transition("summarize-docs", "1.0.0", SkillStatus.APPROVED, "tests-pass")
        with pytest.raises(ValueError):
            reg.transition("summarize-docs", "1.0.0", SkillStatus.PUBLISHED, "")
    finally:
        reg.close()


def test_publish_without_evidence_or_approval(tmp_path):
    reg = _registry(tmp_path)
    try:
        reg.register(_make_spec())
        with pytest.raises(ValueError):  # DRAFT, not APPROVED
            reg.publish("summarize-docs", "1.0.0", "some-evidence")
        reg.transition("summarize-docs", "1.0.0", SkillStatus.TESTING)
        with pytest.raises(ValueError):  # TESTING, not APPROVED
            reg.publish("summarize-docs", "1.0.0", "some-evidence")
        reg.transition("summarize-docs", "1.0.0", SkillStatus.APPROVED, "tests-pass")
        with pytest.raises(ValueError):  # APPROVED but empty evidence
            reg.publish("summarize-docs", "1.0.0", "")
        with pytest.raises(ValueError):
            reg.publish("summarize-docs", "1.0.0", "   ")
    finally:
        reg.close()


def test_unknown_id(tmp_path):
    reg = _registry(tmp_path)
    try:
        with pytest.raises(KeyError):
            reg.get("missing", "9.9.9")
        with pytest.raises(KeyError):
            reg.transition("missing", "9.9.9", SkillStatus.TESTING)
        with pytest.raises(KeyError):
            reg.publish("missing", "9.9.9", "evidence")
        assert reg.list_versions("missing") == []
    finally:
        reg.close()


def test_sql_injection_as_data(tmp_path):
    reg = _registry(tmp_path)
    try:
        evil = "x'; DROP TABLE skills; --"
        reg.register(_make_spec(skill_id=evil, implementation="safe.op"))
        assert reg.get(evil, "1.0.0").skill_id == evil
        # Table survives; normal rows still work.
        reg.register(_make_spec(skill_id="plain"))
        assert reg.get("plain", "1.0.0").name == "Summarize docs"
    finally:
        reg.close()
    # Reopen to prove persistence layer is intact.
    reg2 = _registry(tmp_path)
    try:
        assert reg2.get("plain", "1.0.0").skill_id == "plain"
    finally:
        reg2.close()


def test_inputs_outputs_json_roundtrip_size_boundary(tmp_path):
    reg = _registry(tmp_path)
    try:
        payload = {"k": "v" * 100}
        assert len(json.dumps(payload).encode()) < 64 * 1024
        reg.register(_make_spec(inputs=payload))
        assert reg.get("summarize-docs", "1.0.0").inputs == payload
    finally:
        reg.close()
