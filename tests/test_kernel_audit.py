"""Tests for the audit log: append-only writes, filters, pagination."""

import pytest
from pydantic import ValidationError

from agency.kernel.audit import AuditEntry, AuditFilter, AuditLog
from agency.kernel.policies import ActionClass


async def test_append_and_query_roundtrip(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    await log.initialize()
    try:
        eid = await log.append(AuditEntry(
            agent="red-1", task="t-1", target="staging/api",
            authorization="granted", capability="simulate",
            action=ActionClass.L2_CONTROLLED_TESTING,
            result="allowed", evidence={"finding": "x"}, model="hermes"))
        assert isinstance(eid, str)
        rows = await log.query(AuditFilter(agent="red-1"))
        assert len(rows) == 1
        assert rows[0].entry_id == eid
        assert rows[0].action is ActionClass.L2_CONTROLLED_TESTING
        assert rows[0].evidence == {"finding": "x"}
        assert rows[0].model == "hermes"
    finally:
        await log.close()


async def test_query_filters_and_pagination(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    await log.initialize()
    try:
        for idx in range(5):
            await log.append(AuditEntry(
                agent="red-1" if idx % 2 == 0 else "blue-1",
                action="L1_SAFE_ANALYSIS",
                result="allowed" if idx % 2 == 0 else "denied"))
        assert len(await log.query(AuditFilter(agent="red-1"))) == 3
        assert len(await log.query(AuditFilter(result="denied"))) == 2
        assert len(await log.query(AuditFilter(agent="red-1", limit=1))) == 1
        assert len(await log.query(AuditFilter(agent="red-1", limit=1, offset=1))) == 1
        assert await log.count(AuditFilter(agent="blue-1")) == 2
        assert await log.count() == 5
    finally:
        await log.close()


async def test_filter_by_task_target_action(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    await log.initialize()
    try:
        await log.append(AuditEntry(agent="a", task="t1", target="prod/api",
                                    action="L2_CONTROLLED_TESTING", result="allowed"))
        await log.append(AuditEntry(agent="a", task="t2", target="staging/api",
                                    action="L2_CONTROLLED_TESTING", result="denied"))
        assert len(await log.query(AuditFilter(task="t1"))) == 1
        assert len(await log.query(AuditFilter(target="staging/api"))) == 1
        assert len(await log.query(AuditFilter(action="L2_CONTROLLED_TESTING"))) == 2
    finally:
        await log.close()


async def test_entries_are_immutable(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    entry = AuditEntry(agent="red-1", action="L1_SAFE_ANALYSIS", result="allowed")
    with pytest.raises(ValidationError):
        entry.agent = "blue-1"
    with pytest.raises(ValidationError):
        entry.evidence = {}
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


async def test_missing_and_plain_string_action_roundtrip(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    await log.initialize()
    try:
        await log.append(AuditEntry(agent="red-1", action=None, result="pending"))
        await log.append(AuditEntry(agent="red-1", action="custom_command", result="denied"))
        rows = await log.query(AuditFilter(agent="red-1"))
        by_result = {r.result: r for r in rows}
        assert by_result["pending"].action is None
        assert by_result["denied"].action == "custom_command"
    finally:
        await log.close()


async def test_context_manager_and_as_ts(tmp_path):
    async with AuditLog(tmp_path / "audit.db") as log:
        entry = AuditEntry(agent="x", result="allowed")
        assert entry.as_ts()
        eid = await log.append(entry)
        assert await log.count() == 1
        assert (await log.query())[0].entry_id == eid
