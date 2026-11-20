#!/usr/bin/env python3
"""Import, study, dedupe, and install GitHub skill/agent Markdown.

Usage:
    uv run python scripts/import_github_skills.py [--dry-run] [--promote]
    uv run python scripts/import_github_skills.py --repos URL [URL ...] --promote
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agency.factory.github_skills import (
    DEFAULT_REPOS,
    GitHubSkillImportService,
)
from agency.factory.prompts import PromptRegistry
from agency.skills.registry import SkillRegistry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="import and study without writing")
    parser.add_argument("--promote", action="store_true", help="publish reviewed skill drafts")
    parser.add_argument(
        "--from-registry",
        action="store_true",
        help="study prompts already quarantined in the registry (no network)",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="write a consolidated catalog/manifest without importing or writing skills",
    )
    parser.add_argument(
        "--discover-new",
        action="store_true",
        help="print repos whose tree SHA is new/changed (no import, no writes)",
    )
    parser.add_argument(
        "--emit-plan",
        metavar="PATH",
        help="with --discover-new: write the pending repo list for `agency loop run repos:PATH`",
    )
    parser.add_argument("--approver", default="owner")
    parser.add_argument("--repos", nargs="*", default=list(DEFAULT_REPOS))
    parser.add_argument("--prompts-db", default=str(ROOT / "data" / "prompts.db"))
    parser.add_argument("--skills-db", default=str(ROOT / "data" / "skills.db"))
    parser.add_argument("--data-dir", default=str(ROOT / "data"))
    args = parser.parse_args()
    if args.emit_plan and not args.discover_new:
        parser.error("--emit-plan requires --discover-new")

    if args.discover_new:
        discovery_prompts = PromptRegistry(args.prompts_db)
        discovery = GitHubSkillImportService(
            discovery_prompts,
            SkillRegistry(":memory:"),
        )
        try:
            pending = discovery.discover_new_repos(tuple(args.repos))
        finally:
            discovery.close()
            discovery_prompts.close()
        if args.emit_plan:
            plan = Path(args.emit_plan)
            plan.parent.mkdir(parents=True, exist_ok=True)
            plan.write_text("\n".join(pending) + "\n", encoding="utf-8")
            print(f"wrote {len(pending)} repos to {plan}")
        else:
            for repo in pending:
                print(repo)
        return 0

    prompts = PromptRegistry(":memory:" if args.dry_run else args.prompts_db)
    skills = SkillRegistry(":memory:" if args.dry_run else args.skills_db)
    service = GitHubSkillImportService(
        prompts,
        skills,
        approver=args.approver,
        data_dir=args.data_dir,
    )
    try:
        report = service.run(
            tuple(args.repos),
            promote=args.promote,
            evidence=(
                "Automated import review: inert Markdown parsed under quarantine bounds; "
                "source provenance and license preserved; no upstream code executed."
            ),
            write_catalog=not args.dry_run,
            from_registry=args.from_registry,
            report_only=args.report_only,
        )
    finally:
        service.close()
        prompts.close()
        skills.close()

    print("REPOS")
    for repo in report.repos:
        if repo.error:
            print(f"  ERROR {repo.repo}: {repo.error}")
        else:
            print(f"  OK    {repo.repo}: prompts={repo.prompts} skills={repo.skills}")
    canonical = sum(1 for rec in report.records if rec.decision == "canonical")
    duplicates = sum(1 for rec in report.records if rec.decision == "duplicate")
    print(f"STUDY canonical={canonical} duplicates={duplicates}")
    print(
        f"SKILLS new={len(report.new_skills)} improved={len(report.improved_skills)} promoted={len(report.promoted)}"
    )
    print("ATTACHMENTS")
    for agent, refs in report.attachments.items():
        print(f"  {agent}: {len(refs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
