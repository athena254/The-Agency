"""Tests for agency.kernel.audit."""

import pytest
from pydantic import ValidationError

from agency.kernel.audit import AuditEntry, AuditFilter, AuditLog
from agency.kernel.policies import ActionClass


async def test_append_and_query_roundtrip(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    await log.initialize()
    try:
        eid = await log.append(
            AuditEntry(
                agent="red-1",
                task="t-1",
                target="staging/api",
                authorization="granted",
                capability="simulate",
                action=ActionClass.L2_CONTROLLED_TESTING,
                result="allowed",
                evidence={"finding": "x"},
                model="hermes",
            )
        )
        assert isinstance(eid, str)

        rows = await log.query(AuditFilter(agent="red-1"))
        assert len(rows) == 1
        entry = rows[0]
        assert entry.entry_id == eid
        assert entry.action is ActionClass.L2_CONTROLLED_TESTING
        assert entry.evidence == {"finding": "x"}
        assert entry.model == "hermes"
    finally:
        await log.close()


async def test_query_filters_and_pagination(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    await log.initialize()
    try:
        for idx in range(5):
            await log.append(
                AuditEntry(
                    agent="red-1" if idx % 2 == 0 else "blue-1",
                    action="L1_SAFE_ANALYSIS",
                    result="allowed" if idx % 2 == 0 else "denied",
                )
            )
        assert len(await log.query(AuditFilter(agent="red-1"))) == 3
        assert len(await log.query(AuditFilter(result="denied"))) == 2
        assert len(await log.query(AuditFilter(agent="red-1", limit=1))) == 1
        assert len(await log.query(AuditFilter(agent="red-1", limit=1, offset=1))) == 1
        assert await log.count(AuditFilter(agent="blue-1")) == 2
    finally:
        await log.close()


async def test_entries_are_immutable(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    entry = AuditEntry(agent="red-1", action="L1_SAFE_ANALYSIS", result="allowed")
    with pytest.raises(ValidationError):
        entry.agent = "blue-1"  # frozen model -> assignment raises ValidationError
    with pytest.raises(ValidationError):
        entry.evidence = {}  # type: ignore[misc]
    await log.close()


async def test_duplicate_entry_id_rejected(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    await log.initialize()
    try:
        entry = AuditEntry(agent="red-1")
        await log.append(entry)
        with pytest.raises(ValueError):
            await log.append(entry.model_copy())
    finally:
        await log.close()


async def test_survives_reopen(tmp_path):
    path = tmp_path / "audit.db"
    log = AuditLog(path)
    await log.initialize()
    await log.append(AuditEntry(agent="red-1", result="allowed"))
    await log.close()

    reopened = AuditLog(path)
    await reopened.initialize()
    try:
        rows = await reopened.query(AuditFilter(agent="red-1"))
        assert len(rows) == 1
        assert rows[0].result == "allowed"
    finally:
        await reopened.close()


async def test_not_initialized_raises(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    with pytest.raises(RuntimeError):
        await log.append(AuditEntry(agent="red-1"))


async def test_missing_action_roundtrip(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    await log.initialize()
    try:
        await log.append(AuditEntry(agent="red-1", action=None, result="pending"))
        rows = await log.query(AuditFilter(agent="red-1"))
        assert rows[0].action is None
    finally:
        await log.close()


async def test_plain_string_action_roundtrip(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    await log.initialize()
    try:
        await log.append(AuditEntry(agent="red-1", action="custom_command", result="denied"))
        rows = await log.query(AuditFilter(agent="red-1"))
        assert rows[0].action == "custom_command"
    finally:
        await log.close()