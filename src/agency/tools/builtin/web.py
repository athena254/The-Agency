"""Web tools: DuckDuckGo HTML search and HTTP page fetch.

Stdlib + httpx only — HTML is parsed with :mod:`html.parser`, no bs4.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
import urllib.parse
from collections.abc import Callable
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

_DnsResolver = Callable[[str], list[str]]

_MAX_FETCH_REDIRECTS = 3

_FETCH_DNS_TIMEOUT = 5.0  # seconds
_MAX_FETCH_CONCURRENT = 10

_BLOCKED_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".localdomain",
    ".intranet",
    ".corp",
    ".home",
    ".invalid",
)

_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


def _default_resolve(host: str) -> list[str]:
    """Resolve ``host`` to IP strings via the system resolver."""
    infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


async def _validate_fetch_url_async(
    url: str, resolver: _DnsResolver, semaphore: asyncio.Semaphore | None = None
) -> str:
    """Async version of :func:`_validate_fetch_url` that offloads DNS to a worker thread.

    All structural URL validation runs synchronously (fast). Only the DNS resolution
    call is offloaded via :func:`asyncio.to_thread` and bounded by
    :data:`_FETCH_DNS_TIMEOUT`. Fails closed on timeout or error.

    Residual risk: this is a preflight check only (TOCTOU/DNS-rebind). It does
    NOT pin the address used by httpx, so it cannot prove safety for untrusted
    URLs on its own. ``web_fetch`` must stay beta-disabled until an
    enforceable proxy/egress rule or HTTPS domain allowlist plus host-network
    review exists.
    """
    for ch in url:
        if ord(ch) <= 31 or ord(ch) == 127:
            raise ValueError("control character in url blocked")
    cleaned = url.strip()
    if not cleaned:
        raise ValueError("missing required param: 'url'")
    if any(ch.isspace() for ch in cleaned):
        raise ValueError("whitespace in url blocked")
    try:
        parsed = urllib.parse.urlparse(cleaned)
    except ValueError as exc:
        raise ValueError(f"malformed url blocked: {exc}") from exc
    if parsed.scheme.lower() != "https":
        raise ValueError("'url' must use https://")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("missing hostname blocked")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("credentials in url blocked")
    if "@" in (parsed.netloc or ""):
        raise ValueError("userinfo syntax blocked")
    try:
        normalized = httpx.URL(cleaned)
    except Exception as exc:
        raise ValueError(f"malformed url blocked: {exc}") from exc
    if normalized.host.lower().rstrip(".") != hostname.lower().rstrip("."):
        raise ValueError("odd host syntax blocked")
    reason = _hostname_block_reason(hostname)
    if reason is not None:
        raise ValueError(reason)
    try:
        if semaphore is not None:
            async with semaphore:
                addresses = await asyncio.wait_for(
                    asyncio.to_thread(resolver, hostname), timeout=_FETCH_DNS_TIMEOUT
                )
        else:
            addresses = await asyncio.wait_for(
                asyncio.to_thread(resolver, hostname), timeout=_FETCH_DNS_TIMEOUT
            )
    except TimeoutError:
        raise ValueError("DNS resolution timed out")
    except Exception as exc:
        raise ValueError(f"DNS resolution failed or blocked: {exc}") from exc
    if not addresses:
        raise ValueError("DNS resolution failed or blocked: no addresses")
    for raw in addresses:
        ip_text = raw.split("%")[0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError as exc:
            raise ValueError(f"unparsable resolved address blocked: {raw}") from exc
        if not ip.is_global:
            raise ValueError(f"resolved address {ip_text} blocked (not global)")
    return cleaned


def _parse_numeric_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """Decode decimal/octal/hex IPv4 forms (e.g. ``2130706433``, ``0x7f.0.0.1``).

    Returns the IPv4 address when ``host`` is an all-numeric obscured form,
    else None. Standard dotted-decimal is handled by :mod:`ipaddress` itself;
    this catches evasion forms that some stacks interpret via ``inet_aton``.
    """
    if ":" in host:
        return None
    lowered = host.lower().rstrip(".")
    if not lowered:
        return None
    # Single decimal integer, e.g. "2130706433".
    if lowered.isdigit():
        try:
            value = int(lowered, 10)
        except ValueError:
            return None
        if 0 <= value <= 0xFFFFFFFF:
            return ipaddress.IPv4Address(value)
        return None
    if "." not in lowered:
        return None
    parts = lowered.split(".")
    if not 1 <= len(parts) <= 4:
        return None
    values: list[int] = []
    for part in parts:
        if not part:
            return None
        try:
            if part.startswith("0x"):
                values.append(int(part, 16))
            elif part.startswith("0") and len(part) > 1 and part.isdigit():
                values.append(int(part, 8))
            elif part.isdigit():
                values.append(int(part, 10))
            else:
                return None
        except ValueError:
            return None
    try:
        if len(values) == 1:
            if not 0 <= values[0] <= 0xFFFFFFFF:
                return None
            return ipaddress.IPv4Address(values[0])
        if len(values) == 2:
            a, b = values
            if not (0 <= a <= 0xFF and 0 <= b <= 0xFFFFFF):
                return None
            return ipaddress.IPv4Address((a << 24) | b)
        if len(values) == 3:
            a, b, c = values
            if not (0 <= a <= 0xFF and 0 <= b <= 0xFF and 0 <= c <= 0xFFFF):
                return None
            return ipaddress.IPv4Address((a << 24) | (b << 16) | c)
        a, b, c, d = values
        for octet in values:
            if not 0 <= octet <= 0xFF:
                return None
        return ipaddress.IPv4Address(f"{a}.{b}.{c}.{d}")
    except ipaddress.AddressValueError:
        return None


def _hostname_block_reason(host: str) -> str | None:
    """Return a block reason for literal/internal hostnames, else None."""
    normalized = host.lower().rstrip(".")
    if not normalized:
        return "missing hostname"
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return "localhost blocked"
    for suffix in _BLOCKED_SUFFIXES:
        if normalized.endswith(suffix):
            return f"internal suffix {suffix!r} blocked"
    if "." not in normalized and normalized != "localhost":
        # Single-label names (intranet hosts) fail closed.
        try:
            ipaddress.ip_address(normalized)
        except ValueError:
            if _parse_numeric_ipv4(normalized) is not None:
                return "obscured numeric IP form blocked"
            return "single-label hostname blocked"
    try:
        ipaddress.ip_address(normalized)
        return "IP literal blocked"
    except ValueError:
        pass
    if _parse_numeric_ipv4(normalized) is not None:
        return "obscured numeric IP form blocked"
    return None


def _validate_fetch_url(url: str, resolver: _DnsResolver) -> str:
    """Deprecated: use :func:`_validate_fetch_url_async`. Sync wrapper for backward compatibility."""
    return asyncio.run(_validate_fetch_url_async(url, resolver))


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
    """Fetch an HTTPS URL and return extracted text (capped).

    SSRF hardening is best-effort preflight only: structural HTTPS validation,
    IP-literal/localhost/internal-suffix rejection, per-hop DNS checks, manual
    redirect validation (max 3 hops, no automatic follows). DNS preflight does
    NOT pin the address used by httpx (TOCTOU/rebind residual), so
    ``web_fetch`` must remain beta-disabled until a proxy/egress rule or HTTPS
    domain allowlist plus host-network review exists.
    """

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        dns_resolver: _DnsResolver | None = None,
    ) -> None:
        self._transport = transport
        self._resolver: _DnsResolver = dns_resolver or _default_resolve
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(_MAX_FETCH_CONCURRENT)
        self.spec = ToolSpec(
            name="web_fetch",
            description=(
                "Fetch a web page (https only, public hosts) and return its text "
                "content (first 8000 chars)."
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
        try:
            current_url = await _validate_fetch_url_async(
                url.strip(), self._resolver, self._semaphore
            )
        except ValueError as exc:
            return ToolResult(
                tool="web_fetch",
                ok=False,
                error=f"blocked: {exc}",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=15.0,
                follow_redirects=False,
                headers={"User-Agent": _DESKTOP_UA},
            ) as client:
                hops = 0
                while True:
                    async with client.stream("GET", current_url) as response:
                        status = response.status_code
                        if status in _REDIRECT_STATUSES:
                            location = response.headers.get("location")
                            if not location:
                                return ToolResult(
                                    tool="web_fetch",
                                    ok=False,
                                    error=f"fetch failed with status {status} for {current_url}",
                                    duration_ms=int((time.perf_counter() - start) * 1000),
                                )
                            if hops >= _MAX_FETCH_REDIRECTS:
                                return ToolResult(
                                    tool="web_fetch",
                                    ok=False,
                                    error="blocked: too many redirects",
                                    duration_ms=int((time.perf_counter() - start) * 1000),
                                )
                            next_url = urllib.parse.urljoin(current_url, location)
                            try:
                                current_url = await _validate_fetch_url_async(
                                    next_url, self._resolver, self._semaphore
                                )
                            except ValueError as exc:
                                return ToolResult(
                                    tool="web_fetch",
                                    ok=False,
                                    error=f"blocked redirect: {exc}",
                                    duration_ms=int((time.perf_counter() - start) * 1000),
                                )
                            hops += 1
                            continue
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
