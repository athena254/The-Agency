"""Durable uncertainty readiness checks against real isolated SQLite."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from contextlib import closing
from pathlib import Path
from typing import cast

import pytest

from agency.admission.models import AdmissionRecord, OwnerPrincipal, PilotError
from agency.admission.store import OwnerPilotStore

OWNER = OwnerPrincipal("owner", "local", "local")
FOREIGN = OwnerPrincipal("foreign", "local", "foreign")


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[OwnerPilotStore]:
    instance = OwnerPilotStore(str(tmp_path / "pilot.db"))
    try:
        await instance.initialize()
        yield instance
    finally:
        await instance.close()


async def test_old_uncertainty_survives_newer_history_and_restart(
    store: OwnerPilotStore,
) -> None:
    _, thread = await store.create_thread(OWNER, "thread", "Synthetic title")
    old = await store.admit(
        OWNER, "old", operation="turn", payload={"thread_id": thread.id, "message": "synthetic"}
    )
    await store.transition(old.sequence, expected="ADMITTED", state="UNCERTAIN", phase="uncertain")
    for index in range(101):
        row = await store.admit(
            OWNER,
            f"new-{index}",
            operation="turn",
            payload={"thread_id": thread.id, "message": "synthetic"},
        )
        await store.transition(row.sequence, expected="ADMITTED", state="FAILED", phase="failed")
    visible = await store.list_tasks(OWNER, limit=100)
    assert len(visible) == 100
    assert all(row.state == "FAILED" and row.sequence > old.sequence for row in visible)
    assert await store.has_uncertainty(OWNER) is True
    await store.close()
    reopened = OwnerPilotStore(store._db_path)
    try:
        await reopened.initialize()
        assert await reopened.has_uncertainty(OWNER) is True
    finally:
        await reopened.close()


def snapshot(path: str) -> tuple[str, ...]:
    with closing(sqlite3.connect(path)) as db:
        return tuple(db.iterdump())


async def uncertain_turn(store: OwnerPilotStore) -> AdmissionRecord:
    _, thread = await store.create_thread(OWNER, "thread", "Synthetic title")
    row = await store.admit(
        OWNER, "turn", operation="turn", payload={"thread_id": thread.id, "message": "synthetic"}
    )
    await store.transition(row.sequence, expected="ADMITTED", state="UNCERTAIN", phase="uncertain")
    return row


async def test_empty_and_foreign_owner_queries_do_not_create_or_change_rows(
    store: OwnerPilotStore,
) -> None:
    before = snapshot(store._db_path)
    assert await store.has_uncertainty(OWNER) is False
    assert await store.has_uncertainty(FOREIGN) is False
    assert snapshot(store._db_path) == before
    await uncertain_turn(store)
    before = snapshot(store._db_path)
    assert await store.has_uncertainty(OWNER) is True
    assert await store.has_uncertainty(FOREIGN) is False
    assert snapshot(store._db_path) == before
    await store.ensure_workspace(FOREIGN)
    assert await store.has_uncertainty(FOREIGN) is False


@pytest.mark.parametrize(
    "state", ["ADMITTED", "RUNNING", "COMPLETED", "FAILED", "DENIED", "CANCELLED"]
)
async def test_only_uncertain_turns_block(store: OwnerPilotStore, state: str) -> None:
    row = await uncertain_turn(store)
    # Synthetic durable state fixture; this is not a production clearance API.
    with closing(sqlite3.connect(store._db_path)) as db, db:
        db.execute("UPDATE pilot_requests SET state=? WHERE sequence=?", (state, row.sequence))
    before = snapshot(store._db_path)
    assert await store.has_uncertainty(OWNER) is False
    assert snapshot(store._db_path) == before


@pytest.mark.parametrize("binding", ["request_owner", "thread_owner", "workspace", "operation"])
async def test_owner_thread_workspace_and_turn_scope(store: OwnerPilotStore, binding: str) -> None:
    row = await uncertain_turn(store)
    foreign_workspace = await store.ensure_workspace(FOREIGN)
    with closing(sqlite3.connect(store._db_path)) as db, db:
        if binding == "request_owner":
            db.execute(
                "UPDATE pilot_requests SET owner_id=? WHERE sequence=?",
                (FOREIGN.owner_id, row.sequence),
            )
        elif binding == "thread_owner":
            db.execute("UPDATE threads SET owner=? WHERE id=?", (FOREIGN.owner_id, row.thread_id))
        elif binding == "workspace":
            db.execute(
                "UPDATE threads SET workspace_id=? WHERE id=?", (foreign_workspace, row.thread_id)
            )
        else:
            db.execute(
                "UPDATE pilot_requests SET operation='cancel' WHERE sequence=?", (row.sequence,)
            )
    before = snapshot(store._db_path)
    assert await store.has_uncertainty(OWNER) is False
    assert await store.has_uncertainty(FOREIGN) is False
    assert snapshot(store._db_path) == before


@pytest.mark.parametrize("closed", [False, True], ids=["uninitialized", "closed"])
async def test_unavailable_lifecycle_is_not_false_ready(tmp_path: Path, closed: bool) -> None:
    instance = OwnerPilotStore(str(tmp_path / "unavailable.db"))
    try:
        if closed:
            await instance.initialize()
            await instance.close()
        with pytest.raises(PilotError) as exc:
            await instance.has_uncertainty(OWNER)
        assert exc.value.code == "store"
        assert str(exc.value) == "Owner store unavailable."
        assert exc.value.status_code == 503
    finally:
        await instance.close()


@pytest.mark.parametrize("invalid", ["wrong_type", "subclass", "tampered"])
async def test_principal_is_concretely_revalidated(store: OwnerPilotStore, invalid: str) -> None:
    class DerivedPrincipal(OwnerPrincipal):
        pass

    principal: object = object()
    expected = "unauthorized"
    if invalid == "subclass":
        principal = DerivedPrincipal("owner", "local", "local")
    elif invalid == "tampered":
        principal = OwnerPrincipal("owner", "local", "local")
        object.__setattr__(principal, "owner_id", "")
        expected = "invalid_request"
    before = snapshot(store._db_path)
    with pytest.raises(PilotError) as exc:
        await store.has_uncertainty(cast(OwnerPrincipal, principal))
    assert exc.value.code == expected
    assert snapshot(store._db_path) == before


@pytest.mark.parametrize("fault", [False, True], ids=["success", "sqlite-fault"])
async def test_sqlite_connections_close_and_faults_are_sanitized(
    store: OwnerPilotStore, monkeypatch: pytest.MonkeyPatch, fault: bool
) -> None:
    await uncertain_turn(store)
    if fault:
        with closing(sqlite3.connect(store._db_path)) as db, db:
            db.execute("DROP TABLE pilot_requests")
    connections: list[sqlite3.Connection] = []
    original = store._connect

    def connect() -> sqlite3.Connection:
        conn = original()
        connections.append(conn)
        return conn

    monkeypatch.setattr(store, "_connect", connect)
    if fault:
        with pytest.raises(PilotError) as exc:
            await store.has_uncertainty(OWNER)
        assert exc.value.code == "store"
        assert str(exc.value) == "Owner store unavailable."
        assert exc.value.__suppress_context__ is True
    else:
        assert await store.has_uncertainty(OWNER) is True
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        connections[0].execute("SELECT 1")


async def test_connection_oserror_is_sanitized(
    store: OwnerPilotStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail() -> sqlite3.Connection:
        raise OSError("synthetic private path and payload")

    monkeypatch.setattr(store, "_connect", fail)
    with pytest.raises(PilotError) as exc:
        await store.has_uncertainty(OWNER)
    assert exc.value.code == "store"
    assert str(exc.value) == "Owner store unavailable."
    assert exc.value.__suppress_context__ is True
