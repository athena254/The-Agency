"""Worktree-scoped, read-only file tool for promoted forge apprentices."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from agency.forge.protected_paths import is_protected
from agency.tools.base import ToolContext, ToolResult, ToolRisk, ToolSpec

_MAX_BYTES = 65536


class ForgeReadTool:
    """Read a bounded UTF-8 file strictly inside the assigned worktree."""

    spec = ToolSpec(
        name="forge_read",
        description="Read a bounded UTF-8 file strictly inside the assigned worktree.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        risk=ToolRisk.READ_ONLY,
    )

    @staticmethod
    def _error(reason: str) -> ToolResult:
        return ToolResult(tool="forge_read", ok=False, error=f"forge_read: {reason}")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if not isinstance(args, dict) or set(args) != {"path"}:
            return self._error("invalid request")
        root_raw = ctx.metadata.get("worktree_root")
        if not isinstance(root_raw, str) or not root_raw.strip():
            return ToolResult(tool=self.spec.name, ok=False, error="worktree_root required")
        root = Path(root_raw)
        if not root.is_absolute():
            return ToolResult(tool=self.spec.name, ok=False, error="worktree_root required")
        raw_path = args["path"]
        if not isinstance(raw_path, str) or not raw_path.strip():
            return self._error("invalid path")
        if ".." in raw_path.replace("\\", "/").split("/"):
            return self._error("path traversal rejected")
        try:
            resolved_root = root.resolve()
            target = (root / raw_path).resolve()
        except (OSError, RuntimeError):
            return self._error("path resolution failed")
        if target == resolved_root or resolved_root not in target.parents:
            return self._error("path outside worktree")
        if is_protected(target.relative_to(resolved_root).as_posix()):
            return self._error("protected path rejected")
        try:
            if not target.is_file() or target.stat().st_size > _MAX_BYTES:
                return self._error("file not found" if not target.is_file() else "file too large")
            data = target.read_bytes()
        except OSError:
            return self._error("file unreadable")
        if len(data) > _MAX_BYTES:
            return self._error("file too large")
        return ToolResult(
            tool=self.spec.name,
            ok=True,
            output={
                "content": data.decode("utf-8", errors="replace"),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            },
        )


__all__ = ["ForgeReadTool"]
