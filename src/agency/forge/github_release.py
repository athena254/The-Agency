"""Guarded GitHub tag + release planning and execution.

Releasing is an owner-scoped action. Nothing is tagged, pushed, or published
unless every gate is PASS, a named human approver is recorded, the target
repository is explicitly allow-listed, and the worktree is clean. Dry-run is
the default and never mutates anything: it returns the exact commands that
would be run as strings.

Execution uses argument lists only (never a shell), never force-pushes,
never deletes tags, and never adds ``--draft`` implicitly.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)

GithubReleaseGate = Literal["PASS", "FAIL", "BLOCKED"]

CAVEATS: tuple[str, ...] = (
    "release_authorization_is_owner_scoped",
    "release_not_authorized",
)

_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.-]+)?$")
_NON_HUMAN_APPROVERS = frozenset({"agent", "forge", "auto", "system", "cli", "bot"})
_MAX_TITLE = 200
_MAX_NOTES = 2000
_ALLOWED_REPOS_ENV = "AGENCY_FORGE_ALLOWED_RELEASE_REPOS"


class GithubReleaseReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(min_length=1)
    created_at: datetime
    root: str = ""
    tag: str = ""
    repo: str = ""
    remote: str = "origin"
    source_sha: str = ""
    gate: GithubReleaseGate = "BLOCKED"
    created: bool = False
    dry_run: bool = True
    approved_by: str = ""
    blocked_reasons: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    caveats: tuple[str, ...] = CAVEATS
    note: str = (
        "Owner-scoped GitHub release: all gates PASS, named human approval, "
        "allow-listed repo, clean worktree. Dry-run by default; no force-push, "
        "no tag deletion, no implicit draft."
    )


def _gate_value(obj: Any) -> str:
    """Mirror ship.py's gate coercion: objects with .gate, strings, bools, dicts."""
    if obj is None:
        return "FAIL"
    if isinstance(obj, bool):
        return "PASS" if obj else "FAIL"
    if isinstance(obj, str):
        return obj.upper()
    gate = getattr(obj, "gate", None)
    if gate is not None:
        return str(gate).upper()
    if isinstance(obj, dict):
        if "gate" in obj:
            return str(obj["gate"]).upper()
        if "status" in obj:
            return str(obj["status"]).upper()
    return str(obj).upper()


def _gate_values(gates: Any) -> tuple[str, ...]:
    if gates is None:
        return ("FAIL",)
    if isinstance(gates, dict):
        values = tuple(_gate_value(value) for value in gates.values())
        return values or ("FAIL",)
    if isinstance(gates, (list, tuple, set, frozenset)):
        values = tuple(_gate_value(value) for value in gates)
        return values or ("FAIL",)
    return (_gate_value(gates),)


def _normalize_repo(value: str) -> str:
    name = str(value).strip().lower()
    if "://" in name:
        name = name.split("://", 1)[1]
    if name.startswith("git@"):
        name = name.split(":", 1)[1]
    name = name.removeprefix("github.com/").removesuffix(".git")
    return name.strip("/")


def _allowed_repos(allow_repos: Any) -> tuple[str, ...]:
    entries: list[str] = []
    if isinstance(allow_repos, str):
        entries.extend(allow_repos.split(","))
    elif allow_repos is not None:
        try:
            entries.extend(str(item) for item in allow_repos)
        except TypeError:
            entries.append(str(allow_repos))
    entries.extend(os.environ.get(_ALLOWED_REPOS_ENV, "").split(","))
    return tuple(item.strip() for item in entries if item.strip())


def _git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _append_proof(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


def _caveats(dry_run: bool, gate: str, authorized: bool) -> tuple[str, ...]:
    caveats = ["release_authorization_is_owner_scoped"]
    if not dry_run and gate == "PASS" and authorized:
        caveats.append("human_approval_recorded")
    else:
        caveats.append("release_not_authorized")
    return tuple(caveats)


def plan_github_release(
    root: str | Path,
    tag: str,
    *,
    gates: Any = None,
    approved_by: str = "",
    remote: str = "origin",
    repo: str = "",
    dry_run: bool = True,
    allow_repos: Any = None,
    sha: str = "",
    title: str = "",
    notes: str = "",
    proof_log: str | Path | None = None,
) -> GithubReleaseReport:
    """Plan (and optionally execute) an owner-authorized GitHub release."""
    root_path = Path(root)
    tag = str(tag)
    repo = str(repo).strip()
    remote = str(remote).strip() or "origin"
    approved_by = str(approved_by).strip()
    dry_run = bool(dry_run)
    requested_sha = str(sha).strip()

    blocked: list[str] = []
    head_sha = ""
    report_id = str(uuid4())

    # Cheap, side-effect-free preconditions first; if any fails, never touch git.
    if not _TAG_RE.fullmatch(tag):
        blocked.append("invalid_tag_format")
    allowed = _allowed_repos(allow_repos)
    normalized_repo = _normalize_repo(repo)
    if not repo:
        blocked.append("repo_missing")
    elif not any(_normalize_repo(entry) == normalized_repo for entry in allowed):
        blocked.append("repo_not_allowlisted")
    if not approved_by:
        blocked.append("approval_missing")
    elif approved_by.lower() in _NON_HUMAN_APPROVERS:
        blocked.append("approver_not_human")
    gate_values = _gate_values(gates)
    if any(value != "PASS" for value in gate_values):
        not_pass = sorted({value for value in gate_values if value != "PASS"})
        blocked.append("gates_not_pass:" + ",".join(not_pass))

    if not blocked:
        # Read-only git checks: HEAD, cleanliness, and tag collisions.
        head = _git(root_path, ["rev-parse", "HEAD"])
        if head.returncode != 0:
            blocked.append("head_unknown")
        else:
            head_sha = head.stdout.strip()
            if not head_sha:
                blocked.append("head_unknown")
            elif requested_sha and requested_sha != head_sha:
                blocked.append("sha_mismatch")

        status = _git(root_path, ["status", "--porcelain"])
        if status.returncode != 0:
            blocked.append("git_status_failed")
        elif status.stdout.strip():
            blocked.append("working_tree_dirty")

        local_tag = _git(root_path, ["rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"])
        if local_tag.returncode == 0:
            blocked.append("tag_exists_locally")

        remote_tag = _git(
            root_path, ["ls-remote", "--exit-code", "--tags", remote, f"refs/tags/{tag}"]
        )
        if remote_tag.returncode == 0:
            blocked.append("tag_exists_on_remote")

    release_title = (str(title).strip() or tag)[:_MAX_TITLE]
    release_notes = (str(notes).strip() or f"Release {tag}")[:_MAX_NOTES]

    command_specs: list[list[str]] = [
        ["git", "-C", str(root_path), "tag", "-a", tag, "-m", tag],
        ["git", "-C", str(root_path), "push", remote, f"refs/tags/{tag}:refs/tags/{tag}"],
        [
            "gh",
            "release",
            "create",
            tag,
            "--repo",
            repo,
            "--title",
            release_title,
            "--notes",
            release_notes,
            "--verify-tag",
        ],
    ]
    # Display strings mirror the argv lists; execution always uses the lists.
    planned_commands = tuple(" ".join(spec) for spec in command_specs)

    created = False
    commands: tuple[str, ...] = ()
    gate: GithubReleaseGate

    if blocked:
        gate = "BLOCKED"
    elif dry_run:
        gate = "PASS"
        commands = planned_commands
    else:
        # gh availability is checked only when executing; dry-run stays offline.
        if shutil.which("gh") is None:
            blocked.append("gh_cli_unavailable")
        else:
            auth = subprocess.run(
                ["gh", "auth", "status"], capture_output=True, text=True, check=False
            )
            if auth.returncode != 0:
                blocked.append("gh_auth_status_failed")
        if blocked:
            gate = "BLOCKED"
        else:
            executed: list[str] = []
            for spec in command_specs:
                proc = subprocess.run(spec, capture_output=True, text=True, check=False)
                executed.append(" ".join(spec))
                if proc.returncode != 0:
                    blocked.append(f"command_failed:{spec[0]}")
                    break
            commands = tuple(executed)
            if blocked:
                gate = "FAIL"
            else:
                created = True
                gate = "PASS"
                proof_path = (
                    Path(proof_log) if proof_log else (root_path / ".forge-release-proof.jsonl")
                )
                try:
                    _append_proof(
                        proof_path,
                        {
                            "event": "github_release",
                            "report_id": report_id,
                            "tag": tag,
                            "sha": head_sha,
                            "repo": repo,
                            "approver": approved_by,
                            "gate": gate,
                        },
                    )
                except OSError:
                    blocked.append("proof_append_failed")
                    gate = "FAIL"

    authorized = bool(approved_by and approved_by.lower() not in _NON_HUMAN_APPROVERS and allowed)
    report = GithubReleaseReport(
        report_id=report_id,
        created_at=datetime.now(UTC),
        root=str(root_path),
        tag=tag,
        repo=repo,
        remote=remote,
        source_sha=head_sha,
        gate=gate,
        created=created,
        dry_run=dry_run,
        approved_by=approved_by,
        blocked_reasons=tuple(blocked),
        commands=commands,
        caveats=_caveats(dry_run, gate, authorized),
    )
    logger.info(
        "forge.github_release",
        gate=gate,
        tag=tag,
        created=created,
        dry_run=dry_run,
        blocked=len(blocked),
    )
    return report


plan_github_release_report = plan_github_release

__all__ = [
    "CAVEATS",
    "GithubReleaseGate",
    "GithubReleaseReport",
    "plan_github_release",
    "plan_github_release_report",
]
