"""Study quarantined GitHub Markdown imports and turn them into Agency skills.

Import always begins as inert DRAFT prompt records via ``MarkdownImportService``.
This module only reads that metadata: it never executes upstream content,
follows upstream links, installs anything, or fetches transitive URLs.
Promotion and attachment are explicit, evidenced, and opt-in.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from agency.factory.imports import ImportedMarkdown, MarkdownImportService
from agency.factory.prompts import PromptRegistry
from agency.kernel.identity import Capability
from agency.kernel.policies import ActionClass
from agency.skills.registry import SkillRegistry, SkillSpec, SkillStatus

DEFAULT_REPOS: tuple[str, ...] = (
    "https://github.com/msitarzewski/agency-agents.git",
    "https://github.com/affaan-m/ECC.git",
    "https://github.com/morluto/rea.git",
    "https://github.com/mattpocock/skills.git",
    "https://github.com/obra/superpowers.git",
    "https://github.com/anthropics/skills.git",
    "https://github.com/Graphify-Labs/graphify.git",
    "https://github.com/elementalsouls/Claude-BugHunter.git",
    "https://github.com/cortex-docs/cortex.git",
    "https://github.com/addyosmani/agent-skills.git",
    "https://github.com/headroomlabs-ai/headroom.git",
    "https://github.com/tt-a1i/archify.git",
    "https://github.com/garrytan/gstack.git",
    "https://github.com/Panniantong/Agent-Reach.git",
    "https://github.com/thedotmack/claude-mem.git",
    "https://github.com/Leonxlnx/taste-skill.git",
    "https://github.com/michaelshimeles/rstack.git",
)

AGENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "coding-agent": (
        "code",
        "review",
        "test",
        "debug",
        "refactor",
        "lint",
        "type",
        "compile",
        "build",
        "git",
        "security",
        "documentation",
        "bug",
    ),
    "blacksmith": (
        "forge",
        "build",
        "ship",
        "deploy",
        "ci",
        "release",
        "test",
        "verification",
        "verify",
        "worker",
    ),
    "researcher": (
        "research",
        "search",
        "web",
        "study",
        "analysis",
        "document",
        "evidence",
        "source",
        "summar",
    ),
}

MAX_SKILLS_PER_AGENT = 16
MAX_IMPROVEMENTS = 24
MIN_SIMILARITY = 0.60
DEDUPE_SIMILARITY = 0.82
_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class RepoReport:
    repo: str
    prompts: int = 0
    skills: int = 0
    error: str = ""


@dataclass(frozen=True)
class StudyRecord:
    prompt_id: str
    name: str
    description: str
    source: str
    source_path: str
    license_id: str
    digest: str
    group: str
    decision: str


@dataclass(frozen=True)
class RunReport:
    repos: tuple[RepoReport, ...] = ()
    records: tuple[StudyRecord, ...] = ()
    new_skills: tuple[tuple[str, str], ...] = ()
    improved_skills: tuple[tuple[str, str], ...] = ()
    promoted: tuple[tuple[str, str], ...] = ()
    attachments: dict[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repos": [vars(item) for item in self.repos],
            "records": [vars(item) for item in self.records],
            "new_skills": [list(item) for item in self.new_skills],
            "improved_skills": [list(item) for item in self.improved_skills],
            "promoted": [list(item) for item in self.promoted],
            "attachments": {
                agent: [list(ref) for ref in refs] for agent, refs in self.attachments.items()
            },
        }


def _tokens(text: str) -> set[str]:
    return {word for word in _WORD_RE.findall(text.casefold()) if len(word) > 2}


def _similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _implementation_name(record: StudyRecord) -> str:
    source = record.source.removeprefix("https://github.com/")
    owner_repo = source.split("/")[:2]
    stem = Path(record.source_path).stem.casefold()
    raw = "ext-" + "-".join([*owner_repo, stem])
    cleaned = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-._")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = "ext-" + cleaned
    return cleaned[:100]


def _next_version(versions: list[str]) -> str:
    if not versions:
        return "1.0.0"
    major, minor, patch = versions[-1].split(".")
    return f"{major}.{minor}.{int(patch) + 1}"


def _skill_id(record: StudyRecord) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", record.name.casefold()).strip("-")[:48] or "external-skill"
    digest = record.digest.removeprefix("sha256:")[:10]
    return f"ext-{slug}-{digest}"[:100]


class GitHubSkillImportService:
    """Import, study, dedupe, and promote GitHub skill/agent Markdown."""

    def __init__(
        self,
        prompt_registry: PromptRegistry,
        skill_registry: SkillRegistry,
        *,
        client: Any = None,
        approver: str = "",
        publish_authorizer: Any = None,
        data_dir: str | Path = "data",
    ) -> None:
        self._prompts = prompt_registry
        self._skills = skill_registry
        self._importer = MarkdownImportService(prompt_registry, client=client)
        # Promotion (gaps.md #13) requires an explicit human approver identity
        # and a publish authorizer; both fail closed in promote().
        self._approver = approver.strip()
        self._publish_authorizer = publish_authorizer
        self._data_dir = Path(data_dir)

    def close(self) -> None:
        self._importer.close()

    def _known_tree_shas(self) -> set[str]:
        shas: set[str] = set()
        for spec in self._prompts.list_specs():
            if not spec.provenance.startswith("import-quarantine:"):
                continue
            try:
                payload = json.loads(spec.provenance.split(":", 1)[1])
            except (ValueError, IndexError):
                continue
            match = re.search(r"#([0-9a-fA-F]{40,64}):", str(payload.get("source", "")))
            if match is not None:
                shas.add(match.group(1).casefold())
        return shas

    def discover_new_repos(self, repos: tuple[str, ...] = DEFAULT_REPOS) -> tuple[str, ...]:
        """Return repos whose current tree SHA is not in the prompt registry."""
        known = self._known_tree_shas()
        pending: list[str] = []
        for repo in repos:
            try:
                sha = self._importer.remote_tree_sha(repo)
            except Exception:  # noqa: BLE001 - unreachable repo is still pending work
                pending.append(repo)
                continue
            if sha.casefold() not in known:
                pending.append(repo)
        return tuple(pending)

    def import_repos(
        self, repos: tuple[str, ...] = DEFAULT_REPOS
    ) -> tuple[list[ImportedMarkdown], tuple[RepoReport, ...]]:
        imported: list[ImportedMarkdown] = []
        reports: list[RepoReport] = []
        for repo in repos:
            try:
                # Generic bounds live in the importer (gaps.md #12): oversized
                # repositories are narrowed and bounded there, without
                # per-repo hand-tuned path filters.
                items = self._importer.import_github_repository(repo)
            except Exception as exc:  # noqa: BLE001 - one bad repo must not stop the run
                reports.append(RepoReport(repo, error=f"{type(exc).__name__}: {exc}"[:400]))
                continue
            imported.extend(items)
            reports.append(
                RepoReport(
                    repo,
                    prompts=len(items),
                    skills=sum(1 for item in items if item.source_kind == "skill"),
                )
            )
        return imported, tuple(reports)

    def study_and_dedupe(
        self, imported: list[ImportedMarkdown], *, known: set[str] | None = None
    ) -> tuple[StudyRecord, ...]:
        records = [
            StudyRecord(
                prompt_id=item.candidate.prompt_id,
                name=item.candidate.name,
                description=item.candidate.description,
                source=item.source,
                source_path=item.source_path,
                license_id=item.license_id,
                digest=item.source_digest,
                group="",
                decision="candidate",
            )
            for item in imported
            if item.source_kind == "skill"
        ]
        return self._dedupe_records(
            records, self._known_prompt_digests() if known is None else known
        )

    def study_records_from_registry(self, repos: tuple[str, ...]) -> tuple[StudyRecord, ...]:
        needles = tuple(
            repo.removeprefix("https://github.com/").removesuffix(".git").casefold()
            for repo in repos
        )
        records: list[StudyRecord] = []
        for spec in self._prompts.list_specs():
            if not spec.provenance.startswith("import-quarantine:"):
                continue
            try:
                payload = json.loads(spec.provenance.split(":", 1)[1])
            except (ValueError, IndexError):
                continue
            source = str(payload.get("source", ""))
            if not any(needle in source.casefold() for needle in needles):
                continue
            if spec.domain != "skill":
                continue
            records.append(
                StudyRecord(
                    prompt_id=spec.prompt_id,
                    name=spec.name,
                    description=spec.description,
                    source=source,
                    source_path=str(payload.get("path", "")),
                    license_id=str(payload.get("license", "UNKNOWN")),
                    digest=str(payload.get("digest", "")),
                    group="",
                    decision="candidate",
                )
            )
        return self._dedupe_records(records, set())

    def _dedupe_records(
        self, records: list[StudyRecord], known: set[str]
    ) -> tuple[StudyRecord, ...]:
        canonical: list[tuple[StudyRecord, set[str]]] = []
        updated: list[StudyRecord] = []
        for record in records:
            if record.digest in known:
                updated.append(replace(record, decision="already-imported"))
                continue
            tokens = _tokens(f"{record.name} {record.description}")
            digest_match = next(
                (entry for entry in canonical if entry[0].digest == record.digest), None
            )
            similar = next(
                (
                    entry
                    for entry in canonical
                    if _similarity(tokens, entry[1]) >= DEDUPE_SIMILARITY
                ),
                None,
            )
            if digest_match is not None or similar is not None:
                winner = digest_match if digest_match is not None else similar
                assert winner is not None
                record = replace(record, group=winner[0].group, decision="duplicate")
            else:
                group = f"group-{len(canonical) + 1:04d}"
                record = replace(record, group=group, decision="canonical")
                canonical.append((record, tokens))
            updated.append(record)
        return tuple(updated)

    def _known_prompt_digests(self) -> set[str]:
        digests: set[str] = set()
        for spec in self._prompts.list_specs():
            if not spec.provenance.startswith("import-quarantine:"):
                continue
            try:
                payload = json.loads(spec.provenance.split(":", 1)[1])
            except (ValueError, IndexError):
                continue
            digest = payload.get("digest")
            if isinstance(digest, str):
                digests.add(digest)
        return digests

    def _known_skill_ids(self) -> set[str]:
        try:
            rows = self._skills._conn.execute("SELECT DISTINCT skill_id FROM skills").fetchall()
        except Exception:  # noqa: BLE001 - best effort
            return set()
        return {str(row[0]) for row in rows}

    def _existing_skill_specs(self) -> list[SkillSpec]:
        specs: list[SkillSpec] = []
        for skill_id in self._known_skill_ids():
            versions = self._skills.list_versions(skill_id)
            if versions:
                specs.append(versions[-1])
        return specs

    def make_skills(self, records: tuple[StudyRecord, ...]) -> tuple[tuple[str, str], ...]:
        known = self._known_skill_ids()
        created: list[tuple[str, str]] = []
        for record in records:
            if record.decision != "canonical":
                continue
            skill_id = _skill_id(record)
            if skill_id in known:
                continue
            try:
                spec = SkillSpec(
                    skill_id=skill_id,
                    version="1.0.0",
                    name=record.name[:120],
                    description=record.description[:500],
                    inputs={"task": {"type": "string"}},
                    outputs={"result": {"type": "string"}},
                    permissions=(),
                    implementation=_implementation_name(record),
                    provenance=record.source,
                    status=SkillStatus.DRAFT,
                )
                self._skills.register(spec)
                created.append((skill_id, spec.version))
            except (ValueError, KeyError):
                continue
        return tuple(created)

    def improve_existing(self, records: tuple[StudyRecord, ...]) -> tuple[tuple[str, str], ...]:
        improved: list[tuple[str, str]] = []
        for record in records:
            if record.decision != "canonical" or len(improved) >= MAX_IMPROVEMENTS:
                continue
            incoming = _tokens(f"{record.name} {record.description}")
            best: tuple[float, SkillSpec] | None = None
            for spec in self._existing_skill_specs():
                score = _similarity(incoming, _tokens(f"{spec.name} {spec.description}"))
                if score >= MIN_SIMILARITY and (best is None or score > best[0]):
                    best = (score, spec)
            if best is None:
                continue
            spec = best[1]
            version = _next_version(
                [item.version for item in self._skills.list_versions(spec.skill_id)]
            )
            detail = record.description.strip()[:240]
            merged = f"{spec.description[:240]} | imported insight: {detail}".strip()[:500]
            try:
                successor = SkillSpec(
                    skill_id=spec.skill_id,
                    version=version,
                    name=spec.name[:120],
                    description=merged,
                    inputs=dict(spec.inputs),
                    outputs=dict(spec.outputs),
                    permissions=tuple(spec.permissions),
                    implementation=spec.implementation,
                    provenance=f"improved-by-import:{record.source}",
                    status=SkillStatus.DRAFT,
                )
                self._skills.register(successor)
                improved.append((spec.skill_id, version))
            except (ValueError, KeyError):
                continue
        return tuple(improved)

    def promote(
        self, refs: tuple[tuple[str, str], ...], evidence: str
    ) -> tuple[tuple[str, str], ...]:
        # Distinct human decision (gaps.md #13, SPEC_AGENT_FACTORY_V2 §12.3):
        # promotion requires an explicitly configured approver identity and a
        # publish authorizer — never the historical default "owner".
        if not self._approver:
            raise ValueError("import promotion requires an explicit approver identity")
        if self._publish_authorizer is None:
            raise ValueError("import promotion requires a publish authorizer")
        promoted: list[tuple[str, str]] = []
        for skill_id, version in refs:
            try:
                self._skills.transition(skill_id, version, SkillStatus.TESTING, evidence)
                self._skills.transition(skill_id, version, SkillStatus.APPROVED, evidence)
                self._skills.publish(
                    skill_id,
                    version,
                    evidence,
                    approver=self._approver,
                    authorizer=self._publish_authorizer,
                )
                promoted.append((skill_id, version))
            except (ValueError, KeyError):
                continue
        return tuple(promoted)

    def attach_to_agents(
        self,
        refs: tuple[tuple[str, str], ...],
        agent_ids: tuple[str, ...] = ("coding-agent", "blacksmith", "researcher"),
    ) -> dict[str, tuple[tuple[str, str], ...]]:
        attachments: dict[str, tuple[tuple[str, str], ...]] = {}
        for agent in agent_ids:
            keywords = AGENT_KEYWORDS.get(agent, ())
            picked: list[tuple[str, str]] = []
            for skill_id, version in refs:
                text = skill_id.casefold()
                if any(word in text for word in keywords):
                    picked.append((skill_id, version))
                if len(picked) >= MAX_SKILLS_PER_AGENT:
                    break
            attachments[agent] = tuple(picked)
        manifest = self._data_dir / "external_skill_attachments.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(
                {agent: [list(ref) for ref in picked] for agent, picked in attachments.items()},
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return attachments

    def published_refs_for_repos(self, repos: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
        needles = tuple(
            repo.removeprefix("https://github.com/").removesuffix(".git").casefold()
            for repo in repos
        )
        refs: list[tuple[str, str]] = []
        for skill_id in self._known_skill_ids():
            versions = self._skills.list_versions(skill_id)
            if not versions:
                continue
            latest = versions[-1]
            if latest.status is SkillStatus.PUBLISHED and any(
                needle in latest.provenance.casefold() for needle in needles
            ):
                refs.append((skill_id, latest.version))
        return tuple(refs)

    def apply_manifest(
        self,
        kernel_registry: Any,
        manifest_path: str | Path | None = None,
    ) -> int:
        path = (
            Path(manifest_path)
            if manifest_path is not None
            else self._data_dir / "external_skill_attachments.json"
        )
        if not path.exists():
            return 0
        payload = json.loads(path.read_text(encoding="utf-8"))
        attachments = {
            str(agent): tuple((str(ref[0]), str(ref[1])) for ref in refs)
            for agent, refs in payload.items()
            if isinstance(refs, list)
        }
        return self.apply_to_kernel(kernel_registry, attachments)

    def apply_to_kernel(
        self,
        kernel_registry: Any,
        attachments: dict[str, tuple[tuple[str, str], ...]],
    ) -> int:
        granted = 0
        for agent_id, refs in attachments.items():
            if kernel_registry.get(agent_id) is None:
                continue
            for skill_id, _version in refs:
                name = f"skill-{skill_id}"[:128]
                kernel_registry.grant_capability(
                    agent_id,
                    Capability(name=name, max_level=ActionClass.L0_OBSERVATION),
                )
                granted += 1
        return granted

    def run(
        self,
        repos: tuple[str, ...] = DEFAULT_REPOS,
        *,
        promote: bool = False,
        evidence: str = "automated import review; source provenance preserved",
        write_catalog: bool = True,
        from_registry: bool = False,
        report_only: bool = False,
    ) -> RunReport:
        if report_only:
            records = self.study_records_from_registry(repos)
            refs = self.published_refs_for_repos(repos)
            attachments = self.attach_to_agents(refs)
            report = RunReport(
                repos=tuple(
                    RepoReport(
                        repo,
                        prompts=sum(
                            1
                            for r in records
                            if repo.removeprefix("https://github.com/").removesuffix(".git")
                            in r.source
                        ),
                    )
                    for repo in repos
                ),
                records=records,
                promoted=refs,
                attachments=attachments,
            )
            if write_catalog:
                self._data_dir.mkdir(parents=True, exist_ok=True)
                (self._data_dir / "external_skills_catalog.json").write_text(
                    json.dumps(report.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
                )
            return report
        if from_registry:
            records = self.study_records_from_registry(repos)
            repo_reports = tuple(
                RepoReport(
                    repo,
                    prompts=sum(
                        1
                        for r in records
                        if repo.removeprefix("https://github.com/").removesuffix(".git") in r.source
                    ),
                )
                for repo in repos
            )
        else:
            prior = self._known_prompt_digests()
            imported, repo_reports = self.import_repos(repos)
            records = self.study_and_dedupe(imported, known=prior)
        new_skills = self.make_skills(records)
        improved = self.improve_existing(records)
        candidates = new_skills + improved
        promoted = self.promote(candidates, evidence) if promote else ()
        attachments = self.attach_to_agents(promoted or candidates)
        report = RunReport(
            repos=repo_reports,
            records=records,
            new_skills=new_skills,
            improved_skills=improved,
            promoted=promoted,
            attachments=attachments,
        )
        if write_catalog:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            catalog = self._data_dir / "external_skills_catalog.json"
            catalog.write_text(
                json.dumps(report.to_dict(), indent=2, sort_keys=True),
                encoding="utf-8",
            )
        return report


def grant_manifest_capabilities(
    kernel_registry: Any,
    manifest_path: str | Path = "data/external_skill_attachments.json",
) -> int:
    """Grant L0 capabilities for a persisted attachment manifest at bootstrap."""
    path = Path(manifest_path)
    if not path.exists():
        return 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    granted = 0
    for agent_id, refs in payload.items():
        if kernel_registry.get(agent_id) is None:
            continue
        for ref in refs:
            if not isinstance(ref, list) or len(ref) != 2:
                continue
            name = f"skill-{ref[0]}"[:128]
            kernel_registry.grant_capability(
                agent_id,
                Capability(name=name, max_level=ActionClass.L0_OBSERVATION),
            )
            granted += 1
    return granted


__all__ = [
    "DEFAULT_REPOS",
    "GitHubSkillImportService",
    "RepoReport",
    "RunReport",
    "StudyRecord",
    "grant_manifest_capabilities",
]
