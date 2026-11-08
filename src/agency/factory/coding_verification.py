"""Non-executing fixed checker and copied-evidence validation.

The native adapter must establish the admitted Git base, live factory identity,
approval provenance, exclusive claim and history. CodingAgentReport has no such
fields. This module neither invents those facts nor invokes Forge/Git/providers.
Fresh verification reads files; stored receipt validation must not do so.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Literal, cast

from agency.factory.coding_models import (
    CodingBinding,
    CodingEdit,
    CodingReceipt,
    _text,
    canonical_sha256,
    coding_branch,
    literal_patch,
)
from agency.forge.coding_agent import CodingAgentReport

_ObjectiveStatus = Literal["passed", "failed", "unable_to_verify"]
_ReceiptReason = Literal[
    "matched",
    "criterion_mismatch",
    "inventory_mismatch",
    "artifact_unavailable",
    "unsafe_representation",
    "forge_failed",
    "binding_mismatch",
]
_ArtifactFailure = Literal["unsafe_representation", "inventory_mismatch", "artifact_unavailable"]


def criteria_document() -> dict[str, object]:
    """A fresh copy of the only admitted criterion, never caller-selected."""
    return {"schema": "literal-assignment.v1", "path": "m.py", "name": "x", "value": 2}


def checker_source_sha256() -> str:
    """Hash this trusted module's bytes, not a checker in the candidate tree."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _matches_literal(text: str) -> bool:
    try:
        module = ast.parse(text, filename="m.py", mode="exec")
    except (SyntaxError, ValueError, RecursionError):
        return False
    if len(module.body) != 1:
        return False
    statement = module.body[0]
    return (
        isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
        and statement.targets[0].id == "x"
        and isinstance(statement.value, ast.Constant)
        and type(statement.value.value) is int
        and statement.value.value == 2
    )


def _digest_text(text: str | None) -> str | None:
    return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()


def _receipt(
    binding: CodingBinding,
    edits: tuple[CodingEdit, ...],
    report: CodingAgentReport,
    text: str | None,
    patch: str | None,
    status: _ObjectiveStatus,
    reason: _ReceiptReason,
) -> CodingReceipt:
    return CodingReceipt(
        binding=binding,
        plan_sha256=canonical_sha256([edit.model_dump(mode="json") for edit in edits]),
        forge_report_id=report.report_id,
        forge_report_sha256=canonical_sha256(report.model_dump(mode="json")),
        artifact_sha256=_digest_text(text),
        patch_sha256=_digest_text(patch),
        objective_status=status,
        reason=reason,
        release_authorized=False,
        tests_run=False,
    )


def _root_path(value: str | Path) -> Path:
    """Lexical validation only; intentionally safe to reuse during stored replay."""
    if type(value) is not str and not isinstance(value, Path):
        raise ValueError("invalid coding evidence")
    path = Path(value)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or "\x00" in str(path)
        or str(path).startswith(("\\\\", "//"))
    ):
        raise ValueError("invalid coding evidence")
    return path


def _root_key(value: str | Path) -> str:
    return os.path.normcase(str(_root_path(value)))


def _inputs(
    binding: CodingBinding,
    edits: tuple[CodingEdit, ...],
    report: CodingAgentReport,
    patch: str | None,
) -> tuple[CodingBinding, tuple[CodingEdit, ...], CodingAgentReport, str]:
    """Validate copied native types and plan relationships, without any IO."""
    try:
        if type(binding) is not CodingBinding or type(report) is not CodingAgentReport:
            raise ValueError
        binding = CodingBinding.model_validate(binding)
        if type(edits) is not tuple or len(edits) != 1 or type(edits[0]) is not CodingEdit:
            raise ValueError
        edits = (CodingEdit.model_validate(edits[0]),)
        report = CodingAgentReport.model_validate(report.model_dump(mode="python"), strict=True)
        canonical_sha256(report.model_dump(mode="json"))
        _text(report.report_id, maximum=512)
        if not report.report_id.strip() or len(report.report_id) > 128 or report.edits_applied > 1:
            raise ValueError
        if (
            report.branch != coding_branch(binding)
            or report.tests is not None
            or "tests_not_run" not in report.caveats
            or (report.gate == "PASS" and report.edits_applied != 1)
        ):
            raise ValueError
        root = _root_key(report.worktree_path)
        if root != _root_key(report.inspection.root) or root != _root_key(report.security.root):
            raise ValueError
        edit = edits[0]
        baseline = "x = 1\n"
        if baseline.count(edit.old) != 1:
            raise ValueError
        planned_text = baseline.replace(edit.old, edit.new, 1)
        _text(planned_text, maximum=8192)
        if patch is not None:
            _text(patch, maximum=32768)
            if patch != literal_patch(planned_text):
                raise ValueError
    except (ValueError, TypeError, UnicodeError, AttributeError, RecursionError):
        raise ValueError("invalid coding evidence") from None
    return binding, edits, report, planned_text


def _bind_artifact(
    text: str, planned_text: str, patch: str | None, report: CodingAgentReport
) -> None:
    """Bind readable artifact bytes to the plan, diff and native copied inventory."""
    try:
        _text(text, maximum=8192)
        if text != planned_text or patch is None or patch != literal_patch(text):
            raise ValueError
        if report.inspection.gate == "PASS":
            records = report.inspection.files
            if (
                len(records) != 1
                or records[0].path != "m.py"
                or records[0].sha256 != _digest_text(text)
                or records[0].size_bytes != len(text.encode("utf-8"))
            ):
                raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("invalid coding evidence") from None


def _forge_passed(report: CodingAgentReport) -> bool:
    return (
        report.gate == "PASS"
        and report.inspection.gate == "PASS"
        and report.security.gate == "PASS"
    )


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _inventory(root: Path) -> _ArtifactFailure | None:
    """Bounded flat inventory, rejecting ancestor redirects before following paths."""
    for directory in (*reversed(root.parents), root):
        info = os.lstat(directory)
        if _is_link(info) or not stat.S_ISDIR(info.st_mode):
            return "unsafe_representation"
    names = set()
    with os.scandir(root) as entries:
        for entry in entries:
            names.add(entry.name)
            if entry.name not in ("m.py", ".git") or len(names) > 2:
                return "inventory_mismatch"
    if "m.py" not in names:
        return "artifact_unavailable"
    if ".git" not in names:
        return "inventory_mismatch"
    for name in names:
        info = os.lstat(root / name)
        if _is_link(info) or not stat.S_ISREG(info.st_mode):
            return "unsafe_representation"
    return None


def _read_artifact(root: Path) -> tuple[str | None, _ArtifactFailure | None]:
    """Read at most 8192 bytes; pre/post handle metadata checks enforce the cap.

    Handle identity and post-read metadata checks detect ordinary concurrent file
    replacement; they are not a hostile-kernel or concurrent-writer sandbox.
    """
    try:
        reason = _inventory(root)
        if reason is not None:
            return None, reason
        path = root / "m.py"
        before = os.lstat(path)
        if before.st_size > 8192:
            return None, "unsafe_representation"
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if (
                _is_link(opened)
                or not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or opened.st_size > 8192
            ):
                return None, "unsafe_representation"
            data = handle.read(8192)
            after = os.fstat(handle.fileno())
        if (
            len(data) > 8192
            or len(data) != before.st_size
            or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
        ):
            return None, "unsafe_representation"
        reason = _inventory(root)
        final = os.lstat(path)
        if reason is not None:
            return None, reason
        if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size) != (
            final.st_dev,
            final.st_ino,
            final.st_mtime_ns,
            final.st_size,
        ):
            return None, "unsafe_representation"
        text = data.decode("utf-8")
        _text(text, maximum=8192)
        return text, None
    except FileNotFoundError:
        return None, "artifact_unavailable"
    except (OSError, ValueError, UnicodeError):
        return None, "unsafe_representation"


def verify_literal_artifact(
    *,
    binding: CodingBinding,
    edits: tuple[CodingEdit, ...],
    forge_report: CodingAgentReport,
    artifact_root: str | Path,
    patch: str | None,
) -> CodingReceipt:
    """Read and parse fixed literal data; never import or execute generated code."""
    binding, edits, forge_report, planned_text = _inputs(binding, edits, forge_report, patch)
    if (
        binding.checker_sha256 != checker_source_sha256()
        or binding.criteria_sha256 != canonical_sha256(criteria_document())
        or _root_key(artifact_root) != _root_key(forge_report.worktree_path)
    ):
        raise ValueError("invalid coding evidence")
    root = _root_path(artifact_root)
    text, reason = _read_artifact(root)
    if reason is not None:
        status: _ObjectiveStatus = (
            "failed" if reason == "inventory_mismatch" else "unable_to_verify"
        )
        return _receipt(binding, edits, forge_report, None, patch, status, reason)
    if root.resolve() != _root_path(forge_report.worktree_path).resolve():
        raise ValueError("invalid coding evidence")
    # _read_artifact yields a str text exactly when reason is None.
    artifact_text = cast("str", text)
    _bind_artifact(artifact_text, planned_text, patch, forge_report)
    if not _forge_passed(forge_report):
        return _receipt(
            binding, edits, forge_report, artifact_text, patch, "failed", "forge_failed"
        )
    passed = _matches_literal(artifact_text)
    outcome: _ReceiptReason = "matched" if passed else "criterion_mismatch"
    return _receipt(
        binding,
        edits,
        forge_report,
        artifact_text,
        patch,
        "passed" if passed else "failed",
        outcome,
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid coding evidence")
        result[key] = value
    return result


def _stored_receipt(value: object) -> CodingReceipt:
    if type(value) is CodingReceipt:
        # Revalidate before dumping: model_copy/model_construct bypass normal checks.
        value = CodingReceipt.model_validate(value).model_dump(mode="json")
    elif type(value) is str:
        _text(value, maximum=65536)
        value = json.loads(value, object_pairs_hook=_unique_object)
    if type(value) is not dict:
        raise ValueError("invalid coding evidence")
    original_digest = canonical_sha256(value)
    receipt = CodingReceipt.model_validate(value)
    # Persisted snapshots are complete, not partially filled defaults or coerced JSON.
    if canonical_sha256(receipt.model_dump(mode="json")) != original_digest:
        raise ValueError("invalid coding evidence")
    return receipt


def validate_coding_receipt(
    receipt: object,
    *,
    binding: CodingBinding,
    edits: tuple[CodingEdit, ...],
    forge_report: CodingAgentReport,
    artifact_text: str | None,
    patch: str | None,
) -> dict[str, object]:
    """Validate copied stored evidence with NO IO, current checker or AST evaluation.

    Return the identical complete receipt as plain JSON, not a regenerated verdict.
    This is integrity/binding validation, not cryptographic attestation against a
    malicious writer who can rewrite every stored input and digest.
    """
    try:
        saved = _stored_receipt(receipt)
        binding, edits, forge_report, planned_text = _inputs(binding, edits, forge_report, patch)
        if (
            saved.binding != binding
            or saved.plan_sha256 != canonical_sha256([e.model_dump(mode="json") for e in edits])
            or saved.forge_report_id != forge_report.report_id
            or saved.forge_report_sha256 != canonical_sha256(forge_report.model_dump(mode="json"))
            or (artifact_text is None) != (saved.artifact_sha256 is None)
            or (patch is None) != (saved.patch_sha256 is None)
        ):
            raise ValueError
        if artifact_text is not None:
            _text(artifact_text, maximum=8192)
            _bind_artifact(artifact_text, planned_text, patch, forge_report)
        if saved.artifact_sha256 != _digest_text(
            artifact_text
        ) or saved.patch_sha256 != _digest_text(patch):
            raise ValueError
        if saved.reason in ("matched", "criterion_mismatch", "forge_failed"):
            if artifact_text is None:
                raise ValueError
            expected_forge_pass = saved.reason != "forge_failed"
            if _forge_passed(forge_report) != expected_forge_pass:
                raise ValueError
        elif saved.reason in (
            "inventory_mismatch",
            "artifact_unavailable",
            "unsafe_representation",
        ):
            if artifact_text is not None:
                raise ValueError
        else:
            # Invalid bindings raise; fresh checking never issues a trusted binding_mismatch receipt.
            raise ValueError
        return saved.model_dump(mode="json")
    except (ValueError, TypeError, UnicodeError, AttributeError, RecursionError):
        raise ValueError("invalid coding evidence") from None
