"""Web tools: DuckDuckGo HTML search and HTTP page fetch.

Stdlib + httpx only — HTML is parsed with :mod:`html.parser`, no bs4.
"""

from __future__ import annotations

import time
import urllib.parse
from html.parser import HTMLParser
from typing import Any

import httpx
import structlog

from agency.tools.base import ToolContext, ToolResult, ToolRisk, ToolSpec

logger = structlog.get_logger(__name__)

_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

_DDG_ENDPOINT = "https://html.duckduckgo.com/html/"

_MAX_FETCH_BYTES = 200 * 1024  # 200 KB response cap
_MAX_FETCH_CHARS = 8000  # text chars returned


def _decode_ddg_url(href: str) -> str:
    """Unwrap DuckDuckGo's ``//duckduckgo.com/l/?uddg=<urlencoded>`` redirect."""
    parsed = urllib.parse.urlparse(href)
    if "duckduckgo.com" in (parsed.netloc or ""):
        params = urllib.parse.parse_qs(parsed.query)
        uddg = params.get("uddg")
        if uddg:
            return urllib.parse.unquote(uddg[0])
    if href.startswith("//"):
        return "https:" + href
    return href


class _DDGResultParser(HTMLParser):
    """Extract ``result__a`` links + ``result__snippet`` texts, in order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._capture: str | None = None  # "title" | "snippet" | None
        self._buf: list[str] = []
        self._href: str = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        classes = dict(attrs).get("class", "") or ""
        if "result__a" in classes:
            self._capture = "title"
            self._buf = []
            self._href = dict(attrs).get("href", "") or ""
        elif "result__snippet" in classes:
            self._capture = "snippet"
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._capture is None:
            return
        text = "".join(self._buf).strip()
        if self._capture == "title":
            self.results.append({"title": text, "url": _decode_ddg_url(self._href), "snippet": ""})
        elif self._capture == "snippet" and self.results and not self.results[-1]["snippet"]:
            self.results[-1]["snippet"] = text
        self._capture = None
        self._buf = []


class _TextExtractor(HTMLParser):
    """Strip <script>/<style> blocks and tags, keep visible text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in ("p", "br", "div", "li", "h1", "h2", "h3", "h4", "tr"):
            self._chunks.append(" ")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._chunks.append(data)

    def text(self) -> str:
        """Collapsed-whitespace text."""
        return " ".join("".join(self._chunks).split())


def extract_text(html: str) -> str:
    """Strip scripts/styles/tags from ``html`` and collapse whitespace."""
    extractor = _TextExtractor()
    extractor.feed(html)
    return extractor.text()


class WebSearchTool:
    """Search the web via the DuckDuckGo HTML endpoint."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self.spec = ToolSpec(
            name="web_search",
            description=(
                "Search the web and return up to max_results results with title, url, and snippet."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
            risk=ToolRisk.READ_ONLY,
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        del ctx
        start = time.perf_counter()
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult(tool="web_search", ok=False, error="missing required param: 'query'")
        max_results = args.get("max_results", 5)
        if not isinstance(max_results, int) or isinstance(max_results, bool):
            return ToolResult(tool="web_search", ok=False, error="'max_results' must be an integer")
        max_results = max(1, min(max_results, 20))

        url = _DDG_ENDPOINT + "?q=" + urllib.parse.quote_plus(query.strip())
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=15.0,
                headers={"User-Agent": _DESKTOP_UA},
            ) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            return ToolResult(
                tool="web_search",
                ok=False,
                error=f"search request failed: {type(exc).__name__}",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        if response.status_code < 200 or response.status_code >= 300:
            return ToolResult(
                tool="web_search",
                ok=False,
                error=f"search request failed with status {response.status_code}",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        try:
            parser = _DDGResultParser()
            parser.feed(response.text)
            results = parser.results[:max_results]
        except Exception as exc:  # noqa: BLE001 — parse failure is a tool error
            return ToolResult(
                tool="web_search",
                ok=False,
                error=f"failed to parse search results: {type(exc).__name__}",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        logger.info("tool.web_search", result_count=len(results))
        return ToolResult(
            tool="web_search",
            ok=True,
            output=results,
            duration_ms=int((time.perf_counter() - start) * 1000),
            evidence={
                "engine": "duckduckgo",
                "query": query,
                "result_count": len(results),
            },
        )


class WebFetchTool:
    """Fetch a URL and return extracted text (capped)."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self.spec = ToolSpec(
            name="web_fetch",
            description=(
                "Fetch a web page (http/https) and return its text content (first 8000 chars)."
            ),
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            risk=ToolRisk.READ_ONLY,
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        del ctx
        start = time.perf_counter()
        url = args.get("url")
        if not isinstance(url, str) or not url.strip():
            return ToolResult(tool="web_fetch", ok=False, error="missing required param: 'url'")
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            return ToolResult(
                tool="web_fetch",
                ok=False,
                error="'url' must start with http:// or https://",
            )
        try:
            async with (
                httpx.AsyncClient(
                    transport=self._transport,
                    timeout=15.0,
                    follow_redirects=True,
                    headers={"User-Agent": _DESKTOP_UA},
                ) as client,
                client.stream("GET", url) as response,
            ):
                status = response.status_code
                final_url = str(response.url)
                if status < 200 or status >= 300:
                    return ToolResult(
                        tool="web_fetch",
                        ok=False,
                        error=f"fetch failed with status {status} for {final_url}",
                        duration_ms=int((time.perf_counter() - start) * 1000),
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=16384):
                    body.extend(chunk)
                    if len(body) >= _MAX_FETCH_BYTES:
                        del body[_MAX_FETCH_BYTES:]
                        break
        except httpx.HTTPError as exc:
            return ToolResult(
                tool="web_fetch",
                ok=False,
                error=f"fetch request failed: {exc}",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        bytes_read = len(body)
        try:
            html = bytes(body).decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 — defensive, decode rarely fails
            return ToolResult(
                tool="web_fetch",
                ok=False,
                error=f"failed to decode response body: {exc}",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        text = extract_text(html)[:_MAX_FETCH_CHARS]
        logger.info("tool.web_fetch", url=final_url, status=status, bytes_read=bytes_read)
        return ToolResult(
            tool="web_fetch",
            ok=True,
            output={
                "url": final_url,
                "status": status,
                "text": text,
                "bytes_read": bytes_read,
            },
            duration_ms=int((time.perf_counter() - start) * 1000),
            evidence={"url": final_url, "status": status, "bytes_read": bytes_read},
        )
