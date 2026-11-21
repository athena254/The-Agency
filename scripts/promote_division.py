#!/usr/bin/env python3
"""Promote imported DRAFT agent records to PUBLISHED, grouped by division.

One division = one SkillSpector scan (the owner-decided reviewer, PLAN
TOOL_PROVISIONING §8.2): every DRAFT record of the division is materialized
into a quarantine bundle, scanned, and only a PASS bundle promotes its
records DRAFT -> TESTING -> APPROVED -> PUBLISHED with the scan as durable
evidence. Records owning a CRITICAL finding stay quarantined even inside a
PASS bundle. The scanner never executes the scanned markdown and this script
never executes upstream code (AGENTS.md rule).

Grouping is derived from each record's import-quarantine provenance (the
imported source path), not from prompt-id prefixes or on-disk clone state: a
record's division is the parent directory of its source path
(agency-agents/engineering/... -> "engineering"), read straight from the
registry — so every quarantined record is covered even when the clones move.

Usage:
    python scripts/promote_division.py --division research [--dry-run]
    python scripts/promote_division.py --all
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agency.factory.prompts import PromptRegistry, PromptStatus
from agency.tools.reviewer import SkillSpectorReviewer, verdict_for_report

MIN_BODY_CHARS = 500
CHUNK = 50  # SkillSpector caps skills per scan; chunk bundles at this size
# Non-agent content the import walk also captured (repo readmes/indexes):
EXCLUDED_DIVISIONS = {
    "README",
    "AGENTS",
    "CLAUDE",
    "changelog",
}  # excludes clone-pointer debris (see AGENCY_AGENTS_IMPORT_REVIEW §2)
APPROVER = "skillspector-reviewer"


def division_records(db_path: str, division: str) -> list[dict]:
    """All DRAFT records of one division, grouped from provenance (source path).

    Grouping comes from the record's own provenance JSON — the source file's
    parent directory (``agency-agents/academic/x.md`` -> "academic") — so it
    covers every quarantined record regardless of on-disk clone state
    (location-derived ids go stale when clones move; provenance does not).
    """
    conn = sqlite3.connect(db_path)
    out: list[dict] = []
    query = (
        "SELECT prompt_id, name, length(body), status, provenance FROM prompts "
        "WHERE status = ? AND provenance LIKE 'import-quarantine:%'"
    )
    for pid, name, body_len, status, provenance in conn.execute(query, ("DRAFT",)):
        payload = json.loads(provenance.split(":", 1)[1])
        source_path = str(payload.get("path", ""))
        parts = source_path.split("/")
        record_division = parts[1] if len(parts) > 2 else parts[0]
        if record_division != division:
            continue
        out.append({"prompt_id": pid, "name": name, "body_len": body_len, "status": status})
    conn.close()
    return out


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
    db_path: str,
    *,
    dry_run: bool,
) -> dict:
    records = division_records(db_path, division)
    eligible = [r for r in records if r["body_len"] >= MIN_BODY_CHARS]
    skipped_debris = len(records) - len(eligible)

    # Dedupe by record name across clones (review finding: 746 overlaps), and
    # keep only records still awaiting promotion — re-runs are idempotent.
    by_name: dict[str, dict] = {}
    for record in eligible:
        by_name.setdefault(record["name"].casefold(), record)
    deduped = sorted(
        (r for r in by_name.values() if r["status"] == "DRAFT"),
        key=lambda r: r["prompt_id"],
    )
    already_published = len(eligible) - sum(1 for r in eligible if r["status"] == "DRAFT")

    result: dict = {
        "division": division,
        "records": len(records),
        "eligible": len(eligible),
        "deduped": len(deduped),
        "skipped_debris": skipped_debris,
        "already_published": already_published,
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
    parser.add_argument(
        "--all",
        action="store_true",
        help="Every quarantined division in the registry (minus non-agent divisions).",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db", default=str(ROOT / "data" / "prompts.db"))
    args = parser.parse_args()

    # Always read the real registry (dry-run differs only in not writing).
    prompts = PromptRegistry(args.db)
    reviewer = SkillSpectorReviewer(extra_args=("--recursive", "--max-skills", str(CHUNK)))
    if not reviewer.is_available():
        print(
            "ERROR: skillspector is not available (pyproject dependency; "
            "uv pip install 'skillspector @ git+https://github.com/NVIDIA/skillspector.git'). "
            "Promotion stays blocked (fail closed).",
            file=sys.stderr,
        )
        return 2

    # Divisions come from the registry's own provenance (source parent dirs),
    # not from the on-disk clones — covers every quarantined record.
    divisions: dict[str, int] = {}
    for pid, prov in prompts._conn_required().execute(
        "SELECT prompt_id, provenance FROM prompts WHERE status = ? "
        "AND provenance LIKE 'import-quarantine:%'",
        (PromptStatus.DRAFT.value,),
    ):
        try:
            payload = json.loads(prov.split(":", 1)[1])
        except (ValueError, IndexError):
            continue
        parts = str(payload.get("path", "")).split("/")
        division = parts[1] if len(parts) > 2 else parts[0]
        divisions[division] = divisions.get(division, 0) + 1
    wanted = (
        sorted(d for d in divisions if d not in EXCLUDED_DIVISIONS)
        if args.all
        else list(args.division or [])
    )
    unknown = [d for d in wanted if d not in divisions]
    if unknown:
        print(f"unknown divisions (no quarantined records): {unknown}", file=sys.stderr)
        return 2

    out_dir = ROOT / "data" / "promotion_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    totals: Counter = Counter()
    for division in wanted:
        result = run_division(prompts, reviewer, division, args.db, dry_run=args.dry_run)
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
