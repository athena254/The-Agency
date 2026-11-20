from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from agency.factory.github_skills import GitHubSkillImportService, grant_manifest_capabilities
from agency.factory.imports import ImportedMarkdown
from agency.factory.prompts import PromptKind, PromptRegistry, PromptSpec, PromptStatus
from agency.kernel.identity import Agent
from agency.kernel.registry import AgentRegistry
from agency.skills.registry import SkillRegistry, SkillSpec, SkillStatus


def _imported(name: str, description: str, path: str, digest: str) -> ImportedMarkdown:
    source = f"https://github.com/acme/skills#sha:{path}"
    source_digest = f"sha256:{digest}"
    provenance = "import-quarantine:" + json.dumps(
        {"source": source, "path": path, "digest": source_digest, "license": "MIT"},
        sort_keys=True,
        separators=(",", ":"),
    )
    candidate = PromptSpec(
        prompt_id=f"prompt-{digest}",
        version="0.0.0",
        name=name,
        description=description,
        domain="skill",
        kind=PromptKind.SEGMENT,
        body="# Skill\n\nInert imported instructions.",
        provenance=provenance,
        status=PromptStatus.DRAFT,
    )
    return ImportedMarkdown(candidate, source, "skill", path, source_digest, "MIT", {})


@pytest.fixture
def service(tmp_path: Path) -> GitHubSkillImportService:
    return GitHubSkillImportService(
        PromptRegistry(":memory:"),
        SkillRegistry(str(tmp_path / "skills.db")),
        data_dir=tmp_path,
    )


def test_study_dedupes_similar_and_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = GitHubSkillImportService(
        PromptRegistry(":memory:"),
        SkillRegistry(str(tmp_path / "skills.db")),
        data_dir=tmp_path,
    )
    items = [
        _imported("Code Review", "Review code for defects", "skills/a/SKILL.md", "1" * 64),
        _imported("Code Review", "Review code for defects", "skills/b/SKILL.md", "1" * 64),
        _imported(
            "Code Review", "Review code for defects and style", "skills/c/SKILL.md", "2" * 64
        ),
        _imported(
            "Database Migrations", "Plan safe schema migrations", "skills/d/SKILL.md", "3" * 64
        ),
    ]
    monkeypatch.setattr(svc._importer, "import_github_repository", lambda _repo, **_kwargs: items)
    imported, reports = svc.import_repos(("https://github.com/acme/skills.git",))
    assert len(imported) == 4 and reports[0].prompts == 4
    records = svc.study_and_dedupe(imported)
    canonical = [r for r in records if r.decision == "canonical"]
    duplicates = [r for r in records if r.decision == "duplicate"]
    assert len(canonical) == 3
    assert len(duplicates) == 1
    assert {r.group for r in records if r.group} == {r.group for r in canonical}
    svc.close()


def test_make_skills_promote_and_attach(tmp_path: Path, service: GitHubSkillImportService) -> None:
    record = _imported("Code Review", "Review code for defects", "skills/code/SKILL.md", "4" * 64)
    records = service.study_and_dedupe([record])
    created = service.make_skills(records)
    assert len(created) == 1
    skill_id, version = created[0]
    assert service._skills.get(skill_id, version).status is SkillStatus.DRAFT
    promoted = service.promote(created, "test review evidence")
    assert promoted == created
    assert service._skills.get(skill_id, version).status is SkillStatus.PUBLISHED
    attached = service.attach_to_agents(promoted, agent_ids=("coding-agent",))
    assert attached["coding-agent"] == created
    manifest = json.loads((tmp_path / "external_skill_attachments.json").read_text())
    assert manifest["coding-agent"] == [list(created[0])]
    kernel = AgentRegistry()
    kernel.register(Agent(id="coding-agent", name="Coding Agent", capabilities=[]))
    assert service.apply_to_kernel(kernel, attached) == 1
    assert any(
        cap.name == f"skill-{skill_id}" for cap in kernel.get_required("coding-agent").capabilities
    )


def test_improve_existing_builds_successor(service: GitHubSkillImportService) -> None:
    service._skills.register(
        SkillSpec(
            skill_id="existing-skill",
            version="1.0.0",
            name="Code Review",
            description="Review code for defects",
            inputs={},
            outputs={},
            permissions=(),
            implementation="existing.review",
            provenance="local",
            status=SkillStatus.DRAFT,
        )
    )
    service._skills.transition("existing-skill", "1.0.0", SkillStatus.TESTING, "test")
    service._skills.transition("existing-skill", "1.0.0", SkillStatus.APPROVED, "test")
    service._skills.publish("existing-skill", "1.0.0", "test")
    record = _imported("Code Review", "Review code for defects", "skills/code/SKILL.md", "5" * 64)
    improved = service.improve_existing(service.study_and_dedupe([record]))
    assert improved == (("existing-skill", "1.0.1"),)
    successor = service._skills.get("existing-skill", "1.0.1")
    assert successor.status is SkillStatus.DRAFT
    assert "imported insight" in successor.description


def test_discover_new_repos_uses_registry_tree_shas(
    service: GitHubSkillImportService, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = "https://github.com/acme/skills.git"
    monkeypatch.setattr(service._importer, "remote_tree_sha", lambda _repo: "a" * 40)
    assert service.discover_new_repos((repo,)) == (repo,)

    imported = _imported("Known Skill", "Already imported", "skills/known/SKILL.md", "8" * 64)
    known_source = "https://github.com/acme/skills.git#" + "a" * 40 + ":skills/known/SKILL.md"
    candidate = replace(
        imported.candidate,
        provenance="import-quarantine:"
        + json.dumps(
            {
                "source": known_source,
                "path": imported.source_path,
                "digest": imported.source_digest,
                "license": "MIT",
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    service._prompts.register(candidate)
    assert service.discover_new_repos((repo,)) == ()


def test_grant_manifest_capabilities(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"coding-agent": [["ext-code-review", "1.0.0"]]}), encoding="utf-8"
    )
    kernel = AgentRegistry()
    kernel.register(Agent(id="coding-agent", name="Coding Agent", capabilities=[]))
    assert grant_manifest_capabilities(kernel, manifest) == 1
    assert "skill-ext-code-review" in [
        cap.name for cap in kernel.get_required("coding-agent").capabilities
    ]
    assert grant_manifest_capabilities(kernel, tmp_path / "missing.json") == 0


def test_study_from_registry_creates_skills_without_network(
    tmp_path: Path, service: GitHubSkillImportService
) -> None:
    imported = _imported(
        "Web Research", "Research topics on the web", "skills/web/SKILL.md", "7" * 64
    )
    service._prompts.register(imported.candidate)
    records = service.study_records_from_registry(("https://github.com/acme/skills.git",))
    assert len(records) == 1 and records[0].decision == "canonical"
    assert len(service.make_skills(records)) == 1


def test_run_writes_catalog_and_isolates_repo_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = GitHubSkillImportService(
        PromptRegistry(":memory:"),
        SkillRegistry(str(tmp_path / "skills.db")),
        data_dir=tmp_path,
    )
    good = _imported(
        "Security Audit", "Audit code for security issues", "skills/sec/SKILL.md", "6" * 64
    )

    def fake_import(repo: str, **_kwargs):
        if "bad" in repo:
            raise RuntimeError("boom")
        return [good]

    monkeypatch.setattr(svc._importer, "import_github_repository", fake_import)
    report = svc.run(("https://github.com/acme/bad.git", "https://github.com/acme/skills.git"))
    assert report.repos[0].error and report.repos[1].prompts == 1
    assert len(report.records) == 1
    assert len(report.new_skills) == 1
    assert (tmp_path / "external_skills_catalog.json").exists()
    assert (tmp_path / "external_skill_attachments.json").exists()
    svc.close()
