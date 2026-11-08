"""Regression for SPEC-B1: SQLite TEXT affinity does not exclude BLOB storage."""

import json
import sqlite3

import pytest

from agency.admission.models import PilotError
from tests.test_calculator_verification_api import wait_done
from tests.test_owner_pilot_api import api, concrete  # noqa: F401 -- dependency fixture


async def test_blob_result_is_store_error_without_dispatch_or_writes(api):  # noqa: F811 -- pytest fixture injection
    client, c, _ = api
    c["replies"][1] = json.dumps({"final": "5"})
    made = await client.post(
        "/api/pilot/threads", json={"request_id": "thread", "title": "BLOB corruption regression"}
    )
    accepted = await client.post(
        "/api/pilot/calculator-tasks",
        json={"request_id": "calc", "thread_id": made.json()["thread"]["id"], "expression": "2+3"},
    )
    assert accepted.status_code == 202
    task_id = accepted.json()["task_id"]
    assert (await wait_done(client, task_id))["state"] == "COMPLETED"
    control = await client.get(f"/api/pilot/tasks/{task_id}/verification")
    assert control.status_code == 200 and control.json()["receipt"]["status"] == "passed"
    row = await c["store"].get(c["owner"], task_id)
    with sqlite3.connect(c["store"]._db_path) as writer:
        # Same previously valid JSON bytes and unchanged correct digest; only
        # SQLite's representation type differs from the normal text writer.
        writer.execute(
            "UPDATE pilot_requests SET result_json=? WHERE task_id=?",
            (sqlite3.Binary(row.result_json.encode("utf-8")), task_id),
        )
        assert (
            writer.execute(
                "SELECT typeof(result_json) FROM pilot_requests WHERE task_id=?", (task_id,)
            ).fetchone()[0]
            == "blob"
        )
    attempts_before = len(c["requests"])
    with sqlite3.connect(c["store"]._db_path) as observer:
        version_before = observer.execute("PRAGMA data_version").fetchone()[0]
        response = await client.get(f"/api/pilot/tasks/{task_id}/verification")
        assert response.status_code == 503, response.text
        assert response.json() == {
            "error": "Owner store unavailable.",
            "code": "store",
            "request_id": None,
            "task_id": None,
            "uncertain": False,
            "retryable": False,
        }
        with pytest.raises(PilotError) as denied:
            await c["service"].get_verification(c["owner"], task_id)
        assert denied.value.code == "store"
        assert observer.execute("PRAGMA data_version").fetchone()[0] == version_before
    assert len(c["requests"]) == attempts_before == 2
