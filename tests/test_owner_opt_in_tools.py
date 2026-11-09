"""Owner-pilot opt-in memory/web_fetch capability tests (hermetic)."""

from __future__ import annotations

import asyncio
import importlib

import httpx
from pydantic import SecretStr

from agency.admission.auth import OwnerAuthenticator
from agency.admission.models import OwnerExecutionGrant, OwnerTaskRequest
from agency.admission.policy import OwnerToolPolicy
from agency.memory.sms.scope import PrincipalMemoryScope, PrincipalScope
from agency.prototype.app import create_app
from agency.tools.base import ToolContext, ToolRisk, ToolSpec
from tests.test_owner_service import concrete as concrete_fixture

concrete = concrete_fixture

_ARTICLE_HTML = "<html><body><h1>Hello</h1><p>Public content here.</p></body></html>"


def _make_service(c, **flags):
    module = importlib.import_module("agency.admission.service")
    service = module.OwnerPilotService(
        butler=c["butler"],
        store=c["store"],
        budget=c["budget"],
        audit=c["audit"],
        provider_config=c["config"],
        owner_id="owner",
        **flags,
    )
    c["service"] = service
    return service


async def _terminal(service, owner, task_id):
    async with asyncio.timeout(10):
        while True:
            view = await service.get_task(owner, task_id)
            if view.state not in {"ADMITTED", "RUNNING"}:
                return view
            await asyncio.sleep(0.005)


async def _grant(c, service, **kwargs) -> OwnerExecutionGrant:
    _, thread = await service.create_thread(c["owner"], "grant-thread", "Synthetic")
    admitted = await c["store"].admit(
        c["owner"],
        "grant-request",
        operation="turn",
        payload={"thread_id": thread.id, "message": "prompt"},
    )
    lease = await c["budget"].reserve_request(c["owner"], admitted.request_id, admitted.task_id)
    return OwnerExecutionGrant(lease, c["budget"], **kwargs)


async def test_default_flags_are_off_and_tools_disabled(concrete):
    c = concrete
    service = _make_service(c)
    try:
        await service.start()
        caps = await service.capabilities(c["owner"])
        assert caps.tools == ["calculator", "utc_time", "web_search"]
        assert {"web_fetch", "memory"} <= set(caps.disabled)
        status = await service.tool_status(c["owner"])
        assert status["memory_tools"] == {
            "enabled": False,
            "guard": "R-04",
            "reason": "opt_in_disabled",
        }
        assert status["web_fetch"] == {
            "enabled": False,
            "guard": "B-05",
            "reason": "opt_in_disabled",
        }
    finally:
        await service.stop()


async def test_opt_in_flags_surface_enabled_tools(concrete):
    c = concrete
    service = _make_service(
        c,
        memory_db_path=str(c["store"]._db_path + "-memory.db"),
        enable_memory_tools=True,
        enable_web_fetch=True,
    )
    try:
        await service.start()
        caps = await service.capabilities(c["owner"])
        assert caps.tools == [
            "calculator",
            "utc_time",
            "web_search",
            "web_fetch",
            "memory_query",
            "memory_write",
        ]
        assert "web_fetch" not in caps.disabled and "memory" not in caps.disabled
        status = await service.tool_status(c["owner"])
        assert status["memory_tools"]["enabled"] is True
        assert status["memory_tools"]["reason"] == "configured"
        assert status["web_fetch"]["enabled"] is True
        assert status["web_fetch"]["reason"] == "configured"
    finally:
        await service.stop()


async def test_memory_tools_turn_is_scoped_to_principal(concrete):
    c = concrete
    service = _make_service(
        c,
        memory_db_path=str(c["store"]._db_path + "-memory.db"),
        enable_memory_tools=True,
    )
    c["replies"][:] = [
        '{"action":{"name":"memory_write","args":{"content":"owner secret alpha"}}}',
        '{"action":{"name":"memory_query","args":{"query":"alpha"}}}',
        '{"final":"stored and recalled"}',
    ]
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
        admitted = await service.submit(
            c["owner"],
            OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="remember alpha"),
        )
        result = await _terminal(service, c["owner"], admitted.task_id)
        assert result.state == "COMPLETED", (result.state, result.error_code, result.response)
        assert result.response == "stored and recalled"
        assert result.usage.tool_attempts == 2
        expected_agent = PrincipalScope(
            owner_id="owner", transport="local", subject_id="subject"
        ).agent_id
        items = await service._memory_store.list_by_agent(expected_agent)
        assert [item.content for item in items] == ["owner secret alpha"]
    finally:
        await service.stop()


async def test_memory_tools_are_not_registered_by_default(concrete):
    c = concrete
    service = _make_service(c)
    c["replies"][:] = [
        '{"action":{"name":"memory_write","args":{"content":"should not run"}}}',
        '{"final":"done"}',
    ]
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
        admitted = await service.submit(
            c["owner"],
            OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="remember"),
        )
        result = await _terminal(service, c["owner"], admitted.task_id)
        assert result.state == "FAILED", (result.state, result.error_code, result.response)
    finally:
        await service.stop()


async def test_web_fetch_turn_uses_pinned_fetch(concrete, monkeypatch):
    from agency.tools.builtin import web

    c = concrete
    monkeypatch.setattr(web, "_default_resolve", lambda _host: ["93.184.216.34"])

    fetched: list[str] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "provider.invalid":
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": c["replies"].pop(0)}}]},
            )
        fetched.append(request.url.host or "")
        return httpx.Response(200, text=_ARTICLE_HTML)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: c["client"](
            transport=httpx.MockTransport(handle),
            **{k: v for k, v in kw.items() if k != "transport"},
        ),
    )
    service = _make_service(c, enable_web_fetch=True)
    c["replies"][:] = [
        '{"action":{"name":"web_fetch","args":{"url":"https://example.com/article"}}}',
        '{"final":"fetched"}',
    ]
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
        admitted = await service.submit(
            c["owner"],
            OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="fetch article"),
        )
        result = await _terminal(service, c["owner"], admitted.task_id)
        assert result.state == "COMPLETED", result
        assert result.response == "fetched"
        assert result.usage.tool_attempts == 1
        assert "example.com" in fetched
    finally:
        await service.stop()


async def test_web_fetch_is_not_registered_by_default(concrete):
    c = concrete
    service = _make_service(c)
    c["replies"][:] = [
        '{"action":{"name":"web_fetch","args":{"url":"https://example.com/article"}}}',
        '{"final":"done"}',
    ]
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
        admitted = await service.submit(
            c["owner"],
            OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="fetch"),
        )
        result = await _terminal(service, c["owner"], admitted.task_id)
        assert result.state == "FAILED", (result.state, result.error_code, result.response)
        assert {r.url.host for r in c["requests"]} == {"provider.invalid"}
    finally:
        await service.stop()


async def test_grant_accepts_opt_in_tool_names(concrete):
    c = concrete
    service = _make_service(c)
    await service.start()
    try:
        grant = await _grant(c, service, allowed_tools=frozenset({"memory_query", "web_fetch"}))
        assert grant.allowed_tools == frozenset({"memory_query", "web_fetch"})
    finally:
        await service.stop()


async def test_policy_denies_memory_tools_without_scope(concrete):
    c = concrete
    service = _make_service(c)
    await service.start()
    try:
        grant = await _grant(c, service, allowed_tools=frozenset({"memory_query", "memory_write"}))
        ctx = ToolContext("owner-pilot", grant.lease.task_id, audit=c["audit"], owner_grant=grant)
        policy = OwnerToolPolicy()
        assert (
            policy(ToolSpec(name="memory_query", description="x"), {"query": "x"}, ctx).allowed
            is False
        )
        assert (
            policy(
                ToolSpec(name="memory_write", description="x", risk=ToolRisk.MUTATES_STATE),
                {"content": "x"},
                ctx,
            ).allowed
            is False
        )
    finally:
        await service.stop()


async def test_policy_allows_scoped_memory_tools(concrete):
    c = concrete
    service = _make_service(
        c,
        memory_db_path=str(c["store"]._db_path) + "-memory.db",
        enable_memory_tools=True,
        enable_web_fetch=True,
    )
    await service.start()
    try:
        grant = await _grant(
            c,
            service,
            allowed_tools=frozenset({"memory_query", "memory_write", "web_fetch"}),
        )
        scope = PrincipalMemoryScope(
            service._memory_store,
            PrincipalScope("owner", "local", "subject"),
        )
        ctx = ToolContext("owner-pilot", grant.lease.task_id, audit=c["audit"], owner_grant=grant)
        ctx.memory_scope = scope
        policy = OwnerToolPolicy()
        assert (
            policy(ToolSpec(name="memory_query", description="x"), {"query": "x"}, ctx).allowed
            is True
        )
        assert (
            policy(
                ToolSpec(name="memory_write", description="x", risk=ToolRisk.MUTATES_STATE),
                {"content": "x"},
                ctx,
            ).allowed
            is True
        )
        assert (
            policy(
                ToolSpec(name="web_fetch", description="x"),
                {"url": "https://example.com"},
                ctx,
            ).allowed
            is True
        )
    finally:
        await service.stop()


async def test_tool_status_http_read_reports_reasons(concrete):
    c = concrete
    service = _make_service(c)
    auth = OwnerAuthenticator(
        owner_id="owner",
        subject_id="subject",
        bearer_secret=SecretStr("SYNTHETIC_OWNER_TOKEN_32_CHARACTERS"),
    )
    app = create_app(c["butler"], pilot=service, authenticator=auth)
    async with (
        app.router.lifespan_context(app),
        c["client"](transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client,
    ):
        client.headers.update(
            {
                "Authorization": "Bearer SYNTHETIC_OWNER_TOKEN_32_CHARACTERS",
                "X-Prototype-CSRF": app.state.csrf_token,
                "Origin": "http://127.0.0.1",
            }
        )
        response = await client.get("/api/pilot/tool-status")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["memory_tools"]["enabled"] is False
        assert body["memory_tools"]["guard"] == "R-04"
        assert body["memory_tools"]["reason"] == "opt_in_disabled"
        assert body["web_fetch"]["enabled"] is False
        assert body["web_fetch"]["guard"] == "B-05"
