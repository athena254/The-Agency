"""Forge gated remote merge — push a verified branch to a remote, never by default.

Owner-scoped and fail-closed. Nothing is written to a remote unless:

* every Forge gate (inspection, security, tests, review) is PASS,
* a named human approver is supplied (generic agent identities are refused),
* the remote is on the explicit allow-list,
* the working tree is clean, and
* ``execute=True`` is passed on top of the default dry-run.

No force push, no deletion, no tag or release side effects. Every report
repeats its caveats so a PASS can never be mistaken for a release approval.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)

CAVEATS: tuple[str, ...] = (
    "remote_write_capability",
    "human_approval_required",
    "release_not_authorized",
)
_DENIED_APPROVERS = frozenset({"agent", "forge", "auto", "system", "cli", "bot", "none"})
_MAX_OUTPUT_CHARS = 2_000


def _gate_value(obj: Any) -> str:
    if obj is None:
        return "FAIL"
    if isinstance(obj, bool):
        return "PASS" if obj else "FAIL"
    if isinstance(obj, str):
        return obj.upper()
    if isinstance(obj, dict):
        for key in ("gate", "status"):
            if key in obj:
                return str(obj[key]).upper()
    gate = getattr(obj, "gate", None)
    if gate is not None:
        return str(gate).upper()
    return str(obj).upper()


def _git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _bound(text: str) -> str:
    return text[-_MAX_OUTPUT_CHARS:] if len(text) > _MAX_OUTPUT_CHARS else text


def _allow_list(explicit: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if explicit:
        return tuple(str(x).strip() for x in explicit if str(x).strip())
    return tuple(
        part.strip()
        for part in os.environ.get("AGENCY_FORGE_ALLOWED_REMOTES", "").split(",")
        if part.strip()
    )


class RemoteMergeReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(min_length=1)
    created_at: datetime
    root: str = ""
    remote: str = ""
    source_branch: str = ""
    target_branch: str = ""
    source_sha: str = ""
    target_sha: str = ""
    gate: str
    pushed: bool = False
    dry_run: bool = True
    approved_by: str = ""
    blocked_reasons: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    stdout: str = ""
    stderr: str = ""
    proof_path: str = ""
    caveats: tuple[str, ...] = CAVEATS
    note: str = (
        "Gated remote fast-forward push. Human approval and an allow-listed "
        "remote are mandatory. No release is authorized."
    )


def plan_remote_merge(
    root: str | Path,
    remote: str,
    target_branch: str,
    *,
    pipeline_gate: Any = None,
    security_gate: Any = None,
    test_gate: Any = None,
    review_gate: Any = None,
    gates: dict[str, Any] | None = None,
    approved_by: str = "",
    execute: bool = False,
    dry_run: bool = True,
    allow_remotes: list[str] | tuple[str, ...] | None = None,
    source_branch: str = "",
    proof_log: str | Path | None = None,
    changed_paths: list[str] | None = None,
) -> RemoteMergeReport:
    """Plan — and optionally execute — a gated fast-forward remote push.

    The default is a dry run. ``execute=True`` is required for any write.
    """
    repo = Path(root).resolve()
    report_id = str(uuid4())
    remote = str(remote).strip()
    display_remote = re.sub(r"(://)[^/@]+@", r"\1***@", remote)
    target_branch = str(target_branch).strip()
    gates = gates or {}
    gate_values = {
        "pipeline": _gate_value(gates.get("pipeline", pipeline_gate)),
        "security": _gate_value(gates.get("security", security_gate)),
        "tests": _gate_value(gates.get("tests", test_gate)),
        "review": _gate_value(gates.get("review", review_gate)),
    }
    approval = str(approved_by or "").strip()
    allowed = _allow_list(allow_remotes)
    doing_write = bool(execute and not dry_run)
    blocked: list[str] = []
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    source_sha = ""
    target_sha = ""

    fetch_cmd = f"git -C {repo} fetch {display_remote} refs/heads/{target_branch}"
    ff_cmd = f"git -C {repo} merge-base --is-ancestor FETCH_HEAD HEAD"
    push_cmd = f"git -C {repo} push {display_remote} HEAD:refs/heads/{target_branch}"
    commands = (fetch_cmd, ff_cmd, push_cmd)

    if any(value != "PASS" for value in gate_values.values()):
        blocked.extend(
            f"gate_not_pass:{name}={value}"
            for name, value in gate_values.items()
            if value != "PASS"
        )
    if not approval or approval.lower() in _DENIED_APPROVERS:
        blocked.append("human_approval_required")
    if remote not in allowed:
        blocked.append("remote_not_allow_listed")
    if "@" in remote or "://" in remote and not remote.startswith(("http://", "https://")):
        blocked.append("remote_credential_or_scheme_invalid")
    if not remote or not target_branch:
        blocked.append("remote_or_target_missing")
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", target_branch or "x"):
        blocked.append("invalid_target_branch")

    if not repo.is_dir():
        blocked.append("root_not_a_directory")
    else:
        status = _git(repo, ["status", "--porcelain"])
        if status.returncode != 0:
            blocked.append("git_status_failed")
        elif status.stdout.strip():
            blocked.append("working_tree_dirty")
        head = _git(repo, ["rev-parse", "HEAD"])
        if head.returncode != 0:
            blocked.append("head_unresolved")
        else:
            source_sha = head.stdout.strip()
        if not source_branch:
            branch = _git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
            source_branch = branch.stdout.strip() if branch.returncode == 0 else ""
        if source_branch and source_branch == target_branch:
            blocked.append("source_equals_target_branch")

    if not blocked:
        fetch = _git(repo, ["fetch", remote, f"refs/heads/{target_branch}"])
        stdout_parts.append(_bound(fetch.stdout))
        stderr_parts.append(_bound(fetch.stderr))
        if fetch.returncode != 0:
            blocked.append("remote_fetch_failed")
        else:
            tsha = _git(repo, ["rev-parse", "FETCH_HEAD"])
            target_sha = tsha.stdout.strip() if tsha.returncode == 0 else ""
            ff = _git(repo, ["merge-base", "--is-ancestor", "FETCH_HEAD", "HEAD"])
            if ff.returncode != 0:
                blocked.append("non_fast_forward")
            elif changed_paths:
                reviewed = {str(p).replace("\\", "/").removeprefix("./") for p in changed_paths}
                diff = _git(repo, ["diff", "--name-only", "FETCH_HEAD..HEAD"])
                if diff.returncode == 0:
                    unreviewed = sorted(
                        line.strip().replace("\\", "/")
                        for line in diff.stdout.splitlines()
                        if line.strip() and line.strip().replace("\\", "/") not in reviewed
                    )
                    if unreviewed:
                        blocked.append("unreviewed_paths_in_merge")

    if doing_write and not blocked:
        push = _git(repo, ["push", remote, f"HEAD:refs/heads/{target_branch}"])
        stdout_parts.append(_bound(push.stdout))
        stderr_parts.append(_bound(push.stderr))
        if push.returncode != 0:
            blocked.append("remote_push_failed")

    proof_path = Path(proof_log) if proof_log else (repo / ".forge-remote-merge-proof.jsonl")
    if doing_write and not blocked:
        proof_path.parent.mkdir(parents=True, exist_ok=True)
        with open(proof_path, "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "report_id": report_id,
                        "event": "remote_merge",
                        "remote": display_remote,
                        "source_branch": source_branch,
                        "target_branch": target_branch,
                        "source_sha": source_sha,
                        "target_sha": target_sha,
                        "approved_by": approval,
                        "gates": gate_values,
                    }
                )
                + "\n"
            )

    pushed = bool(doing_write and not blocked)
    gate = "BLOCKED" if blocked else ("PASS" if pushed or not doing_write else "FAIL")
    caveats = CAVEATS + (("dry_run",) if not doing_write else ("human_approval_recorded",))
    logger.info(
        "forge.remote_merge",
        gate=gate,
        remote=display_remote,
        target=target_branch,
        dry_run=not doing_write,
        pushed=pushed,
        blocked=blocked,
    )
    return RemoteMergeReport(
        report_id=report_id,
        created_at=datetime.now(UTC),
        root=str(repo),
        remote=display_remote,
        source_branch=source_branch,
        target_branch=target_branch,
        source_sha=source_sha,
        target_sha=target_sha,
        gate=gate,
        pushed=pushed,
        dry_run=not doing_write,
        approved_by=approval,
        blocked_reasons=tuple(blocked),
        commands=commands,
        stdout="\n".join(p for p in stdout_parts if p),
        stderr="\n".join(p for p in stderr_parts if p),
        proof_path=str(proof_path) if pushed else "",
        caveats=caveats,
    )
