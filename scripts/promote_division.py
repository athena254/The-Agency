#!/usr/bin/env python3
"""Promote imported DRAFT agent records to PUBLISHED, grouped by division.

One division = one SkillSpector scan (the owner-decided reviewer, PLAN
TOOL_PROVISIONING §8.2): every DRAFT record of the division is materialized
into a quarantine bundle, scanned, and only a PASS bundle promotes its
records DRAFT -> TESTING -> APPROVED -> PUBLISHED with the scan as durable
evidence. Records owning a CRITICAL finding stay quarantined even inside a
PASS bundle. The scanner never executes the scanned markdown and this script
never executes upstream code (AGENTS.md rule).

Grouping is derived from the on-disk clones (workspace/inbox/<clone>/...),
not from prompt-id prefixes: a record's division is the parent directory of
its source file (agency-agents/engineering/... -> "engineering"), matched
back to the registry through the importer's deterministic id construction
(`factory/imports.py::_candidate_id`).

Usage:
    python scripts/promote_division.py --division research [--dry-run]
    python scripts/promote_division.py --all
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agency.factory.prompts import PromptRegistry, PromptStatus  # noqa: E402
from agency.tools.reviewer import SkillSpectorReviewer, verdict_for_report  # noqa: E402

MIN_BODY_CHARS = 500
CHUNK = 50  # SkillSpector caps skills per scan; chunk bundles at this size  # excludes clone-pointer debris (see AGENCY_AGENTS_IMPORT_REVIEW §2)
APPROVER = "skillspector-reviewer"
CLONES = ("rstack", "ECC", "agency-agents", "skills")
# Must mirror scripts/import_external_agents_skills.py REPOS — the prompt id
# derives from sha12(f"{origin}@main|{relative}"), so origins are load-bearing.
ORIGINS = {
    "rstack": "https://github.com/michaelshimeles/rstack",
    "ECC": "https://github.com/affaan-m/ECC",
    "agency-agents": "https://github.com/msitarzewski/agency-agents",
    "skills": "https://github.com/mattpocock/skills",
}


def candidate_id(source_identity: str, stem: str) -> str:
    """Replicate ``factory/imports.py::_candidate_id`` (id = slug + sha12 of location)."""
    slug = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in stem.casefold())
    slug = slug.strip("-._")[:44] or "imported-markdown"
    identity = hashlib.sha256(source_identity.encode("utf-8")).hexdigest()[:12]
    base = f"{slug}-{identity}"
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in base)
    cleaned = cleaned.strip("-._")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"imported-{cleaned}"
    return cleaned[:110]


def collect_divisions(inbox: Path) -> dict[str, dict[str, list[Path]]]:
    """Map division -> {clone: [markdown files]}, from the on-disk clones."""
    out: dict[str, dict[str, list[Path]]] = {}
    for clone in CLONES:
        base = inbox / clone
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            relative = path.relative_to(inbox).as_posix()
            parts = Path(relative).parts
            division = parts[1] if len(parts) > 2 else clone  # <clone>/<division>/file.md
            out.setdefault(division, {}).setdefault(clone, []).append(path)
    return out


def division_records(
    prompts: PromptRegistry, files: dict[str, list[Path]], division: str
) -> list[dict]:
    """Registry rows for a division's files, via the deterministic id construction."""
    records: list[dict] = []
    seen_ids: set[str] = set()
    for clone, paths in sorted(files.items()):
        for path in paths:
            relative = path.relative_to(ROOT / "workspace" / "inbox").as_posix()
            source_identity = f"{ORIGINS[clone]}@main|{relative}"
            pid = candidate_id(source_identity, Path(relative).stem)
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            try:
                spec = prompts.get(pid, "0.0.0")
            except (KeyError, ValueError):
                continue
            records.append(
                {
                    "prompt_id": pid,
                    "name": spec.name,
                    "body_len": len(spec.body),
                    "source": relative,
                }
            )
    return records


def materialize(records: list[dict], bundle: Path, prompts: PromptRegistry) -> None:
    for record in records:
        spec = prompts.get(record["prompt_id"], "0.0.0")
        target = bundle / record["prompt_id"]
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(spec.body, encoding="utf-8")


def promote_record(prompts: PromptRegistry, prompt_id: str, evidence: str) -> None:
    prompts.transition(prompt_id, "0.0.0", PromptStatus.TESTING, evidence)
    prompts.transition(
        prompt_id,
        "0.0.0",
        PromptStatus.APPROVED,
        f"{evidence}; batch recommendation PASS",
    )
    prompts.publish(
        prompt_id,
        "0.0.0",
        f"{evidence}; owner directive 2026-11-21: system-reviewed division promotion",
        approver=APPROVER,
    )


def run_division(
    prompts: PromptRegistry,
    reviewer: SkillSpectorReviewer,
    division: str,
    files: dict[str, list[Path]],
    *,
    dry_run: bool,
) -> dict:
    records = division_records(prompts, files, division)
    eligible = [r for r in records if r["body_len"] >= MIN_BODY_CHARS]
    skipped_debris = len(records) - len(eligible)

    # Dedupe by record name across clones (review finding: 746 overlaps).
    by_name: dict[str, dict] = {}
    for record in eligible:
        by_name.setdefault(record["name"].casefold(), record)
    deduped = sorted(by_name.values(), key=lambda r: r["prompt_id"])

    result: dict = {
        "division": division,
        "records": len(records),
        "eligible": len(eligible),
        "deduped": len(deduped),
        "skipped_debris": skipped_debris,
        "promoted": [],
        "critical_quarantined": [],
        "bundle_verdict": "",
        "scan_summary": "",
        "chunks": 0,
        "dry_run": dry_run,
    }
    if not deduped:
        result["bundle_verdict"] = "EMPTY"
        return result

    # SkillSpector caps skills per scan — chunk the bundle, gate each chunk,
    # and attribute verdicts per record via the embedded per-skill sub-reports.
    chunks = [deduped[i : i + CHUNK] for i in range(0, len(deduped), CHUNK)]
    result["chunks"] = len(chunks)
    with tempfile.TemporaryDirectory(prefix="division-scan-") as tmp:
        for index, chunk in enumerate(chunks, start=1):
            bundle = Path(tmp) / f"{division}-{index:03d}"
            materialize(chunk, bundle, prompts)
            verdict = reviewer.review(bundle)
            result["bundle_verdict"] = verdict.verdict
            result["scan_summary"] = (
                f"chunk {index}/{len(chunks)}: {verdict.findings_summary}; "
                + result["scan_summary"]
            )
            if verdict.verdict != "PASS":
                continue

            # Recursive bundles embed one full single-skill report per record:
            # attribute verdicts per record (CRITICAL anywhere → quarantined;
            # the chunk gate already cleared BLOCK/executable-HIGH).
            per_record = {}
            for skill_report in verdict.report_json.get("skills", []) or []:
                if isinstance(skill_report, dict):
                    per_record[skill_report.get("name", "")] = verdict_for_report(skill_report)
            critical_ids = {
                record["prompt_id"]
                for record in chunk
                if per_record.get(record["prompt_id"], verdict).verdict == "FAIL"
            }
            result["critical_quarantined"].extend(sorted(critical_ids))

            scan_evidence = (
                f"skillspector chunk {index}/{len(chunks)} scan {verdict.score}/100 "
                f"{verdict.verdict}: {verdict.findings_summary}"
            )
            for record in chunk:
                if record["prompt_id"] in critical_ids:
                    continue
                if dry_run:
                    result["promoted"].append(record["prompt_id"])
                    continue
                promote_record(prompts, record["prompt_id"], scan_evidence)
                result["promoted"].append(record["prompt_id"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--division", action="append", help="Division name (repeatable).")
    parser.add_argument("--all", action="store_true", help="Every division found in the clones.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db", default=str(ROOT / "data" / "prompts.db"))
    parser.add_argument("--inbox", default=str(ROOT / "workspace" / "inbox"))
    args = parser.parse_args()

    inbox = Path(args.inbox)
    # Always read the real registry (dry-run differs only in not writing).
    prompts = PromptRegistry(args.db)
    reviewer = SkillSpectorReviewer(
        extra_args=("--recursive", "--max-skills", str(CHUNK))
    )
    if not reviewer.is_available():
        print(
            "ERROR: skillspector is not available (pyproject dependency; "
            "uv pip install 'skillspector @ git+https://github.com/NVIDIA/skillspector.git'). "
            "Promotion stays blocked (fail closed).",
            file=sys.stderr,
        )
        return 2

    clone_map = collect_divisions(inbox)
    wanted = sorted(clone_map) if args.all else list(args.division or [])
    unknown = [d for d in wanted if d not in clone_map]
    if unknown:
        print(f"unknown divisions: {unknown}", file=sys.stderr)
        return 2

    out_dir = ROOT / "data" / "promotion_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    totals: Counter = Counter()
    for division in wanted:
        result = run_division(
            prompts, reviewer, division, clone_map[division], dry_run=args.dry_run
        )
        totals["records"] += result["records"]
        totals["eligible"] += result["eligible"]
        totals["deduped"] += result["deduped"]
        totals["promoted"] += len(result["promoted"])
        totals["critical_quarantined"] += len(result["critical_quarantined"])
        print(
            f"{division:24} records={result['records']:4} eligible={result['eligible']:4} "
            f"deduped={result['deduped']:4} verdict={result['bundle_verdict']:11} "
            f"promoted={len(result['promoted']):4} "
            f"critical_q={len(result['critical_quarantined']):2} "
            f"scan={result['scan_summary'][:70]}"
        )
        if not args.dry_run:
            (out_dir / f"{division}.json").write_text(
                json.dumps(result, indent=2), encoding="utf-8"
            )
    print(f"TOTAL {dict(totals)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
