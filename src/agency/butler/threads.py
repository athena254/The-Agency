"""Persistent, owner-checked thread metadata/message store for Butler.

This module is the storage foundation for multi-threaded Butler
conversations. It is deliberately *not* a Butler API integration: it
owns workspace/thread/message persistence with explicit ownership
boundaries, and nothing more.

Design notes:

- Standard-library ``sqlite3`` only, synchronous, deterministic local
  persistence. Every query is parameterized; writes run inside
  transactions (``with connection:`` blocks).
- Ownership is always an explicit ``owner`` argument. Unknown ids and
  wrong-owner ids both raise the same :exc:`KeyError` so callers (and
  attackers) get no existence oracle.
- Branching records provenance via ``parent_thread_id`` only; parent
  messages are never copied into the child implicitly.
- No secrets are stored here and no identity is inferred from message
  text; the caller supplies ``owner`` and this store enforces it.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

__all__ = [
    "Thread",
    "ThreadMessage",
    "ThreadStore",
    "Workspace",
]

_VALID_ROLES = frozenset({"user", "assistant", "system"})

_DEFAULT_THREAD_STATE = "active"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
    id          TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    title       TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS threads (
    id                TEXT PRIMARY KEY,
    workspace_id      TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    owner             TEXT NOT NULL,
    title             TEXT NOT NULL,
    parent_thread_id  TEXT REFERENCES threads(id) ON DELETE SET NULL,
    state             TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS thread_messages (
    id          TEXT PRIMARY KEY,
    thread_id   TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workspaces_owner
    ON workspaces(owner);
CREATE INDEX IF NOT EXISTS idx_threads_workspace
    ON threads(workspace_id);
CREATE INDEX IF NOT EXISTS idx_threads_owner
    ON threads(owner);
CREATE INDEX IF NOT EXISTS idx_thread_messages_thread
    ON thread_messages(thread_id);
"""


def _utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def _require_nonblank(value: str, name: str) -> str:
    """Validate a free-text field, returning the stripped value."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string.")
    return value.strip()


def _require_content(value: str) -> str:
    """Validate message content, preserving it verbatim."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("content must be a non-blank string.")
    return value


def _require_role(role: str) -> str:
    """Validate that ``role`` is one of user|assistant|system."""
    if role not in _VALID_ROLES:
        raise ValueError(f"role must be one of {sorted(_VALID_ROLES)}.")
    return role


@dataclass(frozen=True)
class Workspace:
    """A persistent, owner-scoped container for threads."""

    id: str
    owner: str
    title: str
    created_at: str


@dataclass(frozen=True)
class Thread:
    """A conversation thread inside a workspace.

    ``parent_thread_id`` records branch provenance only; a branched
    thread starts with no messages of its own.
    """

    id: str
    workspace_id: str
    owner: str
    title: str
    parent_thread_id: str | None
    state: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ThreadMessage:
    """One message appended to a thread."""

    id: str
    thread_id: str
    role: str
    content: str
    created_at: str


class ThreadStore:
    """Synchronous SQLite store for workspaces, threads and messages.

    Lifecycle::

        store = ThreadStore("/path/to/threads.db")
        try:
            ...
        finally:
            store.close()

    ``":memory:"`` works for ephemeral use; a file path persists across
    restarts (reopen the path with a new :class:`ThreadStore`).
    :meth:`close` is idempotent.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    @property
    def db_path(self) -> str:
        """Filesystem path (or ``":memory:"``) backing this store."""
        return self._db_path

    def close(self) -> None:
        """Close the connection. Safe to call more than once."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("ThreadStore is closed.")
        return self._conn

    # ------------------------------------------------------------------ #
    # Workspaces
    # ------------------------------------------------------------------ #

    def create_workspace(self, owner: str, title: str) -> Workspace:
        """Create a workspace owned by ``owner``."""
        clean_owner = _require_nonblank(owner, "owner")
        clean_title = _require_nonblank(title, "title")
        workspace = Workspace(
            id=uuid.uuid4().hex,
            owner=clean_owner,
            title=clean_title,
            created_at=_utc_now_iso(),
        )
        conn = self._connection()
        with conn:
            conn.execute(
                "INSERT INTO workspaces (id, owner, title, created_at) VALUES (?, ?, ?, ?)",
                (workspace.id, workspace.owner, workspace.title, workspace.created_at),
            )
        return workspace

    def list_workspaces(self, owner: str) -> list[Workspace]:
        """All workspaces owned by ``owner``, oldest first."""
        clean_owner = _require_nonblank(owner, "owner")
        conn = self._connection()
        rows = conn.execute(
            "SELECT id, owner, title, created_at FROM workspaces"
            " WHERE owner = ? ORDER BY created_at ASC, id ASC",
            (clean_owner,),
        ).fetchall()
        return [Workspace(**dict(row)) for row in rows]

    # ------------------------------------------------------------------ #
    # Threads
    # ------------------------------------------------------------------ #

    def _workspace_or_raise(self, owner: str, workspace_id: str) -> None:
        """Raise KeyError unless ``workspace_id`` belongs to ``owner``."""
        conn = self._connection()
        row = conn.execute(
            "SELECT id FROM workspaces WHERE id = ? AND owner = ?",
            (workspace_id, owner),
        ).fetchone()
        if row is None:
            raise KeyError(workspace_id)

    def _thread_row_or_raise(self, owner: str, thread_id: str) -> sqlite3.Row:
        """Fetch a thread row or raise KeyError (unknown or wrong owner)."""
        conn = self._connection()
        row = conn.execute(
            "SELECT id, workspace_id, owner, title, parent_thread_id,"
            " state, created_at, updated_at FROM threads"
            " WHERE id = ? AND owner = ?",
            (thread_id, owner),
        ).fetchone()
        if row is None:
            raise KeyError(thread_id)
        return cast(sqlite3.Row, row)

    def create_thread(
        self,
        owner: str,
        workspace_id: str,
        title: str,
        parent_thread_id: str | None = None,
    ) -> Thread:
        """Create a thread in ``owner``'s workspace.

        The workspace must belong to ``owner``; a ``parent_thread_id``
        must belong to the same owner *and* the same workspace. The
        child starts with no messages — parent history is never copied.
        """
        clean_owner = _require_nonblank(owner, "owner")
        clean_title = _require_nonblank(title, "title")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspace_id must be a non-blank string.")
        self._workspace_or_raise(clean_owner, workspace_id)

        clean_parent: str | None = None
        if parent_thread_id is not None:
            if not isinstance(parent_thread_id, str) or not parent_thread_id.strip():
                raise ValueError("parent_thread_id must be a non-blank string.")
            clean_parent = parent_thread_id
            parent = self._thread_row_or_raise(clean_owner, clean_parent)
            if parent["workspace_id"] != workspace_id:
                raise ValueError("parent thread must be in the same workspace.")

        now = _utc_now_iso()
        thread = Thread(
            id=uuid.uuid4().hex,
            workspace_id=workspace_id,
            owner=clean_owner,
            title=clean_title,
            parent_thread_id=clean_parent,
            state=_DEFAULT_THREAD_STATE,
            created_at=now,
            updated_at=now,
        )
        conn = self._connection()
        with conn:
            conn.execute(
                "INSERT INTO threads (id, workspace_id, owner, title,"
                " parent_thread_id, state, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    thread.id,
                    thread.workspace_id,
                    thread.owner,
                    thread.title,
                    thread.parent_thread_id,
                    thread.state,
                    thread.created_at,
                    thread.updated_at,
                ),
            )
        return thread

    def get_thread(self, owner: str, thread_id: str) -> Thread:
        """Fetch one thread. Unknown/wrong-owner ids raise KeyError."""
        clean_owner = _require_nonblank(owner, "owner")
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread_id must be a non-blank string.")
        row = self._thread_row_or_raise(clean_owner, thread_id)
        return Thread(**dict(row))

    def list_threads(self, owner: str, workspace_id: str) -> list[Thread]:
        """All of ``owner``'s threads in ``workspace_id``, oldest first."""
        clean_owner = _require_nonblank(owner, "owner")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspace_id must be a non-blank string.")
        self._workspace_or_raise(clean_owner, workspace_id)
        conn = self._connection()
        rows = conn.execute(
            "SELECT id, workspace_id, owner, title, parent_thread_id,"
            " state, created_at, updated_at FROM threads"
            " WHERE workspace_id = ? AND owner = ?"
            " ORDER BY created_at ASC, id ASC",
            (workspace_id, clean_owner),
        ).fetchall()
        return [Thread(**dict(row)) for row in rows]

    # ------------------------------------------------------------------ #
    # Messages
    # ------------------------------------------------------------------ #

    def append_message(self, owner: str, thread_id: str, role: str, content: str) -> ThreadMessage:
        """Append a message to ``owner``'s thread and touch ``updated_at``."""
        clean_owner = _require_nonblank(owner, "owner")
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread_id must be a non-blank string.")
        _require_role(role)
        clean_content = _require_content(content)
        self._thread_row_or_raise(clean_owner, thread_id)

        message = ThreadMessage(
            id=uuid.uuid4().hex,
            thread_id=thread_id,
            role=role,
            content=clean_content,
            created_at=_utc_now_iso(),
        )
        conn = self._connection()
        with conn:
            conn.execute(
                "INSERT INTO thread_messages (id, thread_id, role, content, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    message.id,
                    message.thread_id,
                    message.role,
                    message.content,
                    message.created_at,
                ),
            )
            conn.execute(
                "UPDATE threads SET updated_at = ? WHERE id = ? AND owner = ?",
                (message.created_at, thread_id, clean_owner),
            )
        return message

    def list_messages(self, owner: str, thread_id: str) -> list[ThreadMessage]:
        """All messages in ``owner``'s thread, oldest first."""
        clean_owner = _require_nonblank(owner, "owner")
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread_id must be a non-blank string.")
        self._thread_row_or_raise(clean_owner, thread_id)
        conn = self._connection()
        rows = conn.execute(
            "SELECT id, thread_id, role, content, created_at FROM thread_messages"
            " WHERE thread_id = ? ORDER BY rowid ASC",
            (thread_id,),
        ).fetchall()
        return [ThreadMessage(**dict(row)) for row in rows]
