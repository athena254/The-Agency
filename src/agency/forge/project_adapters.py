"""Reuse project dependencies across isolated worktrees."""

from __future__ import annotations

from pathlib import Path

from agency.forge.adapters import adapter_for


def prepare_dependencies(root: str | Path, workdir: Path, language: str) -> str:
    """Delegate to the product adapter for this language."""
    return adapter_for(language).prepare_dependencies(root, workdir)
