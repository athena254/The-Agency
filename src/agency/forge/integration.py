"""Combine disjoint parallel work without touching main, then run a real suite."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict

DEFAULT_TEST_TIMEOUT_S = 120
MIN_TEST_TIMEOUT_S = 1
MAX_TEST_TIMEOUT_S = 300
MAX_TEST_ARGS = 32
MAX_TEST_ARG_CHARS = 256


class IntegrationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gate: str
    base: str
    branches: tuple[str, ...]
    overlapping: tuple[str, ...] = ()
    combined: tuple[str, ...] = ()
    test_gate: str = "NOT_RUN"
    test_command: tuple[str, ...] = ()
    test_returncode: int | None = None
    test_timed_out: bool = False
    note: str = ""

    @property
    def ready(self) -> bool:
        """Ready for a human only when work combined and the suite passed."""
        return self.gate == "PASS" and self.test_gate == "PASS"


def _git(repo: str | Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _split_command(command: str) -> list[str]:
    r"""Split a shell-quoted command without mangling Windows paths.

    ``shlex`` in posix mode eats backslashes in paths such as
    ``C:\Python\python.exe``; on Windows use non-posix mode and remove the
    outer quotes that mode leaves on quoted arguments.
    """
    posix = os.name != "nt"
    parts = shlex.split(command, posix=posix)
    if not posix:
        parts = [
            arg[1:-1] if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in "\"'" else arg
            for arg in parts
        ]
    return parts


def _bounded_command(command: tuple[str, ...] | list[str] | str) -> tuple[str, ...]:
    if isinstance(command, str):
        parts = _split_command(command)
    else:
        parts = [str(arg) for arg in command]
    return tuple(arg[:MAX_TEST_ARG_CHARS] for arg in parts[:MAX_TEST_ARGS])


def _run_test_command(
    work: Path, command: tuple[str, ...], timeout_s: int
) -> tuple[str, int | None, bool]:
    """Run the product's test command in the combined worktree.

    Output is deliberately discarded (DEVNULL): it keeps memory bounded and
    keeps secrets out of files and logs. Only the gate, return code and
    timeout flag are recorded.
    """
    if not command:
        return "NOT_RUN", None, False
    try:
        result = subprocess.run(
            list(command),
            cwd=str(work),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_s,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return "FAIL", None, True
    except (OSError, ValueError):
        return "FAIL", None, False
    return ("PASS" if result.returncode == 0 else "FAIL"), result.returncode, False


def propose_integration(
    base_repo: str | Path,
    branches: list[str],
    *,
    worktree: str | Path,
    output_branch: str,
    test_command: tuple[str, ...] | list[str] | str = (),
    test_timeout_s: int = DEFAULT_TEST_TIMEOUT_S,
) -> IntegrationReport:
    """Copy disjoint changed paths into one worktree and gate on its tests."""
    base = str(base_repo)
    command = _bounded_command(test_command)
    timeout = max(MIN_TEST_TIMEOUT_S, min(MAX_TEST_TIMEOUT_S, int(test_timeout_s)))
    changed: dict[str, set[str]] = {}
    for branch in branches:
        path_text = _git(base, "diff", "--name-only", f"{output_branch}..{branch}")
        changed[branch] = {p for p in path_text.splitlines() if p}
    seen: set[str] = set()
    overlaps: set[str] = set()
    for branch_paths in changed.values():
        overlaps |= seen & branch_paths
        seen |= branch_paths
    if overlaps:
        return IntegrationReport(
            gate="FAIL",
            base=base,
            branches=tuple(branches),
            overlapping=tuple(sorted(overlaps)),
            combined=(),
            note="overlapping changes; no suite run",
        )

    work = Path(worktree)
    work.mkdir(parents=True, exist_ok=True)
    combined: list[str] = []
    for branch, paths in changed.items():
        for path in sorted(paths):
            content = _git(base, "show", f"{branch}:{path}")
            if not content:
                continue
            target = work / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            combined.append(path)
    subprocess.run(["git", "-C", str(work), "add", "-A"], capture_output=True, check=False)
    subprocess.run(
        ["git", "-C", str(work), "commit", "-qm", f"integrate {len(branches)} parallel branches"],
        capture_output=True,
        check=False,
    )
    test_gate, returncode, timed_out = _run_test_command(work, command, timeout)
    note = "disjoint work combined; "
    if test_gate == "PASS":
        note += "test command passed; ready for human review"
    elif test_gate == "FAIL":
        note += (
            "test command timed out; branch not ready"
            if timed_out
            else "test command failed; branch not ready"
        )
    else:
        note += "no test command supplied; branch not verified"
    note += "; no merge or push performed"
    return IntegrationReport(
        gate="PASS",
        base=base,
        branches=tuple(branches),
        overlapping=(),
        combined=tuple(sorted(set(combined))),
        test_gate=test_gate,
        test_command=command,
        test_returncode=returncode,
        test_timed_out=timed_out,
        note=note,
    )
