"""Tests for the Butler intelligence document layer.

Covers committed-template loading, private-copy precedence, fail-closed
frontmatter validation, path-safety enforcement (traversal and symlink
escape), the shared frontmatter validator, and factory-splitter delegation.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agency.butler.documents import (
    BASE_DOCUMENTS,
    DocumentError,
    load_base,
    load_on_demand,
)
from agency.core.frontmatter import (
    DocumentSchema,
    FrontmatterError,
    split_frontmatter,
    validate_document,
)
from agency.factory.imports import ImportError as FactoryImportError
from agency.factory.imports import _split_frontmatter

TEMPLATE_ROOT = (
    Path(__file__).resolve().parents[1] / "config" / "intelligence" / "templates" / "butler"
)


def _write_doc(root: Path, name: str, text: str) -> None:
    doc_dir = root / "intelligence" / "butler"
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / f"{name}.md").write_text(text, encoding="utf-8")


def _make_link(link: Path, target: Path) -> None:
    """Create ``link`` pointing at ``target`` via symlink or Windows junction.

    File/directory symlinks need a privilege on Windows; junctions do not,
    and ``Path.resolve()`` follows them identically — so a junction fallback
    keeps the path-safety tests runnable on stock Windows machines.
    """
    try:
        link.symlink_to(target)
        return
    except OSError:
        pass
    if sys.platform != "win32":
        pytest.skip("symlink creation unsupported on this platform")
    try:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    except (ImportError, OSError):
        pytest.skip("symlink and junction creation unsupported on this platform")


# ------------------------------------------------------------------ #
# Template loading and precedence
# ------------------------------------------------------------------ #


def test_valid_templates_load(tmp_path: Path) -> None:
    docs = load_base(private_root=tmp_path)

    assert set(docs) == set(BASE_DOCUMENTS)
    assert docs["SOUL"].strip()
    for name, body in docs.items():
        assert not body.startswith("---"), f"{name} frontmatter was not stripped"
    assert "autonomous agent" in docs["BUTLER_CHARTER"]
    assert "orchestrator" in docs["BUTLER_CHARTER"]


def test_private_override_wins(tmp_path: Path) -> None:
    _write_doc(tmp_path, "SOUL", "---\ntype: SOUL\nschema_version: 1\n---\n\nPrivate persona.\n")

    docs = load_base(private_root=tmp_path, template_root=TEMPLATE_ROOT)

    assert "Private persona." in docs["SOUL"]
    assert "Voice" not in docs["SOUL"]


def test_env_var_private_root_is_honored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_doc(tmp_path, "SOUL", "---\ntype: SOUL\nschema_version: 1\n---\n\nPrivate persona.\n")
    monkeypatch.setenv("AGENCY_PRIVATE_DATA_DIR", str(tmp_path))

    docs = load_base(template_root=TEMPLATE_ROOT)

    assert "Private persona." in docs["SOUL"]


def test_missing_optional_documents_are_empty(tmp_path: Path) -> None:
    empty_template = tmp_path / "templates"
    empty_template.mkdir()
    _write_doc(
        tmp_path / "private", "SOUL", "---\ntype: SOUL\nschema_version: 1\n---\n\nPrivate soul.\n"
    )

    docs = load_base(private_root=tmp_path / "private", template_root=empty_template)

    assert docs["MEMORY"] == ""
    assert docs["USER"] == ""
    assert docs["IDENTITY"] == ""


def test_missing_soul_raises(tmp_path: Path) -> None:
    empty_template = tmp_path / "templates"
    empty_template.mkdir()

    with pytest.raises(DocumentError):
        load_base(private_root=tmp_path / "private", template_root=empty_template)


# ------------------------------------------------------------------ #
# Fail-closed frontmatter validation
# ------------------------------------------------------------------ #


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("---\ntype: NOT_SOUL\nschema_version: 1\n---\n\n# Soul\n", id="bad-enum"),
        pytest.param(
            "---\ntype: SOUL\nschema_version: 2\n---\n\n# Soul\n", id="unknown-schema-version"
        ),
        pytest.param("---\nschema_version: 1\n---\n\n# Soul\n", id="missing-type"),
        pytest.param("---\ntype: SOUL\nschema_version: 1\n", id="unterminated"),
    ],
)
def test_invalid_frontmatter_fails_closed(tmp_path: Path, text: str) -> None:
    _write_doc(tmp_path, "SOUL", text)

    with pytest.raises(DocumentError):
        load_base(private_root=tmp_path, template_root=TEMPLATE_ROOT)


def test_on_demand_loads_validated_document(tmp_path: Path) -> None:
    _write_doc(
        tmp_path, "PLAYBOOKS", "---\ntype: PLAYBOOKS\nschema_version: 1\n---\n\nRun the plays.\n"
    )

    body = load_on_demand("PLAYBOOKS", private_root=tmp_path, template_root=TEMPLATE_ROOT)

    assert "Run the plays." in body


def test_on_demand_invalid_frontmatter_raises(tmp_path: Path) -> None:
    _write_doc(tmp_path, "PLAYBOOKS", "---\ntype: SOMETHING_ELSE\nschema_version: 1\n---\n\nx\n")

    with pytest.raises(DocumentError):
        load_on_demand("PLAYBOOKS", private_root=tmp_path, template_root=TEMPLATE_ROOT)


def test_on_demand_missing_everywhere_is_empty(tmp_path: Path) -> None:
    empty_template = tmp_path / "templates"
    empty_template.mkdir()

    body = load_on_demand(
        "PLAYBOOKS", private_root=tmp_path / "private", template_root=empty_template
    )

    assert body == ""


# ------------------------------------------------------------------ #
# Path safety (AIS spec §20)
# ------------------------------------------------------------------ #


@pytest.mark.parametrize("name", ["../SOUL", "SOUL/../EVIL", "..", "..\\SOUL", ""])
def test_traversal_names_are_rejected(tmp_path: Path, name: str) -> None:
    with pytest.raises(DocumentError):
        load_on_demand(name, private_root=tmp_path, template_root=TEMPLATE_ROOT)


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    private = tmp_path / "data"
    outside = tmp_path / "outside" / "butler"
    outside.mkdir(parents=True)
    (outside / "OUTSIDE.md").write_text(
        "---\ntype: OUTSIDE\nschema_version: 1\n---\n\nEscaped.\n", encoding="utf-8"
    )
    (private / "intelligence").mkdir(parents=True)
    _make_link(private / "intelligence" / "butler", outside)

    with pytest.raises(DocumentError):
        load_on_demand("OUTSIDE", private_root=private)


def test_link_inside_root_still_loads(tmp_path: Path) -> None:
    private = tmp_path
    store = private / "store" / "butler"
    store.mkdir(parents=True)
    (store / "LINK.md").write_text(
        "---\ntype: LINK\nschema_version: 1\n---\n\nInside.\n", encoding="utf-8"
    )
    _make_link(private / "intelligence", private / "store")

    body = load_on_demand("LINK", private_root=private, template_root=TEMPLATE_ROOT)

    assert "Inside." in body


# ------------------------------------------------------------------ #
# Shared frontmatter primitives
# ------------------------------------------------------------------ #


def test_split_frontmatter_passthrough_and_nonmapping() -> None:
    plain = "# No frontmatter\n"
    assert split_frontmatter(plain) == ({}, plain)
    with pytest.raises(FrontmatterError):
        split_frontmatter("---\n- a\n- b\n---\nbody\n")


def test_validate_document_namespaces_unknown_keys_and_parses_timestamps() -> None:
    schema = DocumentSchema(
        required_fields=("type", "schema_version"),
        optional_enum_fields={"type": ("SOUL",)},
        known_versions=(1,),
        timestamp_fields=("updated_at",),
    )

    validated = validate_document(
        {
            "type": "SOUL",
            "schema_version": 1,
            "updated_at": "2026-11-22T10:00:00+02:00",
            "note": "owner-supplied",
        },
        schema,
    )

    assert validated["type"] == "SOUL"
    assert validated["updated_at"] == datetime(2026, 11, 22, 8, 0, tzinfo=UTC)
    assert validated["untrusted"] == {"note": "owner-supplied"}


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param({"type": "SOUL", "schema_version": "1"}, id="string-version"),
        pytest.param({"type": "SOUL", "schema_version": True}, id="bool-version"),
        pytest.param({"type": "SOUL", "schema_version": 2}, id="unknown-version"),
        pytest.param(
            {"type": "SOUL", "schema_version": 1, "updated_at": "2026-11-22T10:00:00"},
            id="naive-timestamp",
        ),
        pytest.param({"schema_version": 1}, id="missing-required"),
        pytest.param({1: "SOUL", "schema_version": 1}, id="non-string-key"),
    ],
)
def test_validate_document_rejects_invalid_input(raw: dict) -> None:
    schema = DocumentSchema(
        required_fields=("type", "schema_version"),
        optional_enum_fields={"type": ("SOUL",)},
        known_versions=(1,),
        timestamp_fields=("updated_at",),
    )

    with pytest.raises(FrontmatterError):
        validate_document(raw, schema)


# ------------------------------------------------------------------ #
# Factory splitter delegation
# ------------------------------------------------------------------ #


def test_factory_splitter_delegates_to_core() -> None:
    frontmatter, body = _split_frontmatter("---\nname: Demo\n---\n\nBody.")

    assert frontmatter == {"name": "Demo"}
    assert body == "\nBody."
    with pytest.raises(FactoryImportError):
        _split_frontmatter("---\nname: Demo\n")
