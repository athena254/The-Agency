"""Manifest-based import of Python tools through the Forge gates.

Phase 4a of PLAN_TOOL_PROVISIONING §3.6 + owner decisions §8. A pinned
manifest (JSON) describes a Python tool package: identity, provenance
(source URL + commit SHA + content hash), the tool entries it provides, and
declared network egress. :func:`import_tool` runs the fail-closed pipeline:

1. ``fetch`` — a caller-supplied adapter ``(source_url, commit_sha) -> files``
   returns the exact package bytes (``tool.py``, ``test_tool.py``,
   ``manifest.json``). The default is an honest ``ValueError``: no network
   code lives in this module, so callers provide a thin wrapper (e.g. a git
   archive of the pinned SHA) and tests inject a fake. Quarantine is
   structural — nothing is written to a live location before the gates pass.
2. Content-hash verification — sha256 over the sorted ``name: NUL: bytes``
   pairs of the fetched code files plus the canonical manifest (with the
   hash field itself emptied, since it cannot cover itself). Descriptions
   participate: swapping vetted tool text later breaks the pin (anti
   rug-pull) and forces a full re-vet.
3. Forge gates — the same :func:`agency.autonomy.forge_auto.build_tool`
   verification pass used for internally built tools (inspection, security
   scan, pytest in the isolated subprocess copy; no registry, so the code is
   never resolved/executed in this step).
4. Reviewer — a :class:`~agency.tools.reviewer.ToolReviewer` verdict
   (NVIDIA SkillSpector per owner decision §8.2). ``UNAVAILABLE`` keeps the
   import quarantined and retriable; ``FAIL`` keeps it quarantined with the
   findings; only ``PASS`` continues.
5. Registration — ``build_tool`` again with the registry (idempotent), then
   provenance metadata attached to the registered tool object as
   ``import_provenance`` (or carried in the result detail when the object
   forbids attribute assignment; no registry APIs are invented here).

Re-vetting is structural: there is no cache or shortcut — every import
re-runs the whole flow, so a new version with a different content hash is
vetted exactly like a first import (postmark-mcp lesson).

Trust boundary (same as ``forge_auto``): fetched code is executed in-process
exactly once, only after gates and review pass, to resolve the ``Tool`` for
registration. Never import untrusted manifests without a reviewer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from agency.autonomy.forge_auto import ToolBuildResult, build_tool
from agency.tools.reviewer import ToolReviewer, ToolReviewVerdict

MANIFEST_FILENAME = "manifest.json"
TOOL_FILENAME = "tool.py"
TEST_FILENAME = "test_tool.py"
REQUIRED_FILES = (MANIFEST_FILENAME, TEST_FILENAME, TOOL_FILENAME)

MAX_MANIFEST_BYTES = 64 * 1024
MAX_DESCRIPTION_CHARS = 1024
MAX_NAME_CHARS = 64
MAX_TOOLS = 8
MAX_TESTS_COMMAND_CHARS = 256
MAX_SOURCE_CHARS = 2048
DEFAULT_TESTS_TIMEOUT_S = 120

_INJECTION_SUBSTRINGS = ("system prompt", "ignore previous")
_MANIFEST_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
# Entry names become ToolSpec names at registration, so they must satisfy the
# stricter registry regex; a hyphenated entry would crash mid-gate otherwise.
_ENTRY_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_CONTENT_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SOURCE_RE = re.compile(r"^https?://\S+$")
_DOMAIN_RE = re.compile(r"^[a-z0-9*][a-z0-9*.-]*$")
# The publisher cannot know the hash before computing it; verification
# empties this field before hashing the canonical manifest.
_PLACEHOLDER_HASH = "sha256:" + "0" * 64


def _check_description_text(value: str, field: str) -> str:
    """Nonblank, bounded, and free of markup / prompt-instruction text."""
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string.")
    if not value.strip():
        raise ValueError(f"{field} must be nonblank.")
    if len(value) > MAX_DESCRIPTION_CHARS:
        raise ValueError(f"{field} exceeds {MAX_DESCRIPTION_CHARS} characters.")
    if "<" in value or ">" in value:
        raise ValueError(f"{field} must not contain markup characters '<' or '>'.")
    lowered = value.casefold()
    for needle in _INJECTION_SUBSTRINGS:
        if needle in lowered:
            raise ValueError(f"{field} must not contain the injection substring {needle!r}.")
    return value


class ManifestProvenance(BaseModel):
    """Where the package came from and the hash pinning its exact bytes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    commit_sha: str
    content_hash: str

    @field_validator("source")
    @classmethod
    def _source(cls, value: str) -> str:
        if not isinstance(value, str) or _SOURCE_RE.match(value) is None:
            raise ValueError("provenance.source must be an http(s) URL.")
        if len(value) > MAX_SOURCE_CHARS:
            raise ValueError(f"provenance.source exceeds {MAX_SOURCE_CHARS} characters.")
        return value

    @field_validator("commit_sha")
    @classmethod
    def _commit_sha(cls, value: str) -> str:
        if not isinstance(value, str) or _COMMIT_RE.match(value) is None:
            raise ValueError("provenance.commit_sha must be 40 lowercase hex chars.")
        return value

    @field_validator("content_hash")
    @classmethod
    def _content_hash(cls, value: str) -> str:
        if not isinstance(value, str) or _CONTENT_HASH_RE.match(value) is None:
            raise ValueError("provenance.content_hash must be 'sha256:<64 hex chars>'.")
        return value


class ManifestNetwork(BaseModel):
    """Declared network egress; enforcement is the runtime's job, not ours."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed_domains: tuple[str, ...] = ()

    @field_validator("allowed_domains", mode="before")
    @classmethod
    def _domains(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str) or not isinstance(value, (list, tuple)):
            raise TypeError("network.allowed_domains must be a list of hostnames.")
        out: list[str] = []
        for item in value:
            if not isinstance(item, str) or _DOMAIN_RE.match(item) is None or len(item) > 253:
                raise ValueError(
                    "network.allowed_domains entries must be bare lowercase hostnames "
                    "(optionally '*.'-prefixed)."
                )
            out.append(item)
        return tuple(out)


class ToolManifestEntry(BaseModel):
    """One tool provided by the package; entry names are registry-safe."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    input_schema: dict[str, Any]
    risk: Literal["read", "write", "dangerous"]

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not isinstance(value, str) or _ENTRY_NAME_RE.match(value) is None:
            raise ValueError(f"tool entry name {value!r} must match ^[a-z][a-z0-9_]*$.")
        if len(value) > MAX_NAME_CHARS:
            raise ValueError(f"tool entry name exceeds {MAX_NAME_CHARS} characters.")
        return value

    @field_validator("description")
    @classmethod
    def _description(cls, value: str) -> str:
        return _check_description_text(value, "tool entry description")


class ToolManifest(BaseModel):
    """Frozen import manifest; every field is validated fail-closed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    version: str
    description: str
    provenance: ManifestProvenance
    tools: tuple[ToolManifestEntry, ...]
    network: ManifestNetwork = ManifestNetwork()
    tests_command: str = "pytest"

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not isinstance(value, str) or _MANIFEST_NAME_RE.match(value) is None:
            raise ValueError(f"manifest name {value!r} must match ^[a-z][a-z0-9_-]*$.")
        if len(value) > MAX_NAME_CHARS:
            raise ValueError(f"manifest name exceeds {MAX_NAME_CHARS} characters.")
        return value

    @field_validator("version")
    @classmethod
    def _version(cls, value: str) -> str:
        if not isinstance(value, str) or _VERSION_RE.match(value) is None:
            raise ValueError(f"version {value!r} must be semver core X.Y.Z.")
        return value

    @field_validator("description")
    @classmethod
    def _description(cls, value: str) -> str:
        return _check_description_text(value, "description")

    @field_validator("tests_command")
    @classmethod
    def _tests_command(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("tests_command must be a nonblank string.")
        if len(value) > MAX_TESTS_COMMAND_CHARS:
            raise ValueError(f"tests_command exceeds {MAX_TESTS_COMMAND_CHARS} characters.")
        if "\n" in value or "\r" in value or "\x00" in value:
            raise ValueError("tests_command must be a single command line.")
        return value

    @model_validator(mode="after")
    def _tools_bounds(self) -> ToolManifest:
        if not 1 <= len(self.tools) <= MAX_TOOLS:
            raise ValueError(f"manifest must declare between 1 and {MAX_TOOLS} tools.")
        return self


def parse_manifest(data: dict[str, Any]) -> ToolManifest:
    """Validate a manifest dict fail-closed (unknown fields rejected)."""
    if not isinstance(data, dict):
        raise TypeError("manifest must be a JSON object.")
    return ToolManifest.model_validate(data)


def load_manifest(path: str | Path) -> ToolManifest:
    """Read and validate a manifest from disk; missing file raises FileNotFoundError."""
    manifest_path = Path(path)
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError(f"manifest exceeds {MAX_MANIFEST_BYTES} bytes (fail closed).")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"manifest {manifest_path} is not valid JSON: {exc}") from None
    return parse_manifest(data)


def _checked_files(files: Mapping[str, bytes]) -> dict[str, bytes]:
    if not isinstance(files, Mapping):
        raise TypeError("fetched files must be a mapping of filename -> bytes.")
    out: dict[str, bytes] = {}
    for name, data in files.items():
        if not isinstance(name, str):
            raise TypeError("fetched file names must be strings.")
        if isinstance(data, bytearray):
            data = bytes(data)
        if not isinstance(data, bytes):
            raise TypeError(f"fetched file {name!r} must be bytes, not {type(data).__name__}.")
        out[name] = data
    return out


def _hash_manifest(manifest: ToolManifest, files: Mapping[str, bytes]) -> str:
    """Canonical sha256 over sorted file pairs + the hash-emptied manifest.

    The ``manifest.json`` entry of ``files`` is excluded (self-referential);
    instead the parsed manifest itself — descriptions included, content_hash
    emptied — is serialized canonically and hashed alongside the code files.
    """
    payload: dict[str, bytes] = {
        name: data for name, data in _checked_files(files).items() if name != MANIFEST_FILENAME
    }
    dump = manifest.model_dump(mode="json")
    dump["provenance"]["content_hash"] = ""
    payload[MANIFEST_FILENAME] = json.dumps(
        dump, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    digest = hashlib.sha256()
    for name in sorted(payload):
        digest.update(name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(payload[name])
        digest.update(b"\x00")
    return f"sha256:{digest.hexdigest()}"


def compute_content_hash(data: Mapping[str, Any], files: Mapping[str, bytes]) -> str:
    """Compute the ``provenance.content_hash`` for a manifest dict + files.

    Normalizes the dict through :func:`parse_manifest` first (so defaults are
    pinned identically on both sides), with the hash field itself zeroed.
    """
    if not isinstance(data, Mapping):
        raise TypeError("manifest data must be a mapping.")
    normalized = dict(data)
    provenance = dict(normalized.get("provenance") or {})
    provenance["content_hash"] = _PLACEHOLDER_HASH
    normalized["provenance"] = provenance
    return _hash_manifest(parse_manifest(normalized), files)


def verify_content_hash(manifest: ToolManifest, files: Mapping[str, bytes]) -> bool:
    """Recompute the pin over fetched bytes; descriptions participate."""
    computed = _hash_manifest(manifest, files)
    return hmac.compare_digest(computed, manifest.provenance.content_hash)


ImportState = Literal[
    "REGISTERED",
    "GATE_FAILED",
    "REVIEW_FAILED",
    "REVIEWER_UNAVAILABLE",
    "HASH_MISMATCH",
]


class ToolImportResult(BaseModel):
    """Immutable outcome of one :func:`import_tool` run."""

    model_config = ConfigDict(frozen=True)

    name: str
    state: ImportState
    report_ids: tuple[str, ...] = ()
    review: ToolReviewVerdict | None = None
    detail: str = ""


def _check_files_shape(files: Mapping[str, bytes]) -> None:
    names = set(files)
    missing = [name for name in REQUIRED_FILES if name not in names]
    if missing:
        raise ValueError(
            f"fetched files must include exactly {sorted(REQUIRED_FILES)}; missing: {sorted(missing)}"
        )
    extra = sorted(names - set(REQUIRED_FILES))
    if extra:
        raise ValueError(f"fetched package has unexpected files: {extra}")


def _attach_provenance(tool: Any, payload: dict[str, Any]) -> bool:
    try:
        tool.import_provenance = payload
    except (AttributeError, TypeError):
        return False
    return True


def import_tool(
    manifest_path: str | Path,
    *,
    reviewer: ToolReviewer,
    registry: Any = None,
    fetch: Callable[[str, str], Mapping[str, bytes]] | None = None,
    tests_timeout_s: int = DEFAULT_TESTS_TIMEOUT_S,
) -> ToolImportResult:
    """Import a pinned Python tool package through the Forge gates + review.

    ``fetch`` is required and injectable: a callable
    ``(source_url, commit_sha) -> {filename: bytes}`` returning exactly
    ``tool.py``, ``test_tool.py`` and ``manifest.json``. Passing ``None``
    raises immediately — this module ships no network code (honest v1).

    ``reviewer`` must implement the :class:`ToolReviewer` protocol; a
    ``UNAVAILABLE`` verdict quarantines the import (retriable), ``FAIL``
    quarantines it with findings, and only ``PASS`` reaches registration.

    ``registry`` (optional) receives the tool via the same idempotent
    ``build_tool`` path used for Forge-built tools; provenance metadata is
    attached as ``import_provenance`` on each registered tool object, or
    carried in the result detail when attachment is impossible.
    """
    if fetch is None:
        raise ValueError(
            "network fetch adapter required: pass fetch=(source_url, commit_sha) -> files; "
            "importing.py ships no network code (fail closed by design)."
        )
    if (
        reviewer is None
        or not callable(getattr(reviewer, "review", None))
        or not callable(getattr(reviewer, "is_available", None))
    ):
        raise TypeError(
            "reviewer must implement the ToolReviewer protocol (is_available + review)."
        )

    manifest = load_manifest(manifest_path)
    files = _checked_files(fetch(manifest.provenance.source, manifest.provenance.commit_sha))
    _check_files_shape(files)

    if not verify_content_hash(manifest, files):
        return ToolImportResult(
            name=manifest.name,
            state="HASH_MISMATCH",
            detail=(
                f"content hash mismatch: pinned {manifest.provenance.content_hash} does not "
                f"match the fetched bytes (computed {_hash_manifest(manifest, files)}); "
                "import stays quarantined"
            ),
        )
    try:
        fetched = parse_manifest(json.loads(files[MANIFEST_FILENAME].decode("utf-8")))
    except (TypeError, ValueError) as exc:
        return ToolImportResult(
            name=manifest.name,
            state="HASH_MISMATCH",
            detail=f"fetched {MANIFEST_FILENAME} is not a valid manifest: {exc}",
        )
    if fetched != manifest:
        return ToolImportResult(
            name=manifest.name,
            state="HASH_MISMATCH",
            detail="fetched manifest.json disagrees with the manifest supplied to import_tool",
        )

    try:
        tool_code = files[TOOL_FILENAME].decode("utf-8")
        test_code = files[TEST_FILENAME].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("fetched tool.py/test_tool.py must be UTF-8 text.") from exc

    # Forge gates, verification pass: never resolves/executes the code.
    gate_results: list[ToolBuildResult] = [
        build_tool(entry.name, tool_code, test_code, None, timeout_s=tests_timeout_s)
        for entry in manifest.tools
    ]
    report_ids = tuple(r.report_id for r in gate_results if r.report_id)
    failed = [r for r in gate_results if r.gate != "PASS"]
    if failed:
        return ToolImportResult(
            name=manifest.name,
            state="GATE_FAILED",
            report_ids=report_ids,
            detail=(
                "forge gate failed for "
                + ", ".join(f"{r.name} ({r.gate})" for r in failed)
                + "; import stays quarantined"
            ),
        )

    # Reviewer verdict on the quarantined tree (files written read-only-ish
    # into a private temp dir; nothing live has been touched yet).
    with tempfile.TemporaryDirectory(prefix="tool-import-") as tmp:
        root = Path(tmp)
        (root / TOOL_FILENAME).write_bytes(files[TOOL_FILENAME])
        (root / TEST_FILENAME).write_bytes(files[TEST_FILENAME])
        (root / MANIFEST_FILENAME).write_bytes(files[MANIFEST_FILENAME])
        verdict = reviewer.review(root)
    if verdict.verdict == "UNAVAILABLE":
        return ToolImportResult(
            name=manifest.name,
            state="REVIEWER_UNAVAILABLE",
            report_ids=report_ids,
            review=verdict,
            detail=(
                f"reviewer unavailable: {verdict.findings_summary}; "
                "import stays quarantined (retriable)"
            ),
        )
    if verdict.verdict != "PASS":
        return ToolImportResult(
            name=manifest.name,
            state="REVIEW_FAILED",
            report_ids=report_ids,
            review=verdict,
            detail=f"reviewer rejected the package: {verdict.findings_summary}",
        )

    review_summary = {
        "scanner": verdict.scanner,
        "score": verdict.score,
        "verdict": verdict.verdict,
        "findings_summary": verdict.findings_summary,
    }
    report_ids_list = list(report_ids)
    registered: list[str] = []
    unattached: list[str] = []
    if registry is not None:
        for entry in manifest.tools:
            result = build_tool(
                entry.name, tool_code, test_code, registry, timeout_s=tests_timeout_s
            )
            if result.report_id:
                report_ids_list.append(result.report_id)
            if result.gate != "PASS":
                return ToolImportResult(
                    name=manifest.name,
                    state="GATE_FAILED",
                    report_ids=tuple(report_ids_list),
                    review=verdict,
                    detail=(
                        f"forge gate failed during registration for {entry.name} "
                        f"({result.gate}); import stays quarantined"
                    ),
                )
            registered.append(f"{entry.name} ({result.detail})")
            payload = {
                **manifest.provenance.model_dump(),
                "manifest_name": manifest.name,
                "manifest_version": manifest.version,
                "entry": {
                    "name": entry.name,
                    "description": entry.description,
                    "risk": entry.risk,
                },
                "review": review_summary,
            }
            if not _attach_provenance(registry.get(entry.name), payload):
                unattached.append(entry.name)
        detail = "registered " + ", ".join(registered)
        if unattached:
            detail += (
                "; import_provenance could not be attached for "
                + ", ".join(unattached)
                + " (carried here): "
                + json.dumps(review_summary, sort_keys=True)
            )
    else:
        detail = (
            "verified through forge gates and reviewer; no registry supplied (nothing registered)"
        )
    return ToolImportResult(
        name=manifest.name,
        state="REGISTERED",
        report_ids=tuple(report_ids_list),
        review=verdict,
        detail=detail,
    )


__all__ = [
    "DEFAULT_TESTS_TIMEOUT_S",
    "MANIFEST_FILENAME",
    "MAX_DESCRIPTION_CHARS",
    "MAX_MANIFEST_BYTES",
    "MAX_TOOLS",
    "REQUIRED_FILES",
    "TEST_FILENAME",
    "TOOL_FILENAME",
    "ManifestNetwork",
    "ManifestProvenance",
    "ToolImportResult",
    "ToolManifest",
    "ToolManifestEntry",
    "compute_content_hash",
    "import_tool",
    "load_manifest",
    "parse_manifest",
    "verify_content_hash",
]
