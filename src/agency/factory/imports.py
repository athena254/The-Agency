"""Bounded import of untrusted Markdown into quarantined Factory prompt drafts.

Imported bodies are inert data. This module does not execute content, follow
Markdown links, or treat upstream metadata as authorization.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlparse

import httpx
import yaml  # type: ignore[import-untyped]

from agency.factory.prompts import PromptKind, PromptRegistry, PromptSpec, PromptStatus

MAX_MARKDOWN_BYTES = 64 * 1024
MAX_FILES = 500
MAX_REPOSITORY_BYTES = 5 * 1024 * 1024
MAX_TREE_BYTES = 1024 * 1024
_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_WORD_RE = re.compile(r"[a-z0-9]+")


class ImportError(ValueError):
    """Invalid, unsafe, or oversized Markdown import request."""


@dataclass(frozen=True)
class ImportedMarkdown:
    candidate: PromptSpec
    source: str
    source_kind: str
    source_path: str
    source_digest: str
    license_id: str
    frontmatter: dict[str, Any]


@dataclass(frozen=True)
class SimilarityMatch:
    candidate: PromptSpec
    score: float


def _slug(value: str) -> str:
    result = _ID_RE.sub("-", value.strip()).strip("-.")
    if not result:
        result = "imported-markdown"
    return result[:110]


def _candidate_id(source_path: str, source_identity: str) -> str:
    normalized_path = source_path.replace("\\", "/")
    stem = _slug(Path(normalized_path).stem)
    # Identity derives from the source location, never content: revisions of
    # one source collide as immutable records rather than silently forking IDs.
    identity = hashlib.sha256(source_identity.encode("utf-8")).hexdigest()[:12]
    return _slug(f"{stem}-{identity}")


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return {}, text
    lines = text.splitlines(keepends=True)
    closing = next((i for i in range(1, min(len(lines), 202)) if lines[i].strip() == "---"), None)
    if closing is None:
        raise ImportError("unterminated YAML frontmatter")
    header = "".join(lines[1:closing])
    if len(header.encode("utf-8")) > 16 * 1024:
        raise ImportError("frontmatter exceeds 16 KiB")
    try:
        raw = yaml.safe_load(header) or {}
    except yaml.YAMLError as exc:
        raise ImportError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
        raise ImportError("frontmatter must be a string-keyed mapping")
    body = "".join(lines[closing + 1 :])
    return raw, body


def _classify(path: str, frontmatter: dict[str, Any]) -> str:
    normalized = path.replace("\\", "/").casefold()
    name = PurePosixPath(normalized).name
    if name == "skill.md" or "skill" in normalized.split("/") or "skill" in frontmatter:
        return "skill"
    return "agent"


def _safe_source(source: str, *, allowed_hosts: frozenset[str] = frozenset({"github.com"})) -> str:
    parsed = urlparse(source)
    if parsed.scheme:
        if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
            raise ImportError("remote source host is not allowed")
        path_parts = [part for part in parsed.path.split("/") if part]
        if any(part in {".", ".."} for part in path_parts):
            raise ImportError("source path traversal is not allowed")
    return source


class MarkdownImportService:
    """Parse standalone files or GitHub repositories into `DRAFT` prompts."""

    def __init__(
        self,
        registry: PromptRegistry,
        *,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._registry = registry
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _parse_markdown(
        self,
        content: str | Path,
        *,
        source_label: str | None = None,
        license_id: str = "UNKNOWN",
        source_path_override: str | None = None,
        source_identity: str | None = None,
    ) -> ImportedMarkdown:
        original_bytes: bytes | None = None
        if isinstance(content, Path):
            path = content
            if path.suffix.casefold() != ".md":
                raise ImportError("only standalone .md files may be imported")
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise ImportError(f"cannot read Markdown source: {exc}") from exc
            if len(data) > MAX_MARKDOWN_BYTES:
                raise ImportError("Markdown exceeds 64 KiB")
            label = _safe_source(source_label) if source_label and urlparse(source_label).scheme else (
                source_label or str(path.resolve())
            )
            source_path = source_path_override or path.resolve().as_posix()
            original_bytes = data
        else:
            label = _safe_source(
                source_label or "standalone.md",
                allowed_hosts=frozenset({"github.com", "raw.githubusercontent.com"}),
            )
            data = content.encode("utf-8")
            if len(data) > MAX_MARKDOWN_BYTES:
                raise ImportError("Markdown exceeds 64 KiB")
            source_path = source_path_override or PurePosixPath(urlparse(label).path).name or "standalone.md"
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ImportError("Markdown must be UTF-8 text") from exc
        frontmatter, body = _split_frontmatter(text)
        source_kind = _classify(source_path, frontmatter)
        name_value = frontmatter.get("name")
        name = name_value.strip() if isinstance(name_value, str) and name_value.strip() else Path(source_path).stem.replace("-", " ").title()
        description = frontmatter.get("description", "")
        if not isinstance(description, str):
            description = ""
        if not body.strip():
            body = text
        prompt_id = _candidate_id(source_path, source_identity or label)
        digest = "sha256:" + hashlib.sha256(original_bytes or data).hexdigest()
        provenance = "import-quarantine:" + json.dumps(
            {"source": label, "path": source_path, "digest": digest, "license": license_id},
            sort_keys=True,
            separators=(",", ":"),
        )
        candidate = PromptSpec(
            prompt_id=prompt_id,
            version="0.0.0",
            name=name[:200],
            description=description[:2000],
            domain=_slug(source_kind),
            kind=PromptKind.PACK if source_kind == "agent" else PromptKind.SEGMENT,
            body=body,
            provenance=provenance,
            status=PromptStatus.DRAFT,
        )
        return ImportedMarkdown(candidate, label, source_kind, source_path, digest, license_id, frontmatter)

    def _persist(self, imported: ImportedMarkdown) -> ImportedMarkdown:
        try:
            candidate = self._registry.register(imported.candidate)
        except ValueError as exc:
            raise ImportError(f"candidate already imported; use a new source path: {exc}") from exc
        return ImportedMarkdown(
            candidate,
            imported.source,
            imported.source_kind,
            imported.source_path,
            imported.source_digest,
            imported.license_id,
            imported.frontmatter,
        )

    def import_markdown(self, source: str | Path, *, source_label: str | None = None) -> ImportedMarkdown:
        return self._persist(self._parse_markdown(source, source_label=source_label))

    def import_github_repository(self, repository_url: str, *, ref: str = "main") -> list[ImportedMarkdown]:
        _safe_source(repository_url)
        parsed = urlparse(repository_url)
        parts = [part for part in parsed.path.strip("/").removesuffix(".git").split("/") if part]
        if len(parts) != 2 or any(part in {".", ".."} for part in parts):
            raise ImportError("expected https://github.com/{owner}/{repo}[.git]")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", ref):
            raise ImportError("ref must be a simple branch or tag name")
        owner, repo = parts
        api = f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}"
        try:
            tree_response = self._client.get(
                f"{api}/git/trees/{quote(ref, safe='')}", params={"recursive": "1"}
            )
            tree_response.raise_for_status()
            tree_bytes = tree_response.content
            if len(tree_bytes) > MAX_TREE_BYTES:
                raise ImportError("Git tree exceeds 1 MiB")
            tree = tree_response.json()
            if not isinstance(tree, dict) or not isinstance(tree.get("tree"), list):
                raise ImportError("GitHub tree response is malformed or truncated")
            if tree.get("truncated") is True:
                raise ImportError("GitHub tree is truncated; use a narrower repository/ref")
            tree_sha = tree.get("sha")
            if not isinstance(tree_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40,64}", tree_sha):
                raise ImportError("GitHub tree response is missing a valid immutable tree SHA")
            license_response = self._client.get(f"{api}/license")
            license_id = "UNKNOWN"
            if license_response.status_code == 200:
                license_data = license_response.json()
                license_obj = license_data.get("license") if isinstance(license_data, dict) else None
                if isinstance(license_obj, dict):
                    license_id = str(license_obj.get("spdx_id") or "UNKNOWN")[:128]
            blobs = [
                entry for entry in tree.get("tree", [])
                if entry.get("type") == "blob"
                and isinstance(entry.get("path"), str)
                and entry["path"].casefold().endswith(".md")
            ]
            if len(blobs) > MAX_FILES:
                raise ImportError(f"repository has more than {MAX_FILES} Markdown files")
            try:
                total_size = sum(int(entry.get("size", 0)) for entry in blobs)
            except (TypeError, ValueError) as exc:
                raise ImportError("GitHub tree contains an invalid blob size") from exc
            if total_size > MAX_REPOSITORY_BYTES:
                raise ImportError("Markdown content exceeds 5 MiB total")
            out: list[ImportedMarkdown] = []
            seen_ids: set[str] = set()
            for entry in blobs:
                path = entry["path"]
                pure = PurePosixPath(path)
                if pure.is_absolute() or ".." in pure.parts or "\\" in path:
                    raise ImportError("unsafe path in GitHub tree")
                if path.casefold() == "license" or path.casefold().startswith("license."):
                    continue
                if int(entry.get("size", 0)) > MAX_MARKDOWN_BYTES:
                    continue
                raw_url = (
                    f"https://raw.githubusercontent.com/{quote(owner)}/{quote(repo)}/"
                    f"{quote(ref, safe='')}/{quote(path, safe='/')}"
                )
                try:
                    with self._client.stream("GET", raw_url) as response:
                        response.raise_for_status()
                        chunks: list[bytes] = []
                        bytes_read = 0
                        for chunk in response.iter_bytes(chunk_size=8192):
                            bytes_read += len(chunk)
                            if bytes_read > MAX_MARKDOWN_BYTES:
                                break
                            chunks.append(chunk)
                    if bytes_read > MAX_MARKDOWN_BYTES:
                        continue
                    content_bytes = b"".join(chunks)
                    remote_source = f"{repository_url}#{tree_sha}:{path}"
                    result = self._parse_markdown(
                        content_bytes.decode("utf-8-sig", errors="strict"),
                        source_label=remote_source,
                        license_id=license_id,
                        source_path_override=path,
                        source_identity=remote_source,
                    )
                    import_id = result.candidate.prompt_id
                    if import_id in seen_ids:
                        continue
                    seen_ids.add(import_id)
                    out.append(self._persist(result))
                except (UnicodeDecodeError, httpx.HTTPError, ValueError) as exc:
                    raise ImportError(f"failed to import Markdown file {path!r}: {exc}") from exc
            return out
        except httpx.HTTPError as exc:
            raise ImportError(f"GitHub read failed: {exc}") from exc

    def find_similar(self, prompt_id: str, *, threshold: float = 0.45) -> list[SimilarityMatch]:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1")
        target = self._registry.get(prompt_id, "0.0.0")
        target_words = set(
            _WORD_RE.findall(f"{target.name} {target.description} {target.body}".casefold())
        )
        matches: list[SimilarityMatch] = []
        for candidate in self._registry.list_specs():
            candidate_id, version = candidate.prompt_id, candidate.version
            if (
                candidate_id == prompt_id
                or version != "0.0.0"
                or not candidate.provenance.startswith("import-quarantine:")
            ):
                continue
            words = set(_WORD_RE.findall(f"{candidate.name} {candidate.description} {candidate.body}".casefold()))
            union = target_words | words
            score = len(target_words & words) / len(union) if union else 1.0
            if score >= threshold:
                matches.append(SimilarityMatch(candidate, score))
        return sorted(matches, key=lambda item: (-item.score, item.candidate.prompt_id))

    def propose_merge(
        self,
        source_ids: list[str] | tuple[str, ...],
        *,
        prompt_id: str,
        version: str,
        creator: str,
    ) -> PromptSpec:
        if len(source_ids) < 2 or len(source_ids) > 10 or len(set(source_ids)) != len(source_ids):
            raise ImportError("merge requires 2-10 distinct explicitly selected source IDs")
        if not creator.strip():
            raise ImportError("merge creator must be nonblank")
        sources = [self._registry.get(item, "0.0.0") for item in source_ids]
        if any(not source.provenance.startswith("import-quarantine:") for source in sources):
            raise ImportError("merge sources must be quarantined imported candidates")
        source_records = [
            json.loads(source.provenance.removeprefix("import-quarantine:")) for source in sources
        ]
        bodies = "\n\n---\n\n".join(source.body.rstrip() for source in sources)
        body = "# Reviewed merge proposal\n\n" + bodies
        if len(body.encode("utf-8")) > MAX_MARKDOWN_BYTES:
            raise ImportError("merged body exceeds 64 KiB")
        provenance = "reviewed-import:" + json.dumps(
            {"creator": creator, "sources": source_records},
            sort_keys=True,
            separators=(",", ":"),
        )
        candidate = PromptSpec(
            prompt_id=prompt_id,
            version=version,
            name=f"Merged: {sources[0].name}"[:200],
            description="Human-selected imported Markdown merge proposal.",
            domain=sources[0].domain,
            kind=PromptKind.PACK,
            body=body,
            provenance=provenance,
            status=PromptStatus.DRAFT,
        )
        return self._registry.register(candidate)


__all__ = ["ImportError", "ImportedMarkdown", "MarkdownImportService", "SimilarityMatch"]