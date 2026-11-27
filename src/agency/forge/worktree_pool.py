"""Bounded worktree pool for parallel Forge tasks.

One slot per task id, unique branch, absolute paths, TTL reaping and a simple
path-conflict rule. Built on the existing ``forge.worktree`` primitives; it
never mutates the canonical checkout beyond git worktree registration.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict

from agency.forge.worktree import WorktreeError, create_worktree, remove_worktree

logger = structlog.get_logger(__name__)

_TASK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
MAX_SLOTS = 100


class PoolEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    worktree_path: str
    branch: str
    base_ref: str
    changed_paths: tuple[str, ...] = ()
    created_at: float


class SlotReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    gate: str
    worktree_path: str = ""
    branch: str = ""
    blocked_reasons: tuple[str, ...] = ()
    active_slots: int = 0
    caveats: tuple[str, ...] = ("local_worktrees_only", "no_auto_merge")


class WorktreePool:
    """Thread-safe registry of at most ``max_slots`` isolated worktrees."""

    def __init__(
        self,
        repo_root: str | Path,
        *,
        base_dir: str | Path | None = None,
        max_slots: int = MAX_SLOTS,
        ttl_s: float = 3600.0,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        if not self.repo_root.is_dir():
            raise ValueError("repo_root must be an existing directory")
        if max_slots < 1 or max_slots > MAX_SLOTS:
            raise ValueError(f"max_slots must be 1..{MAX_SLOTS}")
        if ttl_s < 0:
            raise ValueError("ttl_s must be non-negative")
        self.max_slots = max_slots
        self.ttl_s = ttl_s
        self.base_dir = Path(base_dir) if base_dir else (self.repo_root.parent / "forge-pool")
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.worktree_root = self.base_dir / "worktrees"
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.base_dir / "pool.json"
        self._lock = threading.RLock()
        self._entries = self._load()

    def _load(self) -> dict[str, PoolEntry]:
        if not self.registry_path.is_file():
            return {}
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
            return {key: PoolEntry.model_validate(value) for key, value in payload.items()}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        payload = {key: value.model_dump(mode="json") for key, value in self._entries.items()}
        self.registry_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def active_slots(self) -> int:
        with self._lock:
            return len(self._entries)

    def acquire(
        self,
        task_id: str,
        *,
        changed_paths: list[str] | tuple[str, ...] = (),
        base_ref: str = "HEAD",
    ) -> SlotReport:
        task_id = str(task_id).strip()
        blocked: list[str] = []
        if not _TASK_RE.fullmatch(task_id):
            return SlotReport(task_id=task_id, gate="BLOCKED", blocked_reasons=("invalid_task_id",))
        paths = tuple(sorted({str(p).replace("\\", "/") for p in changed_paths}))
        with self._lock:
            if task_id in self._entries:
                existing = self._entries[task_id]
                return SlotReport(
                    task_id=task_id,
                    gate="PASS",
                    worktree_path=existing.worktree_path,
                    branch=existing.branch,
                    active_slots=len(self._entries),
                )
            if len(self._entries) >= self.max_slots:
                blocked.append("pool_full")
            for entry in self._entries.values():
                if set(entry.changed_paths) & set(paths):
                    blocked.append(f"path_conflict:{entry.task_id}")
                    break
            if blocked:
                return SlotReport(
                    task_id=task_id,
                    gate="BLOCKED",
                    blocked_reasons=tuple(blocked),
                    active_slots=len(self._entries),
                )
            branch = f"factory/{task_id}"
            try:
                info = create_worktree(
                    self.repo_root,
                    branch,
                    base_ref=base_ref,
                    worktree_root=self.worktree_root,
                )
            except WorktreeError as exc:
                return SlotReport(
                    task_id=task_id,
                    gate="BLOCKED",
                    blocked_reasons=(f"worktree_failed:{type(exc).__name__}",),
                    active_slots=len(self._entries),
                )
            self._entries[task_id] = PoolEntry(
                task_id=task_id,
                worktree_path=info.worktree_path,
                branch=info.branch,
                base_ref=base_ref,
                changed_paths=paths,
                created_at=time.time(),
            )
            self._save()
            logger.info("forge.worktree_pool_acquired", task_id=task_id, branch=branch)
            return SlotReport(
                task_id=task_id,
                gate="PASS",
                worktree_path=info.worktree_path,
                branch=info.branch,
                active_slots=len(self._entries),
            )

    def release(self, task_id: str) -> bool:
        with self._lock:
            entry = self._entries.pop(str(task_id), None)
            if entry is None:
                return False
            remove_worktree(self.repo_root, entry.worktree_path)
            self._save()
            return True

    def reap(self) -> tuple[str, ...]:
        now = time.time()
        with self._lock:
            expired = [
                key for key, entry in self._entries.items() if now - entry.created_at > self.ttl_s
            ]
            for key in expired:
                entry = self._entries.pop(key)
                remove_worktree(self.repo_root, entry.worktree_path)
            if expired:
                self._save()
            return tuple(expired)
