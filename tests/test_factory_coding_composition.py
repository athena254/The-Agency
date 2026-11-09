"""Real launcher resources with controlled Uvicorn boundary; no socket listener."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
import uvicorn

from agency.prototype import pilot_launcher as launcher
from tests.test_factory_coding_api import client_for, completed
from tests.test_factory_coding_service import coding_case as coding_case_fixture
from tests.test_factory_coding_service import payload
from tests.test_pilot_launcher import config_file, protect, token_file

coding_case = coding_case_fixture
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skip(reason="pending coding pilot wiring; preserved from resi/coding-rollout"),
]


def settings_for(c, monkeypatch, **changes):
    data = c.root / "private-launcher"
    data.mkdir()
    protect(data)
    values = {
        "owner_id": c.owner.owner_id,
        "subject_id": c.owner.subject_id,
        "model": c.config.model,
        "base_url": c.config.base_url,
        "api_key": c.config.api_key.get_secret_value(),
        "max_tokens": c.config.max_tokens,
        "temperature": c.config.temperature,
        "timeout": c.config.timeout,
    }
    values.update(changes)
    config = config_file(data, **values)
    token_file(data)
    # Explicit synthetic operator provisioning of the ACTUAL launcher database.
    with (
        sqlite3.connect(c.root / "owner.sqlite") as source,
        sqlite3.connect(data / "pilot.db") as target,
    ):
        source.backup(target)
    work = c.root / "launcher-cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    return launcher.validate(config, data)


def assert_closed(pilot):
    assert pilot._store._closed and pilot._budget._closed
    assert pilot._audit._conn is None
    assert pilot._butler._threads._conn is None
    assert pilot._butler.orchestrator.skill_registry._conn is None


async def test_trusted_launcher_injection_reaches_real_asgi_native_and_closes(
    coding_case, monkeypatch
):
    c = coding_case
    settings = settings_for(c, monkeypatch)
    loop = asyncio.get_running_loop()
    seen = []

    class Server:
        def __init__(self, config):
            assert config.host == "127.0.0.1" and config.workers == 1
            assert config.reload is False and config.proxy_headers is False
            assert config.forwarded_allow_ips == ""
            self.app = config.app

        async def serve(self):
            app = self.app
            pilot = app.state.pilot
            seen.append(pilot)
            assert pilot._coding is c.executor
            assert pilot._workspace_id == c.workspace
            assert c.executor._loop is asyncio.get_running_loop() is loop
            assert pilot._config_id != c.executor.profile.provider_config_id
            assert pilot._butler.orchestrator._llm_adapter._provider == "echo"
            async with app.router.lifespan_context(app), client_for(c, app) as client:
                client.headers["Authorization"] = "Bearer " + "t" * 64
                response = await client.post(
                    "/api/pilot/coding-tasks", json=payload(c).model_dump()
                )
                assert response.status_code == 202, response.text
                view = await completed(client, response.json()["task_id"])
                assert view["state"] == "COMPLETED", view
                receipt = await client.get(f"/api/pilot/tasks/{view['task_id']}/coding-receipt")
                assert receipt.status_code == 200 and receipt.json()["reason"] == "available"
                assert view["usage"] == {"model_attempts": 1, "tool_attempts": 0}
                assert not pilot._butler._started and not pilot._butler.orchestrator._started

    monkeypatch.setattr(uvicorn, "Server", Server)
    await launcher.serve(settings, coding=c.executor)
    assert len(seen) == 1 and len(c.calls) == 1
    assert_closed(seen[0])
    assert c.executor._stopping
    assert c.factory.get("fixture-coder", "1.0.0") is not None
    assert c.runtimes.get_agent(c.agent.id).agent is c.agent


@pytest.mark.parametrize(
    "fault",
    [
        "type",
        "owner",
        "provider",
        "model",
        "base_url",
        "max_tokens",
        "temperature",
        "timeout",
        "profile",
        "loop",
    ],
)
async def test_invalid_injection_stays_caller_owned_before_resources(
    coding_case, monkeypatch, fault
):
    from agency.admission.store import OwnerPilotStore
    from agency.llm.config import ProviderKind

    c = coding_case
    settings = settings_for(c, monkeypatch)
    coding = c.executor
    if fault == "type":
        coding = object()
    elif fault == "owner":
        object.__setattr__(coding.profile, "owner_id", "foreign-owner")
    elif fault == "profile":
        object.__setattr__(coding.profile, "provider_config_id", "mutated-profile")
    elif fault == "loop":
        monkeypatch.setattr(coding, "_loop", object())
    else:
        values = {
            "provider": ProviderKind.ECHO,
            "model": "different",
            "base_url": "https://other.invalid/v1",
            "max_tokens": 123,
            "temperature": 0.125,
            "timeout": 7.0,
        }
        # Valid immutable settings are revalidated from the real private config file.
        if fault == "provider":
            object.__setattr__(coding.profile, "provider", "echo")
        else:
            raw = __import__("json").loads(settings.config_path.read_text())
            raw[fault] = values[fault]
            settings.config_path.write_text(__import__("json").dumps(raw))
            settings = launcher.validate(settings.config_path, settings.data_dir)
    created = []
    init = OwnerPilotStore.__init__

    def constructing(self, *a, **kw):
        created.append(self)
        init(self, *a, **kw)

    monkeypatch.setattr(OwnerPilotStore, "__init__", constructing)

    def no_listener(*a, **kw):
        raise RuntimeError("controlled server boundary; no listener")

    monkeypatch.setattr(uvicorn, "Server", no_listener)
    try:
        with pytest.raises(launcher.LauncherError):
            await launcher.serve(settings, coding=coding)  # type: ignore[arg-type]
    finally:
        if fault == "loop":
            monkeypatch.setattr(c.executor, "_loop", asyncio.get_running_loop())
    assert created == [], "invalid injection acquired stores before transfer validation"
    assert not c.executor._stopping
    assert c.factory.get("fixture-coder", "1.0.0") is not None


async def test_constructor_failure_drains_native_before_any_store_close(coding_case, monkeypatch):
    import agency.admission.service as service_module
    from agency.admission.store import OwnerPilotStore

    c = coding_case
    settings = settings_for(c, monkeypatch)
    release, constructed, stopping = asyncio.Event(), asyncio.Event(), asyncio.Event()
    operation = c.executor._remember(asyncio.create_task(release.wait()))
    stores, closed = [], []
    initialize, close, stop = OwnerPilotStore.initialize, OwnerPilotStore.close, c.executor.stop

    async def initialized(self):
        await initialize(self)
        stores.append(self)

    async def closing(self):
        closed.append(self)
        await close(self)

    async def draining(**kw):
        stopping.set()
        return await stop(**kw)

    def fail_service(self, **kw):
        constructed.set()
        raise RuntimeError("synthetic constructor failure")

    monkeypatch.setattr(OwnerPilotStore, "initialize", initialized)
    monkeypatch.setattr(OwnerPilotStore, "close", closing)
    monkeypatch.setattr(c.executor, "stop", draining)
    monkeypatch.setattr(service_module.OwnerPilotService, "__init__", fail_service)
    launch = asyncio.create_task(launcher.serve(settings, coding=c.executor))
    try:
        async with asyncio.timeout(15):
            await constructed.wait()
        # Wait for either actual drain entry or an unsafe completed launcher.
        for _ in range(100):
            if stopping.is_set() or launch.done():
                break
            await asyncio.sleep(0.01)
        assert stopping.is_set(), (
            "accepted native ownership was abandoned after constructor failure"
        )
        assert not launch.done() and not operation.done()
        assert closed == [] and stores and not stores[0]._closed
        launch.cancel()
        launch.cancel()
        await asyncio.sleep(0.02)
        assert not launch.done() and closed == []
    finally:
        release.set()
        await operation
        try:
            await launch
        except (launcher.LauncherError, asyncio.CancelledError):
            pass
    assert c.executor._stopping and stores[0]._closed
    assert c.factory.get("fixture-coder", "1.0.0") is not None


@pytest.mark.parametrize("phase", ["store", "orchestrator", "startup", "server"])
async def test_posttransfer_failures_drain_before_resource_callbacks(
    coding_case, monkeypatch, phase
):
    from agency.admission.service import OwnerPilotService
    from agency.admission.store import OwnerPilotStore
    from agency.orchestrator import AgencyOrchestrator

    c = coding_case
    settings = settings_for(c, monkeypatch)
    release, reached, stopping = asyncio.Event(), asyncio.Event(), asyncio.Event()
    operation = c.executor._remember(asyncio.create_task(release.wait()))
    closed, pilots = [], []
    close, stop = OwnerPilotStore.close, c.executor.stop

    async def closing(self):
        closed.append(self)
        assert c.executor._drained(), "owned store closed before native settlement"
        await close(self)

    async def draining(**kw):
        stopping.set()
        return await stop(**kw)

    monkeypatch.setattr(OwnerPilotStore, "close", closing)
    monkeypatch.setattr(c.executor, "stop", draining)
    if phase == "store":

        async def fail_store(self):
            reached.set()
            raise RuntimeError("synthetic initialize failure")

        monkeypatch.setattr(OwnerPilotStore, "initialize", fail_store)
    elif phase == "orchestrator":

        def fail_orchestrator(self, **kw):
            reached.set()
            raise RuntimeError("synthetic orchestrator failure")

        monkeypatch.setattr(AgencyOrchestrator, "__init__", fail_orchestrator)
    elif phase == "startup":

        async def fail_start(self):
            pilots.append(self)
            reached.set()
            raise RuntimeError("synthetic service startup failure")

        monkeypatch.setattr(OwnerPilotService, "start", fail_start)

    class Server:
        def __init__(self, config):
            self.app = config.app

        async def serve(self):
            if phase == "startup":
                async with self.app.router.lifespan_context(self.app):
                    pytest.fail("startup fault not reached")
            else:
                pilots.append(self.app.state.pilot)
                reached.set()
                raise RuntimeError("synthetic controlled server error")

    monkeypatch.setattr(uvicorn, "Server", Server)
    launch = asyncio.create_task(launcher.serve(settings, coding=c.executor))
    try:
        await asyncio.wait_for(reached.wait(), 15)
        await asyncio.wait_for(stopping.wait(), 5)
        await asyncio.sleep(0.08)
        assert not launch.done() and closed == []
        assert not operation.done()
    finally:
        release.set()
        await operation
        result = await asyncio.gather(launch, return_exceptions=True)
    assert isinstance(result[0], launcher.LauncherError)
    assert str(result[0]) == "startup_or_shutdown_failed"
    assert closed and c.executor._stopping
    for pilot in pilots:
        assert_closed(pilot)
    assert c.factory.get("fixture-coder", "1.0.0") is not None


@pytest.mark.parametrize("boundary", ["model", "host", "publish"])
async def test_launcher_cancellation_retains_real_model_host_hooks_and_loop(
    coding_case, monkeypatch, boundary
):
    import threading

    import httpx

    import agency.agents.factory_coding as native

    c = coding_case
    settings = settings_for(c, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    host_entered, host_release = threading.Event(), threading.Event()
    observed, drain_results, closed = [], [], []
    stop, run_host = c.executor.stop, native.AgencyCodingAgent.run_task

    async def draining(**kw):
        result = await stop(**kw)
        drain_results.append(result)
        return result

    monkeypatch.setattr(c.executor, "stop", draining)

    async def held_http(request):
        c.calls.append("held-model")
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"choices": [{"message": {"content": c.response}}]})

    def held_host(*a, **kw):
        host_entered.set()
        assert host_release.wait(15), "fixture hold must always release"
        return run_host(*a, **kw)

    if boundary == "model":
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kw: c.http_client(transport=httpx.MockTransport(held_http), **kw),
        )
    elif boundary == "host":
        monkeypatch.setattr(native.AgencyCodingAgent, "run_task", held_host)

    class Server:
        def __init__(self, config):
            self.app = config.app

        async def serve(self):
            app = self.app
            pilot = app.state.pilot
            observed.append(pilot)
            for obj in (pilot._store, pilot._budget, pilot._audit):
                close = obj.close

                async def closing(close=close):
                    assert c.executor._drained()
                    closed.append("close")
                    await close()

                monkeypatch.setattr(obj, "close", closing)
            if boundary == "publish":
                stage = pilot._store.stage_coding_result

                async def held_stage(*a, **kw):
                    entered.set()
                    await release.wait()
                    return await stage(*a, **kw)

                monkeypatch.setattr(pilot._store, "stage_coding_result", held_stage)
            async with app.router.lifespan_context(app), client_for(c, app) as client:
                client.headers["Authorization"] = "Bearer " + "t" * 64
                response = await client.post(
                    "/api/pilot/coding-tasks", json=payload(c).model_dump()
                )
                assert response.status_code == 202, response.text
                await asyncio.Event().wait()

    monkeypatch.setattr(uvicorn, "Server", Server)
    launch = asyncio.create_task(launcher.serve(settings, coding=c.executor))
    try:
        async with asyncio.timeout(20):
            if boundary == "host":
                while not host_entered.is_set():
                    await asyncio.sleep(0.005)
            else:
                await entered.wait()
        launch.cancel()
        await asyncio.sleep(0.12)
        assert False in drain_results
        assert not launch.done() and closed == []
        pilot = observed[0]
        assert not pilot._store._closed and pilot._butler._threads._conn is not None
        launch.cancel()
        await asyncio.sleep(0.02)
        assert not launch.done() and closed == []
    finally:
        release.set()
        host_release.set()
        result = await asyncio.gather(launch, return_exceptions=True)
    assert isinstance(result[0], asyncio.CancelledError)
    assert drain_results[-1] is True and c.executor._drained()
    assert_closed(observed[0])
    assert c.factory.get("fixture-coder", "1.0.0") is not None


async def test_partial_thread_constructor_retains_connection_until_native_settles(
    coding_case, monkeypatch
):
    from agency.butler.threads import ThreadStore

    c = coding_case
    settings = settings_for(c, monkeypatch)
    release, reached = asyncio.Event(), asyncio.Event()
    operation = c.executor._remember(asyncio.create_task(release.wait()))
    initialize, close = ThreadStore.__init__, ThreadStore.close
    threads, closed = [], []

    def failing(self, *a, **kw):
        initialize(self, *a, **kw)
        # OwnerPilotStore's worker also uses short-lived schema ThreadStores;
        # inject only at the launcher's creating-thread resource constructor.
        if __import__("threading").get_ident() == c.executor._thread:
            threads.append(self)
            reached.set()
            raise RuntimeError("synthetic failure after real thread connection creation")

    def closing(self):
        if self in threads:
            closed.append(self)
        close(self)

    monkeypatch.setattr(ThreadStore, "__init__", failing)
    monkeypatch.setattr(ThreadStore, "close", closing)
    launch = asyncio.create_task(launcher.serve(settings, coding=c.executor))
    try:
        await asyncio.wait_for(reached.wait(), 15)
        await asyncio.sleep(0.12)
        assert not launch.done() and not operation.done()
        assert closed == [], "partial ThreadStore closed before transferred native drain"
        assert threads[0]._conn is not None
    finally:
        release.set()
        await operation
        result = await asyncio.gather(launch, return_exceptions=True)
    assert isinstance(result[0], launcher.LauncherError)
    assert str(result[0]) == "startup_or_shutdown_failed"
    assert closed == threads and threads[0]._conn is None


async def test_launcher_default_has_no_coding_and_does_not_provision_scope(
    coding_case, monkeypatch
):
    c = coding_case
    settings = settings_for(c, monkeypatch)
    seen = []

    class Server:
        def __init__(self, config):
            self.app = config.app

        async def serve(self):
            app = self.app
            pilot = app.state.pilot
            seen.append(pilot)
            assert pilot._coding is None and pilot._workspace_id == ""
            async with app.router.lifespan_context(app), client_for(c, app) as client:
                client.headers["Authorization"] = "Bearer " + "t" * 64

                def forbidden(*a, **kw):
                    raise AssertionError("coding must not provision")

                monkeypatch.setattr(pilot._store, "ensure_workspace", forbidden)
                response = await client.post(
                    "/api/pilot/coding-tasks", json=payload(c).model_dump()
                )
                assert response.status_code == 503 and response.json()["code"] == "disabled"
                assert await pilot._store.list_tasks(c.owner) == []

    monkeypatch.setattr(uvicorn, "Server", Server)
    await launcher.serve(settings)
    assert_closed(seen[0])
    assert not c.executor._stopping and c.calls == []
