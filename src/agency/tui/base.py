# ruff: noqa: BLE001, S110
"""Shared Textual base for both TUIs (Claude-Code-style shell)."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Input, RichLog

from agency.tui.brain import Brain
from agency.tui.permission import Mode, PermissionPolicy


class BaseTUI(App[None]):
    TITLE = "Agency"
    SUB_TITLE = ""
    CSS = """
    #transcript { height: 1fr; }
    #composer { height: 3; border: solid green; }
    #status { height: 1; color: yellow; }
    """

    BINDINGS = [  # noqa: RUF012
        Binding("ctrl+c", "quit", "Quit"),
        Binding("ctrl+l", "clear", "Clear"),
        Binding("f1", "help", "Help"),
    ]

    def __init__(
        self,
        *,
        yolo: bool = False,
        model: str | None = None,
        repo: str = "",
        server_url: str = "http://127.0.0.1:8000",
    ) -> None:
        super().__init__()
        self.policy = PermissionPolicy.from_env()
        if yolo:
            self.policy.mode = Mode.YOLO
        self.brain = Brain(model=model)
        self.repo = repo or "."
        self.server_url = server_url.rstrip("/")
        self.pending: str = ""
        self.draft_text: str = ""
        self.spec_passed: bool = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="transcript", markup=True, highlight=True)
        yield Input(id="composer", placeholder="Type a message, or /help  (Enter to send)")
        yield Footer()

    def say(self, text: str) -> None:
        try:
            self.query_one("#transcript", RichLog).write(text)
        except Exception:
            pass

    def action_clear(self) -> None:
        try:
            self.query_one("#transcript", RichLog).clear()
        except Exception:
            pass

    def action_help(self) -> None:
        self.say(self.help_text())

    def help_text(self) -> str:
        return (
            "Commands: /help /status /model <name> /yolo /clear /quit\n"
            "Modes: auto (ask) | plan | yolo (bypass approvals, audited)\n"
            "Ctrl+C quit  Ctrl+L clear  F1 help"
        )

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.clear()
        if not text:
            return
        self.say(f"[bold cyan]you[/] {text}")
        if text.startswith("/"):
            await self.handle_slash(text)
            return
        await self.handle_message(text)

    async def handle_slash(self, text: str) -> None:
        parts = text.split()
        cmd = parts[0].lower()
        if cmd in ("/help", "/h"):
            self.say(self.help_text())
        elif cmd == "/clear":
            self.action_clear()
        elif cmd in ("/quit", "/exit"):
            self.exit()
        elif cmd == "/yolo":
            self.policy.mode = Mode.YOLO
            self.say("[bold red]YOLO ON[/] — human approvals bypassed (still audited)")
        elif cmd == "/model" and len(parts) > 1:
            self.brain = Brain(model=parts[1])
            self.say(f"model -> {parts[1]} ({self.brain.describe()})")
        elif cmd == "/status":
            await self.show_status()
        else:
            self.say(f"unknown command {cmd} — try /help")

    async def show_status(self) -> None:  # pragma: no cover - overridden
        self.say(f"mode={self.policy.mode.value} brain={self.brain.describe()} repo={self.repo}")

    async def handle_message(self, text: str) -> None:  # pragma: no cover - overridden
        await self.chat(text)

    async def chat(self, text: str) -> None:
        self.say("[dim]thinking...[/]")
        try:
            reply = await self.brain.ask(text)
        except Exception as exc:
            self.say(f"[red]LLM error: {type(exc).__name__}[/]")
            return
        self.say(f"[bold green]agent[/] {reply}")
