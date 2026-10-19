"""Durable owner admission and atomic transcript publication, not peer authority."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

from agency.admission.models import (
    AdmissionRecord,
    MessageView,
    OwnerPrincipal,
    OwnerTaskRequest,
    PilotError,
    RequestReceipt,
    ThreadView,
)
from agency.agents.assessment import ExecutionAssessment, output_digest
from agency.butler.threads import ThreadStore
from agency.workflows.recovery import RecoverableLock

_T = TypeVar("_T")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS pilot_subjects (
 quota_subject_id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT UNIQUE NOT NULL,
 workspace_id TEXT UNIQUE NOT NULL REFERENCES workspaces(id));
CREATE TABLE IF NOT EXISTS pilot_requests (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL REFERENCES pilot_subjects(owner_id),
 request_id TEXT NOT NULL, operation TEXT NOT NULL, input_digest TEXT NOT NULL, input_json TEXT NOT NULL,
 task_id TEXT UNIQUE, thread_id TEXT REFERENCES threads(id), resource_id TEXT,
 state TEXT NOT NULL CHECK(state IN ('ADMITTED','RUNNING','COMPLETED','FAILED','DENIED','CANCELLED','UNCERTAIN')),
 phase TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),
 model_attempts INTEGER NOT NULL DEFAULT 0 CHECK(model_attempts BETWEEN 0 AND 8),
 tool_attempts INTEGER NOT NULL DEFAULT 0 CHECK(tool_attempts BETWEEN 0 AND 4),
 result_json TEXT, result_digest TEXT, error_code TEXT, provider_config_id TEXT,
 user_message_id TEXT, assistant_message_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 UNIQUE(owner_id,request_id));
CREATE TABLE IF NOT EXISTS pilot_events (
 event_id INTEGER PRIMARY KEY AUTOINCREMENT, sequence INTEGER NOT NULL REFERENCES pilot_requests(sequence),
 phase TEXT NOT NULL, code TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_pilot_owner_tasks ON pilot_requests(owner_id, sequence);
"""


def _iso(moment: float) -> str:
    return datetime.fromtimestamp(moment, UTC).isoformat()


def _id(value: str) -> None:
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value) is None:
        raise PilotError("invalid_request")


def _principal(value: OwnerPrincipal) -> None:
    if type(value) is not OwnerPrincipal:
        raise PilotError("unauthorized")
    value.__post_init__()


def _canonical(value: dict[str, Any], *, maximum: int = 32768) -> tuple[str, str]:
    try:
        text = json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        data = text.encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise PilotError("invalid_request") from None
    if len(data) > maximum:
        raise PilotError("too_large")
    return text, hashlib.sha256(data).hexdigest()


class OwnerPilotStore:
    def __init__(self, db_path: str) -> None:
        if (
            type(db_path) is not str
            or not db_path.strip()
            or db_path == ":memory:"
            or db_path.startswith("file:")
        ):
            raise PilotError("invalid_request")
        self._db_path = str(Path(db_path).resolve())
        self._lock = RecoverableLock(self._db_path)
        self._gate = asyncio.Lock()
        self._ready = False
        self._closed = False
        self._fresh_turns: set[int] = set()
        self._blocked_sequences: set[int] = set()

    async def _offload(self, work: Callable[[], _T]) -> _T:
        task = asyncio.create_task(asyncio.to_thread(work))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Hold the lifecycle gate until the SQLite worker really stops.
            drain = asyncio.gather(task, return_exceptions=True)
            while not drain.done():
                try:
                    await asyncio.shield(drain)
                except asyncio.CancelledError:
                    continue
            if not task.cancelled():
                task.exception()
            raise

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            conn.execute("PRAGMA synchronous=FULL")
            if mode != "wal" or conn.execute("PRAGMA synchronous").fetchone()[0] != 2:
                raise PilotError("store")
            return conn
        except BaseException:
            conn.close()
            raise

    def _initialize(self) -> None:
        try:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            if not self._lock.acquire():
                raise PilotError("store")
            # Reuse the concrete existing schema; never duplicate its DDL.
            threads = ThreadStore.__new__(ThreadStore)
            try:
                ThreadStore.__init__(threads, self._db_path)
            finally:
                if getattr(threads, "_conn", None) is not None:
                    threads.close()
            conn = self._connect()
            try:
                conn.executescript(_SCHEMA)
            finally:
                conn.close()
            self._ready = True
        except BaseException:
            self._lock.release()
            raise

    async def initialize(self) -> None:
        async with self._gate:
            if self._closed:
                raise PilotError("store")
            if not self._ready:
                try:
                    await self._offload(self._initialize)
                except (OSError, sqlite3.Error, ValueError):
                    raise PilotError("store") from None
                except asyncio.CancelledError:
                    self._ready = False
                    self._lock.release()
                    raise

    async def close(self) -> None:
        async with self._gate:
            self._ready = False
            self._closed = True
            self._lock.release()

    def _transaction(self, work: Callable[[sqlite3.Connection], _T]) -> _T:
        conn: sqlite3.Connection | None = None
        try:
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
            result = work(conn)
            conn.execute("COMMIT")
            return result
        except (sqlite3.Error, OSError, ValueError):
            raise PilotError("store") from None
        finally:
            if conn is not None:
                conn.close()  # closing rolls back any uncommitted writes

    async def _run(self, work: Callable[[sqlite3.Connection], _T]) -> _T:
        async with self._gate:
            if not self._ready or self._closed:
                raise PilotError("store")
            return await self._offload(lambda: self._transaction(work))

    @staticmethod
    def _workspace(conn: sqlite3.Connection, principal: OwnerPrincipal) -> str:
        row = conn.execute(
            "SELECT workspace_id FROM pilot_subjects WHERE owner_id=?", (principal.owner_id,)
        ).fetchone()
        if row is not None:
            return str(row[0])
        workspace = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO workspaces VALUES (?,?,?,?)",
            (workspace, principal.owner_id, "Owner pilot", _iso(time.time())),
        )
        conn.execute(
            "INSERT INTO pilot_subjects(owner_id,workspace_id) VALUES (?,?)",
            (principal.owner_id, workspace),
        )
        return workspace

    async def ensure_workspace(self, principal: OwnerPrincipal) -> str:
        _principal(principal)
        return await self._run(lambda conn: self._workspace(conn, principal))

    @staticmethod
    def _thread(conn: sqlite3.Connection, principal: OwnerPrincipal, thread_id: str) -> sqlite3.Row:
        _id(thread_id)
        row = conn.execute(
            "SELECT t.* FROM threads t JOIN pilot_subjects s ON s.workspace_id=t.workspace_id WHERE t.id=? AND t.owner=? AND s.owner_id=?",
            (thread_id, principal.owner_id, principal.owner_id),
        ).fetchone()
        if row is None:
            raise PilotError("not_found")
        return cast(sqlite3.Row, row)

    @staticmethod
    def _thread_view(row: sqlite3.Row) -> ThreadView:
        return ThreadView(**{key: row[key] for key in ThreadView.model_fields})

    async def create_thread(
        self,
        principal: OwnerPrincipal,
        request_id: str,
        title: str,
        parent_thread_id: str | None = None,
    ) -> tuple[RequestReceipt, ThreadView]:
        _principal(principal)
        _id(request_id)
        if type(title) is not str or not 1 <= len(title) <= 120:
            raise PilotError("invalid_request")
        payload, digest = _canonical({"title": title, "parent_thread_id": parent_thread_id})

        def work(conn: sqlite3.Connection) -> tuple[RequestReceipt, ThreadView]:
            existing = conn.execute(
                "SELECT * FROM pilot_requests WHERE owner_id=? AND request_id=?",
                (principal.owner_id, request_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["operation"] != "new_thread"
                    or existing["input_digest"] != digest
                    or existing["input_json"] != payload
                ):
                    raise PilotError("conflict")
                thread = self._thread(conn, principal, existing["resource_id"])
                return RequestReceipt(
                    request_id=request_id,
                    state=existing["state"],
                    replayed=True,
                    resource_id=thread["id"],
                ), self._thread_view(thread)
            workspace = self._workspace(conn, principal)
            if parent_thread_id is not None:
                self._thread(conn, principal, parent_thread_id)
            thread_id, now = uuid.uuid4().hex, time.time()
            conn.execute(
                "INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)",
                (
                    thread_id,
                    workspace,
                    principal.owner_id,
                    title,
                    parent_thread_id,
                    "active",
                    _iso(now),
                    _iso(now),
                ),
            )
            conn.execute(
                "INSERT INTO pilot_requests(owner_id,request_id,operation,input_digest,input_json,thread_id,resource_id,state,phase,created_at,updated_at) VALUES (?,?,?,?,?,?,?,'COMPLETED','committed',?,?)",
                (
                    principal.owner_id,
                    request_id,
                    "new_thread",
                    digest,
                    payload,
                    thread_id,
                    thread_id,
                    now,
                    now,
                ),
            )
            return RequestReceipt(
                request_id=request_id, state="COMPLETED", replayed=False, resource_id=thread_id
            ), self._thread_view(self._thread(conn, principal, thread_id))

        return await self._run(work)

    @staticmethod
    def _record(row: sqlite3.Row, *, replayed: bool = False) -> AdmissionRecord:
        return AdmissionRecord(
            **(
                dict(row)
                | {"cancel_requested": bool(row["cancel_requested"]), "replayed": replayed}
            )
        )

    @staticmethod
    def _request_row(
        conn: sqlite3.Connection, principal: OwnerPrincipal, request_id: str
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT r.* FROM pilot_requests r JOIN threads t ON t.id=r.thread_id JOIN pilot_subjects s ON s.workspace_id=t.workspace_id WHERE r.owner_id=? AND r.request_id=? AND t.owner=r.owner_id AND s.owner_id=r.owner_id",
            (principal.owner_id, request_id),
        ).fetchone()
        if row is None:
            raise PilotError("not_found")
        return cast(sqlite3.Row, row)

    async def admit(
        self, principal: OwnerPrincipal, request_id: str, *, operation: str, payload: dict[str, Any]
    ) -> AdmissionRecord:
        _principal(principal)
        _id(request_id)
        if type(operation) is not str or operation != "turn" or type(payload) is not dict:
            raise PilotError("invalid_request")
        try:
            validated = OwnerTaskRequest.model_validate({"request_id": request_id, **payload})
        except ValueError:
            raise PilotError("invalid_request") from None
        if set(payload) != {"thread_id", "message"}:
            raise PilotError("invalid_request")
        text, digest = _canonical(payload)

        def work(conn: sqlite3.Connection) -> AdmissionRecord:
            self._thread(conn, principal, validated.thread_id)
            existing = conn.execute(
                "SELECT * FROM pilot_requests WHERE owner_id=? AND request_id=?",
                (principal.owner_id, request_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["operation"] != operation
                    or existing["input_digest"] != digest
                    or existing["input_json"] != text
                ):
                    raise PilotError("conflict")
                return self._record(existing, replayed=True)
            task_id, now = uuid.uuid4().hex, time.time()
            conn.execute(
                "INSERT INTO pilot_requests(owner_id,request_id,operation,input_digest,input_json,task_id,thread_id,resource_id,state,phase,user_message_id,assistant_message_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,'ADMITTED','admitted',?,?,?,?)",
                (
                    principal.owner_id,
                    request_id,
                    operation,
                    digest,
                    text,
                    task_id,
                    validated.thread_id,
                    task_id,
                    uuid.uuid4().hex,
                    uuid.uuid4().hex,
                    now,
                    now,
                ),
            )
            row = self._request_row(conn, principal, request_id)
            self._fresh_turns.add(row["sequence"])
            return self._record(row)

        return await self._run(work)

    async def _claim(
        self, principal: OwnerPrincipal, request_id: str, task_id: str
    ) -> tuple[AdmissionRecord, int]:
        _principal(principal)
        _id(request_id)
        _id(task_id)

        def work(conn: sqlite3.Connection) -> tuple[AdmissionRecord, int]:
            row = self._request_row(conn, principal, request_id)
            if row["task_id"] != task_id or row["operation"] != "turn":
                raise PilotError("not_found")
            self._thread(conn, principal, row["thread_id"])
            if (
                row["sequence"] not in self._fresh_turns
                or row["state"] != "ADMITTED"
                or row["phase"] != "admitted"
                or row["cancel_requested"]
                or time.time() >= row["created_at"] + 120
            ):
                raise PilotError("conflict")
            # Irrevocable even if the separate quota transaction later fails.
            self._fresh_turns.discard(row["sequence"])
            conn.execute(
                "UPDATE pilot_requests SET phase='reserved',updated_at=? WHERE sequence=?",
                (time.time(), row["sequence"]),
            )
            quota = conn.execute(
                "SELECT quota_subject_id FROM pilot_subjects WHERE owner_id=?",
                (principal.owner_id,),
            ).fetchone()[0]
            return self._record(row), int(quota)

        return await self._run(work)

    async def get(self, principal: OwnerPrincipal, task_id: str) -> AdmissionRecord:
        _principal(principal)
        _id(task_id)

        def work(conn: sqlite3.Connection) -> AdmissionRecord:
            row = conn.execute(
                "SELECT * FROM pilot_requests WHERE owner_id=? AND task_id=? AND operation='turn'",
                (principal.owner_id, task_id),
            ).fetchone()
            if row is None:
                raise PilotError("not_found")
            self._thread(conn, principal, row["thread_id"])
            return self._record(row)

        return await self._run(work)

    async def get_request(self, principal: OwnerPrincipal, request_id: str) -> AdmissionRecord:
        _principal(principal)
        _id(request_id)
        return await self._run(
            lambda conn: self._record(self._request_row(conn, principal, request_id))
        )

    async def list_tasks(
        self, principal: OwnerPrincipal, *, limit: int = 50
    ) -> list[AdmissionRecord]:
        _principal(principal)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise PilotError("invalid_request")
        return await self._run(
            lambda conn: [
                self._record(row)
                for row in conn.execute(
                    "SELECT r.* FROM pilot_requests r JOIN threads t ON t.id=r.thread_id JOIN pilot_subjects s ON s.workspace_id=t.workspace_id WHERE r.owner_id=? AND t.owner=r.owner_id AND s.owner_id=r.owner_id AND r.operation='turn' ORDER BY r.sequence DESC LIMIT ?",
                    (principal.owner_id, limit),
                ).fetchall()
            ]
        )

    @staticmethod
    def _turn(conn: sqlite3.Connection, sequence: int) -> sqlite3.Row:
        if type(sequence) is not int or not 1 <= sequence <= 2**63 - 1:
            raise PilotError("invalid_request")
        row = conn.execute(
            "SELECT r.* FROM pilot_requests r JOIN threads t ON t.id=r.thread_id JOIN pilot_subjects s ON s.owner_id=r.owner_id AND s.workspace_id=t.workspace_id WHERE r.sequence=? AND r.operation='turn' AND t.owner=r.owner_id",
            (sequence,),
        ).fetchone()
        if row is None:
            raise PilotError("not_found")
        return cast(sqlite3.Row, row)

    @staticmethod
    def _live(row: sqlite3.Row) -> None:
        if row["state"] != "RUNNING" or row["cancel_requested"]:
            raise PilotError("conflict")
        if time.time() >= row["created_at"] + 120:
            raise PilotError("uncertainty", uncertain=True)

    @staticmethod
    def _event(conn: sqlite3.Connection, sequence: int, phase: str, code: str) -> None:
        conn.execute(
            "INSERT INTO pilot_events(sequence,phase,code,created_at) VALUES (?,?,?,?)",
            (sequence, phase, code, time.time()),
        )

    async def transition(
        self, sequence: int, *, expected: str, state: str, phase: str, error_code: str | None = None
    ) -> None:
        transitions = {
            "ADMITTED": {"RUNNING", "FAILED", "DENIED", "CANCELLED", "UNCERTAIN"},
            "RUNNING": {"FAILED", "UNCERTAIN"},
        }
        phases = {
            "admitted",
            "reserved",
            "running",
            "dispatch",
            "request",
            "result",
            "staged",
            "committed",
            "finished",
            "failed",
            "denied",
            "cancelled",
            "uncertain",
            "recovery",
        }
        codes = {
            "unauthorized",
            "forbidden",
            "not_found",
            "conflict",
            "too_large",
            "media",
            "invalid_request",
            "quota",
            "disabled",
            "audit",
            "store",
            "uncertainty",
        }
        if (
            type(expected) is not str
            or type(state) is not str
            or type(phase) is not str
            or state not in transitions.get(expected, set())
            or phase not in phases
        ):
            raise PilotError("conflict")
        if error_code is not None and (type(error_code) is not str or error_code not in codes):
            raise PilotError("invalid_request")

        def work(conn: sqlite3.Connection) -> None:
            row = self._turn(conn, sequence)
            if row["state"] != expected or (
                state == "RUNNING"
                and (row["cancel_requested"] or time.time() >= row["created_at"] + 120)
            ):
                raise PilotError("conflict")
            if state == "CANCELLED" and (row["model_attempts"] or row["tool_attempts"]):
                raise PilotError("conflict")
            conn.execute(
                "UPDATE pilot_requests SET state=?,phase=?,error_code=?,updated_at=? WHERE sequence=?",
                (state, phase, error_code, time.time(), sequence),
            )
            self._event(conn, sequence, phase, error_code or state.lower())
            if state != "RUNNING":
                self._blocked_sequences.add(sequence)
                self._fresh_turns.discard(sequence)

        await self._run(work)

    @staticmethod
    def _result(
        row: sqlite3.Row, text: str, assessment: ExecutionAssessment | None
    ) -> dict[str, Any]:
        if type(text) is not str or not text.strip():
            raise PilotError("invalid_request")
        try:
            if len(text.encode("utf-8")) > 65536:
                raise PilotError("too_large")
        except UnicodeError:
            raise PilotError("invalid_request") from None
        if assessment is not None:
            if type(assessment) is not ExecutionAssessment:
                raise PilotError("invalid_request")
            assessment = ExecutionAssessment.model_validate(assessment)
            if (
                assessment.task_id != row["task_id"]
                or assessment.output_sha256 != output_digest(text)
                or assessment.execution_status != "completed"
            ):
                raise PilotError("conflict")
        return {
            "task_id": row["task_id"],
            "response": text,
            "assessment": None if assessment is None else assessment.model_dump(mode="json"),
        }

    async def stage_result(self, sequence: int, result: dict[str, Any]) -> None:
        if type(result) is not dict or set(result) != {"task_id", "response", "assessment"}:
            raise PilotError("invalid_request")
        # Snapshot before the first await; caller mutation cannot rewrite a result.
        encoded, _ = _canonical(result, maximum=524288)
        snapshot = json.loads(encoded)

        def work(conn: sqlite3.Connection) -> None:
            row = self._turn(conn, sequence)
            self._live(row)
            try:
                assessment = (
                    None
                    if snapshot["assessment"] is None
                    else ExecutionAssessment.model_validate(snapshot["assessment"])
                )
                bound = self._result(row, snapshot["response"], assessment)
            except ValueError:
                raise PilotError("invalid_request") from None
            if snapshot != bound:
                raise PilotError("conflict")
            text, digest = _canonical(bound, maximum=524288)
            if row["result_json"] is not None and (
                row["result_json"] != text or row["result_digest"] != digest
            ):
                raise PilotError("conflict")
            conn.execute(
                "UPDATE pilot_requests SET result_json=?,result_digest=?,phase='staged',updated_at=? WHERE sequence=?",
                (text, digest, time.time(), sequence),
            )
            self._event(conn, sequence, "staged", "staged")

        await self._run(work)

    async def commit_turn(
        self, sequence: int, *, text: str, assessment: ExecutionAssessment | None
    ) -> None:
        def work(conn: sqlite3.Connection) -> None:
            row = self._turn(conn, sequence)
            self._live(row)
            result, digest = _canonical(self._result(row, text, assessment), maximum=524288)
            if row["result_json"] != result or row["result_digest"] != digest:
                raise PilotError("conflict")
            now = time.time()
            payload = json.loads(row["input_json"])
            if (
                hashlib.sha256(row["input_json"].encode("utf-8")).hexdigest() != row["input_digest"]
                or payload["thread_id"] != row["thread_id"]
            ):
                raise PilotError("store")
            for message_id, role, content in (
                (row["user_message_id"], "user", payload["message"]),
                (row["assistant_message_id"], "assistant", text),
            ):
                conn.execute(
                    "INSERT INTO thread_messages(id,thread_id,role,content,created_at) VALUES (?,?,?,?,?)",
                    (message_id, row["thread_id"], role, content, _iso(now)),
                )
            conn.execute(
                "UPDATE threads SET updated_at=? WHERE id=? AND owner=?",
                (_iso(now), row["thread_id"], row["owner_id"]),
            )
            conn.execute(
                "UPDATE pilot_requests SET state='COMPLETED',phase='committed',updated_at=? WHERE sequence=?",
                (now, sequence),
            )
            self._event(conn, sequence, "committed", "completed")
            self._blocked_sequences.add(sequence)
            self._fresh_turns.discard(sequence)

        await self._run(work)

    async def request_cancel(
        self, principal: OwnerPrincipal, task_id: str, request_id: str
    ) -> AdmissionRecord:
        _principal(principal)
        _id(task_id)
        _id(request_id)
        payload, digest = _canonical({"task_id": task_id})

        def work(conn: sqlite3.Connection) -> AdmissionRecord:
            target = conn.execute(
                "SELECT * FROM pilot_requests WHERE owner_id=? AND task_id=? AND operation='turn'",
                (principal.owner_id, task_id),
            ).fetchone()
            if target is None:
                raise PilotError("not_found")
            self._thread(conn, principal, target["thread_id"])
            existing = conn.execute(
                "SELECT * FROM pilot_requests WHERE owner_id=? AND request_id=?",
                (principal.owner_id, request_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["operation"] != "cancel"
                    or existing["input_digest"] != digest
                    or existing["input_json"] != payload
                ):
                    raise PilotError("conflict")
                return self._record(target, replayed=True)
            now = time.time()
            conn.execute(
                "INSERT INTO pilot_requests(owner_id,request_id,operation,input_digest,input_json,thread_id,resource_id,state,phase,created_at,updated_at) VALUES (?,?,?,?,?,?,?,'COMPLETED','cancelled',?,?)",
                (
                    principal.owner_id,
                    request_id,
                    "cancel",
                    digest,
                    payload,
                    target["thread_id"],
                    task_id,
                    now,
                    now,
                ),
            )
            if target["state"] in ("ADMITTED", "RUNNING"):
                state = (
                    "CANCELLED"
                    if target["state"] == "ADMITTED"
                    and not target["model_attempts"]
                    and not target["tool_attempts"]
                    else target["state"]
                )
                conn.execute(
                    "UPDATE pilot_requests SET cancel_requested=1,state=?,phase='cancelled',updated_at=? WHERE sequence=?",
                    (state, now, target["sequence"]),
                )
                self._fresh_turns.discard(target["sequence"])
                self._blocked_sequences.add(target["sequence"])
                self._event(conn, target["sequence"], "cancelled", "cancelled")
            return self._record(self._turn(conn, target["sequence"]))

        return await self._run(work)

    async def recover_interrupted(self) -> list[AdmissionRecord]:
        def work(conn: sqlite3.Connection) -> list[AdmissionRecord]:
            rows = conn.execute(
                "SELECT * FROM pilot_requests WHERE operation='turn' AND state IN ('ADMITTED','RUNNING') ORDER BY sequence"
            ).fetchall()
            recovered = []
            for row in rows:
                conn.execute(
                    "UPDATE pilot_requests SET state='UNCERTAIN',phase='recovery',error_code='uncertainty',updated_at=? WHERE sequence=?",
                    (time.time(), row["sequence"]),
                )
                self._fresh_turns.discard(row["sequence"])
                self._blocked_sequences.add(row["sequence"])
                self._event(conn, row["sequence"], "recovery", "uncertainty")
                recovered.append(self._record(self._turn(conn, row["sequence"])))
            return recovered

        return await self._run(work)

    async def reserve_attempt(
        self, sequence: int, *, kind: Literal["model", "tool"], limit: int
    ) -> bool:
        if (
            type(kind) is not str
            or kind not in ("model", "tool")
            or type(limit) is not int
            or not 1 <= limit <= (8 if kind == "model" else 4)
        ):
            raise PilotError("invalid_request")

        def work(conn: sqlite3.Connection) -> bool:
            row = self._turn(conn, sequence)
            field = "model_attempts" if kind == "model" else "tool_attempts"
            if (
                row["state"] != "RUNNING"
                or row["cancel_requested"]
                or row[field] >= limit
                or time.time() >= row["created_at"] + 120
            ):
                return False
            if kind == "model":
                conn.execute(
                    "UPDATE pilot_requests SET model_attempts=model_attempts+1,updated_at=? WHERE sequence=?",
                    (time.time(), sequence),
                )
            else:
                conn.execute(
                    "UPDATE pilot_requests SET tool_attempts=tool_attempts+1,updated_at=? WHERE sequence=?",
                    (time.time(), sequence),
                )
            self._event(conn, sequence, "dispatch", kind)
            return True

        return await self._run(work)

    async def list_threads(self, principal: OwnerPrincipal) -> list[ThreadView]:
        _principal(principal)
        return await self._run(
            lambda conn: [
                self._thread_view(row)
                for row in conn.execute(
                    "SELECT t.* FROM threads t JOIN pilot_subjects s ON s.workspace_id=t.workspace_id WHERE t.owner=? AND s.owner_id=? ORDER BY t.rowid",
                    (principal.owner_id, principal.owner_id),
                ).fetchall()
            ]
        )

    async def list_messages(self, principal: OwnerPrincipal, thread_id: str) -> list[MessageView]:
        _principal(principal)

        def work(conn: sqlite3.Connection) -> list[MessageView]:
            self._thread(conn, principal, thread_id)
            return [
                MessageView(**dict(row))
                for row in conn.execute(
                    "SELECT id,thread_id,role,content,created_at FROM thread_messages WHERE thread_id=? ORDER BY rowid",
                    (thread_id,),
                ).fetchall()
            ]

        return await self._run(work)
