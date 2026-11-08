"""Agency coding agent: a thin operator over the Forge bounded coding agent.

The agent's default workspace is this repository (``The-Agency``). It never
mutates the original checkout directly: every task runs through
:func:`agency.forge.run_coding_agent`, which applies edits in an isolated git
worktree and runs the inspection + security (+ optional test) gates before
returning a verdict.

This is a *proposal* helper, not an authority. A ``PASS`` report is evidence
only; merge/release approval stays with a human.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agency.forge import CodingAgentReport, CodingAgentStore, run_coding_agent
from agency.forge.coding_agent import _normalize_edit, _resolve_edits
from agency.forge.protected_paths import is_protected

# Repository root = parents[3] of this file (src/agency/agents/coding.py).
DEFAULT_WORKSPACE = Path(__file__).resolve().parents[3]


def read_skill(skill_path: Path | str) -> str:
    """Read a skill markdown file and return its text.

    Raises ``FileNotFoundError`` if the path does not exist.
    """
    path = Path(skill_path)
    if not path.is_file():
        raise FileNotFoundError(f"skill not found: {path}")
    return path.read_text(encoding="utf-8")


class AgencyCodingAgent:
    """Operator that runs bounded coding tasks against the Agency workspace."""

    def __init__(
        self, workspace: Path | str | None = None, store_path: Path | str | None = None
    ) -> None:
        self.workspace = Path(workspace) if workspace is not None else DEFAULT_WORKSPACE
        self.store_path = Path(store_path) if store_path is not None else None

    def run_task(
        self,
        branch: str,
        edits: list[dict[str, Any]],
        *,
        test_paths: list[str] | None = None,
        run_tests_flag: bool = False,
        cleanup: bool = False,
        test_timeout_s: int = 120,
        persist: bool = True,
    ) -> CodingAgentReport:
        """Apply ``edits`` in an isolated worktree and return the Forge verdict.

        When ``persist`` is true and a ``store_path`` was configured, the report
        is also saved to :class:`CodingAgentStore` so it survives restarts.
        """
        # Protected-paths guardrail: refuse before creating a worktree.
        for raw_edit in _resolve_edits(edits):
            parsed = _normalize_edit(raw_edit)
            if parsed is not None and is_protected(parsed[0]):
                raise ValueError(f"protected path: {parsed[0]}")

        report = run_coding_agent(
            self.workspace,
            branch,
            edits,
            test_paths=test_paths,
            run_tests_flag=run_tests_flag,
            cleanup=cleanup,
            test_timeout_s=test_timeout_s,
        )
        if persist and self.store_path is not None:
            store = CodingAgentStore(self.store_path)
            try:
                store.save(report)
            finally:
                store.close()
        return report

    async def arun_task(self, *args: Any, **kwargs: Any) -> CodingAgentReport:
        """Async wrapper around :meth:`run_task` (runs in a worker thread)."""
        return await asyncio.to_thread(self.run_task, *args, **kwargs)

    def run_with_skill(
        self,
        skill_path: Path | str,
        branch: str,
        edits: list[dict[str, Any]],
        **kwargs: Any,
    ) -> CodingAgentReport:
        """Run a task, first verifying the named skill file is readable."""
        read_skill(skill_path)  # fail fast if missing
        return self.run_task(branch, edits, **kwargs)


__all__ = ["DEFAULT_WORKSPACE", "AgencyCodingAgent", "read_skill"]
