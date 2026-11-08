"""Strict contracts for the one trusted coding fixture (no native execution)."""

import pytest
from pydantic import ValidationError

from agency.factory.coding_models import FactoryCodingRequest


def binding_data():
    return {
        "owner_id": "owner-1",
        "workspace_id": "workspace-1",
        "request_id": "request:1",
        "task_id": "task-1",
        "thread_id": "thread-1",
        "profile_id": "literal-assignment-v1",
        "profile_sha256": "a" * 64,
        "blueprint_id": "blueprint-1",
        "blueprint_version": "1",
        "runtime_agent_id": "agent-1",
        "base_commit": "b" * 40,
        "checker_sha256": "c" * 64,
        "criteria_sha256": "d" * 64,
    }


def test_binding_edit_and_receipt_are_strict_frozen_snapshots():
    from agency.factory.coding_models import CodingBinding, CodingEdit, CodingReceipt

    bound = CodingBinding(**binding_data())
    edit = CodingEdit(path="m.py", old="1", new="2")
    receipt = CodingReceipt(
        binding=bound,
        plan_sha256="e" * 64,
        forge_report_id="report-1",
        forge_report_sha256="f" * 64,
        artifact_sha256="a" * 64,
        patch_sha256="b" * 64,
        objective_status="passed",
        reason="matched",
    )
    assert receipt.release_authorized is False and receipt.tests_run is False
    assert bound.schema_version == "factory-coding-binding.v1"
    for item, field, value in [
        (bound, "owner_id", "changed"),
        (edit, "new", "3"),
        (receipt, "reason", "forge_failed"),
    ]:
        with pytest.raises(ValidationError):
            setattr(item, field, value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner_id", True),
        ("workspace_id", ""),
        ("task_id", "a" * 129),
        ("request_id", "bad/id"),
        ("thread_id", "x\n"),
        ("owner_id", "\udfff"),
        ("blueprint_version", 1),
        ("blueprint_version", False),
        ("runtime_agent_id", " "),
        ("base_commit", "a" * 39),
        ("base_commit", "g" * 40),
        ("profile_sha256", "A" * 64),
        ("checker_id", "constructor"),
        ("checker_version", 1),
        ("profile_id", {}),
        ("schema_version", ["factory-coding-binding.v1"]),
        ("extra", "no"),
    ],
)
def test_binding_rejects_bad_fields(field, value):
    from agency.factory.coding_models import CodingBinding

    data = binding_data()
    data[field] = value
    with pytest.raises(ValueError):
        CodingBinding(**data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("path", "../m.py"),
        ("path", "./m.py"),
        ("path", "/m.py"),
        ("path", "M.py"),
        ("path", "m\\.py"),
        ("path", "m.py\x00"),
        ("old", ""),
        ("new", ""),
        ("old", True),
        ("new", "\ud800"),
        ("new", "é" * 4097),
        ("file", "m.py"),
    ],
)
def test_edit_rejects_unsafe_or_coerced_fields(field, value):
    from agency.factory.coding_models import CodingEdit

    data = {"path": "m.py", "old": "1", "new": "2"}
    data[field] = value
    with pytest.raises(ValueError):
        CodingEdit(**data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("objective_status", "__proto__"),
        ("objective_status", True),
        ("reason", ["matched"]),
        ("reason", "constructor"),
        ("release_authorized", True),
        ("release_authorized", 0),
        ("tests_run", 0),
        ("tests_run", True),
        ("artifact_sha256", None),
        ("patch_sha256", None),
        ("reason", "forge_failed"),
        ("forge_report_id", ""),
        ("plan_sha256", "z" * 64),
        ("other", False),
    ],
)
def test_receipt_rejects_bad_types_and_inconsistent_pass(field, value):
    from agency.factory.coding_models import CodingBinding, CodingReceipt

    data = {
        "binding": CodingBinding(**binding_data()),
        "plan_sha256": "e" * 64,
        "forge_report_id": "report-1",
        "forge_report_sha256": "f" * 64,
        "artifact_sha256": "a" * 64,
        "patch_sha256": "b" * 64,
        "objective_status": "passed",
        "reason": "matched",
    }
    data[field] = value
    with pytest.raises(ValueError):
        CodingReceipt(**data)


def request(**changes):
    return dict(
        request_id="request:1",
        thread_id="thread-1",
        profile_id="literal-assignment-v1",
        task="Set x to 2",
        **changes,
    )


def test_request_valid_and_frozen():
    item = FactoryCodingRequest(**request())
    assert item.task == "Set x to 2"
    with pytest.raises(ValidationError):
        item.task = "replace"


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_id", True),
        ("request_id", 1),
        ("request_id", " leading"),
        ("request_id", "x\n"),
        ("request_id", "a" * 129),
        ("thread_id", "../thread"),
        ("profile_id", ["literal-assignment-v1"]),
        ("profile_id", "__proto__"),
        ("task", 3),
        ("task", ""),
        ("task", " \n"),
        ("task", "x" * 8001),
        ("task", "\ud800"),
        ("repo", "/untrusted"),
        ("check_command", "pytest"),
    ],
)
def test_request_rejects_malformed_fields(field, value):
    data = request()
    data[field] = value
    with pytest.raises((ValidationError, ValueError)):
        FactoryCodingRequest(**data)


def test_request_preserves_exact_input_tokens():
    data = request()
    data["task"] = "  Set x to 2\n"
    assert FactoryCodingRequest(**data).model_dump() == data


def test_canonical_json_branch_and_fixed_baseline_diff():
    import hashlib

    from agency.factory.coding_models import (
        CodingBinding,
        canonical_sha256,
        coding_branch,
        literal_patch,
    )

    assert (
        canonical_sha256({"z": "é", "a": [None, False, 2]})
        == hashlib.sha256('{"a":[null,false,2],"z":"é"}'.encode()).hexdigest()
    )
    assert canonical_sha256({"a": 1, "b": 2}) == canonical_sha256({"b": 2, "a": 1})
    bound = CodingBinding(**binding_data())
    assert (
        coding_branch(bound)
        == "factory-" + canonical_sha256({"workspace_id": "workspace-1", "task_id": "task-1"})[:24]
    )
    assert literal_patch("x = 2\n") == ("--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n")
    assert literal_patch("x = 1\n") == ""
    assert literal_patch("x = 2") == ("--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n-x = 1\n+x = 2")
    assert literal_patch("x = 2\r\n").endswith("+x = 2\r\n")


@pytest.mark.parametrize(
    "value",
    [
        object(),
        b"bytes",
        (1,),
        {1: "key"},
        float("nan"),
        float("inf"),
        "\ud800",
        "x" * 65537,
        [0] * 2048,
    ],
)
def test_canonical_rejects_non_json_or_unbounded_values(value):
    from agency.factory.coding_models import canonical_sha256

    with pytest.raises(ValueError):
        canonical_sha256(value)


def test_canonical_depth_nodes_size_cycles_and_no_normalization():
    from agency.factory.coding_models import canonical_sha256

    node = 0
    for _ in range(16):
        node = [node]
    assert len(canonical_sha256(node)) == 64
    with pytest.raises(ValueError):
        canonical_sha256([node])
    assert len(canonical_sha256([0] * 2047)) == 64
    assert len(canonical_sha256("x" * 65534)) == 64
    with pytest.raises(ValueError):
        canonical_sha256("x" * 65535)
    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(ValueError):
        canonical_sha256(cyclic)
    assert canonical_sha256("é") != canonical_sha256("e\u0301")


@pytest.mark.parametrize("value", [True, b"x = 2\n", "\ud800", "é" * 4097])
def test_literal_patch_rejects_invalid_text(value):
    from agency.factory.coding_models import literal_patch

    with pytest.raises(ValueError):
        literal_patch(value)
