"""First guarded self-improvement loop for The Agency.

Observes bounded problems in Forge reports, drafts issues, and bridges an
accepted issue to a queued job. It never publishes without named human
approval, never dispatches a job itself, and never merges or releases.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agency.forge.correlation import make_key
from agency.forge.orchestration import BlacksmithOps, ForgeJob

MAX_DETAIL_CHARS = 1000
ALLOW_REPOS_ENV = "AGENCY_FORGE_ALLOWED_ISSUE_REPOS"
_NON_HUMAN = frozenset({"agent", "forge", "auto", "system", "cli", "bot", "none"})


class ProblemCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    source: str
    title: str = Field(max_length=200)
    detail: str = Field(default="", max_length=MAX_DETAIL_CHARS)
    key: str = ""


class IssueDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    repo: str
    title: str = Field(max_length=200)
    body: str = Field(max_length=4000)
    key: str
    dry_run: bool = True
    commands: tuple[str, ...] = ()


class PublishReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    issue_url: str = ""
    repo: str
    key: str
    gate: str
    published: bool = False
    approved_by: str = ""
    blocked_reasons: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ("no_auto_merge", "no_release_authorization")


def _allowed_repos() -> tuple[str, ...]:
    return tuple(
        part.strip().lower()
        for part in os.environ.get(ALLOW_REPOS_ENV, "").split(",")
        if part.strip()
    )


def detect_problems(reports: list[dict[str, Any]]) -> tuple[ProblemCandidate, ...]:
    """Turn failing Forge reports into bounded, deduplicated candidates."""
    seen: set[str] = set()
    found: list[ProblemCandidate] = []
    for report in reports:
        gate = str(report.get("gate", "")).upper()
        if gate not in ("FAIL", "ERROR"):
            continue
        task_id = str(report.get("task_id") or "").strip()
        source = str(report.get("source") or report.get("kind") or "forge_report").strip()
        title = f"Forge gate {gate}: {task_id or source}"[:200]
        detail = str(report.get("note") or report.get("failure_detail") or "")[:MAX_DETAIL_CHARS]
        key = make_key(task_id or source, f"issue:{gate}:{detail[:80]}")
        if key in seen:
            continue
        seen.add(key)
        found.append(
            ProblemCandidate(
                task_id=task_id or key[:8],
                source=source[:80],
                title=title,
                detail=detail,
                key=key,
            )
        )
    return tuple(found)


def plan_issue(candidate: ProblemCandidate, *, repo: str) -> IssueDraft:
    """Produce an exact command plan; never contact GitHub."""
    body = (
        "## Observed problem\n\n"
        f"{candidate.detail or 'Forge gate failed.'}\n\n"
        f"Source: `{candidate.source}`  \n"
        f"Correlation key: `{candidate.key}`\n"
    )[:4000]
    command = (
        "gh issue create --repo "
        + repo
        + " --title "
        + json.dumps(candidate.title)
        + " --body-file -"
    )
    return IssueDraft(
        repo=repo,
        title=candidate.title,
        body=body,
        key=candidate.key,
        commands=(command,),
    )


def publish_issue(
    draft: IssueDraft,
    *,
    approved_by: str = "",
    dry_run: bool = True,
    proof_log: str | Path | None = None,
) -> PublishReport:
    """Publish only with named human approval, allow-listed repo and execute=true."""
    blocked: list[str] = []
    approver = str(approved_by).strip()
    if not approver or approver.lower() in _NON_HUMAN:
        blocked.append("human_approval_required")
    if draft.repo.lower() not in _allowed_repos():
        blocked.append("repo_not_allowlisted")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", draft.repo):
        blocked.append("invalid_repo")
    if not dry_run and not blocked:
        try:
            proc = subprocess.run(
                [
                    "gh",
                    "issue",
                    "create",
                    "--repo",
                    draft.repo,
                    "--title",
                    draft.title,
                    "--body",
                    draft.body,
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )
            url = proc.stdout.strip().splitlines()[-1]
        except (OSError, subprocess.SubprocessError):
            blocked.append("issue_create_failed")
            url = ""
    else:
        url = ""
    proof = Path(proof_log) if proof_log else Path("data/forge_self_improve_proof.jsonl")
    if not dry_run and not blocked:
        proof.parent.mkdir(parents=True, exist_ok=True)
        with proof.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "key": draft.key,
                        "repo": draft.repo,
                        "issue_url": url,
                        "approved_by": approver,
                        "at": datetime.now(UTC).isoformat(),
                    }
                )
                + "\n"
            )
    return PublishReport(
        issue_url=url,
        repo=draft.repo,
        key=draft.key,
        gate="BLOCKED" if blocked else "PASS",
        published=bool(not dry_run and not blocked),
        approved_by=approver,
        blocked_reasons=tuple(blocked),
    )


def bridge_issue_to_job(
    ops: BlacksmithOps,
    *,
    issue_url: str,
    repo_path: str,
    task_id: str,
    title: str,
) -> ForgeJob:
    """Create a queued job for an accepted issue; never dispatch it here."""
    path = Path(repo_path)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("repo_path must be an absolute existing directory")
    if not issue_url.strip():
        raise ValueError("issue_url is required")
    return ops.submit(
        title=title[:200],
        task=f"Address issue {issue_url} (task {task_id}).",
        repo=str(path),
    )
