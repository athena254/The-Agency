"""Forge v2 git worktree isolation helper.

Creates an isolated working copy (git worktree) for a feature/issue so each
agent keeps its own station. The original checkout is never mutated by the
agent's edits. Bounded: validates branch names, uses a dedicated worktree
root, and refuses to overwrite existing worktrees.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict

logger = structlog.get_logger(__name__)

_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,120}$")


class WorktreeInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    worktree_path: str
    branch: str
    base_ref: str


class WorktreeError(Exception):
    """Raised when a worktree cannot be created or removed safely."""


def _validate_branch(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise WorktreeError("branch name must be a nonblank string")
    if ".." in name or name.startswith(("/", "-")):
        raise WorktreeError(f"unsafe branch name: {name!r}")
    if not _BRANCH_RE.match(name):
        raise WorktreeError(f"invalid branch name: {name!r}")
    return name


def _run_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def create_worktree(
    repo_root: str | Path,
    branch: str,
    *,
    base_ref: str = "origin/main",
    worktree_root: str | Path | None = None,
) -> WorktreeInfo:
    """Create a new git worktree for ``branch`` based on ``base_ref``.

    Returns WorktreeInfo. Raises WorktreeError on failure. Never mutates the
    original working tree beyond registering the worktree.
    """
    repo = Path(repo_root)
    if not repo.is_dir():
        raise WorktreeError(f"repo root not a directory: {repo}")
    branch = _validate_branch(branch)
    base_ref = _validate_branch(base_ref.replace("origin/", "")) if False else base_ref
    # base_ref is allowed to contain slashes (origin/main); validate loosely
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,120}$", base_ref):
        raise WorktreeError(f"invalid base ref: {base_ref!r}")

    root = Path(worktree_root) if worktree_root else (repo.parent / f"{repo.name}-worktrees")
    root.mkdir(parents=True, exist_ok=True)
    wt_path = root / f"{branch.replace('/', '-')}-{uuid4().hex[:8]}"
    if wt_path.exists():
        raise WorktreeError(f"worktree path already exists: {wt_path}")

    # Ensure base ref exists
    check = _run_git(repo, ["rev-parse", "--verify", base_ref])
    if check.returncode != 0:
        raise WorktreeError(f"base ref not found: {base_ref}: {check.stderr.strip()}")

    # Create branch + worktree
    proc = _run_git(repo, ["worktree", "add", "-b", branch, str(wt_path), base_ref])
    if proc.returncode != 0:
        # branch may already exist; try without -b
        proc2 = _run_git(repo, ["worktree", "add", str(wt_path), branch])
        if proc2.returncode != 0:
            raise WorktreeError(
                f"failed to create worktree: {proc.stderr.strip() or proc2.stderr.strip()}"
            )
    logger.info("forge.worktree_created", path=str(wt_path), branch=branch)
    return WorktreeInfo(worktree_path=str(wt_path), branch=branch, base_ref=base_ref)


def remove_worktree(repo_root: str | Path, worktree_path: str | Path) -> None:
    repo = Path(repo_root)
    wt = Path(worktree_path)
    if not wt.is_dir():
        raise WorktreeError(f"worktree not found: {wt}")
    proc = _run_git(repo, ["worktree", "remove", "--force", str(wt)])
    if proc.returncode != 0:
        raise WorktreeError(f"failed to remove worktree: {proc.stderr.strip()}")
    logger.info("forge.worktree_removed", path=str(wt))
