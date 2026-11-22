"""Forge Ship slice: locally merge a worktree branch into its target.

Human approval is REQUIRED by default. The fast-forward happens only when the
pipeline gate and the smoke gate are both PASS and either a human approval is
granted or auto-merge is explicitly opted into via ``allow_auto_merge=True``.
No release is authorized.

Branch-ownership rules (never desync a checkout):
- target checked out in the main repo: refuse when that repo is dirty, then
  ``checkout`` + ``merge --ff-only`` there (the working tree updates honestly);
- target checked out in a sibling worktree: refuse (updating the ref behind
  that worktree's back corrupts its index/HEAD view);
- target checked out nowhere: ``git fetch . <source>:<target>`` — a ref-only
  fast-forward that touches no working tree and still fails on non-ff.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)

ShipGate = Literal["PASS", "FAIL"]

CAVEATS: tuple[str, ...] = ("local_only", "ff_only", "release_not_authorized")


class ShipReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(min_length=1)
    created_at: datetime
    root: str = ""
    main_repo: str = ""
    worktree_root: str = ""
    source_branch: str = ""
    target_branch: str
    gate: ShipGate
    merged: bool = False
    blocked_reasons: tuple[str, ...] = ()
    stdout: str = ""
    stderr: str = ""
    proof_path: str = ""
    caveats: tuple[str, ...] = CAVEATS
    note: str = (
        "Local ship via git merge --ff-only; human approval required by default; "
        "requires pipeline PASS and smoke PASS."
    )


def _gate_value(obj: Any) -> str:
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


def _git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _resolve_main_repo(worktree_root: Path) -> tuple[Path, bool]:
    proc = _git(worktree_root, ["rev-parse", "--git-common-dir"])
    if proc.returncode != 0:
        raise ValueError(f"not a git repository: {worktree_root}")
    common = Path(proc.stdout.strip())
    if not common.is_absolute():
        common = (worktree_root / common).resolve()
    # <main>/.git  -> main repo is its parent; a bare/common dir's parent
    # is not a worktree, in which case the worktree root itself is the repo.
    if common.name == ".git" and common.is_dir():
        return common.parent, common.parent.resolve() != worktree_root.resolve()
    if common.is_dir():
        # Common dir pointed elsewhere (linked worktree): its parent is
        # the main repo when it looks like a .git dir was remapped.
        toplevel = _git(worktree_root, ["rev-parse", "--show-toplevel"])
        if toplevel.returncode == 0:
            main = Path(toplevel.stdout.strip())
            # For a linked worktree, show-toplevel is the worktree itself;
            # the main repo is the parent of the common dir if it exists.
            return main, True
    toplevel = _git(worktree_root, ["rev-parse", "--show-toplevel"])
    if toplevel.returncode == 0:
        return Path(toplevel.stdout.strip()), False
    return worktree_root, False


def _current_branch(repo: Path) -> str:
    proc = _git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _append_proof(proof_path: Path, event: dict[str, Any]) -> None:
    proof_path.parent.mkdir(parents=True, exist_ok=True)
    with open(proof_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


def ship_branch(
    worktree_root: str | Path,
    target_branch: str,
    *,
    pipeline_gate: Any = None,
    smoke_gate: Any = None,
    pipeline_report: Any = None,
    smoke_report: Any = None,
    pipeline: Any = None,
    smoke: Any = None,
    allow_auto_merge: bool = False,
    require_human_approval: bool = True,
    human_gate: bool = False,
    approved: bool = False,
    approval: bool = False,
    proof_log: str | Path | None = None,
    repo_root: str | Path | None = None,
    **kwargs: Any,
) -> ShipReport:
    wt = Path(worktree_root)
    target_branch = str(target_branch)
    # Accept aliases.
    if pipeline_gate is None:
        pipeline_gate = pipeline_report if pipeline_report is not None else pipeline
    if smoke_gate is None:
        smoke_gate = smoke_report if smoke_report is not None else smoke
    if pipeline_gate is None:
        pipeline_gate = kwargs.get("pipeline_gate", kwargs.get("pipeline"))
    if smoke_gate is None:
        smoke_gate = kwargs.get("smoke_gate", kwargs.get("smoke"))
    # Human approval is required by default; auto-merge is opt-in only.
    require_human = bool(
        require_human_approval or human_gate or kwargs.get("require_human_approval", False)
    ) and not bool(allow_auto_merge or kwargs.get("allow_auto_merge", False))
    is_approved = bool(
        approved or approval or kwargs.get("approved", False) or kwargs.get("approval", False)
    )

    blocked: list[str] = []
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []

    pg = _gate_value(pipeline_gate)
    sg = _gate_value(smoke_gate)
    if pg != "PASS":
        blocked.append(f"pipeline_gate_not_pass:{pg}")
    if sg != "PASS":
        blocked.append(f"smoke_gate_not_pass:{sg}")
    if require_human and not is_approved:
        blocked.append("human_approval_required")

    try:
        if repo_root is not None:
            main_repo = Path(repo_root)
            is_linked = main_repo.resolve() != wt.resolve()
        else:
            main_repo, is_linked = _resolve_main_repo(wt)
    except ValueError as exc:
        blocked.append(str(exc))
        main_repo = wt
        is_linked = False

    source_branch = _current_branch(wt) if wt.is_dir() else ""
    if source_branch and source_branch == target_branch:
        blocked.append("source_branch_equals_target_branch")

    merged = False
    proof_path = Path(proof_log) if proof_log else (main_repo / ".forge-proof.jsonl")

    if not blocked:
        try:
            if not target_branch:
                blocked.append("target_branch_missing")
            else:
                check = _git(main_repo, ["rev-parse", "--verify", f"refs/heads/{target_branch}"])
                if check.returncode != 0:
                    check2 = _git(main_repo, ["rev-parse", "--verify", target_branch])
                    if check2.returncode != 0:
                        blocked.append(f"target_branch_not_found:{target_branch}")
                if not source_branch:
                    blocked.append("source_branch_unknown")
        except Exception as exc:  # noqa: BLE001
            blocked.append(str(exc))

    # Branch-ownership map: fast-forward only in the checkout that owns the
    # target branch. Updating a checked-out branch behind git's back desyncs
    # that checkout's index/HEAD and shows the merge as staged deletions.
    target_checkout: Path | None = None
    if not blocked:
        try:
            wt_list = _git(main_repo, ["worktree", "list", "--porcelain"])
            current_wt: Path | None = None
            for line in wt_list.stdout.splitlines():
                if line.startswith("worktree "):
                    current_wt = Path(line[len("worktree ") :].strip())
                elif line.startswith("branch ") and current_wt is not None:
                    branch = line[len("branch ") :].strip().removeprefix("refs/heads/")
                    if branch == target_branch:
                        target_checkout = current_wt
                        break
        except Exception as exc:  # noqa: BLE001
            blocked.append(f"worktree_list_failed:{exc}")

    if not blocked and target_checkout is not None:
        if target_checkout.resolve() != main_repo.resolve():
            # Target is checked out in a sibling worktree: refuse rather than
            # desync it.
            blocked.append(f"target_checked_out_elsewhere:{target_checkout}")
        elif _git(main_repo, ["status", "--porcelain"]).stdout.strip():
            # Main repo owns the branch but is dirty: a checkout would hijack
            # or entangle the user's working tree.
            blocked.append("main_repo_dirty")

    if not blocked:
        if target_checkout is not None and target_checkout.resolve() == main_repo.resolve():
            # Merge in the owning main repo: checkout target, ff-only merge.
            co = _git(main_repo, ["checkout", target_branch])
            stdout_parts.append(co.stdout)
            stderr_parts.append(co.stderr)
            if co.returncode != 0:
                blocked.append(f"checkout_failed:{co.stderr.strip()}")
            else:
                mg = _git(main_repo, ["merge", "--ff-only", source_branch])
                stdout_parts.append(mg.stdout)
                stderr_parts.append(mg.stderr)
                if mg.returncode != 0:
                    blocked.append(f"merge_failed:{mg.stderr.strip() or mg.stdout.strip()}")
                else:
                    merged = True
        else:
            # Target not checked out anywhere: update the ref in place with a
            # fetch fast-forward (fails on non-ff; never touches a worktree).
            ff = _git(main_repo, ["fetch", ".", f"{source_branch}:{target_branch}"])
            stdout_parts.append(ff.stdout)
            stderr_parts.append(ff.stderr)
            if ff.returncode != 0:
                blocked.append(f"fast_forward_failed:{ff.stderr.strip() or ff.stdout.strip()}")
            else:
                merged = True

    if merged:
        event = {
            "event": "ship",
            "report_id": None,  # filled below
            "created_at": datetime.now(UTC).isoformat(),
            "source_branch": source_branch,
            "target_branch": target_branch,
            "main_repo": str(main_repo),
            "worktree_root": str(wt),
            "pipeline_gate": pg,
            "smoke_gate": sg,
            "merged": True,
        }
        # Clean up linked worktree first (branch ref survives), then branch.
        if is_linked and wt.is_dir():
            rm = _git(main_repo, ["worktree", "remove", "--force", str(wt)])
            stdout_parts.append(rm.stdout)
            if rm.returncode != 0:
                stderr_parts.append(rm.stderr)
        if source_branch:
            dele = _git(main_repo, ["branch", "-d", source_branch])
            stdout_parts.append(dele.stdout)
            if dele.returncode != 0:
                # Fall back to forced delete; branch already merged ff-only.
                dele2 = _git(main_repo, ["branch", "-D", source_branch])
                stdout_parts.append(dele2.stdout)
                if dele2.returncode != 0:
                    stderr_parts.append(dele2.stderr)
        report_id = str(uuid4())
        event["report_id"] = report_id
        try:
            _append_proof(proof_path, event)
        except OSError as exc:
            stderr_parts.append(str(exc))
            blocked.append(f"proof_append_failed:{exc}")
            merged = False
    else:
        report_id = str(uuid4())

    gate: ShipGate = "PASS" if merged else "FAIL"
    report = ShipReport(
        report_id=report_id,
        created_at=datetime.now(UTC),
        root=str(wt),
        main_repo=str(main_repo),
        worktree_root=str(wt),
        source_branch=source_branch,
        target_branch=target_branch,
        gate=gate,
        merged=merged,
        blocked_reasons=tuple(blocked),
        stdout="\n".join(p for p in stdout_parts if p).strip(),
        stderr="\n".join(p for p in stderr_parts if p).strip(),
        proof_path=str(proof_path) if merged else "",
    )
    logger.info("forge.ship", gate=gate, merged=merged, target=target_branch, blocked=len(blocked))
    return report


ship = ship_branch

__all__ = ["CAVEATS", "ShipGate", "ShipReport", "ship", "ship_branch"]
