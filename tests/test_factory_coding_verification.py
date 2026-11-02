"""Non-executing static criterion and copied-evidence receipt validation."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agency.factory.coding_models import (
    CodingBinding,
    CodingEdit,
    canonical_sha256,
    coding_branch,
    literal_patch,
)
from agency.factory.coding_verification import (
    checker_source_sha256,
    criteria_document,
    verify_literal_artifact,
)
from agency.forge.coding_agent import CodingAgentReport
from agency.forge.inspection import FileRecord, InspectionReport
from agency.forge.security_scan import SecurityReport

BASELINE = "x = 1\n"


def bound(**changes):
    data = {
        "owner_id": "owner",
        "workspace_id": "workspace",
        "request_id": "request",
        "task_id": "task",
        "thread_id": "thread",
        "profile_id": "literal-assignment-v1",
        "profile_sha256": "a" * 64,
        "blueprint_id": "blueprint",
        "blueprint_version": "1",
        "runtime_agent_id": "runtime",
        "base_commit": "b" * 40,
        "checker_sha256": checker_source_sha256(),
        "criteria_sha256": canonical_sha256(criteria_document()),
    }
    data.update(changes)
    return CodingBinding(**data)


def report(root, binding, data=b"x = 2\n", *, gate="PASS", **changes):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    file = FileRecord(
        path="m.py",
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        kind="python",
        syntax_ok=True,
    )
    fields = {
        "report_id": "forge-report",
        "created_at": now,
        "worktree_path": str(root),
        "branch": coding_branch(binding),
        "edits_applied": 1,
        "inspection": InspectionReport(
            report_id="inspection", created_at=now, root=str(root), gate="PASS", files=(file,)
        ),
        "security": SecurityReport(
            report_id="security",
            created_at=now,
            root=str(root),
            gate="PASS",
            scanned_files=1,
            skipped_files=0,
        ),
        "gate": gate,
        "caveats": ("tests_not_run", "release_not_authorized"),
    }
    fields.update(changes)
    return CodingAgentReport(**fields)


def prepared(tmp_path, text="x = 2\n", **changes):
    binding = bound()
    root = tmp_path / "artifact"
    root.mkdir()
    data = text.encode("utf-8")
    (root / "m.py").write_bytes(data)
    (root / ".git").write_text("gitdir: fixed-bookkeeping\n", encoding="utf-8")
    edits = (CodingEdit(path="m.py", old=BASELINE, new=text),)
    native = report(root, binding, data, **changes)
    return {
        "binding": binding,
        "edits": edits,
        "forge_report": native,
        "artifact_root": root,
        "patch": literal_patch(text),
    }


def test_fixed_criterion_and_trusted_source_hash():
    import agency.factory.coding_verification as checker

    assert criteria_document() == {
        "schema": "literal-assignment.v1",
        "path": "m.py",
        "name": "x",
        "value": 2,
    }
    assert type(criteria_document()["value"]) is int
    assert (
        checker_source_sha256() == hashlib.sha256(Path(checker.__file__).read_bytes()).hexdigest()
    )
    document = criteria_document()
    document["value"] = 3
    assert criteria_document()["value"] == 2


@pytest.mark.parametrize("text", ["x = 2\n", "# comment\nx=2 # harmless\n", "x = 2", "x = 2\r\n"])
def test_verified_literal_binds_every_copied_digest(tmp_path, text):
    args = prepared(tmp_path, text)
    item = verify_literal_artifact(**args)
    assert item.objective_status == "passed" and item.reason == "matched"
    assert item.binding == args["binding"]
    assert item.plan_sha256 == canonical_sha256([e.model_dump(mode="json") for e in args["edits"]])
    assert item.forge_report_id == args["forge_report"].report_id
    assert item.forge_report_sha256 == canonical_sha256(
        args["forge_report"].model_dump(mode="json")
    )
    assert item.artifact_sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert item.patch_sha256 == hashlib.sha256(args["patch"].encode("utf-8")).hexdigest()
    assert item.tests_run is False and item.release_authorized is False
    assert "tests_not_run" in args["forge_report"].caveats


@pytest.mark.parametrize(
    "text",
    [
        "x = True\n",
        "x = 2.0\n",
        "x = 1 + 1\n",
        "y = 2\n",
        "x: int = 2\n",
        "x = y = 2\n",
        "(x,) = (2,)\n",
        '"doc"\nx = 2\n',
        "x = 2\npass\n",
        "import os\nx=2\n",
        "x = (\n",
        "\n",
    ],
)
def test_fixed_ast_rejects_every_nonliteral_or_extra_statement(tmp_path, text):
    item = verify_literal_artifact(**prepared(tmp_path, text))
    assert (item.objective_status, item.reason) == ("failed", "criterion_mismatch")


def test_generated_calls_are_never_executed_or_imported(tmp_path):
    sentinel = tmp_path / "not-created"
    text = f'x = __import__("pathlib").Path({str(sentinel)!r}).write_text("executed")\n'
    item = verify_literal_artifact(**prepared(tmp_path, text))
    assert item.objective_status == "failed"
    assert not sentinel.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("report_id", " "),
        ("report_id", "r" * 129),
        ("edits_applied", True),
        ("gate", "__proto__"),
        ("note", "\ud800"),
    ],
)
def test_malformed_native_report_fields_are_sanitized(tmp_path, field, value):
    args = prepared(tmp_path)
    args["forge_report"] = args["forge_report"].model_copy(update={field: value})
    assert_invalid(args)


def test_failed_native_report_cannot_claim_more_than_the_one_permitted_edit(tmp_path):
    args = prepared(tmp_path)
    args["forge_report"] = args["forge_report"].model_copy(
        update={"edits_applied": 2, "gate": "FAIL"}
    )
    assert_invalid(args)


def assert_invalid(args):
    with pytest.raises(ValueError, match="^invalid coding evidence$"):
        verify_literal_artifact(**args)


@pytest.mark.parametrize("field", ["checker_sha256", "criteria_sha256"])
def test_fresh_checker_rejects_wrong_frozen_digests(tmp_path, field):
    args = prepared(tmp_path)
    args["binding"] = args["binding"].model_copy(update={field: "f" * 64})
    assert_invalid(args)


@pytest.mark.parametrize(
    "kind",
    [
        "branch",
        "report_root",
        "inspection_root",
        "security_root",
        "edit_count",
        "tests_caveat",
        "forged_report_type",
    ],
)
def test_report_relationships_cannot_be_laundered(tmp_path, kind):
    args = prepared(tmp_path)
    native = args["forge_report"]
    if kind == "branch":
        native = native.model_copy(update={"branch": "caller-selected"})
    elif kind == "report_root":
        native = native.model_copy(update={"worktree_path": str(tmp_path)})
    elif kind == "inspection_root":
        native = native.model_copy(
            update={"inspection": native.inspection.model_copy(update={"root": str(tmp_path)})}
        )
    elif kind == "security_root":
        native = native.model_copy(
            update={"security": native.security.model_copy(update={"root": str(tmp_path)})}
        )
    elif kind == "edit_count":
        native = native.model_copy(update={"edits_applied": 0})
    elif kind == "tests_caveat":
        native = native.model_copy(update={"caveats": ()})
    else:
        native = native.model_dump(mode="json")
    args["forge_report"] = native
    assert_invalid(args)


@pytest.mark.parametrize(
    "kind",
    [
        "list",
        "empty",
        "two",
        "raw_dict",
        "bad_old",
        "nonunique_old",
        "swapped_new",
        "tampered_edit",
        "patch",
        "patch_bytes",
        "patch_unicode",
        "patch_huge",
        "no_patch",
        "inspection_hash",
        "inspection_size",
        "file_content",
    ],
)
def test_plan_patch_artifact_and_native_inventory_must_agree(tmp_path, kind):
    args = prepared(tmp_path)
    edit = args["edits"][0]
    if kind == "list":
        args["edits"] = [edit]
    elif kind == "empty":
        args["edits"] = ()
    elif kind == "two":
        args["edits"] = (edit, edit)
    elif kind == "raw_dict":
        args["edits"] = (edit.model_dump(),)
    elif kind in ("bad_old", "nonunique_old", "swapped_new", "tampered_edit"):
        field, value = {
            "bad_old": ("old", "absent"),
            "nonunique_old": ("old", " "),
            "swapped_new": ("new", "x = 3\n"),
            "tampered_edit": ("path", "../m.py"),
        }[kind]
        args["edits"] = (edit.model_copy(update={field: value}),)
    elif kind.startswith("patch") or kind == "no_patch":
        args["patch"] = {
            "patch": "forged",
            "patch_bytes": b"diff",
            "patch_unicode": "\ud800",
            "patch_huge": "x" * 32769,
            "no_patch": None,
        }[kind]
    elif kind in ("inspection_hash", "inspection_size"):
        field, value = ("sha256", "f" * 64) if kind.endswith("hash") else ("size_bytes", 1)
        native = args["forge_report"]
        record = native.inspection.files[0].model_copy(update={field: value})
        args["forge_report"] = native.model_copy(
            update={"inspection": native.inspection.model_copy(update={"files": (record,)})}
        )
    else:
        (args["artifact_root"] / "m.py").write_text("x = 3\n", encoding="utf-8")
    assert_invalid(args)


@pytest.mark.parametrize("gate", ["overall", "inspection", "security"])
def test_native_failure_never_becomes_an_objective_pass(tmp_path, gate):
    args = prepared(tmp_path)
    native = args["forge_report"]
    if gate == "overall":
        native = native.model_copy(update={"gate": "FAIL"})
    else:
        nested = getattr(native, gate).model_copy(update={"gate": "FAIL"})
        native = native.model_copy(update={gate: nested})
    args["forge_report"] = native
    item = verify_literal_artifact(**args)
    assert (item.objective_status, item.reason) == ("failed", "forge_failed")


@pytest.mark.parametrize("extra", ["extra.py", "__pycache__", "nested"])
def test_extra_inventory_is_a_failed_not_passed_objective(tmp_path, extra):
    args = prepared(tmp_path)
    target = args["artifact_root"] / extra
    if extra.endswith(".py"):
        target.write_text("x = 2\n", encoding="utf-8")
    else:
        target.mkdir()
    item = verify_literal_artifact(**args)
    assert (item.objective_status, item.reason) == ("failed", "inventory_mismatch")


@pytest.mark.parametrize("missing", ["m.py", "root"])
def test_missing_artifacts_are_unavailable_not_success(tmp_path, missing):
    args = prepared(tmp_path)
    root = args["artifact_root"]
    (root / "m.py").unlink()
    if missing == "root":
        (root / ".git").unlink()
        root.rmdir()
    item = verify_literal_artifact(**args)
    assert (item.objective_status, item.reason) == ("unable_to_verify", "artifact_unavailable")
    assert item.artifact_sha256 is None


@pytest.mark.parametrize("data", [b"x = 2\n" + b" " * 8193, b"\xff\xfe", b"x = 2\x00\n"])
def test_unsafe_byte_representations_are_unverifiable(tmp_path, data):
    args = prepared(tmp_path)
    (args["artifact_root"] / "m.py").write_bytes(data)
    item = verify_literal_artifact(**args)
    assert (item.objective_status, item.reason) == ("unable_to_verify", "unsafe_representation")
    assert item.artifact_sha256 is None


def test_artifact_file_is_bounded_at_8192_bytes(tmp_path):
    text = "x = 2\n#" + "a" * (8192 - 7)
    item = verify_literal_artifact(**prepared(tmp_path, text))
    assert item.objective_status == "passed"


@pytest.mark.parametrize("entry", ["m.py", ".git"])
def test_nonregular_allowed_names_are_rejected(tmp_path, entry):
    args = prepared(tmp_path)
    path = args["artifact_root"] / entry
    path.unlink()
    path.mkdir()
    item = verify_literal_artifact(**args)
    assert (item.objective_status, item.reason) == ("unable_to_verify", "unsafe_representation")


@pytest.mark.parametrize("entry", ["root", "m.py", ".git"])
def test_actual_windows_junctions_are_rejected_without_following(tmp_path, entry):
    import _winapi

    args = prepared(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "m.py").write_text("x = 2\n", encoding="utf-8")
    path = args["artifact_root"] if entry == "root" else args["artifact_root"] / entry
    if entry == "root":
        (path / "m.py").unlink()
        (path / ".git").unlink()
        path.rmdir()
    else:
        path.unlink()
    _winapi.CreateJunction(str(outside), str(path))
    try:
        item = verify_literal_artifact(**args)
        assert (item.objective_status, item.reason) == ("unable_to_verify", "unsafe_representation")
    finally:
        path.rmdir()


@pytest.mark.parametrize("marker", ["symlink", "reparse"])
def test_lstat_link_and_reparse_flags_fail_closed(tmp_path, monkeypatch, marker):
    import os
    import stat
    from types import SimpleNamespace

    args = prepared(tmp_path)
    original = os.lstat
    target = args["artifact_root"] / "m.py"

    def marked(path, *a, **kw):
        result = original(path, *a, **kw)
        if Path(path) == target:
            return SimpleNamespace(
                st_mode=stat.S_IFLNK if marker == "symlink" else result.st_mode,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT if marker == "reparse" else 0,
                st_size=result.st_size,
            )
        return result

    monkeypatch.setattr(os, "lstat", marked)
    item = verify_literal_artifact(**args)
    assert (item.objective_status, item.reason) == ("unable_to_verify", "unsafe_representation")


def test_file_read_is_explicitly_bounded_and_does_not_mutate(tmp_path, monkeypatch):
    import builtins

    args = prepared(tmp_path)
    original = builtins.open
    reads = []

    def bounded_open(path, *a, **kw):
        handle = original(path, *a, **kw)
        if Path(path) != args["artifact_root"] / "m.py":
            return handle

        class Bounded:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                handle.close()

            def fileno(self):
                return handle.fileno()

            def read(self, size=-1):
                assert 0 < size <= 8192
                reads.append(size)
                return handle.read(size)

        return Bounded()

    # Path.open uses io.open; both entrypoints are guarded to cover either implementation.
    import io

    monkeypatch.setattr(builtins, "open", bounded_open)
    monkeypatch.setattr(io, "open", bounded_open)
    before = b"x = 2\n"
    item = verify_literal_artifact(**args)
    assert item.objective_status == "passed" and reads
    with original(args["artifact_root"] / "m.py", "rb") as handle:
        assert handle.read() == before


def stored_args(args, *, artifact_text="x = 2\n"):
    return {
        "binding": args["binding"],
        "edits": args["edits"],
        "forge_report": args["forge_report"],
        "artifact_text": artifact_text,
        "patch": args["patch"],
    }


def test_stored_receipt_is_identical_with_no_io_or_current_checker(tmp_path, monkeypatch):
    import builtins
    import io
    import os
    import subprocess

    import agency.factory.coding_verification as checker

    args = prepared(tmp_path)
    saved = verify_literal_artifact(**args)
    copied = saved.model_dump(mode="json")
    inputs = stored_args(args)
    for name in ("m.py", ".git"):
        (args["artifact_root"] / name).unlink()
    args["artifact_root"].rmdir()

    def deny(*a, **kw):
        raise AssertionError("stored validation performed forbidden work")

    with monkeypatch.context() as guard:
        for module, names in [
            (builtins, ("open", "exec", "eval", "compile")),
            (io, ("open",)),
            (os, ("stat", "lstat", "scandir", "listdir", "open", "getcwd")),
            (subprocess, ("run", "Popen")),
            (
                Path,
                (
                    "open",
                    "read_bytes",
                    "read_text",
                    "write_text",
                    "write_bytes",
                    "resolve",
                    "stat",
                    "lstat",
                    "iterdir",
                ),
            ),
            (
                checker,
                (
                    "checker_source_sha256",
                    "criteria_document",
                    "_matches_literal",
                    "_read_artifact",
                    "_receipt",
                    "verify_literal_artifact",
                ),
            ),
        ]:
            for name in names:
                guard.setattr(module, name, deny)
        returned = checker.validate_coding_receipt(copied, **inputs)
        assert returned == copied
        assert returned is not copied
        assert checker.validate_coding_receipt(saved, **inputs) == copied


@pytest.mark.parametrize(
    "field",
    [
        "owner_id",
        "workspace_id",
        "request_id",
        "task_id",
        "thread_id",
        "profile_sha256",
        "blueprint_id",
        "blueprint_version",
        "runtime_agent_id",
        "base_commit",
        "checker_sha256",
        "criteria_sha256",
    ],
)
def test_stored_receipt_rejects_every_swapped_binding_field(tmp_path, field):
    from agency.factory.coding_verification import validate_coding_receipt

    args = prepared(tmp_path)
    saved = verify_literal_artifact(**args)
    inputs = stored_args(args)
    value = (
        "f" * 64 if field.endswith("sha256") else "f" * 40 if field == "base_commit" else "swapped"
    )
    inputs["binding"] = args["binding"].model_copy(update={field: value})
    with pytest.raises(ValueError, match="^invalid coding evidence$"):
        validate_coding_receipt(saved, **inputs)


@pytest.mark.parametrize(
    "field",
    [
        "plan_sha256",
        "forge_report_id",
        "forge_report_sha256",
        "artifact_sha256",
        "patch_sha256",
        "release_authorized",
        "tests_run",
        "objective_status",
        "reason",
        "binding",
        "schema_version",
        "extra",
    ],
)
def test_stored_receipt_rejects_corrupt_receipt_fields(tmp_path, field):
    from agency.factory.coding_verification import validate_coding_receipt

    args = prepared(tmp_path)
    saved = verify_literal_artifact(**args).model_dump(mode="json")
    value = (
        "f" * 64
        if field.endswith("sha256")
        else {
            "forge_report_id": "other-report",
            "release_authorized": 0,
            "tests_run": 0,
            "objective_status": ["passed"],
            "reason": "__proto__",
            "binding": {},
            "schema_version": "a2.v1",
            "extra": True,
        }.get(field)
    )
    saved[field] = value
    with pytest.raises(ValueError, match="^invalid coding evidence$"):
        validate_coding_receipt(saved, **stored_args(args))


@pytest.mark.parametrize(
    "kind",
    [
        "report",
        "plan",
        "artifact",
        "patch",
        "artifact_none",
        "patch_none",
        "artifact_bytes",
        "artifact_huge",
        "artifact_surrogate",
    ],
)
def test_stored_receipt_rejects_swapped_copied_evidence(tmp_path, kind):
    from agency.factory.coding_verification import validate_coding_receipt

    args = prepared(tmp_path)
    saved = verify_literal_artifact(**args)
    inputs = stored_args(args)
    if kind == "report":
        inputs["forge_report"] = args["forge_report"].model_copy(
            update={"note": "different report"}
        )
    elif kind == "plan":
        inputs["edits"] = (CodingEdit(path="m.py", old="1", new="2"),)
    elif kind == "patch":
        inputs["patch"] += "swapped"
    elif kind == "patch_none":
        inputs["patch"] = None
    else:
        inputs["artifact_text"] = {
            "artifact": "x = 2\n# swapped\n",
            "artifact_none": None,
            "artifact_bytes": b"x = 2\n",
            "artifact_huge": "x" * 8193,
            "artifact_surrogate": "\ud800",
        }[kind]
    with pytest.raises(ValueError, match="^invalid coding evidence$"):
        validate_coding_receipt(saved, **inputs)


@pytest.mark.parametrize(
    "kind",
    [
        "non_text",
        "oversized",
        "deep",
        "nodes",
        "surrogate",
        "unknown_object",
        "duplicate_key",
        "missing_default",
        "nan",
    ],
)
def test_stored_corrupt_json_fails_closed(tmp_path, kind):
    from agency.factory.coding_verification import validate_coding_receipt

    args = prepared(tmp_path)
    saved = verify_literal_artifact(**args).model_dump(mode="json")
    malformed = {
        "non_text": b"{}",
        "oversized": " " * 65537,
        "deep": "[" * 40 + "0" + "]" * 40,
        "nodes": [0] * 2049,
        "surrogate": "\ud800",
        "unknown_object": object(),
        "duplicate_key": '{"schema_version":"factory-coding.v1","schema_version":"a2.v1"}',
        "nan": '{"bad":NaN}',
    }.get(kind)
    if kind == "missing_default":
        del saved["release_authorized"]
        malformed = saved
    with pytest.raises(ValueError, match="^invalid coding evidence$"):
        validate_coding_receipt(malformed, **stored_args(args))


def test_complete_stored_json_text_is_accepted_without_reissuing(tmp_path):
    import json

    from agency.factory.coding_verification import validate_coding_receipt

    args = prepared(tmp_path)
    saved = verify_literal_artifact(**args).model_dump(mode="json")
    assert validate_coding_receipt(json.dumps(saved), **stored_args(args)) == saved


@pytest.mark.parametrize("patch_available", [True, False])
def test_unavailable_stored_artifact_requires_exact_null_copy(tmp_path, patch_available):
    from agency.factory.coding_verification import validate_coding_receipt

    args = prepared(tmp_path)
    (args["artifact_root"] / "m.py").unlink()
    if not patch_available:
        args["patch"] = None
    saved = verify_literal_artifact(**args)
    assert saved.artifact_sha256 is None
    inputs = stored_args(args, artifact_text=None)
    assert validate_coding_receipt(saved, **inputs) == saved.model_dump(mode="json")
    inputs["artifact_text"] = "x = 2\n"
    with pytest.raises(ValueError, match="^invalid coding evidence$"):
        validate_coding_receipt(saved, **inputs)


@pytest.mark.parametrize(
    "kind", ["criterion_mismatch", "forge_failed", "inventory_mismatch", "unsafe_representation"]
)
def test_failed_or_unverifiable_stored_receipts_keep_the_original_verdict(tmp_path, kind):
    from agency.factory.coding_verification import validate_coding_receipt

    text = "x = 3\n" if kind == "criterion_mismatch" else "x = 2\n"
    args = prepared(tmp_path, text, gate="FAIL" if kind == "forge_failed" else "PASS")
    copy = text
    if kind == "inventory_mismatch":
        (args["artifact_root"] / "extra").write_bytes(b"extra")
        copy = None
    elif kind == "unsafe_representation":
        (args["artifact_root"] / "m.py").write_bytes(b"\xff")
        copy = None
    saved = verify_literal_artifact(**args)
    assert saved.reason == kind
    assert validate_coding_receipt(
        saved, **stored_args(args, artifact_text=copy)
    ) == saved.model_dump(mode="json")


@pytest.mark.parametrize("spelling", ["relative", "parent_traversal", "nul", "network"])
def test_invalid_root_spellings_are_rejected_before_filesystem_work(
    tmp_path, monkeypatch, spelling
):
    import agency.factory.coding_verification as checker

    args = prepared(tmp_path)
    args["artifact_root"] = {
        "relative": "artifact",
        "parent_traversal": str(tmp_path / "outside" / ".." / "artifact"),
        "nul": str(tmp_path) + "\x00",
        "network": "//server/share/artifact",
    }[spelling]

    def deny(*a, **kw):
        raise AssertionError("invalid root reached filesystem")

    monkeypatch.setattr(checker, "_read_artifact", deny)
    assert_invalid(args)


def test_checking_does_not_spawn_native_forge_git_or_model_work(tmp_path, monkeypatch):
    import subprocess

    import agency.forge.coding_agent as native
    from agency.forge import worktree

    args = prepared(tmp_path)

    def deny(*a, **kw):
        raise AssertionError("pure checker attempted native execution")

    with monkeypatch.context() as guard:
        for module, names in [
            (subprocess, ("run", "Popen", "call", "check_call", "check_output")),
            (native, ("run_coding_agent", "create_worktree", "run_tests")),
            (worktree, ("create_worktree", "remove_worktree")),
        ]:
            for name in names:
                guard.setattr(module, name, deny)
        assert verify_literal_artifact(**args).objective_status == "passed"
