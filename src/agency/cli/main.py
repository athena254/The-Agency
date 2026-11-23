"""Agency command-line interface.

The CLI is a thin, human-friendly client over the Agency HTTP API
(:mod:`agency.api.server`). Every command that needs server data performs a
single HTTP call and renders the result with Rich.

Command surface
---------------
* ``agency start`` / ``agency stop`` / ``agency status`` — server lifecycle.
* ``agency agent list`` / ``agency agent create`` — identity management.
* ``agency agent request`` — request a governed agent from a purpose.
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
from typing import Annotated, Any, NoReturn

import httpx
import structlog
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agency.autonomy.loop_driver import LoopConfig, LoopDriver
from agency.autonomy.loop_gates import ForgeGateRunner
from agency.autonomy.loop_queue import load_queue
from agency.autonomy.loop_state import LoopBudgets, LoopStore
from agency.autonomy.loop_workers import (
    ClaudeCodeWorker,
    CodexWorker,
    CursorWorker,
    ExternalWorker,
    OpencodeWorker,
    PiWorker,
    striker_label,
)
from agency.forge.external_agent import run_external_agent
from agency.forge.tool_catalog import get_tool, is_available, list_tools

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

forge_app = typer.Typer(help="Run governed coding-agent tasks.", no_args_is_help=True)

loop_app = typer.Typer(
    help="Run the deterministic autonomous loop (Ralph-style).", no_args_is_help=True
)

bridge_app = typer.Typer(help="Inspect and execute external bridges.", no_args_is_help=True)

governance_app = typer.Typer(help="Governance proposals and votes.", no_args_is_help=True)

app.add_typer(agent_app, name="agent")
app.add_typer(task_app, name="task")
app.add_typer(memory_app, name="memory")
app.add_typer(evidence_app, name="evidence")
app.add_typer(risk_app, name="risk")
app.add_typer(forge_app, name="forge")
app.add_typer(loop_app, name="loop")
app.add_typer(bridge_app, name="bridge")
app.add_typer(governance_app, name="governance")

DEFAULT_SERVER_URL = os.environ.get("AGENCY_SERVER_URL", "http://127.0.0.1:8000")
PID_FILE = Path(os.environ.get("AGENCY_PID_FILE", str(Path.home() / ".theagency" / "agency.pid")))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _server_url(value: str | None = None) -> str:
    base = (value or os.environ.get("AGENCY_SERVER_URL", DEFAULT_SERVER_URL)).rstrip("/")
    return base


def _headers(token: str | None = None) -> dict[str, str]:
    value = token or os.environ.get("AGENCY_API_TOKEN")
    if value:
        return {"Authorization": f"Bearer {value}"}
    return {}


def _client(
    base: str, timeout: float = 15.0, headers: dict[str, str] | None = None
) -> httpx.Client:
    return httpx.Client(base_url=base, timeout=timeout, headers=headers or _headers())


def _fail(message: str, *, code: int = 1) -> NoReturn:
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
    # ``--json`` is the machine-readable contract: plain JSON on stdout, no
    # Rich decoration, so `| python -c "import json;json.load(sys.stdin)"` works.
    print(json.dumps(data, default=str, indent=2))


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
            "--workers",
            str(workers),
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


@agent_app.command("get")
def agent_get(
    agent_id: str = typer.Argument(..., help="Agent id."),
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Fetch one agent by id."""
    base = _server_url(server_url)
    with _client(base) as client:
        payload = _handle_response(client.get(f"/v1/agents/{agent_id}"))
    if output_json:
        _print_json(payload)
        return
    agent = payload if isinstance(payload, dict) else {}
    table = _table(f"Agent — {agent_id}", ["Field", "Value"])
    for key in ("id", "name", "domain", "trust_level", "revoked"):
        table.add_row(key, str(agent.get(key, "-")))
    caps = agent.get("capabilities", [])
    cap_names = ", ".join(c.get("name", "?") if isinstance(c, dict) else str(c) for c in caps)
    table.add_row("capabilities", cap_names)
    console.print(table)


@agent_app.command("revoke")
def agent_revoke(
    agent_id: str = typer.Argument(..., help="Agent id to revoke."),
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Revoke an agent identity (record retained for audit)."""
    base = _server_url(server_url)
    with _client(base) as client:
        payload = _handle_response(client.delete(f"/v1/agents/{agent_id}"))
    if output_json:
        _print_json(payload)
        return
    console.print(f"[green]Revoked agent[/green] [bold]{agent_id}[/bold]")


@agent_app.command("capabilities")
def agent_capabilities(
    agent_id: str = typer.Argument(..., help="Agent id."),
    capability: Annotated[
        list[str] | None, typer.Option("--capability", "-c", help="Capability (repeatable).")
    ] = None,
    mode: str = typer.Option("merge", "--mode", help="merge or replace."),
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Merge or replace an agent's capability set."""
    if not capability:
        _fail("at least one --capability is required")
        return
    if mode not in ("merge", "replace"):
        _fail("--mode must be 'merge' or 'replace'")
        return
    base = _server_url(server_url)
    with _client(base) as client:
        payload = _handle_response(
            client.patch(
                f"/v1/agents/{agent_id}/capabilities",
                json={"capabilities": capability, "mode": mode},
            )
        )
    if output_json:
        _print_json(payload)
        return
    console.print(f"[green]Updated capabilities[/green] for agent [bold]{agent_id}[/bold]")
    console.print(f"[dim]mode: {mode} | capabilities: {', '.join(capability)}[/dim]")


@agent_app.command("request")
def agent_request(
    blueprint_id: str = typer.Option(
        ..., "--blueprint-id", "-b", help="Blueprint id for the new governed agent."
    ),
    name: str = typer.Option(..., "--name", "-n", help="Human-readable agent name."),
    purpose: str = typer.Option(
        ..., "--purpose", "-p", help="Free-text purpose the agent is manufactured for."
    ),
    domain: str = typer.Option(
        "general", "--domain", "-d", help="Operational domain (must be allowlisted server-side)."
    ),
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
    token: str | None = typer.Option(
        None, "--token", help="Bearer token (overrides AGENCY_API_TOKEN)."
    ),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Request a governed agent (server-side authorizer + domain allowlist apply)."""
    base = _server_url(server_url)
    body: dict[str, Any] = {
        "blueprint_id": blueprint_id,
        "name": name,
        "purpose": purpose,
        "domain": domain,
    }
    with _client(base, headers=_headers(token)) as client:
        payload = _handle_response(client.post("/v1/agent-requests", json=body))
    if output_json:
        _print_json(payload)
        return
    console.print(
        Panel(
            f"[green]Requested agent[/green] [bold]{payload.get('agent_id')}[/bold]\n"
            f"blueprint: {payload.get('blueprint_id')} v{payload.get('version')}\n"
            f"note: {payload.get('note', '')}",
            expand=False,
        )
    )


# --------------------------------------------------------------------------- #
# Tasks: agency task create / list
# --------------------------------------------------------------------------- #


@task_app.command("create")
def task_create(
    title: str = typer.Option(..., "--title", "-t", help="Short task title."),
    created_by: str = typer.Option(
        "",
        "--created-by",
        help="Creator id (owners only; other callers are bound to their token identity).",
    ),
    agent_id: str | None = typer.Option(
        None, "--agent", help="Registered agent that should execute the task."
    ),
    priority: int = typer.Option(0, "--priority", "-p", min=0, max=10),
    input_json: str = typer.Option("{}", "--input", "-i", help="JSON input payload."),
    parent_id: str | None = typer.Option(None, "--parent-id", help="Parent task id."),
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
    token: str | None = typer.Option(
        None, "--token", help="Bearer token (overrides AGENCY_API_TOKEN)."
    ),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Submit a task for dispatcher execution."""
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
    if agent_id:
        body["agent_id"] = agent_id
    if parent_id:
        body["parent_id"] = parent_id
    base = _server_url(server_url)
    with _client(base, headers=_headers(token)) as client:
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
    token: str | None = typer.Option(
        None, "--token", help="Bearer token (overrides AGENCY_API_TOKEN)."
    ),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """List your tasks, optionally filtered by status."""
    base = _server_url(server_url)
    params: dict[str, Any] = {"limit": limit}
    if status_filter:
        params["status"] = status_filter
    with _client(base, headers=_headers(token)) as client:
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


@task_app.command("get")
def task_get(
    task_id: str = typer.Argument(..., help="Task id."),
    wait: bool = typer.Option(
        False, "--wait", help="Poll until the task reaches a terminal state."
    ),
    wait_timeout: float = typer.Option(
        300.0, "--wait-timeout", min=1, help="Seconds to wait with --wait."
    ),
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
    token: str | None = typer.Option(
        None, "--token", help="Bearer token (overrides AGENCY_API_TOKEN)."
    ),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Fetch a task and its result; with --wait, poll until it finishes."""
    base = _server_url(server_url)
    headers = _headers(token)
    deadline = time.monotonic() + wait_timeout
    with _client(base, headers=headers) as client:
        payload = _handle_response(client.get(f"/v1/tasks/{task_id}"))
        if wait:
            while isinstance(payload, dict) and payload.get("status") in ("pending", "running"):
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.5)
                payload = _handle_response(client.get(f"/v1/tasks/{task_id}"))
    if output_json:
        _print_json(payload)
        return
    task = payload if isinstance(payload, dict) else {}
    table = _table(f"Task — {task_id}", ["Field", "Value"])
    for key in ("task_id", "title", "status", "priority", "created_by"):
        table.add_row(key, str(task.get(key, "-")))
    table.add_row("input", str(task.get("input", "-"))[:120])
    output = task.get("output") or {}
    if isinstance(output, dict) and output:
        table.add_row("result", str(output.get("summary", ""))[:2000])
        table.add_row("verification", str(output.get("verification", "-")))
    if task.get("error"):
        table.add_row("error", str(task.get("error")))
    console.print(table)


@task_app.command("status")
def task_status(
    task_id: str = typer.Argument(..., help="Task id."),
    status_value: str = typer.Argument(
        ..., help="New lifecycle state (e.g. pending, running, blocked, failed)."
    ),
    error: str | None = typer.Option(None, "--error", help="Optional error message."),
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
    token: str | None = typer.Option(
        None, "--token", help="Bearer token (overrides AGENCY_API_TOKEN)."
    ),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Transition a task into a new lifecycle state."""
    base = _server_url(server_url)
    with _client(base, headers=_headers(token)) as client:
        payload = _handle_response(
            client.patch(
                f"/v1/tasks/{task_id}/status",
                json={"status": status_value, "error": error},
            )
        )
    if output_json:
        _print_json(payload)
        return
    console.print(f"[green]Task[/green] [bold]{task_id}[/bold] -> [cyan]{status_value}[/cyan]")


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


@evidence_app.command("get")
def evidence_get(
    finding_id: str = typer.Argument(..., help="Finding id."),
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Fetch one finding by id."""
    base = _server_url(server_url)
    with _client(base) as client:
        payload = _handle_response(client.get(f"/v1/evidence/{finding_id}"))
    if output_json:
        _print_json(payload)
        return
    finding = payload if isinstance(payload, dict) else {}
    table = _table(f"Finding — {finding_id}", ["Field", "Value"])
    for key in ("id", "target", "severity", "confidence", "verification_status"):
        table.add_row(key, str(finding.get(key, "-")))
    console.print(table)


@evidence_app.command("trail")
def evidence_trail(
    finding_id: str = typer.Argument(..., help="Finding id."),
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Show the append-only evidence trail for a finding, oldest first."""
    base = _server_url(server_url)
    with _client(base) as client:
        payload = _handle_response(client.get(f"/v1/evidence/{finding_id}/trail"))
    if output_json:
        _print_json(payload)
        return
    entries = payload if isinstance(payload, list) else []
    table = _table(f"Evidence trail — {finding_id}", ["Timestamp", "Actor", "Action", "Detail"])
    for item in entries:
        table.add_row(
            str(item.get("created_at", "-")),
            str(item.get("actor", "-")),
            str(item.get("action", "-")),
            str(item.get("detail", "-"))[:100],
        )
    console.print(table)
    console.print(f"[dim]{len(entries)} entr(y|ies)[/dim]")


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


# --------------------------------------------------------------------------- #
# Bridges: agency bridge list / execute / health
# --------------------------------------------------------------------------- #


@bridge_app.command("list")
def bridge_list(
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """List registered bridges with capabilities and circuit state."""
    base = _server_url(server_url)
    with _client(base) as client:
        payload = _handle_response(client.get("/v1/bridges"))
    if output_json:
        _print_json(payload)
        return
    bridges = payload if isinstance(payload, list) else []
    table = _table("Bridges", ["Name", "Circuit", "Capabilities"])
    for item in bridges:
        caps = item.get("capabilities", {})
        cap_summary = (
            ", ".join(f"{k}:{v}" for k, v in caps.items()) if isinstance(caps, dict) else str(caps)
        )
        table.add_row(
            str(item.get("name", "-")),
            str(item.get("circuit", "-")),
            cap_summary,
        )
    console.print(table)
    console.print(f"[dim]{len(bridges)} bridge(s)[/dim]")


@bridge_app.command("execute")
def bridge_execute(
    name: str = typer.Argument(..., help="Bridge name from `bridge list`."),
    task: str = typer.Argument(..., help="Natural-language instruction for the bridge."),
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Execute a task via the named bridge behind its circuit breaker."""
    base = _server_url(server_url)
    with _client(base) as client:
        payload = _handle_response(client.post(f"/v1/bridges/{name}/execute", json={"task": task}))
    if output_json:
        _print_json(payload)
        return
    console.print(
        f"[green]Bridge[/green] [bold]{name}[/bold] -> [cyan]{payload.get('status', '-')}[/cyan]"
    )
    output = str(payload.get("output", ""))
    if output:
        console.print(Panel(output, title="Output", expand=False))
    if payload.get("error"):
        console.print(f"[red]Error:[/red] {payload['error']}")


@bridge_app.command("health")
def bridge_health(
    name: str = typer.Argument(..., help="Bridge name from `bridge list`."),
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Liveness probe for a single bridge."""
    base = _server_url(server_url)
    with _client(base) as client:
        payload = _handle_response(client.get(f"/v1/bridges/{name}/health"))
    if output_json:
        _print_json(payload)
        return
    healthy = bool(payload.get("healthy", False)) if isinstance(payload, dict) else False
    style = "green" if healthy else "red"
    console.print(f"Bridge [bold]{name}[/bold] healthy: [{style}]{healthy}[/{style}]")


# --------------------------------------------------------------------------- #
# Governance: agency governance proposals / vote
# --------------------------------------------------------------------------- #


@governance_app.command("proposals")
def governance_proposals(
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """List all open governance proposals."""
    base = _server_url(server_url)
    with _client(base) as client:
        payload = _handle_response(client.get("/v1/governance/proposals"))
    if output_json:
        _print_json(payload)
        return
    proposals = payload.get("proposals", []) if isinstance(payload, dict) else []
    table = _table(
        "Governance Proposals", ["ID", "Type", "Proposer", "Status", "Quorum", "Votes", "Expires"]
    )
    for item in proposals:
        table.add_row(
            str(item.get("proposal_id", "-")),
            str(item.get("proposal_type", "-")),
            str(item.get("proposer_id", "-")),
            str(item.get("status", "-")),
            str(item.get("quorum_required", "-")),
            str(item.get("vote_count", "-")),
            str(item.get("expires_at", "-")),
        )
    console.print(table)
    console.print(f"[dim]{len(proposals)} proposal(s)[/dim]")


@governance_app.command("vote")
def governance_vote(
    proposal_id: str = typer.Argument(..., help="Proposal id."),
    decision: str = typer.Argument(..., help="approve, deny, or abstain."),
    evidence: Annotated[
        list[str] | None, typer.Option("--evidence", "-e", help="Evidence reference (repeatable).")
    ] = None,
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Cast the authenticated owner's vote on an open proposal."""
    if decision not in ("approve", "deny", "abstain"):
        _fail("decision must be 'approve', 'deny', or 'abstain'")
        return
    base = _server_url(server_url)
    with _client(base) as client:
        payload = _handle_response(
            client.post(
                "/v1/governance/vote",
                json={"proposal_id": proposal_id, "decision": decision, "evidence": evidence or []},
            )
        )
    if output_json:
        _print_json(payload)
        return
    console.print(
        f"[green]Vote cast[/green] on proposal [bold]{proposal_id}[/bold] -> [cyan]{decision}[/cyan]"
    )


# --------------------------------------------------------------------------- #
# Forge: governed coding-agent runs
# --------------------------------------------------------------------------- #
# Forge: governed coding-agent runs
# --------------------------------------------------------------------------- #


@forge_app.command("code")
def forge_code(
    task: str = typer.Argument(..., help="Natural-language coding task."),
    repo: str = typer.Option("", "--repo", "-r", help="Repository root (must be a git repo)."),
    agent: str = typer.Option("opencode", "--agent", help="Coding agent: opencode."),
    model: str = typer.Option("opencode/beam-free", "--model", "-m", help="Model for opencode."),
    run_tests: bool = typer.Option(False, "--run-tests", help="Run repository tests after edits."),
    test_path: str = typer.Option("", "--test-path", help="Test path(s) to run (comma-separated)."),
    cleanup: bool = typer.Option(False, "--cleanup", help="Remove the worktree after the run."),
    work_order: str = typer.Option(
        "", "--work-order", help="Path to a rendered striker work order; overrides <task>."
    ),
    output_json: bool = typer.Option(False, "--json", help="Emit the full report as JSON."),
) -> None:
    """Run a coding agent in an isolated worktree, then Forge gates."""
    if repo == "":
        repo = "."
    repo_path = Path(repo).resolve()
    if not repo_path.is_dir():
        _fail(f"repo not found: {repo_path}")
        return
    if agent != "opencode":
        _fail(f"unsupported agent: {agent}")
        return
    if work_order:
        order_path = Path(work_order)
        if not order_path.is_file():
            _fail(f"work order not found: {order_path}")
            return
        task = order_path.read_text(encoding="utf-8")
    cmd = ["opencode", "run", "--auto", "--dir", "{workdir}", "--model", model]
    test_paths = [p.strip() for p in test_path.split(",") if p.strip()] if test_path else []
    report = run_external_agent(
        repo_path,
        cmd,
        task,
        run_tests_flag=run_tests,
        test_paths=test_paths or None,
        cleanup=cleanup,
    )
    if output_json:
        _print_json(report.model_dump(mode="json"))
        return
    style = "green" if report.gate == "PASS" else "red"
    console.print(f"Forge gate: [{style}]{report.gate}[/{style}]")
    console.print(f"Worktree: {report.worktree_path}")
    console.print(f"Changed: {', '.join(report.changed_paths) or '(none)'}")
    console.print(f"Inspection: {report.inspection.gate}")
    console.print(f"Security: {report.security.gate}")
    if report.tests is not None:
        console.print(f"Tests: {report.tests.gate}")
    if report.gate != "PASS":
        raise typer.Exit(1)


@forge_app.command("mcp")
def forge_mcp(
    transport: str = typer.Option("stdio", "--transport", "-t", help="stdio or http."),
    host: str = typer.Option("127.0.0.1", "--host", help="HTTP bind host (loopback only)."),
    port: int = typer.Option(8765, "--port", "-p", help="HTTP bind port."),
    token_file: str = typer.Option("", "--token-file", help="File holding the bearer token."),
) -> None:
    """Serve the Forge over MCP so external agents can drive governed builds.

    stdio needs no token (the caller already owns this process). HTTP requires
    AGENCY_MCP_TOKEN or --token-file; with neither the command exits instead of
    serving unauthenticated.
    """
    from agency.forge.mcp_server import McpConfig, create_http_app, serve_stdio

    if transport not in ("stdio", "http"):
        _fail("transport must be 'stdio' or 'http'")
        return
    token = ""
    if token_file:
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            _fail(f"cannot read token file: {exc}")
            return
    elif os.environ.get("AGENCY_MCP_TOKEN"):
        token = os.environ["AGENCY_MCP_TOKEN"]
    if transport == "http":
        if host not in ("127.0.0.1", "localhost", "::1"):
            _fail("HTTP transport binds loopback only")
            return
        if not token:
            _fail("HTTP transport requires AGENCY_MCP_TOKEN or --token-file")
            return
        console.print(f"[green]Forge MCP listening[/green] on http://{host}:{port}/mcp")
        import uvicorn

        uvicorn.run(
            create_http_app(McpConfig(token=token)), host=host, port=port, log_level="warning"
        )
        return
    raise typer.Exit(serve_stdio(McpConfig(token=token)))


@forge_app.command("status")
def forge_status(
    db: str = typer.Option("forge_jobs.db", "--db", help="Blacksmith job database path."),
    output_json: bool = typer.Option(False, "--json", help="Emit the status as JSON."),
) -> None:
    """Show the Blacksmith's Forge job queue (read-only; no ship authority)."""
    from agency.forge.orchestration import BlacksmithOps

    ops = BlacksmithOps(db)
    try:
        status = ops.status()
    finally:
        ops.close()
    if output_json:
        _print_json(status.model_dump(mode="json"))
        return
    console.print(f"Forge jobs: {status.total}")
    for state, count in sorted(status.by_state.items()):
        console.print(f"  {state}: {count}")
    if status.blocked:
        console.print("Blocked (escalate to human): " + ", ".join(status.blocked))
    for job in status.recent:
        console.print(f"  {job.job_id[:8]} {job.state.value} {job.title} gate={job.gate or '-'}")


@forge_app.command("metrics")
def forge_metrics_cmd(
    db: str = typer.Option("forge_jobs.db", "--db", help="Blacksmith job database path."),
    output_json: bool = typer.Option(False, "--json", help="Emit the metrics as JSON."),
) -> None:
    """Show forge operational metrics (first-pass, rework, verdict mix)."""
    from agency.forge.metrics import compute_metrics, format_metrics

    metrics = compute_metrics(db)
    if output_json:
        _print_json(metrics.model_dump(mode="json"))
        return
    console.print(format_metrics(metrics))


@forge_app.command("calibrate-export")
def forge_calibrate_export(
    db: str = typer.Option("forge_review.db", "--db", help="Review report database path."),
    out: str = typer.Option(
        "forge_calibration.jsonl", "--out", help="Labeling worksheet path (JSONL)."
    ),
) -> None:
    """Export reviewer verdicts for human labeling (gate-calibration runbook).

    Label each exported row by hand (`"correct": true/false` — did the verdict
    match your own judgment on the same diff?), then score the worksheet with
    `forge calibrate-score`. Blocking thresholds are only trustworthy once the
    agreement rate clears the bar; re-run the audit after every rubric edit.
    """
    from agency.forge.reviewer import ReviewStore

    store = ReviewStore(db)
    try:
        reports = store.list_reports()
    finally:
        store.close()
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        for report in reports:
            row = {
                "report_id": report.report_id,
                "created_at": report.created_at.isoformat(),
                "gate": report.gate,
                "score": report.score,
                "threshold": report.threshold,
                "reviewed_sha": report.reviewed_sha,
                "root": report.root,
                "findings": [
                    {
                        "path": f.path,
                        "line": f.line,
                        "rule_id": f.rule_id,
                        "severity": f.severity,
                        "resolved": f.resolved,
                    }
                    for f in report.findings
                ],
                "correct": None,
            }
            fh.write(json.dumps(row) + "\n")
    console.print(f"Exported {len(reports)} verdicts -> {target} (fill 'correct' per row)")


@forge_app.command("calibrate-score")
def forge_calibrate_score(
    labels: str = typer.Option(..., "--labels", help="Labeled worksheet path (JSONL)."),
    min_agreement: float = typer.Option(
        0.75, "--min-agreement", help="Agreement rate below which blocking is not trusted."
    ),
) -> None:
    """Score labeled review verdicts: agreement rate vs the blocking bar."""
    worksheet = Path(labels)
    rows = [
        json.loads(line)
        for line in worksheet.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    labeled = [r for r in rows if isinstance(r.get("correct"), bool)]
    if not labeled:
        console.print("No labeled rows found; set 'correct': true/false per exported verdict.")
        return
    hits = sum(1 for r in labeled if r["correct"])
    agreement = hits / len(labeled)
    console.print(f"Agreement: {agreement:.0%} ({hits}/{len(labeled)} labeled verdicts correct)")
    if agreement >= min_agreement:
        console.print(
            f">= {min_agreement:.0%}: blocking thresholds may stay; re-run after rubric edits."
        )
    else:
        console.print(
            f"< {min_agreement:.0%}: revise the rubric / raise the threshold before trusting"
            " blocking behavior."
        )


# --------------------------------------------------------------------------- #
# Forge: reverse-engineering tool catalog
# --------------------------------------------------------------------------- #


@forge_app.command("tools")
def forge_tools(
    category: str = typer.Option("", "--category", "-c", help="Filter by category."),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """List reverse-engineering tools and whether they are installed."""
    tools = list_tools()
    if category:
        tools = [t for t in tools if t.category == category]
    if output_json:
        _print_json(
            [
                {
                    "name": t.name,
                    "category": t.category,
                    "command": t.command,
                    "available": is_available(t),
                    "install": t.install,
                }
                for t in tools
            ]
        )
        return
    table = _table("Forge tools", ["Name", "Category", "Command", "Status", "Install if missing"])
    for t in tools:
        status = "installed" if is_available(t) else "MISSING"
        table.add_row(
            t.name,
            t.category,
            t.command or "(none)",
            status,
            t.install if not is_available(t) else "",
        )
    console.print(table)


@forge_app.command("tool")
def forge_tool(
    name: str = typer.Argument(..., help="Tool name from `forge tools`."),
) -> None:
    """Show how to install/use a tool (Forge never auto-installs)."""
    spec = get_tool(name)
    if spec is None:
        _fail(f"unknown tool: {name}")
        return
    console.print(f"[bold]{spec.name}[/bold] ({spec.category})")
    console.print(f"Command: {spec.command or '(none)'}")
    console.print(f"Available: {'yes' if is_available(spec) else 'no'}")
    console.print(f"Install: {spec.install}")
    if spec.args_hint:
        console.print(f"Example: {spec.args_hint}")
    if not is_available(spec):
        console.print("[yellow]Tool not installed. Install it manually, then re-run.[/yellow]")
        raise typer.Exit(1)


# --------------------------------------------------------------------------- #
# Loop: deterministic autonomous Ralph-style loop
# --------------------------------------------------------------------------- #


def _make_worker(name: str, model: str | None, timeout_s: int) -> ExternalWorker:
    key = name.lower()
    if key == "opencode":
        return OpencodeWorker(model=model, timeout_s=timeout_s)
    if key in {"claude", "claudecode", "claude-code"}:
        return ClaudeCodeWorker(timeout_s=timeout_s)
    if key == "codex":
        return CodexWorker(timeout_s=timeout_s)
    if key == "pi":
        return PiWorker(timeout_s=timeout_s)
    if key == "cursor":
        return CursorWorker(timeout_s=timeout_s)
    raise typer.BadParameter(f"unsupported worker: {name} (opencode|claudecode|codex|pi|cursor)")


@forge_app.command("ship")
def forge_ship(
    worktree_root: Path = typer.Option(..., "--worktree-root"),  # noqa: B008
    target_branch: str = typer.Option(..., "--target-branch"),
    pipeline_gate: str = typer.Option("FAIL", "--pipeline-gate"),
    smoke_gate: str = typer.Option("FAIL", "--smoke-gate"),
    approved: bool = typer.Option(False, "--approved", help="Explicit human approval."),
    proof_log: Path | None = typer.Option(None, "--proof-log"),  # noqa: B008
    repo_root: Path | None = typer.Option(None, "--repo-root"),  # noqa: B008
    output_json: bool = typer.Option(False, "--json"),
) -> None:
    """Locally fast-forward merge a PASS worktree branch (never releases)."""

    from agency.forge.ship import ship_branch

    yolo = os.environ.get("AGENCY_YOLO") == "1"
    approval_source = "explicit" if approved else ("yolo" if yolo else "")
    if not approval_source:
        _fail("ship requires explicit --approved (or audited -yolo); no merge performed")
    if pipeline_gate.upper() != "PASS" or smoke_gate.upper() != "PASS":
        _fail("ship requires both pipeline and smoke gates to be PASS")

    report = ship_branch(
        str(worktree_root),
        target_branch,
        pipeline_gate=pipeline_gate.upper(),
        smoke_gate=smoke_gate.upper(),
        approved=True,
        proof_log=str(proof_log) if proof_log else None,
        repo_root=str(repo_root) if repo_root else None,
    )
    receipt = report.model_dump(mode="json")
    receipt["approval_source"] = approval_source
    receipt["yolo_audited"] = yolo
    if output_json:
        _print_json(receipt)
    else:
        console.print(f"Ship gate: {report.gate} (report {report.report_id})")
    if report.gate != "PASS":
        raise typer.Exit(code=1)


def _print_loop_result(result: Any, *, output_json: bool) -> None:
    if output_json:
        _print_json(result.model_dump(mode="json"))
        return
    style = (
        "green"
        if result.status == "COMPLETED"
        else ("yellow" if result.status == "PAUSED" else "red")
    )
    console.print(f"Loop status: [{style}]{result.status}[/{style}]")
    console.print(f"Iterations: {result.iterations}")
    console.print(
        f"Items: {result.items_done} done, {result.items_blocked} blocked, "
        f"{result.items_pending} pending"
    )
    console.print(f"Cost: ${result.cost_usd:.4f}")
    if result.detail:
        console.print(f"Detail: {result.detail}")


@loop_app.command("run")
def loop_run(
    goal_source: str = typer.Argument(
        ..., help="GOAL.md/fix_plan.md dir, PRD file, or gh:owner/repo."
    ),
    repo: str = typer.Option("", "--repo", "-r", help="Git repo root (default: cwd)."),
    worker: str = typer.Option(
        "opencode", "--worker", "-w", help="opencode|claudecode|codex|pi|cursor."
    ),
    model: str | None = typer.Option(
        "opencode/beam-free", "--model", "-m", help="Model for the worker."
    ),
    session_id: str = typer.Option("agency-loop", "--session-id", help="Loop session id."),
    db: str = typer.Option("loop.db", "--db", help="Loop state database path."),
    max_hours: float | None = typer.Option(None, "--max-hours", help="Optional wall-clock cap."),
    max_iterations: int | None = typer.Option(
        None, "--max-iterations", help="Optional iteration cap."
    ),
    max_cost: float | None = typer.Option(None, "--max-cost", help="Optional cost cap (USD)."),
    worker_timeout: int = typer.Option(
        7200, "--worker-timeout", help="Per-iteration worker timeout (s)."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Run gates but do not commit."),
    run_tests: bool = typer.Option(True, "--run-tests/--no-run-tests"),
    run_review: bool = typer.Option(True, "--run-review/--no-run-review"),
    output_json: bool = typer.Option(False, "--json", help="Emit the loop result as JSON."),
) -> None:
    """Run the autonomous loop until the queue is done, a budget fires, or you stop it."""
    if repo == "":
        repo = "."
    repo_path = Path(repo).resolve()
    if not repo_path.is_dir():
        _fail(f"repo not found: {repo_path}")
        return
    try:
        items = load_queue(goal_source)
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
        return
    if not items:
        _fail(f"no work items found in: {goal_source}")
        return
    try:
        worker_obj = _make_worker(worker, model, worker_timeout)
    except typer.BadParameter as exc:
        _fail(str(exc))
        return

    worker_obj.name = striker_label(worker_obj)

    config = LoopConfig(
        session_id=session_id,
        goal=goal_source,
        repo=str(repo_path),
        worker=worker_obj.name,
        budgets=LoopBudgets(
            max_hours=max_hours,
            max_iterations=max_iterations,
            max_cost_usd=max_cost,
        ),
        dry_run=dry_run,
    )
    gate = ForgeGateRunner(run_tests=run_tests, run_review=run_review)
    store = LoopStore(db)
    driver = LoopDriver(config, store, worker_obj, gate, items)

    def _on_sigint(signum: int, frame: Any) -> None:
        console.print("\n[yellow]Stop requested; finishing current iteration...[/yellow]")
        driver.request_stop()

    previous = signal.signal(signal.SIGINT, _on_sigint)
    try:
        result = driver.run()
    except Exception as exc:  # noqa: BLE001 - surface the failure, keep state
        _fail(f"loop failed: {type(exc).__name__}: {exc}")
        return
    finally:
        signal.signal(signal.SIGINT, previous)
        store.close()
    _print_loop_result(result, output_json=output_json)


@loop_app.command("resume")
def loop_resume(
    session_id: str = typer.Option("agency-loop", "--session-id", help="Loop session id."),
    repo: str = typer.Option("", "--repo", "-r", help="Git repo root (default: cwd)."),
    worker: str = typer.Option(
        "opencode", "--worker", "-w", help="opencode|claudecode|codex|pi|cursor."
    ),
    model: str | None = typer.Option(
        "opencode/beam-free", "--model", "-m", help="Model for the worker."
    ),
    db: str = typer.Option("loop.db", "--db", help="Loop state database path."),
    worker_timeout: int = typer.Option(7200, "--worker-timeout"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    run_tests: bool = typer.Option(True, "--run-tests/--no-run-tests"),
    run_review: bool = typer.Option(True, "--run-review/--no-run-review"),
    output_json: bool = typer.Option(False, "--json"),
) -> None:
    """Resume a paused loop session from its persisted state."""
    if repo == "":
        repo = "."
    repo_path = Path(repo).resolve()
    if not repo_path.is_dir():
        _fail(f"repo not found: {repo_path}")
        return
    store = LoopStore(db)
    try:
        state = store.load(session_id)
        if state is None:
            _fail(f"no loop session: {session_id}")
            return
        try:
            worker_obj = _make_worker(worker, model, worker_timeout)
        except typer.BadParameter as exc:
            _fail(str(exc))
            return
        worker_obj.name = striker_label(worker_obj)
        config = LoopConfig(
            session_id=session_id,
            goal=state.goal,
            repo=str(repo_path),
            branch=state.branch or "agency-loop",
            worker=worker_obj.name,
            budgets=state.budgets,
            dry_run=dry_run,
        )
        gate = ForgeGateRunner(run_tests=run_tests, run_review=run_review)
        driver = LoopDriver(config, store, worker_obj, gate)

        def _on_sigint(signum: int, frame: Any) -> None:
            console.print("\n[yellow]Stop requested; finishing current iteration...[/yellow]")
            driver.request_stop()

        previous = signal.signal(signal.SIGINT, _on_sigint)
        try:
            result = driver.run()
        except Exception as exc:  # noqa: BLE001
            _fail(f"loop failed: {type(exc).__name__}: {exc}")
            return
        finally:
            signal.signal(signal.SIGINT, previous)
        _print_loop_result(result, output_json=output_json)
    finally:
        store.close()


@loop_app.command("status")
def loop_status(
    session_id: str = typer.Option("agency-loop", "--session-id", help="Loop session id."),
    db: str = typer.Option("loop.db", "--db", help="Loop state database path."),
    output_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Show the persisted state of a loop session."""
    store = LoopStore(db)
    try:
        state = store.load(session_id)
        if state is None:
            _fail(f"no loop session: {session_id}")
            return
        if output_json:
            _print_json(state.model_dump(mode="json"))
            return
        style = {"COMPLETED": "green", "PAUSED": "yellow", "RUNNING": "cyan"}.get(
            state.status, "red"
        )
        console.print(f"Session: {state.session_id}")
        console.print(f"Status: [{style}]{state.status}[/{style}]")
        console.print(f"Goal: {state.goal}")
        console.print(f"Worker: {state.worker}  Branch: {state.branch}")
        console.print(f"Iterations: {state.iteration}  Cost: ${state.cost_usd:.4f}")
        table = _table("Items", ["Id", "Status", "Attempts", "Title"])
        for item in state.queue:
            table.add_row(item.id, item.status, str(item.attempts), item.title)
        console.print(table)
        if state.detail:
            console.print(f"Detail: {state.detail}")
    finally:
        store.close()


@app.command("tui")
def tui(
    yolo: bool = typer.Option(False, "-yolo", "--yolo", help="Bypass human approvals (audited)."),
    model: str | None = typer.Option(None, "--model", "-m", help="LLM model override."),
    server_url: str = typer.Option(DEFAULT_SERVER_URL, "--server-url", "-u"),
) -> None:
    """Launch the Agency TUI (Claude-Code-style terminal UI)."""
    if yolo:
        os.environ["AGENCY_YOLO"] = "1"
    from agency.tui.agency_app import run_agency_tui

    run_agency_tui(yolo=yolo, model=model, server_url=server_url)


def main() -> None:
    """Console-script entry point (``agency = agency.cli:main``)."""
    argv = sys.argv[1:]
    yolo = False
    rest: list[str] = []
    for arg in argv:
        if arg in ("-yolo", "--yolo"):
            yolo = True
            os.environ["AGENCY_YOLO"] = "1"
        else:
            rest.append(arg)
    if not rest and sys.stdin.isatty():
        from agency.tui.agency_app import run_agency_tui

        run_agency_tui(yolo=yolo)
        return
    app(args=rest if rest != argv else None)


if __name__ == "__main__":
    main()


@app.command("consensus-abci-host")
def consensus_abci_host(
    genesis: str = typer.Option(..., "--genesis", help="Path to genesis.json."),
    peer_id: str = typer.Option(..., "--peer-id", help="This node's peer id."),
    port: int = typer.Option(26658, "--port", help="TCP port to bind."),
    host: str = typer.Option("127.0.0.1", "--host", help="Interface to bind."),
    peer_log: str | None = typer.Option(
        None, "--peer-log", help="Optional PeerLog path for durable results."
    ),
) -> None:
    """Run the ABCI socket host for CometBFT."""
    from agency.consensus.abci.host import ABCIHost
    from agency.consensus.app import AgencyApplication
    from agency.consensus.genesis import load_genesis

    try:
        membership = load_genesis(genesis)
    except (ValueError, OSError) as exc:
        _fail(f"invalid genesis: {exc}")

    app = AgencyApplication(membership)

    log = None
    adapter = None
    if peer_log:
        from agency.consensus.actions import ActionRegistry
        from agency.consensus.block_adapter import BlockEngineAdapter
        from agency.consensus.grants import GrantLedger
        from agency.peers.identities import PeerIdentity
        from agency.peers.log import PeerLog

        log = PeerLog(
            peer_log,
            peer_id=peer_id,
            genesis={
                "epoch": membership.epoch,
                "membership_root": membership.root,
                "policy_version": membership.policy_version,
            },
            expected_genesis_root=membership.root,
            check_same_thread=False,
        )
        log.open()
        key_path = Path(peer_log).with_name("issuer.key")
        if not key_path.exists():
            _fail(f"missing issuer key file: {key_path}")
        import base64

        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        raw = base64.b64decode(key_path.read_text(encoding="utf-8").strip())
        issuer = PeerIdentity.from_private_key(Ed25519PrivateKey.from_private_bytes(raw))
        ledger = GrantLedger(membership=membership)
        registry = ActionRegistry()
        adapter = BlockEngineAdapter(
            app=app,
            ledger=ledger,
            issuer=issuer,
            membership=membership,
            logs={peer_id: log},
            registry=registry,
            affected_consumers=[],
            bft_time_ms=0,
        )

    abci_host = ABCIHost(app, host=host, port=port)
    abci_host.start()
    console.print(f"[green]ABCI host listening[/green] on {host}:{abci_host.port}")

    if adapter is not None and log is not None:

        def on_block(result: Any, ctx: Any) -> None:
            adapter.process_result(result, ctx, {}, [])

        abci_host.on_block = on_block

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        abci_host.stop()
        if log is not None:
            try:
                log.close()
            except Exception:  # noqa: BLE001
                console.print("[dim]peer log close failed[/dim]")
        console.print("[yellow]ABCI host stopped[/yellow]")


@app.command("consensus-sign-proposal")
def consensus_sign_proposal(
    genesis: str = typer.Option(..., "--genesis", help="Path to genesis.json."),
    key: str = typer.Option(..., "--key", help="Base64 raw Ed25519 private key."),
    action: str = typer.Option("echo", "--action"),
    resource: str = typer.Option("res", "--resource"),
    output_json: bool = typer.Option(False, "--json"),
) -> None:
    """Build a signed proposal envelope (dev helper)."""
    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from agency.consensus.genesis import load_genesis
    from agency.peers.identities import PeerIdentity
    from agency.peers.proposal_builder import build_proposal_envelope

    membership = load_genesis(genesis)
    raw = base64.b64decode(key.strip())
    issuer = PeerIdentity.from_private_key(Ed25519PrivateKey.from_private_bytes(raw))
    wire = build_proposal_envelope(
        issuer, membership, action_type=action, resource=resource, params={}
    )
    if output_json:
        _print_json({"wire_hex": wire.hex()})
    else:
        console.print(wire.hex())


@app.command("consensus-sign-ballot")
def consensus_sign_ballot(
    genesis: str = typer.Option(..., "--genesis", help="Path to genesis.json."),
    key: str = typer.Option(..., "--key", help="Base64 raw Ed25519 private key."),
    proposal_id: str = typer.Option(..., "--proposal-id"),
    decision: str = typer.Option("approve", "--decision"),
    seq: int = typer.Option(1, "--seq"),
    output_json: bool = typer.Option(False, "--json"),
) -> None:
    """Build a signed ballot envelope (dev helper)."""
    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from agency.consensus.genesis import load_genesis
    from agency.peers.authority import PeerAuthority
    from agency.peers.identities import PeerIdentity
    from agency.peers.proposal_builder import build_ballot_wire

    membership = load_genesis(genesis)
    raw = base64.b64decode(key.strip())
    issuer = PeerIdentity.from_private_key(Ed25519PrivateKey.from_private_bytes(raw))
    authority = PeerAuthority(membership)
    wire = build_ballot_wire(
        authority, issuer, proposal_id=proposal_id, decision=decision, monotonic_seq=seq
    )
    if output_json:
        _print_json({"wire_hex": wire.hex()})
    else:
        console.print(wire.hex())


@app.command("consensus-sign-grant")
def consensus_sign_grant(
    genesis: str = typer.Option(..., "--genesis", help="Path to genesis.json."),
    key: str = typer.Option(..., "--key", help="Base64 raw Ed25519 private key."),
    task_id: str = typer.Option(
        ..., "--task-id", help="Exact HTTP scope, e.g. 'GET /v1/governance/proposals', or READ."
    ),
    proposal_id: str = typer.Option(..., "--proposal-id", help="Proposal the grant cites."),
    issued_at_ms: int = typer.Option(
        1000, "--issued-at-ms", help="Issued BFT time in ms (server pins this via env)."
    ),
    expires_at_ms: int = typer.Option(
        61000, "--expires-at-ms", help="Expiry BFT time in ms (max lifetime 1h)."
    ),
    output_json: bool = typer.Option(False, "--json"),
) -> None:
    """Build a signed, task-scoped grant for X-Agency-Grant (dev helper).

    The private key never leaves this machine; only the signed grant payload
    (public fields + signatures) is printed. Paste the base64 line as the
    X-Agency-Grant header value.
    """
    import base64
    import json as _json

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from agency.consensus.genesis import load_genesis
    from agency.consensus.grants import GrantLedger
    from agency.peers.identities import PeerIdentity

    membership = load_genesis(genesis)
    raw = base64.b64decode(key.strip())
    issuer = PeerIdentity.from_private_key(Ed25519PrivateKey.from_private_bytes(raw))
    grant = GrantLedger(membership=membership).issue(
        issuer,
        proposal_id=proposal_id,
        task_id=task_id,
        issued_at_ms=issued_at_ms,
        expires_at_ms=expires_at_ms,
        affected_consumers=[],
    )
    if grant.attestation is None:
        _fail("issued grant has no affected-set attestation; a grant header requires one")
    payload = {
        "proposal_id": grant.proposal_id,
        "task_id": grant.task_id,
        "issuer_peer_id": grant.issuer_peer_id,
        "membership_epoch": grant.membership_epoch,
        "membership_root": grant.membership_root,
        "nonce": grant.nonce,
        "issued_at_ms": grant.issued_at_ms,
        "expires_at_ms": grant.expires_at_ms,
        "affected_consumers": list(grant.affected_consumers),
        "attestation": {
            "attestor_peer_id": grant.attestation.attestor_peer_id,
            "signature": grant.attestation.signature.hex(),
        },
        "signature": grant.signature.hex(),
    }
    header = base64.b64encode(_json.dumps(payload).encode("utf-8")).decode("ascii")
    if output_json:
        _print_json({"grant_header": header})
    else:
        console.print(header)
