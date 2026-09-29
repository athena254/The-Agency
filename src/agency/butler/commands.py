"""Deterministic Butler conversation commands.

Parsed **before** routing, matching the Agency's deterministic-first
rule (LLMs only where judgment is required). Commands let a user
create, list, switch and inspect threads conversationally, with
fail-closed ambiguity handling:

* ``/switch backend`` with two threads titled ``backend`` raises
  :class:`CommandError` listing both ids — never a silent guess.
* Every command routes through :class:`ThreadStore` ownership checks,
  so one owner can never create into, list, switch to or inspect
  another owner's threads.

Commands
--------
``/new <title>``          create a thread in the owner's default workspace
``/threads``              list the owner's threads across their workspaces
``/switch <n|title>``     set the owner's active thread (persisted)
``/current``              show the active thread and its context state

The default workspace is the owner's oldest workspace; one is created
automatically (titled ``Workspace``) on the first ``/new``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from agency.butler.threads import Thread, ThreadStore, Workspace

if TYPE_CHECKING:
    from agency.butler.context import ThreadContextEngine

log = structlog.get_logger(__name__)

DEFAULT_WORKSPACE_TITLE = "Workspace"


class CommandError(Exception):
    """A command failed for a user-visible, deterministic reason."""


@dataclass(frozen=True)
class CommandResult:
    """Result of executing one Butler command."""

    handled: bool
    text: str
    thread_id: str | None = None
    switched: bool = False


class ThreadCommands:
    """Executor for the Butler's deterministic thread commands."""

    def __init__(
        self,
        thread_store: ThreadStore,
        context_engine: ThreadContextEngine | None = None,
    ) -> None:
        self._threads = thread_store
        self._engine = context_engine

    # ------------------------------------------------------------------ #
    # Parsing
    # ------------------------------------------------------------------ #

    @staticmethod
    def parse(message: str) -> tuple[str, str] | None:
        """Parse ``/command args`` from a message, or return None.

        Returns ``(command, args)`` with the command lowercased. A bare
        ``/`` or an unknown-looking ``/word`` still returns a command so
        the executor can reject unknown commands deterministically.
        Lines beginning with ``//`` are escaped and never parsed.
        """
        text = message.strip()
        if not text.startswith("/"):
            return None
        if text.startswith("//"):
            return None
        parts = text[1:].split(None, 1)
        if not parts or not parts[0]:
            return None
        command = parts[0].lower().lstrip("/")
        args = parts[1].strip() if len(parts) > 1 else ""
        return command, args

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #

    def execute(self, owner: str, command: str, args: str) -> CommandResult:
        """Execute one parsed command for ``owner``."""
        if command == "new":
            return self.cmd_new(owner, args)
        if command == "threads":
            return self.cmd_threads(owner)
        if command == "switch":
            return self.cmd_switch(owner, args)
        if command == "current":
            return self.cmd_current(owner)
        raise CommandError(
            f"Unknown command '/{command}'. Available: /new, /threads, /switch, /current."
        )

    # ------------------------------------------------------------------ #
    # /new
    # ------------------------------------------------------------------ #

    def cmd_new(self, owner: str, title: str) -> CommandResult:
        """Create a thread in the owner's default workspace."""
        clean_title = title.strip()
        if not clean_title:
            raise CommandError("Usage: /new <thread title> — a title is required.")
        workspace = self._default_workspace(owner)
        thread = self._threads.create_thread(owner, workspace.id, clean_title)
        self._threads.set_active_thread(owner, thread.id)
        log.info("butler.cmd_new", owner=owner, thread_id=thread.id)
        return CommandResult(
            handled=True,
            text=(
                f"Created thread '{thread.title}' and switched to it. "
                f"Thread id: {thread.id}"
            ),
            thread_id=thread.id,
            switched=True,
        )

    def _default_workspace(self, owner: str) -> Workspace:
        """The owner's oldest workspace, creating one when none exists."""
        workspaces = self._threads.list_workspaces(owner)
        if workspaces:
            return workspaces[0]
        return self._threads.create_workspace(owner, DEFAULT_WORKSPACE_TITLE)

    # ------------------------------------------------------------------ #
    # /threads
    # ------------------------------------------------------------------ #

    def cmd_threads(self, owner: str) -> CommandResult:
        """List all of the owner's threads across their workspaces."""
        active = self._threads.get_active_thread(owner)
        lines: list[str] = []
        for workspace in self._threads.list_workspaces(owner):
            threads = self._threads.list_threads(owner, workspace.id)
            if not threads:
                continue
            lines.append(f"Workspace '{workspace.title}':")
            for thread in threads:
                marker = " *active*" if active is not None and thread.id == active.id else ""
                messages = self._threads.count_messages(owner, thread.id)
                lines.append(f"  - {thread.title} ({messages} messages){marker}")
        if not lines:
            text = (
                "You have no threads yet. Create one with /new <title>."
            )
        else:
            text = "Your threads:\n" + "\n".join(lines)
        return CommandResult(handled=True, text=text)

    # ------------------------------------------------------------------ #
    # /switch
    # ------------------------------------------------------------------ #

    def cmd_switch(self, owner: str, ref: str) -> CommandResult:
        """Switch the owner's active thread by title or thread id.

        Ambiguity fails closed: multiple title matches raise
        :class:`CommandError` listing every match with its id.
        """
        clean_ref = ref.strip()
        if not clean_ref:
            raise CommandError("Usage: /switch <thread title or id>.")
        threads = self._owned_threads(owner)
        by_id = next((t for t in threads if t.id == clean_ref), None)
        if by_id is not None:
            return self._switch_to(owner, by_id)
        title_matches = [t for t in threads if t.title.strip().lower() == clean_ref.lower()]
        if len(title_matches) == 1:
            return self._switch_to(owner, title_matches[0])
        if len(title_matches) > 1:
            listing = "\n".join(
                f"  - '{t.title}' (id {t.id}, created {t.created_at})" for t in title_matches
            )
            raise CommandError(
                f"'{clean_ref}' matches {len(title_matches)} threads — be specific "
                f"using /switch <id>:\n{listing}"
            )
        raise CommandError(
            f"No thread titled '{clean_ref}'. Use /threads to list yours."
        )

    def _switch_to(self, owner: str, thread: Thread) -> CommandResult:
        self._threads.set_active_thread(owner, thread.id)
        log.info("butler.cmd_switch", owner=owner, thread_id=thread.id)
        return CommandResult(
            handled=True,
            text=f"Switched to '{thread.title}'. Your messages now continue this thread.",
            thread_id=thread.id,
            switched=True,
        )

    def _owned_threads(self, owner: str) -> list[Thread]:
        threads: list[Thread] = []
        for workspace in self._threads.list_workspaces(owner):
            threads.extend(self._threads.list_threads(owner, workspace.id))
        return threads

    # ------------------------------------------------------------------ #
    # /current
    # ------------------------------------------------------------------ #

    def cmd_current(self, owner: str) -> CommandResult:
        """Show the owner's active thread and its context state."""
        active = self._threads.get_active_thread(owner)
        if active is None:
            return CommandResult(
                handled=True,
                text=(
                    "No active thread. Every message is a standalone turn. "
                    "Use /new <title> or /switch <title> to work in a thread."
                ),
            )
        messages = self._threads.count_messages(owner, active.id)
        lines = [
            f"Active thread: '{active.title}' (id {active.id})",
            f"Messages: {messages}",
        ]
        if self._engine is not None:
            summary = self._threads.get_summary(owner, active.id)
            if summary is not None:
                lines.append("Older conversation is summarized in context.")
            else:
                lines.append("No summary yet; recent turns are carried verbatim.")
        return CommandResult(handled=True, text="\n".join(lines), thread_id=active.id)
