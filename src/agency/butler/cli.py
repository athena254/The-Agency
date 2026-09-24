"""Butler CLI — operate the Butler HTTP service from the terminal.

Commands
--------
* ``butler start`` — launch the server (foreground or ``--daemon``).
* ``butler stop`` — stop a daemonised server.
* ``butler status`` — show health via ``GET /v1/health``.
* ``butler agents`` — list agents via ``GET /v1/agents``.

Environment
-----------
BUTLER_SERVER_URL:
    Default server base URL (overridden by ``--server-url`` / ``-u``).
BUTLER_HOST / BUTLER_PORT:
    Defaults for ``butler start``.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import structlog
import typer
from rich.console import Console
from rich.json import JSON
from rich.panel import Panel
from rich.table import Table

from agency.butler.config import ButlerConfig

log = structlog.get_logger(__name__)

console = Console()
err_console = Console(stderr=True)

app: typer.Typer = typer.Typer(
    name="butler",
    help="Conversational gateway for The Agency.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

_DEFAULTS = ButlerConfig()
DEFAULT_SERVER_URL = os.environ.get(
    "BUTLER_SERVER_URL", f"http://{_DEFAULTS.host}:{_DEFAULTS.port}"
)
PID_FILE = Path(os.environ.get("BUTLER_PID_FILE", str(Path.home() / ".theagency" / "butler.pid")))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _server_url(value: str | None = None) -> str:
    """Resolve the server base URL."""
    return (value or os.environ.get("BUTLER_SERVER_URL", DEFAULT_SERVER_URL)).rstrip("/")


def _client(base: str, timeout: float = 15.0) -> httpx.Client:
    return httpx.Client(base_url=base, timeout=timeout)


def _fail(message: str, *, code: int = 1) -> None:
    err_console.print(f"[bold red]Error:[/bold red] {message}")
    raise typer.Exit(code)


def _handle_response(response: httpx.Response) -> Any:
    try:
        payload = response.json() if response.content else None
    except ValueError:
        payload = response.text
    if response.is_error:
        detail: str
        if isinstance(payload, dict):
            detail = str(payload.get("detail", response.text))
        else:
            detail = str(payload or response.text)
        _fail(f"server returned {response.status_code}: {detail}")
    return payload


def _table(title: str, columns: list[str]) -> Table:
    table = Table(title=title, show_lines=False, header_style="bold cyan")
    for column in columns:
        table.add_column(column, overflow="fold")
    return table


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


@app.command("start")
def start(
    host: str = typer.Option(_DEFAULTS.host, "--host", help="Interface to bind."),
    port: int = typer.Option(_DEFAULTS.port, "--port", help="Port to bind."),
    reload: bool = typer.Option(False, "--reload/--no-reload", help="Enable auto-reload."),
    daemon: bool = typer.Option(False, "--daemon/--foreground", help="Run detached in background."),
    log_level: str = typer.Option("info", "--log-level", help="Uvicorn log level."),
    workers: int = typer.Option(1, "--workers", min=1, help="Worker processes."),
) -> None:
    """Launch the Butler server."""
    if daemon:
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "agency.butler.server:app",
            "--host",
            host,
            "--port",
            str(port),
            "--log-level",
            log_level,
        ]
        if reload:
            cmd.append("--reload")
        log.info("butler.start.daemon", host=host, port=port)
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        PID_FILE.write_text(str(proc.pid), encoding="utf-8")
        console.print(f"[green]Butler starting[/green] (pid {proc.pid}) on http://{host}:{port}")
        return

    console.print(f"[green]Starting Butler[/green] on http://{host}:{port} (Ctrl+C to stop)")
    log.info("butler.start.foreground", host=host, port=port, reload=reload)
    try:
        import uvicorn

        uvicorn.run(
            "agency.butler.server:app",
            host=host,
            port=port,
            reload=reload,
            log_level=log_level,
            workers=workers,
        )
    except ImportError:
        _fail("uvicorn is not installed. Install it with: pip install 'uvicorn[standard]'")


@app.command("stop")
def stop() -> None:
    """Stop a daemonised Butler server started with ``butler start --daemon``."""
    if not PID_FILE.exists():
        _fail(f"no pid file at {PID_FILE}; is the server running?")
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        _fail(f"corrupt pid file at {PID_FILE}")
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        console.print(f"[yellow]No process {pid}; removing stale pid file.[/yellow]")
        PID_FILE.unlink(missing_ok=True)
        return
    except OSError as exc:
        _fail(f"could not signal pid {pid}: {exc}")
        return
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    PID_FILE.unlink(missing_ok=True)
    console.print(f"[green]Stopped Butler[/green] (pid {pid})")


@app.command("status")
def status(
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u", help="API base URL."),
    timeout: float = typer.Option(10.0, "--timeout", help="Request timeout in seconds."),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Show Butler health."""
    base = _server_url(server_url)
    try:
        with _client(base, timeout=timeout) as client:
            response = client.get("/v1/health")
    except httpx.ConnectError:
        _fail(f"cannot reach Butler at {base}; is it running? (butler start)")
        return
    payload = _handle_response(response)
    if output_json:
        console.print(JSON(json.dumps(payload, default=str)))
        return
    status_value = payload.get("status", "unknown") if isinstance(payload, dict) else "unknown"
    style = "green" if status_value == "ok" else "yellow"
    body = json.dumps(payload, indent=2, default=str) if isinstance(payload, dict) else str(payload)
    console.print(
        Panel(body, title=f"Butler status: [{style}]{status_value}[/{style}]", expand=False)
    )


@app.command("agents")
def agents(
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u", help="API base URL."),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """List agents known to the Butler."""
    base = _server_url(server_url)
    try:
        with _client(base) as client:
            payload = _handle_response(client.get("/v1/agents"))
    except httpx.ConnectError:
        _fail(f"cannot reach Butler at {base}; is it running? (butler start)")
        return
    if output_json:
        console.print(JSON(json.dumps(payload, default=str)))
        return
    items = payload if isinstance(payload, list) else []
    table = _table("Butler agents", ["ID", "Name", "Domain", "Trust", "Capabilities"])
    for item in items:
        caps = item.get("capabilities", [])
        cap_names = ", ".join(c.get("name", "?") if isinstance(c, dict) else str(c) for c in caps)
        table.add_row(
            str(item.get("id", "")),
            str(item.get("name", "")),
            str(item.get("domain", "")),
            str(item.get("trust_level", "")),
            cap_names,
        )
    console.print(table)
    console.print(f"[dim]{len(items)} agent(s)[/dim]")


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
