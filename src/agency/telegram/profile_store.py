"""Persistent, owner-scoped presentation names for Telegram users."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

_NAME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9 -]{0,31}\Z", re.ASCII)


def validate_name(name: str) -> str:
    """Return a safe plain/Markdown-compatible presentation name."""
    if not isinstance(name, str):
        raise TypeError("name must be text")
    if "\n" in name or "\r" in name:
        raise ValueError("name cannot contain newlines")
    value = name.strip()
    if not _NAME_PATTERN.fullmatch(value) or value.lower() == "reset":
        raise ValueError("name must be 1-32 characters: letters, digits, spaces or hyphens")
    return value


def _user_id(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("a positive Telegram user ID is required")
    return value


class ProfileStore:
    """Small deterministic SQLite store keyed only by Telegram's stable user ID."""

    def __init__(self, db_path: str) -> None:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS telegram_profiles "
            "(user_id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
        )
        self._conn.commit()

    def get_name(self, user_id: int) -> str | None:
        row = self._conn.execute(
            "SELECT name FROM telegram_profiles WHERE user_id = ?", (_user_id(user_id),)
        ).fetchone()
        if row is None:
            return None
        try:
            return validate_name(row[0])
        except (ValueError, TypeError):
            return None

    def set_name(self, user_id: int, name: str) -> None:
        uid, value = _user_id(user_id), validate_name(name)
        with self._conn:
            self._conn.execute(
                "INSERT INTO telegram_profiles (user_id, name) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET name = excluded.name",
                (uid, value),
            )

    def reset_name(self, user_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM telegram_profiles WHERE user_id = ?", (_user_id(user_id),)
            )

    def close(self) -> None:
        self._conn.close()
