"""Agency command-line interface.

The CLI is a thin, human-friendly client over the Agency HTTP API
(:mod:`agency.api.server`). Every command that needs server data performs a
single HTTP call and renders the result with Rich.

Command surface
---------------
* ``agency start`` / ``agency stop`` / ``agency status`` — server lifecycle.
* ``agency agent list`` / ``agency agent create`` — identity management.
* ``agency task create`` / ``agency task list`` — work management.
* ``agency memory search`` — full-text memory lookup.
* ``agency evidence list`` — filtered finding browser.
* ``agency risk report`` — risk vector + category for one finding.

Environment
-----------
AGENCY_SERVER_URL:
    Default server base URL (overridden by ``--server-url`` / ``-u``).
AGENCY_API_TOKEN:
    Optional bearer token sent as ``Authorization`` header.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated, Any

import httpx
import structlog
import typer
from rich.console import Console
from rich.json import JSON
from rich.panel import Panel
from rich.table import Table

log = structlog.get_logger(__name__)

console = Console()
err_console = Console(stderr=True)

app: typer.Typer = typer.Typer(
    name="agency",
    help="Security-first autonomous agent system.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

agent_app = typer.Typer(help="Manage agent identities.", no_args_is_help=True)
task_app = typer.Typer(help="Create and inspect tasks.", no_args_is_help=True)
memory_app = typer.Typer(help="Search agent memory.", no_args_is_help=True)
evidence_app = typer.Typer(help="Browse evidence findings.", no_args_is_help=True)
risk_app = typer.Typer(help="Inspect risk assessments.", no_args_is_help=True)

app.add_typer(agent_app, name="agent")
app.add_typer(task_app, name="task")
app.add_typer(memory_app, name="memory")
app.add_typer(evidence_app, name="evidence")
app.add_typer(risk_app, name="risk")

DEFAULT_SERVER_URL = os.environ.get("AGENCY_SERVER_URL", "http://127.0.0.1:8000")
PID_FILE = Path(os.environ.get("AGENCY_PID_FILE", str(Path.home() / ".theagency" / "agency.pid")))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _server_url(value: str | None = None) -> str:
    base = (value or os.environ.get("AGENCY_SERVER_URL", DEFAULT_SERVER_URL)).rstrip("/")
    return base


def _headers() -> dict[str, str]:
    token = os.environ.get("AGENCY_API_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _client(base: str, timeout: float = 15.0) -> httpx.Client:
    return httpx.Client(base_url=base, timeout=timeout, headers=_headers())


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


def _print_json(data: Any) -> None:
    console.print(JSON(json.dumps(data, default=str)))


def _table(title: str, columns: list[str]) -> Table:
    table = Table(title=title, show_lines=False, header_style="bold cyan")
    for column in columns:
        table.add_column(column, overflow="fold")
    return table


# --------------------------------------------------------------------------- #
# Server lifecycle: start / stop / status
# --------------------------------------------------------------------------- #


@app.command("start")
def start(
    host: str = typer.Option("127.0.0.1", "--host", help="Interface to bind."),
    port: int = typer.Option(8000, "--port", help="Port to bind."),
    reload: bool = typer.Option(False, "--reload/--no-reload", help="Enable uvicorn auto-reload."),
    daemon: bool = typer.Option(False, "--daemon/--foreground", help="Run detached in background."),
    log_level: str = typer.Option("info", "--log-level", help="Uvicorn log level."),
    workers: int = typer.Option(1, "--workers", min=1, help="Uvicorn worker processes."),
) -> None:
    """Launch the Agency API server."""
    if daemon:
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "agency.api.server:app",
            "--host",
            host,
            "--port",
            str(port),
            "--log-level",
            log_level,
        ]
        if reload:
            cmd.append("--reload")
        log.info("agency.start.daemon", host=host, port=port)
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        PID_FILE.write_text(str(proc.pid), encoding="utf-8")
        console.print(
            f"[green]Agency server starting[/green] (pid {proc.pid}) on http://{host}:{port}"
        )
        return

    console.print(f"[green]Starting Agency server[/green] on http://{host}:{port} (Ctrl+C to stop)")
    log.info("agency.start.foreground", host=host, port=port, reload=reload)
    try:
        import uvicorn

        uvicorn.run(
            "agency.api.server:app",
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
    """Stop a daemonised Agency server started with ``agency start --daemon``."""
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
    # Wait briefly for shutdown.
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    PID_FILE.unlink(missing_ok=True)
    console.print(f"[green]Stopped Agency server[/green] (pid {pid})")


@app.command("status")
def status(
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u", help="API base URL."),
    timeout: float = typer.Option(10.0, "--timeout", help="Request timeout in seconds."),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Show server health and component summary."""
    base = _server_url(server_url)
    try:
        with _client(base, timeout=timeout) as client:
            response = client.get("/v1/health")
    except httpx.ConnectError:
        _fail(f"cannot reach server at {base}; is it running? (agency start)")
        return
    payload = _handle_response(response)
    if output_json:
        _print_json(payload)
        return
    status_value = payload.get("status", "unknown") if isinstance(payload, dict) else "unknown"
    style = "green" if status_value == "ok" else "yellow"
    body = json.dumps(payload, indent=2, default=str) if isinstance(payload, dict) else str(payload)
    console.print(
        Panel(body, title=f"Agency status: [{style}]{status_value}[/{style}]", expand=False)
    )


# --------------------------------------------------------------------------- #
# Agents: agency agent list / create
# --------------------------------------------------------------------------- #


@agent_app.command("list")
def agent_list(
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
    domain: str | None = typer.Option(None, "--domain", "-d", help="Filter by domain."),
    include_revoked: bool = typer.Option(
        False, "--include-revoked", help="Include revoked agents."
    ),
    limit: int = typer.Option(100, "--limit", min=1, max=1000),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """List registered agents."""
    base = _server_url(server_url)
    params: dict[str, Any] = {"limit": limit, "include_revoked": include_revoked}
    if domain:
        params["domain"] = domain
    with _client(base) as client:
        payload = _handle_response(client.get("/v1/agents", params=params))
    if output_json:
        _print_json(payload)
        return
    agents = payload if isinstance(payload, list) else payload.get("agents", payload)
    table = _table("Agents", ["ID", "Name", "Domain", "Trust", "Capabilities", "Revoked"])
    for item in agents or []:
        caps = item.get("capabilities", [])
        cap_names = ", ".join(c.get("name", "?") if isinstance(c, dict) else str(c) for c in caps)
        table.add_row(
            str(item.get("id", "")),
            str(item.get("name", "")),
            str(item.get("domain", "")),
            str(item.get("trust_level", "")),
            cap_names,
            str(item.get("revoked", False)),
        )
    console.print(table)
    console.print(f"[dim]{len(agents or [])} agent(s)[/dim]")


@agent_app.command("create")
def agent_create(
    name: str = typer.Option(..., "--name", "-n", help="Human-readable agent name."),
    domain: str = typer.Option("general", "--domain", "-d", help="Operational domain."),
    trust_level: str = typer.Option("unknown", "--trust", help="Trust level."),
    capability: Annotated[
        list[str] | None, typer.Option("--capability", "-c", help="Capability (repeatable).")
    ] = None,
    agent_id: str | None = typer.Option(
        None, "--id", help="Explicit agent id (default: generated)."
    ),
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Register a new agent identity."""
    base = _server_url(server_url)
    body: dict[str, Any] = {
        "name": name,
        "domain": domain,
        "trust_level": trust_level,
        "capabilities": capability or [],
    }
    if agent_id:
        body["id"] = agent_id
    with _client(base) as client:
        payload = _handle_response(client.post("/v1/agents", json=body))
    if output_json:
        _print_json(payload)
        return
    console.print(
        Panel(
            f"[green]Registered agent[/green] [bold]{payload.get('id')}[/bold] ({payload.get('name')})",
            expand=False,
        )
    )


@agent_app.command("propose")
def agent_propose(
    name: str = typer.Option(..., "--name", "-n", help="Human-readable agent name."),
    domain: str = typer.Option("general", "--domain", "-d", help="Operational domain."),
    capability: Annotated[
        list[str] | None, typer.Option("--capability", "-c", help="Capability (repeatable).")
    ] = None,
    proposer: str = typer.Option("user", "--proposer", help="Proposer identity."),
    quorum: float = typer.Option(0.66, "--quorum", help="Quorum threshold (0.0-1.0)."),
    ttl: int = typer.Option(3600, "--ttl", help="Proposal TTL in seconds."),
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Propose a new agent via governance (requires quorum to pass)."""
    base = _server_url(server_url)
    body: dict[str, Any] = {
        "name": name,
        "domain": domain,
        "capabilities": capability or [],
        "proposer": proposer,
        "quorum": quorum,
        "ttl": ttl,
    }
    with _client(base) as client:
        payload = _handle_response(client.post("/v1/governance/propose", json=body))
    if output_json:
        _print_json(payload)
        return
    console.print(
        Panel(
            f"[yellow]Agent proposed[/yellow] [bold]{payload.get('proposal_id', payload.get('id'))}[/bold]\n"
            f"[dim]Status: {payload.get('status', 'open')} | Quorum: {quorum} | Votes: {payload.get('vote_count', 0)}[/dim]",
            expand=False,
        )
    )


# --------------------------------------------------------------------------- #
# Tasks: agency task create / list
# --------------------------------------------------------------------------- #


@task_app.command("create")
def task_create(
    title: str = typer.Option(..., "--title", "-t", help="Short task title."),
    created_by: str = typer.Option(..., "--created-by", help="Creator agent/operator id."),
    priority: int = typer.Option(0, "--priority", "-p", min=0, max=10),
    input_json: str = typer.Option("{}", "--input", "-i", help="JSON input payload."),
    parent_id: str | None = typer.Option(None, "--parent-id", help="Parent task id."),
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Create a new task."""
    try:
        task_input = json.loads(input_json)
    except json.JSONDecodeError as exc:
        _fail(f"invalid --input JSON: {exc}")
        return
    if not isinstance(task_input, dict):
        _fail("--input must decode to a JSON object.")
        return
    body: dict[str, Any] = {
        "title": title,
        "created_by": created_by,
        "priority": priority,
        "input": task_input,
    }
    if parent_id:
        body["parent_id"] = parent_id
    base = _server_url(server_url)
    with _client(base) as client:
        payload = _handle_response(client.post("/v1/tasks", json=body))
    if output_json:
        _print_json(payload)
        return
    console.print(
        Panel(
            f"[green]Created task[/green] [bold]{payload.get('task_id')}[/bold]\n"
            f"title: {payload.get('title')}\nstatus: {payload.get('status')}",
            expand=False,
        )
    )


@task_app.command("list")
def task_list(
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
    status_filter: str | None = typer.Option(None, "--status", "-s", help="Filter by status."),
    limit: int = typer.Option(100, "--limit", min=1, max=1000),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """List tasks, optionally filtered by status."""
    base = _server_url(server_url)
    params: dict[str, Any] = {"limit": limit}
    if status_filter:
        params["status"] = status_filter
    with _client(base) as client:
        payload = _handle_response(client.get("/v1/tasks", params=params))
    if output_json:
        _print_json(payload)
        return
    tasks = payload if isinstance(payload, list) else payload.get("tasks", payload)
    table = _table("Tasks", ["Task ID", "Title", "Status", "Priority", "Created by"])
    for item in tasks or []:
        table.add_row(
            str(item.get("task_id", "")),
            str(item.get("title", "")),
            str(item.get("status", "")),
            str(item.get("priority", "")),
            str(item.get("created_by", "")),
        )
    console.print(table)
    console.print(f"[dim]{len(tasks or [])} task(s)[/dim]")


# --------------------------------------------------------------------------- #
# Memory: agency memory search
# --------------------------------------------------------------------------- #


@memory_app.command("search")
def memory_search(
    query: str = typer.Argument(..., help="Free-text query."),
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
    agent_id: str | None = typer.Option(None, "--agent-id", help="Scope to one agent."),
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=100),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Search agent memory (full-text)."""
    base = _server_url(server_url)
    params: dict[str, Any] = {"q": query, "limit": limit}
    if agent_id:
        params["agent_id"] = agent_id
    with _client(base) as client:
        payload = _handle_response(client.get("/v1/memory/search", params=params))
    if output_json:
        _print_json(payload)
        return
    items = (
        payload if isinstance(payload, list) else payload.get("results", payload.get("items", []))
    )
    table = _table(f"Memory results for '{query}'", ["ID", "Agent", "Tier", "Content"])
    for item in items or []:
        content = str(item.get("content", ""))
        table.add_row(
            str(item.get("id", ""))[:12],
            str(item.get("agent_id", "")),
            str(item.get("tier", "")),
            content[:120],
        )
    console.print(table)
    console.print(f"[dim]{len(items or [])} result(s)[/dim]")


# --------------------------------------------------------------------------- #
# Evidence: agency evidence list
# --------------------------------------------------------------------------- #


@evidence_app.command("list")
def evidence_list(
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
    severity: str | None = typer.Option(None, "--severity", help="Filter by severity."),
    target: str | None = typer.Option(None, "--target", help="Substring match on target."),
    verification_status: str | None = typer.Option(None, "--verification-status"),
    limit: int = typer.Option(100, "--limit", min=1, max=1000),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """List evidence findings with optional filters."""
    base = _server_url(server_url)
    params: dict[str, Any] = {"limit": limit}
    if severity:
        params["severity"] = severity
    if target:
        params["target"] = target
    if verification_status:
        params["verification_status"] = verification_status
    with _client(base) as client:
        payload = _handle_response(client.get("/v1/evidence", params=params))
    if output_json:
        _print_json(payload)
        return
    findings = payload if isinstance(payload, list) else payload.get("findings", [])
    table = _table("Findings", ["ID", "Target", "Severity", "Confidence", "Verification"])
    for item in findings or []:
        table.add_row(
            str(item.get("id", "")),
            str(item.get("target", "")),
            str(item.get("severity", "-")),
            str(item.get("confidence", "")),
            str(item.get("verification_status", "")),
        )
    console.print(table)
    console.print(f"[dim]{len(findings or [])} finding(s)[/dim]")


# --------------------------------------------------------------------------- #
# Risk: agency risk report
# --------------------------------------------------------------------------- #


@risk_app.command("report")
def risk_report(
    finding_id: str = typer.Argument(..., help="Finding id to assess."),
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Show the risk vector and category for a finding."""
    base = _server_url(server_url)
    with _client(base) as client:
        payload = _handle_response(client.get(f"/v1/risk/{finding_id}"))
    if output_json:
        _print_json(payload)
        return
    risk = payload.get("risk", payload) if isinstance(payload, dict) else {}
    category = payload.get("category", "?") if isinstance(payload, dict) else "?"
    table = _table(f"Risk report — {finding_id} [{category}]", ["Dimension", "Value"])
    for key in (
        "impact",
        "likelihood",
        "confidence",
        "exposure",
        "exploitability",
        "detectability",
        "reversibility",
        "blast_radius",
    ):
        table.add_row(key, str(risk.get(key, "-") if isinstance(risk, dict) else "-"))
    console.print(table)
    if isinstance(payload, dict) and payload.get("category"):
        console.print(f"[bold]Category:[/bold] {payload['category']}")


def main() -> None:
    """Console-script entry point (``agency = agency.cli:main``)."""
    app()


if __name__ == "__main__":
    main()
