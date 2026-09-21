"""``agency config`` command-line interface.

Command surface::

    agency config set-key <provider> <key>   # store a provider API key
    agency config get-key <provider>         # show a masked key (or --show)
    agency config list-keys                  # list configured providers
    agency config validate                   # validate settings + key formats
    agency config show                       # display non-secret settings

Key material is masked in all output by default. ``get-key`` only reveals a
full key when the explicit ``--show`` flag is passed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import structlog
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agency.config.keys import APIKeyManager, key_var_name, normalize_provider
from agency.config.settings import AgencySettings, mask_secret, resolve_env_file

log = structlog.get_logger(__name__)

console = Console()
err_console = Console(stderr=True)

config_app: typer.Typer = typer.Typer(
    name="config",
    help="Manage Agency configuration and provider API keys.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# Backwards/forwards-compatible alias for standalone entry points.
app: typer.Typer = config_app

_env_file_override: Path | None = None


@config_app.callback()
def _callback(
    env_file: Annotated[
        Path | None,
        typer.Option(
            "--env-file",
            help="Path to the .env file (default: auto-detected).",
            dir_okay=False,
            resolve_path=True,
        ),
    ] = None,
) -> None:
    """Shared options for all ``config`` commands."""
    global _env_file_override
    _env_file_override = env_file


def _manager() -> APIKeyManager:
    return APIKeyManager(env_file=_env_file_override)


def _settings() -> AgencySettings:
    return AgencySettings.load(env_file=_env_file_override)


def _fail(message: str, *, code: int = 1) -> None:
    err_console.print(f"[bold red]Error:[/bold red] {message}")
    raise typer.Exit(code)


def _table(title: str, columns: list[str]) -> Table:
    table = Table(title=title, show_lines=False, header_style="bold cyan")
    for column in columns:
        table.add_column(column, overflow="fold")
    return table


# --------------------------------------------------------------------------- #
# Key management
# --------------------------------------------------------------------------- #


@config_app.command("set-key")
def set_key(
    provider: str = typer.Argument(..., help="Provider name, e.g. openai, anthropic."),
    key: Annotated[
        str | None,
        typer.Argument(help="API key value. If omitted, you will be prompted securely."),
    ] = None,
) -> None:
    """Store the API key for ``provider`` in the ``.env`` file."""
    name = normalize_provider(provider)
    if not name:
        _fail("provider must not be empty.")
        return
    secret = key.strip() if key else None
    if not secret:
        secret = typer.prompt(f"API key for {name}", hide_input=True).strip()
    if not secret:
        _fail("key must not be empty.")
        return
    try:
        _manager().set_key(name, secret)
    except ValueError as exc:
        log.info("config.cli.set_key.rejected", provider=name)
        _fail(str(exc))
        return
    log.info("config.cli.set_key", provider=name)
    console.print(
        Panel(
            f"[green]Stored key[/green] for provider [bold]{name}[/bold] "
            f"as [cyan]{key_var_name(name)}[/cyan].",
            expand=False,
        )
    )


@config_app.command("get-key")
def get_key(
    provider: str = typer.Argument(..., help="Provider name, e.g. openai, anthropic."),
    show: bool = typer.Option(
        False,
        "--show",
        help="Reveal the full key. Otherwise only a masked form is shown.",
    ),
) -> None:
    """Show the stored key for ``provider`` (masked unless ``--show``)."""
    name = normalize_provider(provider)
    stored = _manager().get_key(name)
    if not stored:
        _fail(f"no key configured for provider {name!r} ({key_var_name(name)}).")
        return
    if show:
        console.print(stored)
    else:
        console.print(
            Panel(
                f"Provider [bold]{name}[/bold]\n"
                f"Variable: [cyan]{key_var_name(name)}[/cyan]\n"
                f"Key: [yellow]{mask_secret(stored)}[/yellow] "
                "[dim](pass --show to reveal)[/dim]",
                expand=False,
            )
        )


@config_app.command("list-keys")
def list_keys() -> None:
    """List providers with a configured key (values always masked)."""
    manager = _manager()
    providers = manager.list_keys()
    if not providers:
        console.print("[yellow]No provider API keys configured.[/yellow]")
        console.print("[dim]Add one with: agency config set-key <provider> <key>[/dim]")
        return
    table = _table("Provider API keys", ["Provider", "Variable", "Format", "Key"])
    for name in providers:
        stored = manager.get_key(name)
        valid = manager.validate_key(name)
        table.add_row(
            name,
            key_var_name(name),
            "[green]valid[/green]" if valid else "[red]invalid[/red]",
            mask_secret(stored),
        )
    console.print(table)
    console.print(f"[dim]{len(providers)} provider(s)[/dim]")


# --------------------------------------------------------------------------- #
# Settings inspection
# --------------------------------------------------------------------------- #


@config_app.command("validate")
def validate() -> None:
    """Validate settings and stored key formats; exit non-zero on problems."""
    settings = _settings()
    manager = _manager()
    problems: list[str] = list(settings.validate())
    # A valid stored provider key satisfies the active-provider key
    # requirement, so AGENCY_LLM_API_KEY need not duplicate it.
    if any("llm_api_key is required" in problem for problem in problems):
        provider = normalize_provider(settings.llm_provider)
        if provider and manager.validate_key(provider):
            problems = [p for p in problems if "llm_api_key is required" not in p]
            console.print(
                f"[dim]Using stored key {key_var_name(provider)} for provider {provider}.[/dim]"
            )
    for name in manager.list_keys():
        if not manager.validate_key(name):
            problems.append(f"stored key for provider {name!r} has an invalid format.")
    if problems:
        table = _table("Configuration problems", ["#", "Problem"])
        for index, problem in enumerate(problems, start=1):
            table.add_row(str(index), problem)
        console.print(table)
        _fail(f"configuration invalid: {len(problems)} problem(s).")
        return
    console.print("[green]Configuration is valid.[/green]")


@config_app.command("show")
def show() -> None:
    """Display current settings (secret values always masked)."""
    settings = _settings()
    env_file = resolve_env_file(_env_file_override)
    table = _table("Agency settings", ["Setting", "Value", "Source"])
    table.add_row(
        "env file", str(env_file), "auto-detected" if _env_file_override is None else "--env-file"
    )
    table.add_row("AGENCY_LLM_PROVIDER", settings.llm_provider, "env")
    table.add_row("AGENCY_LLM_MODEL", settings.llm_model, "env")
    table.add_row("AGENCY_LLM_API_KEY", mask_secret(settings.llm_api_key), "env")
    table.add_row("AGENCY_LLM_BASE_URL", settings.llm_base_url or "<unset>", "env")
    table.add_row("AGENCY_BUTLER_HOST", settings.butler_host, "env")
    table.add_row("AGENCY_BUTLER_PORT", str(settings.butler_port), "env")
    table.add_row("AGENCY_MEMORY_DB_PATH", settings.memory_db_path, "env")
    table.add_row("AGENCY_EVIDENCE_DB_PATH", settings.evidence_db_path, "env")
    table.add_row("AGENCY_AUDIT_DB_PATH", settings.audit_db_path, "env")
    table.add_row("AGENCY_LOG_LEVEL", settings.log_level.upper(), "env")
    console.print(table)


def main() -> None:
    """Standalone entry point (``python -m agency.config.cli``)."""
    config_app()


if __name__ == "__main__":
    main()
