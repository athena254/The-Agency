"""Opt-in local-owner residual-risk receipts, never historical reconciliation.

No generic audit semantics are changed. Transactions lock the three fixed stores;
only explicit prepare writes identities, and only accept writes owner_ack entries.
Attached WAL databases are NOT crash-atomic: partial preparation fails closed.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import json
import math
import re
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar, cast
from uuid import uuid4

from pydantic import SecretStr

from agency.admission.budget import OwnerBudgetAdapter
from agency.admission.models import OwnerExecutionGrant, OwnerPrincipal, PilotError
from agency.admission.store import OwnerPilotStore
from agency.kernel.audit import AuditEntry, BetaAuditLog

MAX_ROWS = 10000
MAX_BYTES = 16 * 1024 * 1024
MARKER = "owner_recovery_identity"
ACTION = "owner.acknowledge_uncertainty"
_T = TypeVar("_T")
COLUMNS = (
    "entry_id",
    "timestamp",
    "agent",
    "task",
    "target",
    "authorization",
    "capability",
    "action",
    "result",
    "evidence",
    "model",
    "model_version",
    "tool_version",
    "environment",
)


def canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def unique(pairs: Any) -> dict[Any, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PilotError("store", uncertain=True)
        result[key] = value
    return result


def strict_json(value: Any) -> Any:
    if type(value) is not str or len(value.encode("utf-8")) > 1024 * 1024:
        raise PilotError("store", uncertain=True)
    return json.loads(
        value,
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
    )


def raw_row(row: Any) -> dict[str, Any]:
    value = dict(row)
    for cell in value.values():
        if cell is not None and type(cell) not in (int, float, str):
            raise PilotError("store", uncertain=True)
        if type(cell) is float and not math.isfinite(cell):
            raise PilotError("store", uncertain=True)
    return value


class OwnerRecovery:
    """Exact, owner-only coordinator. A key alone never authorizes exclusions."""

    def __init__(
        self,
        *,
        store: OwnerPilotStore,
        budget: OwnerBudgetAdapter,
        audit: BetaAuditLog,
        principal: OwnerPrincipal,
        key: SecretStr,
        workspace_id: str = "",
        spending: Any = None,
        operator: bool = False,
    ):
        if (
            type(store) is not OwnerPilotStore
            or type(budget) is not OwnerBudgetAdapter
            or type(audit) is not BetaAuditLog
            or type(principal) is not OwnerPrincipal
            or type(key) is not SecretStr
            or not key.get_secret_value()
            or budget._store is not store
            or spending is not None
        ):
            raise PilotError("invalid_request")
        principal.__post_init__()
        if principal.transport != "local" or type(workspace_id) is not str:
            raise PilotError("forbidden")
        self._store, self._budget, self._audit = store, budget, audit
        self._principal = principal
        self._workspace_id = workspace_id
        self._key = hmac.digest(
            key.get_secret_value().encode(), b"agency.owner-recovery.v1/key", "sha256"
        )
        self._paths = (
            Path(audit._db_path).resolve(),
            Path(store._db_path).resolve(),
            Path(budget._backend._db_path).resolve(),
        )
        if len(set(self._paths)) != 3:
            raise PilotError("invalid_request")
        self._issued = (store, budget, audit, principal, self._paths)
        self._gate = asyncio.Lock()
        self._grant: OwnerExecutionGrant | None = None
        if type(operator) is not bool:
            raise PilotError("invalid_request")
        self._operator = operator

    def for_grant(self, grant: OwnerExecutionGrant) -> OwnerRecovery:
        self.validate_binding(grant, self._audit)
        bound = copy.copy(self)
        bound._grant = grant
        bound._operator = False
        return bound

    def _validate(self) -> None:
        store, budget, audit, principal, paths = self._issued
        if (
            type(self) is not OwnerRecovery
            or self._store is not store
            or self._budget is not budget
            or self._audit is not audit
            or self._principal is not principal
            or budget._store is not store
            or self._paths != paths
            or tuple(
                Path(p).resolve()
                for p in (audit._db_path, store._db_path, budget._backend._db_path)
            )
            != paths
            or store._closed
            or not store._ready
            or store._publication_closed
            or store._lock._handle is None
            or budget._closed
            or audit._conn is None
        ):
            raise PilotError("disabled", uncertain=True)
        principal.__post_init__()

    def validate_binding(self, grant: OwnerExecutionGrant, audit: BetaAuditLog) -> None:
        self._validate()
        if (
            type(grant) is not OwnerExecutionGrant
            or grant.budget is not self._budget
            or audit is not self._audit
            or grant.lease.principal != self._principal
            or self._grant is not None
            and self._grant is not grant
        ):
            raise PilotError("forbidden")
        grant.__post_init__()

    def _mac(self, domain: str, value: Any) -> str:
        return hmac.digest(
            self._key, domain.encode() + b"\0" + canonical(value).encode(), "sha256"
        ).hex()

    def _file_ids(self) -> list[dict[str, Any]]:
        result = []
        for path in self._paths:
            if not path.is_file() or path.is_symlink():
                raise PilotError("store")
            stat = path.stat()
            if stat.st_nlink != 1 or not stat.st_ino:
                raise PilotError("store")
            result.append({"path": str(path), "device": stat.st_dev, "inode": stat.st_ino})
        return result

    @contextmanager
    def _transaction(
        self, readback: Callable[[sqlite3.Connection], Any]
    ) -> Iterator[sqlite3.Connection]:
        self._validate()
        identities = self._file_ids()
        conn = sqlite3.connect(
            self._paths[0].as_uri() + "?mode=rw", uri=True, isolation_level=None, timeout=5
        )
        conn.row_factory = sqlite3.Row
        try:
            for name, path in zip(("pilot", "quota"), self._paths[1:], strict=True):
                conn.execute(f"ATTACH DATABASE ? AS {name}", (path.as_uri() + "?mode=rw",))
            for name in ("main", "pilot", "quota"):
                if conn.execute(f"PRAGMA {name}.journal_mode").fetchone()[0] != "wal":
                    raise PilotError("store")
                conn.execute(f"PRAGMA {name}.synchronous=FULL")
                if conn.execute(f"PRAGMA {name}.synchronous").fetchone()[0] != 2:
                    raise PilotError("store")
            conn.execute("BEGIN IMMEDIATE")
            if identities != self._file_ids():
                raise PilotError("store")
            yield conn
            self._validate()
            if identities != self._file_ids():
                raise PilotError("store")
            conn.execute("COMMIT")
            readback(conn)
        finally:
            conn.close()

    def _rows(
        self, conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        rows = conn.execute(sql + " LIMIT ?", (*params, MAX_ROWS + 1)).fetchall()
        if len(rows) > MAX_ROWS:
            raise PilotError("too_large")
        result = [raw_row(row) for row in rows]
        if len(canonical(result).encode()) > MAX_BYTES:
            raise PilotError("too_large")
        return result

    def _scope(self, conn: sqlite3.Connection) -> dict[str, Any]:
        rows = self._rows(
            conn,
            "SELECT s.*, w.owner AS workspace_owner FROM pilot.pilot_subjects s JOIN pilot.workspaces w ON w.id=s.workspace_id",
        )
        if len(rows) != 1:
            raise PilotError("forbidden")
        row = rows[0]
        if (
            row["owner_id"] != self._principal.owner_id
            or row["workspace_owner"] != row["owner_id"]
            or type(row["workspace_id"]) is not str
            or not row["workspace_id"]
            or type(row["quota_subject_id"]) is not int
            or row["quota_subject_id"] <= 0
            or self._workspace_id
            and self._workspace_id != row["workspace_id"]
        ):
            raise PilotError("forbidden")
        return {
            "owner_id": self._principal.owner_id,
            "transport": "local",
            "subject_id": self._principal.subject_id,
            "workspace_id": row["workspace_id"],
            "quota_subject_id": row["quota_subject_id"],
            "namespace": self._budget._namespace,
        }

    def _audit_rows(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = self._rows(conn, "SELECT * FROM audit_entries ORDER BY entry_id")
        for row in rows:
            if any(value is not None and type(value) is not str for value in row.values()):
                raise PilotError("store")
            if any(
                type(row[k]) is not str or not row[k] for k in ("entry_id", "timestamp", "result")
            ):
                raise PilotError("store")
            for key in ("evidence", "environment"):
                if row[key] is not None:
                    parsed = strict_json(row[key])
                    if type(parsed) is not dict:
                        raise PilotError("store")
        return rows

    def _markers(self, conn: sqlite3.Connection, *, allow_absent: bool = False) -> str | None:
        exists = [
            conn.execute(
                f"SELECT 1 FROM {name}.sqlite_master WHERE type='table' AND name=?", (MARKER,)
            ).fetchone()
            is not None
            for name in ("main", "pilot", "quota")
        ]
        if not any(exists) and allow_absent:
            return None
        if not all(exists):
            raise PilotError("store", uncertain=True)
        expected_scope = self._scope(conn)
        files = self._file_ids()
        group = None
        for index, name in enumerate(("main", "pilot", "quota")):
            rows = self._rows(conn, f"SELECT * FROM {name}.{MARKER}")
            if len(rows) != 1 or set(rows[0]) != {"payload", "mac"}:
                raise PilotError("store")
            record = rows[0]
            body = strict_json(record["payload"])
            if (
                type(body) is not dict
                or set(body) != {"version", "group", "role", "scope", "files"}
                or body["version"] != 1
                or type(body["version"]) is not int
                or type(body["group"]) is not str
                or re.fullmatch("[0-9a-f]{32}", body["group"]) is None
                or body["role"] != name
                or body["scope"] != expected_scope
                or body["files"] != files
                or type(record["mac"]) is not str
                or not hmac.compare_digest(record["mac"], self._mac("identity", body))
            ):
                raise PilotError("store", uncertain=True)
            if group is not None and group != body["group"]:
                raise PilotError("store")
            group = body["group"]
        return group

    def _offline(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT 1 FROM pilot.pilot_requests WHERE state IN ('ADMITTED','RUNNING') OR (result_json IS NOT NULL AND state != 'COMPLETED') LIMIT 1"
        ).fetchone()
        if row is not None:
            raise PilotError("conflict")

    def _snapshot(
        self, conn: sqlite3.Connection, group: str | None
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        scope = self._scope(conn)
        turns = self._rows(
            conn, "SELECT * FROM pilot.pilot_requests WHERE state='UNCERTAIN' ORDER BY sequence"
        )
        uncertain = []
        for row in turns:
            integer_fields = ("sequence", "cancel_requested", "model_attempts", "tool_attempts")
            optional_fields = (
                "resource_id",
                "result_json",
                "result_digest",
                "error_code",
                "provider_config_id",
                "user_message_id",
                "assistant_message_id",
            )
            if (
                any(type(row[k]) is not int for k in integer_fields)
                or row["cancel_requested"] not in (0, 1)
                or not 0 <= row["model_attempts"] <= 8
                or not 0 <= row["tool_attempts"] <= 4
                or any(
                    type(row[k]) not in (int, float) or not math.isfinite(row[k]) or row[k] <= 0
                    for k in ("created_at", "updated_at")
                )
                or any(row[k] is not None and type(row[k]) is not str for k in optional_fields)
            ):
                raise PilotError("store")
            if (
                row["owner_id"] != scope["owner_id"]
                or row["operation"] != "turn"
                or any(
                    type(row[k]) is not str or not row[k]
                    for k in ("request_id", "task_id", "thread_id", "input_json", "input_digest")
                )
                or type(row["sequence"]) is not int
                or row["sequence"] <= 0
            ):
                raise PilotError("store")
            payload = strict_json(row["input_json"])
            if (
                type(payload) is not dict
                or set(payload) != {"thread_id", "message"}
                or payload["thread_id"] != row["thread_id"]
                or type(payload["message"]) is not str
                or digest(payload) != row["input_digest"]
            ):
                raise PilotError("store")
            thread = conn.execute(
                "SELECT owner,workspace_id FROM pilot.threads WHERE id=?", (row["thread_id"],)
            ).fetchone()
            if thread is None or tuple(thread) != (scope["owner_id"], scope["workspace_id"]):
                raise PilotError("forbidden")
            uncertain.append(
                {
                    "sequence": row["sequence"],
                    "request_id": row["request_id"],
                    "task_id": row["task_id"],
                    "fingerprint": digest(row),
                }
            )
        audit_rows = self._audit_rows(conn)
        intents, outcomes, unknowns, receipts = {}, {}, {}, []
        for row in audit_rows:
            evidence = strict_json(row["evidence"]) if row["evidence"] is not None else {}
            phase = evidence.get("phase")
            if row["result"] == "pending" and phase != "intent":
                raise PilotError("store")
            if "version" in evidence and (
                type(evidence["version"]) is not int or evidence["version"] != 1
            ):
                raise PilotError("store")
            if row["action"] == ACTION or phase == "owner_ack":
                receipts.append(row)
                continue
            if phase in ("intent", "outcome", "uncertainty"):
                call = evidence.get("call_id")
                if type(call) is not str or re.fullmatch("[0-9a-f]{32}", call) is None:
                    raise PilotError("store")
                if phase == "intent":
                    if call in intents or row["result"] != "pending":
                        raise PilotError("store")
                    intents[call] = row
                elif phase == "outcome":
                    if call in outcomes:
                        raise PilotError("store")
                    outcomes[call] = row
                else:
                    if call in unknowns:
                        raise PilotError("store")
                    unknowns[call] = row
        if (outcomes.keys() | unknowns.keys()) - intents.keys():
            raise PilotError("store")
        for call, row in unknowns.items():
            intent = intents[call]
            if (row["task"], row["agent"], row["target"]) != (
                intent["task"],
                intent["agent"],
                intent["target"],
            ):
                raise PilotError("store")
        pending = []
        for call, row in intents.items():
            if call in outcomes:
                out = outcomes[call]
                if (out["task"], out["agent"], out["target"]) != (
                    row["task"],
                    row["agent"],
                    row["target"],
                ):
                    raise PilotError("store")
                continue
            if row["task"] != "owner-pilot" or row["agent"] not in ("metered-model", "owner-tools"):
                raise PilotError("forbidden")
            pending.append(
                {"entry_id": row["entry_id"], "call_id": call, "fingerprint": digest(row)}
            )
        snapshot = {
            "version": 1,
            "group": group,
            "scope": scope,
            "files": self._file_ids(),
            "uncertain_turns": uncertain,
            "pending_intents": sorted(pending, key=lambda p: p["entry_id"]),
        }
        return snapshot, receipts

    def _verify_receipt(self, row: dict[str, Any]) -> dict[str, Any]:
        evidence = strict_json(row["evidence"])
        if (
            row["action"] != ACTION
            or row["agent"] != "owner-operator"
            or row["task"] != "owner-pilot"
            or row["result"] != "acknowledged"
            or type(evidence) is not dict
            or set(evidence)
            != {
                "phase",
                "version",
                "request_id",
                "snapshot",
                "snapshot_digest",
                "accept_residual_uncertainty",
                "signature",
            }
            or evidence["phase"] != "owner_ack"
            or type(evidence["version"]) is not int
            or evidence["version"] != 1
            or evidence["accept_residual_uncertainty"] is not True
            or type(evidence["request_id"]) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", evidence["request_id"]) is None
            or evidence["snapshot_digest"] != digest(evidence["snapshot"])
        ):
            raise PilotError("audit", uncertain=True)
        unsigned = dict(row)
        unsigned["evidence"] = {k: v for k, v in evidence.items() if k != "signature"}
        signature = evidence["signature"]
        if type(signature) is not str or not hmac.compare_digest(
            signature, self._mac("owner_ack", unsigned)
        ):
            raise PilotError("audit", uncertain=True)
        return evidence

    def _authorized(self, snapshot: dict[str, Any], receipts: list[dict[str, Any]]) -> bool:
        accepted = False
        requests = set()
        for row in receipts:
            body = self._verify_receipt(row)
            if body["request_id"] in requests:
                raise PilotError("audit")
            requests.add(body["request_id"])
            if body["snapshot"] == snapshot:
                accepted = True
        return accepted

    async def _run(self, work: Callable[[sqlite3.Connection], _T]) -> _T:
        async with self._gate:
            self._validate()

            def transaction() -> _T:
                result: Any = None

                def readback(conn: sqlite3.Connection) -> None:
                    if type(result) is not dict:
                        return
                    if result.get("prepared") is True:
                        self._markers(conn)
                    row = result if "receipt_id" in result else result.get("claimed_row")
                    if row is not None:
                        expected = tuple(
                            canonical(row[k])
                            if k in ("evidence", "environment") and row[k] is not None
                            else row[k]
                            for k in COLUMNS
                        )
                        saved = conn.execute(
                            "SELECT * FROM audit_entries WHERE entry_id=?", (row["entry_id"],)
                        ).fetchone()
                        if saved is None or tuple(saved) != expected:
                            raise PilotError("audit", uncertain=True)

                with self._transaction(readback) as conn:
                    result = work(conn)
                return cast(_T, result)

            try:
                # Retain/drain the worker on cancellation: no abandoned COMMIT.
                result = await self._store._offload(transaction)
                self._validate()
                return result
            except PilotError:
                raise
            except (
                sqlite3.Error,
                OSError,
                ValueError,
                TypeError,
                KeyError,
                UnicodeError,
                RecursionError,
            ):
                raise PilotError("store", uncertain=True) from None

    async def prepare(self) -> dict[str, Any]:
        if self._operator is not True:
            raise PilotError("forbidden")

        def work(conn: sqlite3.Connection) -> dict[str, Any]:
            self._offline(conn)
            existing = self._markers(conn, allow_absent=True)
            if existing is not None:
                return {"prepared": True}
            rows = self._audit_rows(conn)
            if any(
                row["action"] == ACTION
                or (strict_json(row["evidence"]) if row["evidence"] else {}).get("phase")
                == "owner_ack"
                for row in rows
            ):
                raise PilotError("store")
            scope, files, group = self._scope(conn), self._file_ids(), uuid4().hex
            for name in ("main", "pilot", "quota"):
                conn.execute(
                    f"CREATE TABLE {name}.{MARKER} (payload TEXT NOT NULL, mac TEXT NOT NULL)"
                )
                body = {"version": 1, "group": group, "role": name, "scope": scope, "files": files}
                conn.execute(
                    f"INSERT INTO {name}.{MARKER} VALUES (?,?)",
                    (canonical(body), self._mac("identity", body)),
                )
            self._markers(conn)
            return {"prepared": True}

        return await self._run(work)

    async def preview(self) -> dict[str, Any]:
        def work(conn: sqlite3.Connection) -> dict[str, Any]:
            self._offline(conn)
            group = self._markers(conn)
            snapshot, receipts = self._snapshot(conn, group)
            self._authorized(snapshot, receipts)
            return {**snapshot, "digest": digest(snapshot)}

        return await self._run(work)

    async def blocked(self) -> bool:
        def work(conn: sqlite3.Connection) -> bool:
            group = self._markers(conn, allow_absent=True)
            snapshot, receipts = self._snapshot(conn, group)
            if group is None and receipts:
                raise PilotError("store")
            if group is not None and self._authorized(snapshot, receipts):
                return False
            return bool(snapshot["uncertain_turns"] or snapshot["pending_intents"])

        return await self._run(work)

    def _insert(self, conn: sqlite3.Connection, row: dict[str, Any]) -> None:
        values = tuple(
            canonical(row[k]) if k in ("evidence", "environment") and row[k] is not None else row[k]
            for k in COLUMNS
        )
        conn.execute(
            "INSERT INTO audit_entries ("
            + ",".join(COLUMNS)
            + ") VALUES ("
            + ",".join("?" for _ in COLUMNS)
            + ")",
            values,
        )
        saved = conn.execute(
            "SELECT * FROM audit_entries WHERE entry_id=?", (row["entry_id"],)
        ).fetchone()
        if tuple(saved) != values:
            raise PilotError("audit", uncertain=True)

    async def accept(
        self, snapshot_digest: str, request_id: str, *, accept_residual_uncertainty: bool = False
    ) -> dict[str, Any]:
        if self._operator is not True:
            raise PilotError("forbidden")
        if (
            type(snapshot_digest) is not str
            or re.fullmatch("[0-9a-f]{64}", snapshot_digest) is None
            or type(request_id) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", request_id) is None
            or accept_residual_uncertainty is not True
        ):
            raise PilotError("invalid_request")

        def work(conn: sqlite3.Connection) -> dict[str, Any]:
            self._offline(conn)
            group = self._markers(conn)
            snapshot, receipts = self._snapshot(conn, group)
            self._authorized(snapshot, receipts)
            if digest(snapshot) != snapshot_digest:
                raise PilotError("conflict")
            for row in receipts:
                evidence = self._verify_receipt(row)
                if evidence["request_id"] == request_id:
                    if evidence["snapshot_digest"] != snapshot_digest:
                        raise PilotError("conflict")
                    return {**row, "receipt_id": row["entry_id"], "evidence": evidence}
            entry = AuditEntry(
                agent="owner-operator", task="owner-pilot", action=ACTION, result="acknowledged"
            )
            row = entry.model_dump(mode="json")
            row["timestamp"] = entry.as_ts()
            row["evidence"] = {
                "phase": "owner_ack",
                "version": 1,
                "request_id": request_id,
                "snapshot": snapshot,
                "snapshot_digest": snapshot_digest,
                "accept_residual_uncertainty": True,
            }
            row["evidence"]["signature"] = self._mac("owner_ack", row)
            self._insert(conn, row)
            return {**row, "receipt_id": row["entry_id"]}

        return await self._run(work)

    async def claim_intent(
        self, grant: OwnerExecutionGrant, audit: BetaAuditLog, entry: AuditEntry
    ) -> bool:
        self.validate_binding(grant, audit)
        if (
            type(entry) is not AuditEntry
            or entry.task != "owner-pilot"
            or entry.agent not in ("metered-model", "owner-tools")
            or entry.result != "pending"
            or not entry.evidence
            or entry.evidence.get("phase") != "intent"
            or type(entry.evidence.get("call_id")) is not str
            or re.fullmatch("[0-9a-f]{32}", entry.evidence["call_id"]) is None
        ):
            raise PilotError("forbidden")

        def work(conn: sqlite3.Connection) -> dict[str, Any] | bool:
            self.validate_binding(grant, audit)
            lease = grant.lease
            current = conn.execute(
                "SELECT * FROM pilot.pilot_requests WHERE sequence=?", (lease.sequence,)
            ).fetchone()
            scope = self._scope(conn)
            if (
                current is None
                or current["owner_id"] != self._principal.owner_id
                or current["task_id"] != lease.task_id
                or current["request_id"] != lease.request_id
                or current["state"] != "RUNNING"
                or current["cancel_requested"] != 0
                or type(current["created_at"]) not in (float, int)
                or current["created_at"] + 120 != lease.deadline_at
                or time.time() >= lease.deadline_at
                or scope["quota_subject_id"] != lease.quota_subject_id
            ):
                raise PilotError("forbidden")
            group = self._markers(conn, allow_absent=True)
            snapshot, receipts = self._snapshot(conn, group)
            if group is None and receipts:
                raise PilotError("audit")
            if (snapshot["uncertain_turns"] or snapshot["pending_intents"]) and (
                group is None or not self._authorized(snapshot, receipts)
            ):
                return False
            if group is not None:
                self._authorized(snapshot, receipts)
            row = entry.model_dump(mode="json")
            row["timestamp"] = entry.as_ts()
            self._insert(conn, row)
            return {"claimed_row": row}

        result = await self._run(work)
        self.validate_binding(grant, audit)
        return type(result) is dict and "claimed_row" in result
