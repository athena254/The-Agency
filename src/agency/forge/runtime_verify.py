"""Build a real runtime gate: start the product and poll its endpoints."""

from __future__ import annotations

import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class RuntimeReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gate: str
    command: tuple[str, ...]
    endpoints: tuple[str, ...]
    statuses: tuple[tuple[str, int], ...] = ()
    duration_s: float = Field(ge=0)
    note: str = ""


def verify_runtime(
    root: str | Path,
    command: list[str],
    endpoints: list[str],
    *,
    timeout_s: int = 30,
) -> RuntimeReport:
    """Start ``command``, poll each endpoint, then stop the process."""
    if not command or not endpoints:
        return RuntimeReport(
            gate="FAIL",
            command=tuple(command),
            endpoints=tuple(endpoints),
            statuses=(),
            duration_s=0.0,
            note="missing_command_or_endpoints",
        )
    proc = subprocess.Popen(
        command,
        cwd=str(root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + max(1, timeout_s)
    statuses: list[tuple[str, int]] = []
    try:
        while time.monotonic() < deadline and len(statuses) < len(endpoints):
            for url in endpoints:
                if any(url == seen for seen, _ in statuses):
                    continue
                try:
                    with urllib.request.urlopen(url, timeout=2) as response:
                        statuses.append((url, int(response.status)))
                except (urllib.error.URLError, OSError, ValueError):
                    pass
            if len(statuses) < len(endpoints):
                time.sleep(0.25)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    ok = len(statuses) == len(endpoints) and all(200 <= code < 400 for _, code in statuses)
    return RuntimeReport(
        gate="PASS" if ok else "FAIL",
        command=tuple(command),
        endpoints=tuple(endpoints),
        statuses=tuple(statuses),
        duration_s=round(min(timeout_s, max(0, timeout_s - (deadline - time.monotonic()))), 3),
        note="runtime endpoints probed" if ok else "one or more endpoints did not respond",
    )
