"""Tests for WebSearchTool and WebFetchTool (mocked httpx transport)."""

from __future__ import annotations

import urllib.parse
from collections.abc import Callable

import httpx

from agency.tools.base import ToolContext
from agency.tools.builtin.web import WebFetchTool, WebSearchTool
from agency.tools.registry import ToolRegistry


def _uddg(url: str) -> str:
    return "//duckduckgo.com/l/?uddg=" + urllib.parse.quote_plus(url) + "&rut=abc"


DDG_HTML = f"""<html><body>
<div class="result">
  <a class="result__a" href="{_uddg('https://example.com/rust-2026')}">Rust 2026 News</a>
  <a class="result__snippet" href="x">The latest on Rust in 2026.</a>
</div>
<div class="result">
  <a class="result__a" href="{_uddg('https://example.org/edition')}">Edition Guide</a>
  <a class="result__snippet" href="x">All about editions.</a>
</div>
</body></html>"""

ARTICLE_HTML = """<html><head><title>T</title>
<script>var evil = 1;</script>
<style>p { color: red; }</style>
</head><body>
<h1>Rust 2026</h1>
<p>First   paragraph
with    odd spacing.</p>
<p>Second paragraph.</p>
</body></html>"""


def _ctx() -> ToolContext:
    return ToolContext(agent_id="agent-1", task_id="task-1")


def _mock(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def test_search_parses_results_and_decodes_uddg():
    tool = WebSearchTool(transport=_mock(lambda req: httpx.Response(200, text=DDG_HTML)))
    result = await tool.run({"query": "rust 2026"}, _ctx())
    assert result.ok is True
    assert len(result.output) == 2
    assert result.output[0]["title"] == "Rust 2026 News"
    assert result.output[0]["url"] == "https://example.com/rust-2026"
    assert result.output[0]["snippet"] == "The latest on Rust in 2026."
    assert result.output[1]["url"] == "https://example.org/edition"
    assert result.evidence["engine"] == "duckduckgo"
    assert result.evidence["result_count"] == 2


async def test_search_respects_max_results():
    tool = WebSearchTool(transport=_mock(lambda req: httpx.Response(200, text=DDG_HTML)))
    result = await tool.run({"query": "rust", "max_results": 1}, _ctx())
    assert result.ok is True
    assert len(result.output) == 1


async def test_search_network_error_is_not_ok():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    tool = WebSearchTool(transport=_mock(boom))
    result = await tool.run({"query": "rust"}, _ctx())
    assert result.ok is False
    assert result.error


async def test_search_http_error_is_not_ok():
    tool = WebSearchTool(transport=_mock(lambda req: httpx.Response(500, text="oops")))
    result = await tool.run({"query": "rust"}, _ctx())
    assert result.ok is False
    assert "500" in (result.error or "")


async def test_search_zero_results_is_ok_empty():
    tool = WebSearchTool(
        transport=_mock(lambda req: httpx.Response(200, text="<html><body></body></html>"))
    )
    result = await tool.run({"query": "zzznomatch"}, _ctx())
    assert result.ok is True
    assert result.output == []


async def test_fetch_extracts_text_strips_scripts_collapses_whitespace():
    tool = WebFetchTool(transport=_mock(lambda req: httpx.Response(200, text=ARTICLE_HTML)))
    result = await tool.run({"url": "https://example.com/article"}, _ctx())
    assert result.ok is True
    text = result.output["text"]
    assert "evil" not in text
    assert "color" not in text
    assert "Rust 2026" in text
    assert "First paragraph with odd spacing." in text
    assert "  " not in text  # whitespace collapsed
    assert result.output["status"] == 200
    assert result.output["url"] == "https://example.com/article"


async def test_fetch_404_is_not_ok():
    tool = WebFetchTool(transport=_mock(lambda req: httpx.Response(404, text="missing")))
    result = await tool.run({"url": "https://example.com/gone"}, _ctx())
    assert result.ok is False
    assert "404" in (result.error or "")


async def test_fetch_caps_bytes_and_chars():
    big = "<html><body><p>" + ("word " * 60000) + "</p></body></html>"  # ~300 KB

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=big)

    tool = WebFetchTool(transport=_mock(handler))
    result = await tool.run({"url": "https://example.com/big"}, _ctx())
    assert result.ok is True
    assert result.output["bytes_read"] <= 200 * 1024
    assert len(result.output["text"]) <= 8000
    assert result.evidence["bytes_read"] == result.output["bytes_read"]


async def test_fetch_rejects_non_http_url():
    tool = WebFetchTool(transport=_mock(lambda req: httpx.Response(200, text="x")))
    result = await tool.run({"url": "ftp://example.com/file"}, _ctx())
    assert result.ok is False
    assert "http" in (result.error or "")


async def test_params_schema_missing_url_rejected_by_registry():
    registry = ToolRegistry()
    registry.register(WebFetchTool(transport=_mock(lambda req: httpx.Response(200, text="x"))))
    registry.register(WebSearchTool(transport=_mock(lambda req: httpx.Response(200, text=""))))
    missing_url = await registry.call("web_fetch", {}, _ctx())
    assert missing_url.ok is False
    assert "url" in (missing_url.error or "")
    missing_query = await registry.call("web_search", {}, _ctx())
    assert missing_query.ok is False
    assert "query" in (missing_query.error or "")
