"""Combine disjoint parallel work without touching main."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class IntegrationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gate: str
    base: str
    branches: tuple[str, ...]
    overlapping: tuple[str, ...] = ()
    combined: tuple[str, ...] = ()
    note: str = ""


def _git(repo: str | Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def propose_integration(
    base_repo: str | Path,
    branches: list[str],
    *,
    worktree: str | Path,
    output_branch: str,
) -> IntegrationReport:
    """Copy disjoint changed paths from parallel branches into one worktree."""
    base = str(base_repo)
    changed: dict[str, set[str]] = {}
    for branch in branches:
        path_text = _git(base, "diff", "--name-only", f"{output_branch}..{branch}")
        changed[branch] = {p for p in path_text.splitlines() if p}
    seen: set[str] = set()
    overlaps: set[str] = set()
    for branch_paths in changed.values():
        overlaps |= seen & branch_paths
        seen |= branch_paths
    if overlaps:
        return IntegrationReport(
            gate="FAIL",
            base=base,
            branches=tuple(branches),
            overlapping=tuple(sorted(overlaps)),
            combined=(),
            note="overlapping changes",
        )

    work = Path(worktree)
    work.mkdir(parents=True, exist_ok=True)
    combined: list[str] = []
    for branch, paths in changed.items():
        for path in sorted(paths):
            content = _git(base, "show", f"{branch}:{path}")
            if not content:
                continue
            target = work / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            combined.append(path)
    subprocess.run(["git", "-C", str(work), "add", "-A"], capture_output=True, check=False)
    subprocess.run(
        ["git", "-C", str(work), "commit", "-qm", f"integrate {len(branches)} parallel branches"],
        capture_output=True,
        check=False,
    )
    return IntegrationReport(
        gate="PASS",
        base=base,
        branches=tuple(branches),
        overlapping=(),
        combined=tuple(sorted(set(combined))),
        note="disjoint work combined",
    )
