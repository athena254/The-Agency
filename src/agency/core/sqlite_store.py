"""Shared bootstrap for the project's synchronous SQLite stores."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def open_store(path: str | Path, schema: str) -> sqlite3.Connection:
    """Open a SQLite store, applying the project's WAL setup discipline.

    Creates the parent directory for file-backed stores, enables WAL, applies
    the given schema, and commits before returning the live connection.
    """
    db_path = Path(path)
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(schema)
    conn.commit()
    return conn
