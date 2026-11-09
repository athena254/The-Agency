"""Real TCP owner API + Remex journey; synthetic provider, real native Forge.

No real Telegram send, paid inference, live stores or automatic release.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import socket
import sqlite3
from pathlib import Path

import pytest
import uvicorn
from agency.prototype import pilot_launcher as launcher

from tests.test_factory_coding_composition import settings_for
from tests.test_factory_coding_service import coding_case as coding_case_fixture
from tests.test_factory_remex_bridge import bridge, update

coding_case = coding_case_fixture
pytestmark = [pytest.mark.asyncio, pytest.mark.skip(reason="pending coding pilot wiring; preserved from resi/coding-rollout")]


class Sink:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat, text, **kwargs):
        self.messages.append(text)


def bot(c, client, path):
    result = bridge.Bridge.__new__(bridge.Bridge)
    result.client = client
    result.adapter = Sink()
    result.db = sqlite3.connect(path)
    result.db.executescript("CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY,v TEXT NOT NULL);"
                          "CREATE TABLE IF NOT EXISTS turns(id INTEGER PRIMARY KEY,payload TEXT NOT NULL);")
    if result.get("thread") is None:
        result.put("thread", c.thread.id)
    return result


async def started(task, servers, index):
    async with asyncio.timeout(30):
        while len(servers) <= index or not servers[index].started:
            if task.done():
                task.result()
                raise AssertionError("launcher exited before starting its TCP listener")
            await asyncio.sleep(0.01)


async def shutdown(task, servers, index):
    if task is None:
        return
    if len(servers) > index:
        servers[index].should_exit = True
    else:
        task.cancel()
    await asyncio.wait_for(task, 30)


async def test_real_tcp_bot_native_job_reopens_and_replays_without_any_redispatch(
    coding_case, monkeypatch,
):
    c = coding_case
    settings = settings_for(c, monkeypatch)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    settings = dataclasses.replace(settings, port=port)
    base = f"http://127.0.0.1:{port}"
    servers = []
    real_server = uvicorn.Server

    class RecordingServer(real_server):
        def __init__(self, config):
            super().__init__(config)
            servers.append(self)

    monkeypatch.setattr(uvicorn, "Server", RecordingServer)
    headers = {"Authorization": "Bearer " + "t" * 64, "Origin": base}
    first = second = None
    first_bot = second_bot = None
    first_client = c.http_client(base_url=base, headers=headers, trust_env=False, timeout=20)
    second_client = None
    try:
        first = asyncio.create_task(launcher.serve(settings, coding=c.executor))
        await started(first, servers, 0)
        first_bot = bot(c, first_client, c.root / "synthetic-bridge.db")
        assert await first_bot.process_update(update()) is True
        task_id = first_bot.get("task:100")
        assert task_id
        saved_payload = json.loads(first_bot.db.execute(
            "SELECT payload FROM turns WHERE id=100").fetchone()[0])
        receipt = (await first_client.get(
            f"/api/pilot/tasks/{task_id}/coding-receipt")).json()
        before = (await first_client.get(f"/api/pilot/tasks/{task_id}")).json()
        assert before["state"] == "COMPLETED"
        assert before["usage"] == {"model_attempts": 1, "tool_attempts": 0}
        assert receipt["receipt"]["objective_status"] == "passed"
        assert receipt["receipt"]["tests_run"] is False
        assert receipt["receipt"]["release_authorized"] is False
        assert "Objective check: passed" in first_bot.adapter.messages[-1]
        assert len(c.calls) == 1 and "tools" not in c.calls[0]
        pilot = servers[0].config.app.state.pilot
        record = await pilot._store.get_coding_record(c.owner, task_id, c.workspace)
        artifact = Path(record.evidence.forge_report.worktree_path) / "m.py"
        assert artifact.read_bytes() == b"x = 2"
        assert (c.repo / "m.py").read_bytes() == b"x = 1\n"
        messages_before = (await first_client.get(
            f"/api/pilot/threads/{c.thread.id}/messages")).json()
        assert len(messages_before["messages"]) == 2
        assert not await pilot._audit.has_pending_calls()
        await shutdown(first, servers, 0)
        first = None
        assert pilot._closed and pilot._store._closed and c.executor._drained()
        first_bot.db.close()
        first_bot = None
        await first_client.aclose()

        import agency.admission.service as service_module
        import agency.agents.factory_coding as native

        def forbidden(*args, **kwargs):
            raise AssertionError("saved historical replay must not redispatch/provision/reverify")

        monkeypatch.setattr(native, "plan_edits", forbidden)
        monkeypatch.setattr(native.AgencyCodingAgent, "run_task", forbidden)
        monkeypatch.setattr(native, "verify_literal_artifact", forbidden)
        requests = []

        async def observe(request):
            requests.append((request.method, request.url.path))

        second_client = c.http_client(base_url=base, headers=headers, trust_env=False,
                                      timeout=20, event_hooks={"request": [observe]})
        second = asyncio.create_task(launcher.serve(settings))  # native coding now disabled
        await started(second, servers, 1)
        monkeypatch.setattr(service_module, "uuid4", forbidden)
        second_bot = bot(c, second_client, c.root / "synthetic-bridge.db")
        assert await second_bot.process_update(update()) is True
        assert requests == [("GET", f"/api/pilot/tasks/{task_id}"),
                            ("GET", f"/api/pilot/tasks/{task_id}/coding-receipt")]
        assert "Objective check: passed" in second_bot.adapter.messages[-1]
        assert second_bot.get("task:100") == task_id
        csrf = (await second_client.get("/api/session")).json()["csrf_token"]
        second_client.headers["X-Prototype-CSRF"] = csrf
        replay = await second_client.post("/api/pilot/coding-tasks", json=saved_payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True and replay.json()["task_id"] == task_id
        assert (await second_client.get(f"/api/pilot/tasks/{task_id}/coding-receipt")).json() == receipt
        after = (await second_client.get(f"/api/pilot/tasks/{task_id}")).json()
        assert after["usage"] == before["usage"]
        assert (await second_client.get(
            f"/api/pilot/threads/{c.thread.id}/messages")).json() == messages_before
        conflict = await second_client.post("/api/pilot/coding-tasks",
                                           json={**saved_payload, "task": "different request"})
        assert conflict.status_code == 409 and conflict.json()["code"] == "conflict"
        disabled = await second_client.post("/api/pilot/coding-tasks",
                                           json={**saved_payload, "request_id": "fresh-disabled"})
        assert disabled.status_code == 503 and disabled.json()["code"] == "disabled"
        assert len(c.calls) == 1 and artifact.read_bytes() == b"x = 2"
        assert (c.repo / "m.py").read_bytes() == b"x = 1\n"
        print("REAL_TCP_NATIVE_REPLAY " + json.dumps({
            "task_id": task_id, "usage": after["usage"], "receipt": receipt,
            "provider": "synthetic MockTransport", "telegram": "synthetic sink",
            "original_hex": (c.repo / "m.py").read_bytes().hex(),
            "artifact_hex": artifact.read_bytes().hex(), "restart_coding_enabled": False,
        }))
    finally:
        await shutdown(second, servers, 1)
        await shutdown(first, servers, 0)
        if second_client is not None:
            await second_client.aclose()
        await first_client.aclose()
        for instance in (first_bot, second_bot):
            if instance is not None:
                instance.db.close()
