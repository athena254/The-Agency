"""Generic product adapters for isolated worktrees.

One small interface (:meth:`ProductAdapter.prepare_dependencies`) hides the
language- and platform-specific work of making dependencies visible in an
isolated worktree. Nothing here installs packages, uses the network, or
modifies a scheduler.
"""

from __future__ import annotations

import os
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

SUPPORTED_LANGUAGES: tuple[str, ...] = ("node", "python")
MAX_NOTE_CHARS = 200
MAX_LANGUAGE_CHARS = 32
LINK_TIMEOUT_SECONDS = 30


def _bounded(note: str) -> str:
    """Keep every returned note short enough to log safely."""
    return note[:MAX_NOTE_CHARS]


class ProductAdapter(ABC):
    """Make an isolated worktree ready to run a product's tests."""

    language: str = ""

    @abstractmethod
    def prepare_dependencies(self, root: str | Path, workdir: Path) -> str:
        """Make dependencies visible in ``workdir``; return a bounded note."""
        ...


class NodeAdapter(ProductAdapter):
    """Reuse an existing ``node_modules`` tree; never install or fetch it."""

    language = "node"

    def prepare_dependencies(self, root: str | Path, workdir: Path) -> str:
        workdir = Path(workdir)
        if not workdir.is_dir():
            return "worktree missing"

        source = Path(root).resolve() / "node_modules"
        target = workdir / "node_modules"
        if not source.is_dir():
            return "no node_modules to reuse"
        if target.exists() or target.is_symlink():
            return "node_modules already present"

        if os.name == "nt":
            proc = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(target), str(source)],
                capture_output=True,
                text=True,
                check=False,
                timeout=LINK_TIMEOUT_SECONDS,
            )
            if proc.returncode != 0:
                detail = (proc.stderr or "").strip()[:120]
                return _bounded(f"node_modules junction failed: {detail}")
            return "node_modules reused"

        try:
            target.symlink_to(source, target_is_directory=True)
        except OSError as exc:
            return _bounded(f"node_modules symlink failed: {exc.strerror or 'unknown'}")
        return "node_modules reused"


class PythonAdapter(ProductAdapter):
    """Expose an isolated worktree's source tree; never install into it."""

    language = "python"

    def prepare_dependencies(self, root: str | Path, workdir: Path) -> str:
        workdir = Path(workdir)
        if (workdir / "src").is_dir():
            return "python path prepared"
        return "python project ready"


class NoOpAdapter(ProductAdapter):
    """Fallback for products with no dependency-reuse step (e.g. go, rust)."""

    language = "none"

    def prepare_dependencies(self, root: str | Path, workdir: Path) -> str:
        return "no adapter needed"


_REGISTRY: dict[str, ProductAdapter] = {
    "node": NodeAdapter(),
    "python": PythonAdapter(),
}
_NOOP = NoOpAdapter()


def adapter_for(language: str) -> ProductAdapter:
    """Return the adapter for ``language``; unknown languages get a no-op.

    Normalizing and bounding the key keeps an untrusted language string from
    ever steering a shell command. The seam never raises for a language it
    does not know, preserving the legacy ``no adapter needed`` behaviour.
    """
    key = language.strip().lower()[:MAX_LANGUAGE_CHARS]
    return _REGISTRY.get(key, _NOOP)
