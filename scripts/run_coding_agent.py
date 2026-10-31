"""CLI entry point for the Agency coding agent.

Usage:
    python scripts/run_coding_agent.py --branch <branch> --edits <edits.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agency.agents.coding import AgencyCodingAgent  # noqa: E402


def _load_edits(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("edits", "changes", "operations"):
            if isinstance(data.get(key), list):
                return data[key]
    raise ValueError(f"edits file must contain a JSON list (or object with 'edits'): {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="run_coding_agent.py",
        description="Run the Agency coding agent against a branch with a JSON file of edits.",
    )
    parser.add_argument("--branch", required=True, help="Branch name to use for the isolated worktree.")
    parser.add_argument("--edits", type=Path, default=None, help="Path to a JSON file containing the list of edits.")
    parser.add_argument("--skill", type=Path, default=None, help="Optional path to a skill markdown file to verify first.")
    parser.add_argument("--workspace", type=Path, default=None, help="Optional workspace/repository root.")
    parser.add_argument("--store", type=Path, default=None, help="Optional SQLite store path for persisting the report.")
    args = parser.parse_args()

    edits_path = args.edits.resolve() if args.edits is not None else None
    edits = _load_edits(edits_path) if edits_path is not None else []

    workspace = args.workspace.resolve() if args.workspace is not None else None
    store = args.store.resolve() if args.store is not None else None
    skill = args.skill.resolve() if args.skill is not None else None
    agent = AgencyCodingAgent(workspace=workspace, store_path=store)
    if skill is not None:
        report = agent.run_with_skill(skill, args.branch, edits)
    else:
        report = agent.run_task(args.branch, edits)

    print(f"report_id={report.report_id} gate={report.gate}")


if __name__ == "__main__":
    main()
