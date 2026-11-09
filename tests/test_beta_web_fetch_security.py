"""Beta web-fetch SSRF hardening tests (hermetic, no live network)."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Callable

import httpcore
import httpx
import pytest
from httpcore import AsyncMockBackend, AsyncNetworkBackend

from agency.tools.base import ToolContext, ToolResult
from agency.tools.builtin.web import PinnedAddressTransport, WebFetchTool

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


# ---------------------------------------------------------------------- #
# B-05 connection-address pinning
# ---------------------------------------------------------------------- #


def _response(status: int, body: str = "", **headers: str) -> bytes:
    crlf = chr(13) + chr(10)
    lines = [f"HTTP/1.1 {status} Synthetic", f"Content-Length: {len(body.encode('utf-8'))}"]
    lines.extend(f"{key}: {value}" for key, value in headers.items())
    head = crlf.join(lines) + crlf + crlf
    return head.encode("utf-8") + body.encode("utf-8")


class _RecordingBackend(AsyncNetworkBackend):
    """Record the address httpcore connects to and replay canned responses."""

    def __init__(self, responses: list[bytes]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, int]] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: object = None,
    ) -> object:
        self.calls.append((host, port))
        buffer = self._responses.pop(0) if self._responses else b""
        backend = AsyncMockBackend([buffer])
        return await backend.connect_tcp(
            host,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,  # type: ignore[arg-type]
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: object = None,
    ) -> object:
        raise httpcore.UnsupportedProtocol("unix sockets are not supported")

    async def sleep(self, delay: float) -> None:
        await asyncio.sleep(delay)


def _pinned_tool(
    backend: _RecordingBackend,
    resolver: Callable[[str], list[str]] = _public_resolver,
) -> WebFetchTool:
    return WebFetchTool(dns_resolver=resolver, network_backend=backend)


async def test_pinned_transport_connects_to_validated_address() -> None:
    backend = _RecordingBackend([_response(200, ARTICLE_HTML)])
    result = await _pinned_tool(backend).run({"url": "https://example.com/article"}, _ctx())
    assert result.ok is True
    assert backend.calls == [("93.184.216.34", 443)]


async def test_redirect_to_private_address_never_connects() -> None:
    backend = _RecordingBackend([_response(302, "", Location="https://127.0.0.1/evil")])
    result = await _pinned_tool(backend).run({"url": "https://example.com/start"}, _ctx())
    assert result.ok is False
    assert "blocked" in (result.error or "")
    assert backend.calls == [("93.184.216.34", 443)]


async def test_dns_rebind_between_validation_and_connect_is_pinned() -> None:
    """The connection uses the validated address; DNS is not re-resolved."""
    calls: list[str] = []

    def flapping(host: str) -> list[str]:
        calls.append(host)
        return ["93.184.216.34"] if len(calls) == 1 else ["127.0.0.1"]

    backend = _RecordingBackend([_response(200, ARTICLE_HTML)])
    result = await _pinned_tool(backend, flapping).run(
        {"url": "https://example.com/article"}, _ctx()
    )
    assert result.ok is True
    assert len(calls) == 1
    assert backend.calls == [("93.184.216.34", 443)]
    assert all("127.0.0.1" not in host for host, _ in backend.calls)


async def test_each_redirect_hop_is_pinned_separately() -> None:
    def resolver(host: str) -> list[str]:
        return {
            "example.com": ["93.184.216.34"],
            "other.example": ["93.184.216.99"],
        }[host]

    backend = _RecordingBackend(
        [
            _response(302, "", Location="https://other.example/next"),
            _response(200, ARTICLE_HTML),
        ]
    )
    result = await _pinned_tool(backend, resolver).run({"url": "https://example.com/start"}, _ctx())
    assert result.ok is True
    assert backend.calls == [("93.184.216.34", 443), ("93.184.216.99", 443)]


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/article",
        "ftp://example.com/file",
        "file:///etc/passwd",
        "gopher://example.com/",
    ],
)
async def test_blocked_schemes_never_connect(url: str) -> None:
    backend = _RecordingBackend([_response(200, ARTICLE_HTML)])
    result = await _pinned_tool(backend).run({"url": url}, _ctx())
    assert result.ok is False
    assert backend.calls == []


async def test_pinned_transport_refuses_non_global_address() -> None:
    recorder = _RecordingBackend([_response(200, ARTICLE_HTML)])
    transport = PinnedAddressTransport("127.0.0.1", backend=recorder)
    async with httpx.AsyncClient(transport=transport, timeout=5.0) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("https://example.com/")
    assert recorder.calls == []


async def test_private_resolved_address_never_connects() -> None:
    backend = _RecordingBackend([_response(200, ARTICLE_HTML)])
    result = await _pinned_tool(backend, _private_resolver).run(
        {"url": "https://evil.example.com/"}, _ctx()
    )
    assert result.ok is False
    assert backend.calls == []
