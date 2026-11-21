"""Butler intelligence document loader (AIS spec §13.1, §20).

Loads the Butler's persona/intelligence Markdown documents with a fixed
resolution order: the owner's private runtime copy under
``<AGENCY_PRIVATE_DATA_DIR>/intelligence/butler/<NAME>.md`` first, then the
committed default template under ``config/intelligence/templates/butler/``.

Documents are inert data: bodies are never executed and frontmatter is
treated as untrusted (split and schema-validated through
:mod:`agency.core.frontmatter`, failing closed on invalid headers).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import structlog

from agency.core.frontmatter import (
    DocumentSchema,
    FrontmatterError,
    split_frontmatter,
    validate_document,
)

log = structlog.get_logger(__name__)

BASE_DOCUMENTS = ("SOUL", "IDENTITY", "USER", "MEMORY", "BUTLER_CHARTER")
PERSONA_CONTRACT_DOC = "SOUL"

SCHEMA_VERSION = 1

_PRIVATE_SUBDIR = Path("intelligence") / "butler"
_TEMPLATE_SUBDIR = Path("config") / "intelligence" / "templates" / "butler"

# Document names are uppercase identifiers: this alone rejects path
# separators and dot segments before any filesystem access.
_NAME_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


class DocumentError(Exception):
    """A Butler intelligence document is missing, unsafe, or invalid."""


def _document_schema(name: str) -> DocumentSchema:
    """Schema for one document: ``type`` must name the document itself."""
    return DocumentSchema(
        required_fields=("type", "schema_version"),
        optional_enum_fields={"type": (name,)},
        known_versions=(SCHEMA_VERSION,),
    )


def _resolve_roots(
    private_root: str | Path | None, template_root: str | Path | None
) -> tuple[Path, Path]:
    if private_root is not None:
        private = Path(private_root)
    else:
        private = Path(os.environ.get("AGENCY_PRIVATE_DATA_DIR", "data"))
    if template_root is not None:
        template = Path(template_root)
    else:
        template = Path(__file__).resolve().parents[3] / _TEMPLATE_SUBDIR
    return private, template


def _safe_private_path(root: Path, name: str) -> Path:
    """Resolve the private document path and guarantee it stays under ``root``.

    Path safety (AIS spec §20): ``..`` traversal and symlink components
    escaping the configured root are rejected — ``Path.resolve()``
    canonicalizes both, so the prefix check below fails closed on either.
    """
    if not _NAME_RE.fullmatch(name):
        raise DocumentError(f"invalid document name {name!r}")
    resolved_root = root.resolve()
    candidate = (resolved_root / _PRIVATE_SUBDIR / f"{name}.md").resolve()
    if resolved_root != candidate and resolved_root not in candidate.parents:
        raise DocumentError(f"document path for {name!r} escapes the private data root")
    return candidate


def _read_document(name: str, *, private_root: Path, template_root: Path) -> tuple[str, str] | None:
    """Return ``(body, source)`` for one document, or ``None`` if absent.

    The private copy wins over the committed template; frontmatter is
    stripped from the body and validated against the document schema.
    """
    private_path = _safe_private_path(private_root, name)
    path = private_path
    source = "private"
    if not path.is_file():
        log.debug("butler.document.private_missing", name=name)
        path = template_root / f"{name}.md"
        source = "template"
        if not path.is_file():
            log.warning("butler.document.missing", name=name)
            return None
    try:
        frontmatter, body = split_frontmatter(path.read_text(encoding="utf-8"))
        validate_document(frontmatter, _document_schema(name))
    except FrontmatterError as exc:
        log.warning("butler.document.invalid_frontmatter", name=name, error=str(exc))
        raise DocumentError(f"invalid frontmatter for {name}.md: {exc}") from exc
    log.info("butler.document.loaded", name=name, source=source)
    return body, source


def load_base(
    *, private_root: str | Path | None = None, template_root: str | Path | None = None
) -> dict[str, str]:
    """Load the base document set (AIS spec §13.1) as name -> body.

    The base set is SOUL.md, IDENTITY.md, USER.md, MEMORY.md and
    BUTLER_CHARTER.md; bodies have frontmatter stripped and private runtime
    copies win over committed templates. Missing optional documents yield
    ``""``; a missing SOUL.md — the persona contract — raises
    :class:`DocumentError`, as does any document with invalid frontmatter.
    """
    private, template = _resolve_roots(private_root, template_root)
    documents: dict[str, str] = {}
    for name in BASE_DOCUMENTS:
        loaded = _read_document(name, private_root=private, template_root=template)
        if loaded is None:
            if name == PERSONA_CONTRACT_DOC:
                raise DocumentError(
                    "SOUL.md (the persona contract) is missing from both the "
                    "private data root and the committed templates"
                )
            documents[name] = ""
            continue
        documents[name] = loaded[0]
    return documents


def load_on_demand(
    name: str, *, private_root: str | Path | None = None, template_root: str | Path | None = None
) -> str:
    """Load one additional validated document by name; ``""`` when absent.

    ``name`` is the document stem (e.g. ``"PLAYBOOKS"`` for PLAYBOOKS.md)
    and must be a plain uppercase identifier; traversal or symlink-escape
    attempts raise :class:`DocumentError`.
    """
    private, template = _resolve_roots(private_root, template_root)
    loaded = _read_document(name, private_root=private, template_root=template)
    return loaded[0] if loaded is not None else ""


__all__ = ["BASE_DOCUMENTS", "PERSONA_CONTRACT_DOC", "DocumentError", "load_base", "load_on_demand"]
