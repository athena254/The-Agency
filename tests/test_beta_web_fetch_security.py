"""Beta web-fetch SSRF hardening tests (hermetic, no live network)."""

from __future__ import annotations

import socket
from collections.abc import Callable

import httpx
import pytest

from agency.tools.base import ToolContext, ToolResult
from agency.tools.builtin.web import WebFetchTool

ARTICLE_HTML = "<html><body><h1>Hello</h1><p>Public content here.</p></body></html>"

PUBLIC_IPS = ["93.184.216.34"]  # documentation example, globally routable
PRIVATE_IPS = ["127.0.0.1"]


def _ctx() -> ToolContext:
    return ToolContext(agent_id="agent-1", task_id="task-1")


def _public_resolver(host: str) -> list[str]:
    del host
    return list(PUBLIC_IPS)


def _private_resolver(host: str) -> list[str]:
    del host
    return list(PRIVATE_IPS)


def _failing_resolver(host: str) -> list[str]:
    del host
    raise socket.gaierror("mocked DNS failure")


def _tool(
    handler: Callable[[httpx.Request], httpx.Response],
    resolver: Callable[[str], list[str]] = _public_resolver,
) -> tuple[WebFetchTool, list[str]]:
    seen: list[str] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return handler(request)

    transport = httpx.MockTransport(recording)
    tool = WebFetchTool(transport=transport, dns_resolver=resolver)
    return tool, seen


async def _run(
    url: str,
    handler: Callable[[httpx.Request], httpx.Response],
    resolver: Callable[[str], list[str]] = _public_resolver,
) -> tuple[ToolResult, list[str]]:
    tool, seen = _tool(handler, resolver)
    result = await tool.run({"url": url}, _ctx())
    return result, seen


def _ok_handler(request: httpx.Request) -> httpx.Response:
    del request
    return httpx.Response(200, text=ARTICLE_HTML)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/",
        "https://10.0.0.5/admin",
        "https://192.168.1.1/",
        "https://[::1]/",
        "https://[0:0:0:0:0:ffff:127.0.0.1]/",
    ],
)
async def test_private_literal_rejected_no_request(url: str) -> None:
    result, seen = await _run(url, _ok_handler, _public_resolver)
    assert result.ok is False
    assert seen == []


@pytest.mark.parametrize(
    "url",
    [
        "https://2130706433/",
        "https://0x7f.0.0.1/",
        "https://0x7f000001/",
        "https://0177.0.0.1/",
        "https://127.1/",
    ],
)
async def test_numeric_obscured_ip_rejected_no_request(url: str) -> None:
    result, seen = await _run(url, _ok_handler, _public_resolver)
    assert result.ok is False
    assert seen == []


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pass@example.com/",
        "https://example.com@evil.com/",
        "https://user@example.com/",
    ],
)
async def test_userinfo_rejected_no_request(url: str) -> None:
    result, seen = await _run(url, _ok_handler, _public_resolver)
    assert result.ok is False
    assert seen == []


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/",
        "ftp://example.com/file",
        "https://localhost/",
        "https://foo.local/",
        "https://foo.internal/",
        "https://foo.lan/",
        "https:///missing-host",
        "https://example.com/\x01",
    ],
)
async def test_malformed_insecure_or_internal_rejected(url: str) -> None:
    result, seen = await _run(url, _ok_handler, _public_resolver)
    assert result.ok is False
    assert seen == []


async def test_first_hop_redirect_to_private_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(302, headers={"location": "https://127.0.0.1/evil"})

    result, seen = await _run("https://example.com/start", handler, _public_resolver)
    assert result.ok is False
    assert len(seen) == 1
    assert all("127.0.0.1" not in u for u in seen)


async def test_redirect_chain_capped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        # Always redirect to next public hop; validator should cap.
        return httpx.Response(302, headers={"location": "https://example.com/hop"})

    tool, seen = _tool(handler, _public_resolver)
    result = await tool.run({"url": "https://example.com/start"}, _ctx())
    assert result.ok is False
    assert "redirect" in (result.error or "").lower()
    assert len(seen) <= 4  # initial + max hops


async def test_public_https_success_retains_citation() -> None:
    result, seen = await _run("https://example.com/article", _ok_handler, _public_resolver)
    assert result.ok is True
    assert result.output["url"] == "https://example.com/article"
    assert "Public content" in result.output["text"]
    assert len(seen) == 1


async def test_size_bound_enforced() -> None:
    big = "<html><body><p>" + ("word " * 60000) + "</p></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=big)

    tool, _seen = _tool(handler, _public_resolver)
    result = await tool.run({"url": "https://example.com/big"}, _ctx())
    assert result.ok is True
    assert result.output["bytes_read"] <= 200 * 1024
    assert len(result.output["text"]) <= 8000


async def test_dns_private_denied_no_request() -> None:
    result, seen = await _run("https://evil.example.com/", _ok_handler, _private_resolver)
    assert result.ok is False
    assert seen == []


async def test_dns_failure_denied_no_request() -> None:
    result, seen = await _run("https://example.com/", _ok_handler, _failing_resolver)
    assert result.ok is False
    assert seen == []


async def test_dns_rebind_flapping_second_hop_denied() -> None:
    """Residual TOCTOU: each hop is re-validated, so a rebind to private is denied.

    This does NOT prove safety against rebind between check and connect on the
    real transport (see module docstring); web_fetch stays beta-disabled.
    """
    calls: list[str] = []

    def flapping(host: str) -> list[str]:
        calls.append(host)
        # First validation (initial URL) returns public; redirect target rebinds.
        if len(calls) == 1:
            return list(PUBLIC_IPS)
        return list(PRIVATE_IPS)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://example.com/start":
            return httpx.Response(302, headers={"location": "https://example.com/rebound"})
        return httpx.Response(200, text=ARTICLE_HTML)

    result, seen = await _run("https://example.com/start", handler, flapping)
    assert result.ok is False
    # Second request must never be sent after private rebind detected.
    assert seen == ["https://example.com/start"]
