"""Forge Run slice: smoke-test a worktree app over HTTP.

Picks a free port, starts the app according to a simple manifest, waits
for HTTP 200, then kills the process. KISS: FastAPI/uvicorn when the app
defines a FastAPI ``app``, otherwise ``python app.py``, otherwise
``npm run start`` for Node, otherwise a plain ``http.server`` fallback.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)

SmokeGate = Literal["PASS", "FAIL"]

CAVEATS: tuple[str, ...] = ("smoke_only", "local_only", "release_not_authorized")

_PYTHON_CANDIDATES = ("app.py", "main.py", "server.py", "src/app.py", "src/main.py")

MAX_LOG_BYTES = 16 * 1024


class SmokeReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(min_length=1)
    created_at: datetime
    root: str
    gate: SmokeGate
    port: int = 0
    command: tuple[str, ...] = ()
    status_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    logs: str = ""
    caveats: tuple[str, ...] = CAVEATS
    note: str = "Local smoke test; HTTP 200 required; does not authorize merge or deploy."


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _npm_cmd() -> str:
    name = "npm.cmd" if os.name == "nt" else "npm"
    return name


def build_command(root: str | Path, port: int) -> list[str]:
    """Return the start command for *root*, per the simple manifest."""
    real = Path(root)
    for rel in _PYTHON_CANDIDATES:
        candidate = real / rel
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if "FastAPI" in text:
            module = rel[: -len(".py")].replace("/", ".").replace("\\", ".")
            return [
                sys.executable,
                "-m",
                "uvicorn",
                f"{module}:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ]
        return [sys.executable, str(candidate)]
    pkg = real / "package.json"
    if pkg.is_file():
        return [_npm_cmd(), "run", "start", "--", "--port", str(port)]
    return [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"]


_build_command = build_command


def _try_http(url: str, timeout: float = 1.0) -> int | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception:  # noqa: BLE001 - connection refused while booting
        return None


def smoke_app(
    root: str | Path,
    *,
    timeout_s: float = 5.0,
    poll_interval_s: float = 0.2,
    port: int | None = None,
) -> SmokeReport:
    real = Path(root)
    chosen = int(port) if port else _free_port()
    cmd = build_command(real, chosen)
    url = f"http://127.0.0.1:{chosen}/"

    env = dict(os.environ)
    env["PORT"] = str(chosen)
    env["HOST"] = "127.0.0.1"
    existing = env.get("PYTHONPATH", "")
    extra = str(real) + (os.pathsep + str(real / "src") if (real / "src").is_dir() else "")
    env["PYTHONPATH"] = extra + (os.pathsep + existing if existing else "")

    gate: SmokeGate = "FAIL"
    status_code: int | None = None
    stdout = ""
    stderr = ""
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(real),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        deadline = time.monotonic() + max(0.1, float(timeout_s))
        while time.monotonic() < deadline:
            code = _try_http(url)
            if code is not None:
                status_code = code
                if code == 200:
                    gate = "PASS"
                break
            if proc.poll() is not None:
                break
            time.sleep(max(0.05, float(poll_interval_s)))
        if gate == "FAIL" and status_code is None:
            code = _try_http(url)
            if code is not None:
                status_code = code
                if code == 200:
                    gate = "PASS"
    except Exception as exc:  # noqa: BLE001
        stderr = f"{type(exc).__name__}: {exc}"
        gate = "FAIL"
    finally:
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        out, err = proc.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        out, err = proc.communicate(timeout=3)
                else:
                    out, err = proc.communicate(timeout=3)
                stdout = (out or "")[:MAX_LOG_BYTES]
                stderr = (stderr + "\n" + (err or "")) if stderr and err else (err or stderr or "")
                stderr = stderr[:MAX_LOG_BYTES]
            except Exception:  # noqa: BLE001
                try:
                    if proc.poll() is None:
                        proc.kill()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("forge.smoke_kill_failed", error=str(exc))

    logs = "\n".join(part for part in (stdout, stderr) if part).strip()[:MAX_LOG_BYTES]
    report = SmokeReport(
        report_id=str(uuid4()),
        created_at=datetime.now(UTC),
        root=str(real),
        gate=gate,
        port=chosen,
        command=tuple(cmd),
        status_code=status_code,
        stdout=stdout,
        stderr=stderr,
        logs=logs,
    )
    logger.info("forge.smoke", root=str(real), gate=gate, port=chosen, status=status_code)
    return report


run_smoke = smoke_app
smoke = smoke_app

__all__ = [
    "CAVEATS",
    "SmokeGate",
    "SmokeReport",
    "build_command",
    "run_smoke",
    "smoke",
    "smoke_app",
]
