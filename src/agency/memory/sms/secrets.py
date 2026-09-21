"""Secrets node for the Sovereign Mind System.

Per-agent credential storage. Values are encrypted at rest with Fernet
(AES-128-CBC + HMAC-SHA256) and access is gated by a per-secret ACL.

The encryption key is resolved in this order:

1. the ``encryption_key`` constructor argument,
2. the ``AGENCY_SMS_SECRET_KEY`` environment variable,
3. a ``<db>.key`` file next to the database (created on first use, ``0600``).

When no key path is available (e.g. an in-memory database), an ephemeral key is
generated for the lifetime of the process.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Self
from uuid import uuid4

import aiosqlite
import structlog
from cryptography.fernet import Fernet, InvalidToken

from agency.memory.sms.models import ensure_utc, utc_now

logger = structlog.get_logger(__name__)

ENV_KEY = "AGENCY_SMS_SECRET_KEY"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS secrets (
    id          TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL,
    key         TEXT NOT NULL,
    ciphertext  BLOB NOT NULL,
    acl         TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL,
    rotated_at  TEXT,
    revoked     INTEGER NOT NULL DEFAULT 0,
    UNIQUE (agent_id, key)
);

CREATE INDEX IF NOT EXISTS idx_secrets_agent ON secrets(agent_id);
"""


class SecretsStore:
    """Encrypted, ACL-gated key/value storage scoped to an agent."""

    def __init__(
        self,
        db_path: str = ":memory:",
        *,
        encryption_key: bytes | str | None = None,
        key_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self._db_path = str(db_path)
        self._explicit_key = encryption_key
        if key_path is not None:
            self._key_path: str | None = str(key_path)
        elif self._db_path == ":memory:":
            self._key_path = None
        else:
            self._key_path = str(Path(self._db_path).with_suffix(".key"))
        self._fernet: Fernet | None = None
        self._conn: aiosqlite.Connection | None = None
        self._initialized = False

    async def __aenter__(self) -> Self:
        await self.initialize()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("SecretsStore is not initialized")
        return self._conn

    async def initialize(self) -> None:
        """Open the database and create the schema (idempotent)."""

        if self._initialized:
            return
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()
        self._initialized = True
        logger.info("secrets_store.initialized", db_path=self._db_path)

    async def close(self) -> None:
        """Close the connection. Safe to call more than once."""

        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            self._initialized = False
            logger.info("secrets_store.closed", db_path=self._db_path)

    def _cipher(self) -> Fernet:
        """Lazily resolve the Fernet instance, creating a key if needed."""

        if self._fernet is not None:
            return self._fernet

        key: bytes | str | None = self._explicit_key
        if key is None:
            key = os.environ.get(ENV_KEY)
        if key is None and self._key_path is not None and os.path.exists(self._key_path):
            with open(self._key_path, "rb") as handle:
                key = handle.read().strip()
        if key is None:
            key = Fernet.generate_key()
            if self._key_path is not None:
                path = Path(self._key_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(key)
                with contextlib.suppress(OSError):  # best effort on Windows
                    os.chmod(path, 0o600)
            logger.warning("secrets_store.key_generated", key_path=self._key_path)

        key_bytes = key.encode() if isinstance(key, str) else key
        self._fernet = Fernet(key_bytes)
        return self._fernet

    @staticmethod
    def _load_acl(raw: str) -> list[str]:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return []
        return [str(entry) for entry in parsed] if isinstance(parsed, list) else []

    @staticmethod
    def _is_authorized(owner: str, acl: list[str], requester: str) -> bool:
        return requester == owner or requester in acl

    async def store_secret(
        self,
        agent_id: str,
        key: str,
        value: str,
        acl: list[str] | None = None,
    ) -> str:
        """Encrypt and persist ``value`` under ``(agent_id, key)``.

        Existing secrets are overwritten, re-activated and marked un-rotated.
        ``acl`` lists additional agent ids permitted to read the secret.
        Returns the secret id.
        """

        await self.initialize()
        ciphertext = self._cipher().encrypt(value.encode("utf-8"))
        acl_json = json.dumps(sorted(set(acl or [])))
        secret_id = uuid4().hex
        now = utc_now().isoformat()
        await self.connection.execute(
            """
            INSERT INTO secrets (id, agent_id, key, ciphertext, acl, created_at, revoked)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(agent_id, key) DO UPDATE SET
                ciphertext = excluded.ciphertext,
                acl        = excluded.acl,
                rotated_at = NULL,
                revoked    = 0
            """,
            (secret_id, agent_id, key, ciphertext, acl_json, now),
        )
        await self.connection.commit()
        logger.info("secrets_store.stored", agent_id=agent_id, key=key, acl=acl or [])
        return secret_id

    async def get_secret(
        self,
        agent_id: str,
        key: str,
        *,
        requester: str | None = None,
    ) -> str | None:
        """Decrypt a secret for its owner or an ACL-authorized agent.

        Returns ``None`` when the secret is missing, revoked, or the requester
        is not authorized.
        """

        await self.initialize()
        acting = requester or agent_id
        async with self.connection.execute(
            "SELECT ciphertext, acl, revoked FROM secrets WHERE agent_id = ? AND key = ?",
            (agent_id, key),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            logger.warning("secrets_store.get.missing", agent_id=agent_id, key=key)
            return None
        if row["revoked"]:
            logger.warning("secrets_store.get.revoked", agent_id=agent_id, key=key)
            return None
        if not self._is_authorized(agent_id, self._load_acl(row["acl"]), acting):
            logger.warning("secrets_store.get.denied", agent_id=agent_id, key=key, requester=acting)
            return None
        try:
            plaintext = self._cipher().decrypt(bytes(row["ciphertext"]))
        except InvalidToken:
            logger.exception("secrets_store.get.invalid_token", agent_id=agent_id, key=key)
            return None
        return plaintext.decode("utf-8")

    async def rotate_secret(
        self,
        agent_id: str,
        key: str,
        new_value: str | None = None,
    ) -> str:
        """Replace a secret's value with ``new_value`` (or a fresh random one).

        Returns the new plaintext. Raises :class:`KeyError` if the secret does
        not exist or has been revoked.
        """

        await self.initialize()
        async with self.connection.execute(
            "SELECT revoked FROM secrets WHERE agent_id = ? AND key = ?",
            (agent_id, key),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None or row["revoked"]:
            raise KeyError(f"secret {agent_id!r}/{key!r} does not exist or is revoked")

        value = new_value if new_value is not None else secrets.token_urlsafe(32)
        ciphertext = self._cipher().encrypt(value.encode("utf-8"))
        now = utc_now().isoformat()
        await self.connection.execute(
            "UPDATE secrets SET ciphertext = ?, rotated_at = ? WHERE agent_id = ? AND key = ?",
            (ciphertext, now, agent_id, key),
        )
        await self.connection.commit()
        logger.info("secrets_store.rotated", agent_id=agent_id, key=key)
        return value

    async def revoke_secret(self, agent_id: str, key: str) -> bool:
        """Soft-delete a secret so it can no longer be read. Returns success."""

        await self.initialize()
        cursor = await self.connection.execute(
            "UPDATE secrets SET revoked = 1 WHERE agent_id = ? AND key = ?",
            (agent_id, key),
        )
        await self.connection.commit()
        revoked = cursor.rowcount > 0
        logger.info("secrets_store.revoked", agent_id=agent_id, key=key, revoked=revoked)
        return revoked

    async def list_secrets(self, agent_id: str) -> list[str]:
        """Return the keys owned by ``agent_id`` (never their values)."""

        await self.initialize()
        async with self.connection.execute(
            "SELECT key FROM secrets WHERE agent_id = ? AND revoked = 0 ORDER BY key",
            (agent_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [str(row["key"]) for row in rows]

    async def _row_metadata(self, agent_id: str, key: str) -> dict[str, Any] | None:
        """Internal helper used by tests/inspection; never returns plaintext."""

        await self.initialize()
        async with self.connection.execute(
            "SELECT id, agent_id, key, acl, created_at, rotated_at, revoked "
            "FROM secrets WHERE agent_id = ? AND key = ?",
            (agent_id, key),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        result: dict[str, Any] = dict(row)
        result["acl"] = self._load_acl(row["acl"])
        result["created_at"] = self._parse(row["created_at"])
        result["rotated_at"] = self._parse(row["rotated_at"])
        result["revoked"] = bool(row["revoked"])
        return result

    @staticmethod
    def _parse(raw: str | None) -> datetime | None:
        return ensure_utc(datetime.fromisoformat(raw)) if raw else None
