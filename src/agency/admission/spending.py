"""Opt-in owner-pilot USD estimates. Reservations survive unknown provider effects.

This ledger is separate from attempt quotas and never represents a provider invoice.
All monetary arithmetic is exact decimal, bounded to a supported precision.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class SpendingError(Exception):
    """Fixed, non-private fail-closed accounting error."""


class SpendingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    limit_usd: str
    input_usd_per_million: str
    output_usd_per_million: str
    pricing_source: str

    @field_validator("limit_usd", "input_usd_per_million", "output_usd_per_million")
    @classmethod
    def dollars(cls, value: str, info: Any) -> str:
        if re.fullmatch(r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,12})?", value) is None:
            raise ValueError("Invalid spending decimal")
        amount = Decimal(value)
        if info.field_name != "limit_usd" and amount <= 0:
            raise ValueError("Positive spending rate required")
        return format(amount, "f")

    @field_validator("pricing_source")
    @classmethod
    def source(cls, value: str) -> str:
        if not value.strip() or len(value) > 512 or any(ord(c) < 32 for c in value):
            raise ValueError("Invalid pricing source")
        return value


class SpendingLedger:
    """SQLite transactions serialize admission across independent instances.

    No persistent handle/thread; every operation commits before returning. Existing
    settings are immutable: changes refuse startup rather than reset/reprice usage.
    """

    def __init__(self, path: str | Path, config: SpendingConfig, *, identity: str) -> None:
        if type(config) is not SpendingConfig or type(identity) is not str or not identity:
            raise SpendingError("Invalid spending configuration")
        self._path = str(path)
        self._config = config
        self._identity = identity
        marker = json.dumps(
            {"version": 1, "identity": identity, "config": config.model_dump()},
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            with closing(sqlite3.connect(self._path, timeout=5)) as db, db:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                if not tables:
                    db.execute(
                        "CREATE TABLE settings (id INTEGER PRIMARY KEY CHECK(id=1), marker TEXT NOT NULL)"
                    )
                    db.execute(
                        "CREATE TABLE calls (id TEXT PRIMARY KEY, reserved TEXT NOT NULL, input_bound INTEGER NOT NULL, output_bound INTEGER NOT NULL, prompt_tokens INTEGER, completion_tokens INTEGER, reported TEXT)"
                    )
                    db.execute("INSERT INTO settings VALUES (1, ?)", (marker,))
                if db.execute("SELECT marker FROM settings WHERE id=1").fetchall() != [(marker,)]:
                    raise SpendingError("Spending configuration changed")
        except sqlite3.Error:
            raise SpendingError("Spending store unavailable") from None

    def reserve(self, call_id: str, input_bytes: int, max_tokens: int) -> None:
        if (
            type(call_id) is not str
            or not call_id
            or type(input_bytes) is not int
            or input_bytes < 0
            or type(max_tokens) is not int
            or max_tokens <= 0
        ):
            raise SpendingError("Invalid spending reservation")
        with localcontext() as ctx:
            ctx.prec = 80
            amount = (
                Decimal(input_bytes) * Decimal(self._config.input_usd_per_million)
                + Decimal(max_tokens) * Decimal(self._config.output_usd_per_million)
            ) / 1000000
            try:
                with closing(sqlite3.connect(self._path, timeout=5)) as db, db:
                    db.execute("PRAGMA synchronous=FULL")
                    db.execute("BEGIN IMMEDIATE")
                    total = sum(
                        (
                            Decimal(row[0])
                            for row in db.execute("SELECT COALESCE(reported, reserved) FROM calls")
                        ),
                        Decimal(0),
                    )
                    if total + amount > Decimal(self._config.limit_usd):
                        raise SpendingError("Spending ceiling exceeded")
                    db.execute(
                        "INSERT INTO calls VALUES (?, ?, ?, ?, NULL, NULL, NULL)",
                        (call_id, format(amount, "f"), input_bytes, max_tokens),
                    )
            except sqlite3.Error:
                raise SpendingError("Spending store unavailable") from None

    def settle(self, call_id: str, usage: object) -> None:
        if type(usage) is not dict:
            raise SpendingError("Provider usage unresolved")
        prompt: int | None = usage.get("prompt_tokens")
        completion: int | None = usage.get("completion_tokens")
        if (
            type(prompt) is not int
            or type(completion) is not int
            or not 0 <= prompt <= 2147483647
            or not 0 <= completion <= 2147483647
        ):
            raise SpendingError("Provider usage unresolved")
        if "total_tokens" in usage and (
            type(usage["total_tokens"]) is not int or usage["total_tokens"] != prompt + completion
        ):
            raise SpendingError("Provider usage unresolved")
        with localcontext() as ctx:
            ctx.prec = 80
            cost = (
                Decimal(prompt) * Decimal(self._config.input_usd_per_million)
                + Decimal(completion) * Decimal(self._config.output_usd_per_million)
            ) / 1000000
            try:
                with closing(sqlite3.connect(self._path, timeout=5)) as db, db:
                    db.execute("PRAGMA synchronous=FULL")
                    db.execute("BEGIN IMMEDIATE")
                    row = db.execute(
                        "SELECT input_bound, output_bound, prompt_tokens, completion_tokens FROM calls WHERE id=?",
                        (call_id,),
                    ).fetchone()
                    if row is None or prompt > row[0] or completion > row[1]:
                        raise SpendingError("Provider usage unresolved")
                    if row[2] is not None:
                        if row[2:] != (prompt, completion):
                            raise SpendingError("Provider usage conflict")
                        return
                    db.execute(
                        "UPDATE calls SET prompt_tokens=?, completion_tokens=?, reported=? WHERE id=?",
                        (prompt, completion, format(cost, "f"), call_id),
                    )
            except sqlite3.Error:
                raise SpendingError("Spending store unavailable") from None

    def snapshot(self) -> dict[str, Any]:
        try:
            with closing(sqlite3.connect(self._path, timeout=5)) as db:
                rows = db.execute(
                    "SELECT reserved, prompt_tokens, completion_tokens, reported FROM calls"
                ).fetchall()
        except sqlite3.Error:
            raise SpendingError("Spending store unavailable") from None
        with localcontext() as ctx:
            ctx.prec = 80
            return {
                "enabled": True,
                "currency": "USD",
                "limit_usd": self._config.limit_usd,
                "reserved_usd": format(
                    sum((Decimal(r[0]) for r in rows if r[3] is None), Decimal(0)), "f"
                ),
                "reported_cost_usd": format(
                    sum((Decimal(r[3]) for r in rows if r[3] is not None), Decimal(0)), "f"
                ),
                "prompt_tokens": sum(r[1] for r in rows if r[3] is not None),
                "completion_tokens": sum(r[2] for r in rows if r[3] is not None),
                "unresolved_calls": sum(r[3] is None for r in rows),
                "pricing_source": self._config.pricing_source,
            }
