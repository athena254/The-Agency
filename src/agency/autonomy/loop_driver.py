"""Deterministic Ralph-style autonomous loop driver.

The driver owns the while-loop; workers and gates are injected. One item
per iteration, fresh context, harness-run gates, checkpoint + revert on
failure, bounded retries, optional budgets, and a user stop signal.

Determinism rules (mirrors the production loop patterns):

* the harness selects the next item (the worker never self-selects);
* the harness runs the gates (the worker never grades its own work);
* a failed iteration is reverted to the checkpoint before retrying;
* budgets are optional — an unattended run is bounded only by the
  always-on circuit breakers unless the operator sets caps.

This module never authorizes release. A PASS gate is evidence only.
"""

from __future__ import annotations

import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any, Literal, Protocol

import structlog
from pydantic import BaseModel, ConfigDict, Field

from agency.autonomy.loop_state import (
    LoopBudgets,
    LoopEvent,
    LoopItem,
    LoopState,
    LoopStatus,
    LoopStore,
)
from agency.autonomy.proof import ProofBundle
from agency.forge.worktree import (
    WorktreeInfo,
    create_worktree,
    isolation_violations,
    remove_worktree,
    snapshot_changes,
)

logger = structlog.get_logger(__name__)

MAX_ATTEMPTS_PER_ITEM = 10
SAME_ERROR_LIMIT = 5
NO_PROGRESS_LIMIT = 3

ISOLATION_VIOLATION_DETAIL = "worker left its isolated worktree"

WorkerGate = Literal["PASS", "FAIL"]
GateGate = Literal["PASS", "FAIL"]


class WorkerResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gate: WorkerGate
    changed_paths: tuple[str, ...] = ()
    detail: str = ""
    cost_usd: float = 0.0
    duration_s: float = 0.0


class GateResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gate: GateGate
    feedback: str = ""
    reports: tuple[str, ...] = ()
    error_signature: str = ""
    detail: str = ""
    cost_usd: float = 0.0


class WorkerAdapter(Protocol):
    name: str

    def run(self, item: LoopItem, worktree: Path, feedback: str) -> WorkerResult: ...


class GateRunner(Protocol):
    def run(self, worktree: Path, item: LoopItem, changed_paths: list[str]) -> GateResult: ...


class LoopConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    branch: str = "agency-loop"
    worker: str = "opencode"
    max_attempts_per_item: int = MAX_ATTEMPTS_PER_ITEM
    same_error_limit: int = SAME_ERROR_LIMIT
    no_progress_limit: int = NO_PROGRESS_LIMIT
    budgets: LoopBudgets = Field(default_factory=LoopBudgets)
    auto_merge: bool = True
    dry_run: bool = False


class LoopResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str
    status: LoopStatus
    iterations: int
    items_done: int
    items_blocked: int
    items_pending: int
    cost_usd: float
    detail: str = ""


def _git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


class GitWorkspace:
    """Isolated git worktree workspace with checkpoint/reset/commit."""

    def __init__(
        self,
        repo: Path,
        branch: str,
        worktree_root: Path | None = None,
    ) -> None:
        self.repo = repo
        self.branch = branch
        self.info = create_worktree(repo, branch, base_ref="HEAD", worktree_root=worktree_root)
        self.path = Path(self.info.worktree_path)

    @classmethod
    def reuse(cls, repo: Path, branch: str, path: Path) -> GitWorkspace:
        obj = cls.__new__(cls)
        obj.repo = repo
        obj.branch = branch
        obj.path = path
        obj.info = WorktreeInfo(worktree_path=str(path), branch=branch, base_ref="HEAD")
        return obj

    def checkpoint(self) -> str:
        proc = _git(self.path, ["rev-parse", "HEAD"])
        if proc.returncode != 0:
            raise RuntimeError(f"checkpoint failed: {proc.stderr.strip()}")
        return proc.stdout.strip()

    def reset(self, sha: str) -> None:
        proc = _git(self.path, ["reset", "--hard", sha])
        if proc.returncode != 0:
            raise RuntimeError(f"reset failed: {proc.stderr.strip()}")
        _git(self.path, ["clean", "-fd"])

    def commit(self, message: str) -> str:
        _git(self.path, ["add", "-A"])
        proc = _git(self.path, ["commit", "-m", message])
        if proc.returncode != 0:
            status = _git(self.path, ["status", "--porcelain"])
            if status.returncode == 0 and status.stdout.strip() == "":
                # Nothing to commit (for example a no-op item): not a failure.
                return self.checkpoint()
            raise RuntimeError(f"commit failed: {proc.stderr.strip()}")
        return self.checkpoint()

    def changed_paths(self) -> list[str]:
        out = _git(self.path, ["diff", "--name-only", "HEAD"]).stdout
        untracked = _git(self.path, ["ls-files", "--others", "--exclude-standard"]).stdout
        return sorted({p for p in out.splitlines() if p} | {p for p in untracked.splitlines() if p})

    def close(self) -> None:
        remove_worktree(self.repo, self.path)


class LoopDriver:
    """Drives one loop session to completion, budget, or user stop."""

    def __init__(
        self,
        config: LoopConfig,
        store: LoopStore,
        worker: WorkerAdapter,
        gate: GateRunner,
        items: list[LoopItem] | tuple[LoopItem, ...] = (),
        *,
        worktree_root: Path | None = None,
        workspace: GitWorkspace | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.worker = worker
        self.gate = gate
        self.items = tuple(items)
        self._worktree_root = worktree_root
        self._workspace = workspace
        self._stop = Event()
        self._state: LoopState | None = None
        self._proof: ProofBundle | None = None
        self.workspace: GitWorkspace | None = None

    # -- public API ---------------------------------------------------------

    def request_stop(self) -> None:
        self._stop.set()

    @property
    def state(self) -> LoopState | None:
        return self._state

    def status(self) -> LoopState | None:
        """Return the current persisted state (or ``None`` before ``run``)."""
        return self._state

    def run(self) -> LoopResult:
        repo = Path(self.config.repo)
        if not repo.is_absolute():
            raise ValueError("repo must be an absolute path")
        if not repo.is_dir():
            raise ValueError(f"repo is not a directory: {repo}")

        self._state = self._load_or_init()
        self._proof = self._rebuild_proof()
        self.workspace = self._open_workspace(repo)

        self._state = self._state.model_copy(
            update={
                "status": "RUNNING",
                "worker": self.config.worker,
                "branch": self.config.branch,
                "worktree_path": str(self.workspace.path),
            }
        )
        self._emit("loop_start", payload={"goal": self.config.goal})
        start = time.monotonic()
        try:
            while True:
                if self._stop.is_set():
                    self._emit("stopped", payload={"reason": "user_stop"})
                    break
                if self._budget_exhausted(start):
                    self._emit("budget_exhausted", payload=self._budget_payload(start))
                    break
                item = self._next_item()
                if item is None:
                    self._emit("queue_empty")
                    break
                self._run_item(item, start)
        except Exception as exc:
            self._emit("loop_error", payload={"error": type(exc).__name__})
            self._state = self._state.model_copy(
                update={"status": "FAILED", "detail": f"{type(exc).__name__}: {exc}"}
            )
            self._save()
            raise
        finally:
            self._finalize(start)

        return self._result()

    # -- setup --------------------------------------------------------------

    def _load_or_init(self) -> LoopState:
        existing = self.store.load(self.config.session_id)
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        return LoopState(
            session_id=self.config.session_id,
            goal=self.config.goal,
            status="PENDING",
            worker=self.config.worker,
            branch=self.config.branch,
            queue=self.items,
            budgets=self.config.budgets,
            created_at=now,
            updated_at=now,
        )

    def _rebuild_proof(self) -> ProofBundle:
        assert self._state is not None
        return ProofBundle(
            events=list(self._state.proof_events),
            head=self._state.proof_head,
            count=len(self._state.proof_events),
        )

    def _open_workspace(self, repo: Path) -> GitWorkspace:
        if self._workspace is not None:
            return self._workspace
        assert self._state is not None
        existing = self._state.worktree_path
        if existing and Path(existing).is_dir():
            return GitWorkspace.reuse(repo, self.config.branch, Path(existing))
        return GitWorkspace(repo, self.config.branch, self._worktree_root)

    # -- state helpers ------------------------------------------------------

    def _emit(
        self,
        event: str,
        *,
        item_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        assert self._state is not None
        ev = LoopEvent(
            seq=len(self._state.events),
            timestamp=datetime.now(UTC).isoformat(),
            event=event,
            item_id=item_id,
            payload=payload or {},
        )
        self._state = self._state.model_copy(update={"events": self._state.events + (ev,)})
        self._save()

    def _save(self) -> None:
        assert self._state is not None and self._proof is not None
        state = self._state.model_copy(
            update={
                "proof_events": tuple(self._proof.events),
                "proof_head": self._proof.head,
                "updated_at": datetime.now(UTC),
            }
        )
        self._state = state
        self.store.save(state)

    def _update_item(self, item_id: str, **kw: Any) -> None:
        assert self._state is not None
        queue = tuple(
            it.model_copy(update=kw) if it.id == item_id else it for it in self._state.queue
        )
        self._state = self._state.model_copy(update={"queue": queue})

    def _counts(self) -> dict[str, int]:
        assert self._state is not None
        queue = self._state.queue
        return {
            "done": sum(1 for i in queue if i.status == "done"),
            "blocked": sum(1 for i in queue if i.status == "blocked"),
            "pending": sum(1 for i in queue if i.status in ("pending", "active")),
        }

    # -- loop primitives ----------------------------------------------------

    def _next_item(self) -> LoopItem | None:
        assert self._state is not None
        statuses = {i.id: i.status for i in self._state.queue}
        for item in sorted(self._state.queue, key=lambda i: (-i.priority, i.id)):
            if item.status in ("done", "blocked"):
                continue
            if all(statuses.get(d) == "done" for d in item.deps):
                return item
        return None

    def _budget_exhausted(self, start: float) -> bool:
        budgets = self.config.budgets
        assert self._state is not None
        if budgets.max_iterations is not None and self._state.iteration >= budgets.max_iterations:
            return True
        if budgets.max_hours is not None and (time.monotonic() - start) / 3600 >= budgets.max_hours:
            return True
        return bool(
            budgets.max_cost_usd is not None and self._state.cost_usd >= budgets.max_cost_usd
        )

    def _budget_payload(self, start: float) -> dict[str, Any]:
        assert self._state is not None
        return {
            "iteration": self._state.iteration,
            "cost_usd": self._state.cost_usd,
            "elapsed_s": round(time.monotonic() - start, 3),
        }

    def _run_item(self, item: LoopItem, start: float) -> None:
        assert self._state is not None and self._proof is not None and self.workspace is not None
        self._state = self._state.model_copy(update={"current_item_id": item.id})
        self._update_item(item.id, status="active")
        self._emit("item_start", item_id=item.id)

        attempts = 0
        same_error = 0
        no_progress = 0
        last_signature = ""
        feedback = ""

        # Baseline of the original checkout before any worker runs. A worker
        # that escapes its worktree edits this tree, and the worktree diff
        # cannot see those edits; comparing against this baseline after each
        # attempt exposes them.
        before_changes = snapshot_changes(self.workspace.repo)

        while attempts < self.config.max_attempts_per_item:
            if self._stop.is_set() or self._budget_exhausted(start):
                return
            checkpoint = self.workspace.checkpoint()
            self._state = self._state.model_copy(update={"iteration": self._state.iteration + 1})

            worker_result = self.worker.run(item, self.workspace.path, feedback)
            violations = isolation_violations(self.workspace.repo, before_changes)
            if violations:
                iso_detail = (
                    f"{ISOLATION_VIOLATION_DETAIL}; original checkout changed: "
                    + ", ".join(violations)
                )
                if worker_result.detail:
                    iso_detail = f"{iso_detail}\n{worker_result.detail}"
                logger.warning(
                    "loop.isolation_violation",
                    item_id=item.id,
                    paths=list(violations),
                )
                self._emit(
                    "isolation_violation",
                    item_id=item.id,
                    payload={"paths": list(violations)},
                )
                worker_result = worker_result.model_copy(
                    update={"gate": "FAIL", "detail": iso_detail}
                )
            self._state = self._state.model_copy(
                update={"cost_usd": self._state.cost_usd + worker_result.cost_usd}
            )
            gate_result = self.gate.run(
                self.workspace.path, item, list(worker_result.changed_paths)
            )
            self._state = self._state.model_copy(
                update={"cost_usd": self._state.cost_usd + gate_result.cost_usd}
            )
            self._proof.append(
                "loop",
                "iteration",
                {
                    "item_id": item.id,
                    "attempt": attempts + 1,
                    "worker_gate": worker_result.gate,
                    "gate": gate_result.gate,
                    "changed_paths": list(worker_result.changed_paths),
                    "isolation_violations": list(violations),
                },
            )

            if worker_result.gate == "PASS" and gate_result.gate == "PASS":
                if self.config.dry_run:
                    sha = "dry-run"
                else:
                    sha = self.workspace.commit(f"[agency-loop] {item.id}: {item.title}")
                self._proof.append("loop", "item_pass", {"item_id": item.id, "sha": sha})
                self._update_item(item.id, status="done", attempts=attempts + 1)
                self._emit(
                    "item_done",
                    item_id=item.id,
                    payload={"sha": sha, "attempts": attempts + 1},
                )
                return

            if not self.config.dry_run:
                self.workspace.reset(checkpoint)

            signature = gate_result.error_signature or gate_result.detail
            if signature == last_signature:
                same_error += 1
            else:
                same_error = 1
                last_signature = signature
            if not worker_result.changed_paths:
                no_progress += 1
            if worker_result.gate == "FAIL":
                feedback = worker_result.detail or gate_result.feedback or gate_result.detail
            else:
                feedback = gate_result.feedback or gate_result.detail
            attempts += 1
            self._update_item(item.id, attempts=attempts)
            self._emit(
                "item_retry",
                item_id=item.id,
                payload={
                    "attempt": attempts,
                    "signature": signature[:120],
                    "no_progress": no_progress,
                    "same_error": same_error,
                },
            )

            if no_progress >= self.config.no_progress_limit:
                self._update_item(item.id, status="blocked", detail="no_progress")
                self._emit("item_blocked", item_id=item.id, payload={"reason": "no_progress"})
                return
            if same_error >= self.config.same_error_limit:
                self._update_item(item.id, status="blocked", detail="same_error")
                self._emit("item_blocked", item_id=item.id, payload={"reason": "same_error"})
                return

        self._update_item(item.id, status="blocked", detail="max_attempts")
        self._emit("item_blocked", item_id=item.id, payload={"reason": "max_attempts"})

    def _finalize(self, start: float) -> None:
        assert self._state is not None
        queue = list(self._state.queue)
        by_id = {i.id: i for i in queue}
        changed = False
        for idx, item in enumerate(queue):
            if item.status == "pending" and any(
                by_id[d].status == "blocked" for d in item.deps if d in by_id
            ):
                queue[idx] = item.model_copy(
                    update={"status": "blocked", "detail": "dependency_blocked"}
                )
                changed = True
        if changed:
            self._state = self._state.model_copy(update={"queue": tuple(queue)})

        counts = self._counts()
        if self._state.status == "FAILED":
            status: LoopStatus = "FAILED"
        else:
            status = "PAUSED" if counts["pending"] > 0 else "COMPLETED"
        self._state = self._state.model_copy(update={"status": status, "current_item_id": ""})
        self._emit("loop_end", payload={"status": status, **counts})
        self._save()
        if self.workspace is not None:
            self.workspace.close()
            self.workspace = None

    def _result(self) -> LoopResult:
        assert self._state is not None
        counts = self._counts()
        return LoopResult(
            session_id=self._state.session_id,
            status=self._state.status,
            iterations=self._state.iteration,
            items_done=counts["done"],
            items_blocked=counts["blocked"],
            items_pending=counts["pending"],
            cost_usd=round(self._state.cost_usd, 6),
            detail=self._state.detail,
        )


__all__ = [
    "MAX_ATTEMPTS_PER_ITEM",
    "NO_PROGRESS_LIMIT",
    "SAME_ERROR_LIMIT",
    "GateGate",
    "GateResult",
    "GateRunner",
    "GitWorkspace",
    "LoopConfig",
    "LoopDriver",
    "LoopResult",
    "WorkerAdapter",
    "WorkerGate",
    "WorkerResult",
]
