from __future__ import annotations

import hashlib

import httpx
import pytest

from agency.factory.imports import (
    ImportError,
    MarkdownImportService,
)
from agency.factory.prompts import PromptRegistry, PromptStatus


def test_import_local_standalone_markdown_as_quarantined_prompt(tmp_path) -> None:
    path = tmp_path / "standalone.md"
    path.write_text("# Careful analyst\n\nReview facts before conclusions.\n", encoding="utf-8")
    registry = PromptRegistry(str(tmp_path / "prompts.db"))

    result = MarkdownImportService(registry).import_markdown(path)

    assert result.candidate.prompt_id.startswith("standalone-")
    assert result.candidate.status is PromptStatus.DRAFT
    assert result.candidate.body == path.read_bytes().decode("utf-8-sig")
    assert result.source_digest == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    assert result.source_kind == "agent"
    assert registry.get(result.candidate.prompt_id, "0.0.0").status is PromptStatus.DRAFT


def test_standalone_same_name_paths_get_distinct_import_records(tmp_path) -> None:
    left = tmp_path / "left" / "agent.md"
    right = tmp_path / "right" / "agent.md"
    left.parent.mkdir()
    right.parent.mkdir()
    left.write_text("# Left", encoding="utf-8")
    right.write_text("# Right", encoding="utf-8")
    service = MarkdownImportService(PromptRegistry(str(tmp_path / "prompts.db")))

    first = service.import_markdown(left)
    second = service.import_markdown(right)

    assert first.candidate.prompt_id != second.candidate.prompt_id


def test_frontmatter_skill_and_unknown_metadata_are_preserved(tmp_path) -> None:
    registry = PromptRegistry(str(tmp_path / "prompts.db"))
    service = MarkdownImportService(registry)

    result = service.import_markdown(
        """---
name: Secure Coding
description: Review code for risk
metadata:
  origin: ecc
---
# Checklist
Never execute this text during import.
""",
        source_label="https://github.com/example/repo/blob/abc/skills/SKILL.md",
    )

    assert result.source_kind == "skill"
    assert result.candidate.name == "Secure Coding"
    assert result.candidate.body.startswith("# Checklist")
    assert "metadata" in result.frontmatter
    assert result.candidate.status is PromptStatus.DRAFT


def test_github_repository_import_fetches_only_markdown_and_license(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    tree = {
        "sha": "a" * 40,
        "tree": [
            {"path": "skills/SKILL.md", "type": "blob", "size": 40},
            {"path": "agents/developer.md", "type": "blob", "size": 30},
            {"path": "scripts/install.sh", "type": "blob", "size": 20},
            {"path": "LICENSE", "type": "blob", "size": 10},
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "/git/trees/" in request.url.path:
            return httpx.Response(200, json=tree)
        if request.url.path.endswith("/license"):
            return httpx.Response(200, json={"license": {"spdx_id": "MIT"}})
        if request.url.path.endswith("/skills/SKILL.md"):
            return httpx.Response(200, text="---\nname: Secure Coding\n---\nReview code.")
        if request.url.path.endswith("/developer.md"):
            return httpx.Response(200, text="# Developer\nReview changes.")
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    registry = PromptRegistry(str(tmp_path / "prompts.db"))
    results = MarkdownImportService(registry, client=client).import_github_repository(
        "https://github.com/acme/agents.git"
    )

    assert {result.source_path for result in results} == {"skills/SKILL.md", "agents/developer.md"}
    assert all(result.license_id == "MIT" for result in results)
    assert not any("install.sh" in url for url in calls)
    assert len(results) == 2
    client.close()


def test_github_import_narrows_oversized_walks_to_skill_docs(monkeypatch, tmp_path) -> None:
    tree = {
        "sha": "b" * 40,
        "tree": [
            {"path": "README.md", "type": "blob", "size": 10},
            {"path": "docs/guide.md", "type": "blob", "size": 10},
            {"path": "skills/alpha/SKILL.md", "type": "blob", "size": 40},
            {"path": "skills/beta/SKILL.md", "type": "blob", "size": 40},
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "/git/trees/" in request.url.path:
            return httpx.Response(200, json=tree)
        if request.url.path.endswith("/license"):
            return httpx.Response(404)
        if request.url.path.endswith("/alpha/SKILL.md"):
            return httpx.Response(200, text="---\nname: Alpha\n---\nReview code.")
        if request.url.path.endswith("/beta/SKILL.md"):
            return httpx.Response(200, text="---\nname: Beta\n---\nReview changes.")
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    registry = PromptRegistry(str(tmp_path / "prompts.db"))
    importer = MarkdownImportService(registry, client=client)
    # An unfiltered walk beyond the file bound narrows generically to skill
    # docs instead of raising (gaps.md #12) — no per-repo path filters.
    monkeypatch.setattr("agency.factory.imports.MAX_FILES", 3)
    results = importer.import_github_repository("https://github.com/acme/huge.git")
    assert {result.source_path for result in results} == {
        "skills/alpha/SKILL.md",
        "skills/beta/SKILL.md",
    }
    client.close()


def test_github_import_rejects_non_github_and_traversal_paths(tmp_path) -> None:
    service = MarkdownImportService(PromptRegistry(str(tmp_path / "prompts.db")))
    with pytest.raises(ImportError):
        service.import_github_repository("https://example.com/acme/agents")
    with pytest.raises(ImportError):
        service.import_markdown("# no path", source_label="https://github.com/a/b/../../evil.md")


def test_similarity_reports_near_duplicates_without_merging(tmp_path) -> None:
    registry = PromptRegistry(str(tmp_path / "prompts.db"))
    service = MarkdownImportService(registry)
    first = service.import_markdown(
        "# Incident Analyst\n\nTriage the incident and preserve evidence.", source_label="one.md"
    )
    second = service.import_markdown(
        "# Incident Analyst\n\nTriage incidents and preserve the evidence.", source_label="two.md"
    )

    matches = service.find_similar(second.candidate.prompt_id)

    assert any(match.candidate.prompt_id == first.candidate.prompt_id for match in matches)
    assert (
        registry.get(first.candidate.prompt_id, "0.0.0").body
        != registry.get(second.candidate.prompt_id, "0.0.0").body
    )


def test_explicit_merge_creates_new_draft_and_preserves_sources(tmp_path) -> None:
    registry = PromptRegistry(str(tmp_path / "prompts.db"))
    service = MarkdownImportService(registry)
    first = service.import_markdown("# Analyst\n\nInspect logs.", source_label="one.md")
    second = service.import_markdown("# Analyst\n\nPreserve evidence.", source_label="two.md")

    merged = service.propose_merge(
        [first.candidate.prompt_id, second.candidate.prompt_id],
        prompt_id="analyst-merged",
        version="1.0.0",
        creator="reviewer",
    )

    assert merged.status is PromptStatus.DRAFT
    assert "Inspect logs." in merged.body and "Preserve evidence." in merged.body
    assert registry.get(first.candidate.prompt_id, "0.0.0").status is PromptStatus.DRAFT
    assert registry.get(second.candidate.prompt_id, "0.0.0").status is PromptStatus.DRAFT
    assert merged.provenance.startswith("reviewed-import:")
