"""Reuse project dependencies across isolated worktrees."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def prepare_dependencies(root: str | Path, workdir: Path, language: str) -> str:
    """Make dependencies visible in ``workdir`` without reinstalling them.

    Returns a short note describing what was done.
    """
    source = Path(root).resolve() / "node_modules"
    target = workdir / "node_modules"
    if language == "node" and source.is_dir() and not target.exists():
        if os.name == "nt":
            proc = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(target), str(source)],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if proc.returncode != 0:
                return f"node_modules junction failed: {proc.stderr[:120]}"
        else:
            target.symlink_to(source, target_is_directory=True)
        return "node_modules reused"
    if language == "python":
        src_dir = workdir / "src"
        return "python path prepared" if src_dir.is_dir() else "python project ready"
    return "no adapter needed"
