"""Repeatable capacity probe for the Forge control plane.

Measures MCP control-plane calls (admission-like traffic) and isolated worktree
creation. It never builds software, never writes to a real repo, and never
pushes anything; reports must not be read as end-to-end build latency.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agency.forge.mcp_server import ForgeMcpServer, McpConfig


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * (len(ordered) - 1)))]


async def _probe(requests: int, concurrency: int, queue_timeout: float) -> dict[str, object]:
    server = ForgeMcpServer(
        McpConfig(max_concurrent_calls=concurrency, queue_timeout_s=queue_timeout)
    )
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": "forge_capabilities", "arguments": {}},
    }

    async def one(index: int) -> tuple[str, float]:
        started = time.perf_counter()
        try:
            await server.handle({**payload, "id": index})
            return "accepted", time.perf_counter() - started
        except Exception:  # noqa: BLE001 - rejected calls are a measured outcome
            return "rejected", time.perf_counter() - started

    outcomes = await asyncio.gather(*(one(i) for i in range(requests)))
    accepted = [elapsed for kind, elapsed in outcomes if kind == "accepted"]
    rejected = [elapsed for kind, elapsed in outcomes if kind == "rejected"]
    return {
        "accepted": len(accepted),
        "rejected": len(rejected),
        "p50_ms": round(_percentile(accepted, 0.5) * 1000, 2),
        "p95_ms": round(_percentile(accepted, 0.95) * 1000, 2),
        "max_ms": round(max(accepted, default=0.0) * 1000, 2),
    }


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _worktree_samples(count: int) -> list[float]:
    durations: list[float] = []
    with tempfile.TemporaryDirectory(prefix="forge-capacity-") as tmp:
        root = Path(tmp) / "scratch-repo"
        root.mkdir()
        _git(root, "init", "-q")
        _git(root, "config", "user.email", "capacity@local")
        _git(root, "config", "user.name", "capacity")
        (root / "README.md").write_text("capacity probe\n", encoding="utf-8")
        _git(root, "add", "README.md")
        _git(root, "commit", "-qm", "init")
        for index in range(count):
            target = Path(tmp) / f"wt-{index}"
            started = time.perf_counter()
            _git(root, "worktree", "add", "--detach", str(target), "HEAD")
            durations.append(time.perf_counter() - started)
            _git(root, "worktree", "remove", "--force", str(target))
    return durations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=64)
    parser.add_argument("--queue-timeout", type=float, default=30.0)
    parser.add_argument("--worktree-samples", type=int, default=10)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    control = asyncio.run(_probe(args.requests, args.concurrency, args.queue_timeout))
    worktrees = _worktree_samples(args.worktree_samples)
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "requests": args.requests,
        "concurrency": args.concurrency,
        "queue_timeout_s": args.queue_timeout,
        "control_plane": control,
        "worktree_creation": {
            "samples": len(worktrees),
            "p50_ms": round(_percentile(worktrees, 0.5) * 1000, 2),
            "p95_ms": round(_percentile(worktrees, 0.95) * 1000, 2),
        },
        "caveats": [
            "control_plane_only",
            "not_end_to_end_build_latency",
            "single_process_in_memory_probe",
        ],
    }
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
