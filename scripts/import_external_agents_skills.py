#!/usr/bin/env python3
"""Import external agent/skill Markdown into the factory registries.

Walks the quarantined clones in ``workspace/inbox/{rstack,ECC,agency-agents,skills}``
and registers each agent/skill source through ``MarkdownImportService`` as an
immutable ``DRAFT`` prompt record (provenance ``import-quarantine:``) in
``data/prompts.db``. ``SKILL.md`` sources are additionally mirrored as executable
``SkillSpec`` drafts in ``data/skills.db`` with a non-executing pointer
implementation, per SPEC_AGENT_FACTORY_V2 §12.3: imports begin in quarantine and
promotion happens only through a separately evidenced review.

Idempotent: files whose content digest is already present in the prompt registry
are skipped.

Usage:
    uv run --python 3.12 --with pyyaml --with jsonschema python scripts/import_external_agents_skills.py [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agency.factory.imports import MarkdownImportService
from agency.factory.prompts import PromptRegistry
from agency.skills.registry import SkillRegistry, SkillSpec, SkillStatus

INBOX = ROOT / "workspace" / "inbox"

REPOS: dict[str, dict[str, str]] = {
    "rstack": {
        "origin": "https://github.com/michaelshimeles/rstack",
        "license": "UNKNOWN",
    },
    "ECC": {
        "origin": "https://github.com/affaan-m/ECC",
        "license": "MIT",
    },
    "agency-agents": {
        "origin": "https://github.com/msitarzewski/agency-agents",
        "license": "MIT",
    },
    "skills": {
        "origin": "https://github.com/mattpocock/skills",
        "license": "MIT",
    },
}

# Directories scanned for agent/skill Markdown, per repo. ``None`` = whole repo.
SCAN_DIRS: dict[str, tuple[str, ...] | None] = {
    "rstack": None,
    "ECC": ("agents", "skills"),
    "agency-agents": None,
    "skills": ("skills",),
}

SKIP_FILENAMES = {"README.md", "CONTRIBUTING.md", "CHANGELOG.md", "LICENSE.md"}
MAX_SOURCE_BYTES = 64 * 1024


@dataclass
class Counts:
    agents: int = 0
    skills: int = 0
    skipped_large: int = 0
    skipped_duplicate: int = 0
    skipped_notmarkdown: int = 0
    failed: int = 0

    def row(self) -> str:
        return (
            f"agents={self.agents} skills={self.skills} "
            f"skip(large={self.skipped_large}, dup={self.skipped_duplicate}, "
            f"not-md={self.skipped_notmarkdown}) failed={self.failed}"
        )


def iter_sources(repo: str) -> list[Path]:
    base = INBOX / repo
    dirs = SCAN_DIRS[repo]
    roots = [base / d for d in dirs] if dirs else [base]
    files: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            if ".git" in path.parts:
                continue
            if path.name.casefold() in {name.casefold() for name in SKIP_FILENAMES}:
                continue
            files.append(path)
    return files


def digest_of(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def known_digests(registry: PromptRegistry) -> set[str]:
    known: set[str] = set()
    for spec in registry.list_specs():
        if spec.provenance.startswith("import-quarantine:"):
            try:
                payload = json.loads(spec.provenance.split(":", 1)[1])
            except (ValueError, IndexError):
                continue
            digest = payload.get("digest")
            if isinstance(digest, str):
                known.add(digest)
    return known


_FM_KEY_LINE = re.compile(r"^(\s*[A-Za-z_][\w-]*:\s+)(\S.*?)\s*$")


def repair_frontmatter(text: str) -> str:
    """Double-quote unquoted scalar frontmatter values containing ': '.

    Upstream files occasionally ship ``description: foo: bar``, which PyYAML
    rejects ("mapping values are not allowed here"). Quoting the value keeps
    the content byte-faithful enough for quarantine while making it parseable.
    """
    lines = text.splitlines(keepends=True)
    if not text.startswith("---"):
        raise ValueError("no frontmatter to repair")
    closing = next((i for i in range(1, min(len(lines), 202)) if lines[i].strip() == "---"), None)
    if closing is None:
        raise ValueError("unterminated frontmatter")
    changed = False
    for i in range(1, closing):
        match = _FM_KEY_LINE.match(lines[i].rstrip("\r\n"))
        if not match:
            continue
        value = match.group(2)
        if value[0] in "\"'[{>|&*!%#@`" or ": " not in value:
            continue
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        lines[i] = f'{match.group(1)}"{escaped}"\n'
        changed = True
    if not changed:
        raise ValueError("no repairable frontmatter line found")
    return "".join(lines)


def parse_source(
    service: MarkdownImportService, path: Path, relative: str, origin: str, license_id: str
):
    kwargs = {
        "source_label": f"{origin}/blob/main/{relative}",
        "license_id": license_id,
        "source_path_override": relative,
        "source_identity": f"{origin}@main|{relative}",
    }
    try:
        return service._parse_markdown(path, **kwargs)
    except Exception as exc:
        if type(exc).__name__ != "ImportError" or "invalid YAML frontmatter" not in str(exc):
            raise
        repaired = repair_frontmatter(path.read_bytes().decode("utf-8-sig"))
        return service._parse_markdown(repaired, **kwargs)


def impl_name(relative_path: str) -> str:
    """Logical operation name for a SkillSpec: never a path, per registry rules."""
    stem = Path(relative_path).stem.casefold()
    parent = Path(relative_path).parent.name.casefold()
    base = f"imported-{parent or 'repo'}-{stem}"
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in base)
    cleaned = cleaned.strip("-._")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"imported-{cleaned}"
    digest = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:10]
    return f"{cleaned[:44]}-{digest}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Parse without registering.")
    parser.add_argument("--prompts-db", default=str(ROOT / "data" / "prompts.db"))
    parser.add_argument("--skills-db", default=str(ROOT / "data" / "skills.db"))
    args = parser.parse_args()

    prompts = PromptRegistry(":memory:" if args.dry_run else args.prompts_db)
    skills = None if args.dry_run else SkillRegistry(args.skills_db)
    service = MarkdownImportService(prompts)
    known = set() if args.dry_run else known_digests(prompts)

    totals = Counts()
    for repo, meta in REPOS.items():
        counts = Counts()
        for path in iter_sources(repo):
            relative = path.relative_to(INBOX).as_posix()
            origin = meta["origin"]
            if path.stat().st_size > MAX_SOURCE_BYTES:
                counts.skipped_large += 1
                continue
            if digest_of(path) in known:
                counts.skipped_duplicate += 1
                continue
            try:
                imported = parse_source(service, path, relative, origin, meta["license"])
            except ImportError:
                counts.skipped_notmarkdown += 1
                continue
            except Exception:  # noqa: BLE001 - one bad file must not stop the import
                counts.failed += 1
                continue

            if imported.source_kind == "skill":
                counts.skills += 1
            elif imported.source_kind == "agent":
                counts.agents += 1
            else:
                counts.failed += 1
                continue

            if not args.dry_run:
                try:
                    prompts.register(imported.candidate)
                except ValueError:
                    counts.skipped_duplicate += 1
                    if imported.source_kind == "agent":
                        counts.agents -= 1
                    else:
                        counts.skills -= 1
                    continue
                known.add(digest_of(path))
                if imported.source_kind == "skill" and skills is not None:
                    try:
                        skills.register(
                            SkillSpec(
                                skill_id=imported.candidate.prompt_id,
                                version="0.1.0",
                                name=imported.candidate.name[:120],
                                description=imported.candidate.description[:500],
                                inputs={},
                                outputs={},
                                permissions=(),
                                implementation=impl_name(relative),
                                provenance=imported.candidate.provenance,
                                status=SkillStatus.DRAFT,
                            )
                        )
                    except ValueError:
                        pass  # already mirrored; quarantine prompt remains authoritative

        print(f"{repo:15s} {counts.row()}")
        for field in totals.__dataclass_fields__:
            setattr(totals, field, getattr(totals, field) + getattr(counts, field))

    print(f"{'TOTAL':15s} {totals.row()}")
    if not args.dry_run:
        print(f"prompts db: {args.prompts_db}")
        print(f"skills db:  {args.skills_db}")
        prompts.close()
        if skills is not None:
            skills.close()
    return 1 if totals.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
