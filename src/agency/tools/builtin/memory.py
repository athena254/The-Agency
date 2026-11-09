"""Memory tools: query and write against the SMS MemoryStore.

Two modes are supported:

* ``scoped=True`` (owner pilot, R-04): the tool uses the principal-bound
  :class:`~agency.memory.sms.scope.PrincipalMemoryScope` carried on the tool
  context. Caller-supplied ``agent_id`` is rejected and every read/write is
  SQL-bound to the authenticated principal.
* ``scoped=False`` (legacy single-user runtime): the existing
  ``ctx.memory_store`` path is retained unchanged for backward compatibility.
  That path is not tenant-safe and must not be exposed to multiple users.

``MemoryQuery`` has no free-text field, so text queries go through
``MemoryStore.search_fts`` (the full-text path) with a metadata-filter
fallback; ``agent_id``-only lookups use ``list_by_agent``.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from agency.memory.sms.models import MemoryItem, MemoryQuery, MemoryTier
from agency.memory.sms.scope import PrincipalMemoryScope
from agency.tools.base import ToolContext, ToolResult, ToolRisk, ToolSpec

logger = structlog.get_logger(__name__)

_MAX_QUERY_CHARS = 200
_MAX_CONTENT_CHARS = 8000
_MAX_TITLE_CHARS = 200
_MAX_LIMIT = 100


def _item_to_dict(item: MemoryItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "content": item.content,
        "tier": item.tier.value,
        "agent_id": item.agent_id,
        "created_at": item.created_at.isoformat(),
    }


def _bound_limit(raw: object) -> int | None:
    if type(raw) is not int or isinstance(raw, bool):
        return None
    return max(1, min(raw, _MAX_LIMIT))


class MemoryQueryTool:
    """Search stored memories by text and/or owning agent."""

    def __init__(self, *, scoped: bool = False) -> None:
        self._scoped = scoped
        properties: dict[str, Any] = {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 10},
        }
        if not scoped:
            properties["agent_id"] = {"type": "string"}
        self.spec = ToolSpec(
            name="memory_query",
            description=(
                "Search stored memories. Provide 'query' for full-text search, "
                "or no arguments to list your own memories."
                if scoped
                else "Search stored memories. Provide 'query' for full-text search, "
                "'agent_id' to list one agent's memories, or both."
            ),
            parameters={
                "type": "object",
                "properties": properties,
                "additionalProperties": False,
            },
            risk=ToolRisk.READ_ONLY,
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        start = time.perf_counter()
        limit = _bound_limit(args.get("limit", 10))
        if limit is None:
            return ToolResult(tool="memory_query", ok=False, error="'limit' must be an integer")
        if self._scoped:
            return await self._run_scoped(args, ctx, limit, start)
        store = ctx.memory_store
        if store is None:
            return ToolResult(tool="memory_query", ok=False, error="no memory store available")
        query = args.get("query")
        agent_id = args.get("agent_id")
        has_query = isinstance(query, str) and bool(query.strip())
        has_agent = isinstance(agent_id, str) and bool(agent_id.strip())
        normalized_agent_id = agent_id.strip() if isinstance(agent_id, str) and has_agent else None
        if not has_query and not has_agent:
            return ToolResult(
                tool="memory_query",
                ok=False,
                error="query or agent_id required",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        try:
            if has_query:
                assert isinstance(query, str)
                try:
                    items = await store.search_fts(
                        query.strip(),
                        agent_id=normalized_agent_id,
                        limit=limit,
                    )
                except Exception:  # noqa: BLE001 — any FTS failure falls back
                    # FTS syntax (e.g. reserved chars) — fall back to filters.
                    logger.warning("tool.memory_query.fts_fallback")
                    items = await store.search(
                        MemoryQuery(
                            agent_id=normalized_agent_id,
                            limit=limit,
                        )
                    )
            else:
                assert isinstance(agent_id, str)
                items = await store.list_by_agent(agent_id.strip(), limit)
        except Exception:  # noqa: BLE001 — store failure is a redacted tool error
            return ToolResult(
                tool="memory_query",
                ok=False,
                error="memory query failed",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        output = [_item_to_dict(item) for item in items]
        logger.info("tool.memory_query", result_count=len(output))
        return ToolResult(
            tool="memory_query",
            ok=True,
            output=output,
            duration_ms=int((time.perf_counter() - start) * 1000),
            evidence={"result_count": len(output)},
        )

    async def _run_scoped(
        self, args: dict[str, Any], ctx: ToolContext, limit: int, start: float
    ) -> ToolResult:
        scope = ctx.memory_scope
        if type(scope) is not PrincipalMemoryScope:
            return ToolResult(
                tool="memory_query",
                ok=False,
                error="principal-bound memory scope required",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        if "agent_id" in args:
            return ToolResult(
                tool="memory_query",
                ok=False,
                error="agent_id is not permitted; memory scope is principal-bound",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        query = args.get("query")
        if query is not None and (type(query) is not str or len(query) > _MAX_QUERY_CHARS):
            return ToolResult(
                tool="memory_query",
                ok=False,
                error="'query' must be a bounded string",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        try:
            if type(query) is str and query.strip():
                items = await scope.query(query.strip(), limit=limit)
            else:
                items = await scope.list_memories(limit=limit)
        except Exception:  # noqa: BLE001 — store failure is a redacted tool error
            return ToolResult(
                tool="memory_query",
                ok=False,
                error="memory query failed",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        output = [_item_to_dict(item) for item in items]
        logger.info("tool.memory_query", result_count=len(output), scoped=True)
        return ToolResult(
            tool="memory_query",
            ok=True,
            output=output,
            duration_ms=int((time.perf_counter() - start) * 1000),
            evidence={"result_count": len(output), "scoped": True},
        )


class MemoryWriteTool:
    """Store a memory item for the calling agent or principal."""

    def __init__(self, *, scoped: bool = False) -> None:
        self._scoped = scoped
        self.spec = ToolSpec(
            name="memory_write",
            description=(
                "Store a memory for later recall. 'content' is required; "
                "an optional 'title' is prepended to the stored content."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "minLength": 1},
                    "title": {"type": "string"},
                },
                "required": ["content"],
                "additionalProperties": False,
            },
            risk=ToolRisk.MUTATES_STATE,
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        start = time.perf_counter()
        content = args.get("content")
        if not isinstance(content, str) or not content.strip():
            return ToolResult(
                tool="memory_write",
                ok=False,
                error="missing required param: 'content'",
            )
        if len(content) > _MAX_CONTENT_CHARS:
            return ToolResult(
                tool="memory_write",
                ok=False,
                error="'content' exceeds the bounded length",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        title = args.get("title")
        if title is not None and (not isinstance(title, str) or len(title) > _MAX_TITLE_CHARS):
            return ToolResult(
                tool="memory_write",
                ok=False,
                error="'title' must be a bounded string",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        if self._scoped:
            return await self._run_scoped(ctx, content, title, start)
        store = ctx.memory_store
        if store is None:
            return ToolResult(tool="memory_write", ok=False, error="no memory store available")
        text = content.strip()
        if isinstance(title, str) and title.strip():
            text = f"{title.strip()}\n{text}"
        try:
            item = await store.store(
                MemoryItem(agent_id=ctx.agent_id, content=text, tier=MemoryTier.NORMAL)
            )
        except Exception:  # noqa: BLE001 — store failure is a redacted tool error
            return ToolResult(
                tool="memory_write",
                ok=False,
                error="memory write failed",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        logger.info("tool.memory_write", memory_id=item.id, agent_id=ctx.agent_id)
        return ToolResult(
            tool="memory_write",
            ok=True,
            output={"id": item.id},
            duration_ms=int((time.perf_counter() - start) * 1000),
            evidence={"memory_id": item.id},
        )

    async def _run_scoped(
        self, ctx: ToolContext, content: str, title: object, start: float
    ) -> ToolResult:
        scope = ctx.memory_scope
        if type(scope) is not PrincipalMemoryScope:
            return ToolResult(
                tool="memory_write",
                ok=False,
                error="principal-bound memory scope required",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        try:
            item = await scope.write(content, title=title if isinstance(title, str) else None)
        except Exception:  # noqa: BLE001 — store failure is a redacted tool error
            return ToolResult(
                tool="memory_write",
                ok=False,
                error="memory write failed",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        logger.info("tool.memory_write", memory_id=item.id, scoped=True)
        return ToolResult(
            tool="memory_write",
            ok=True,
            output={"id": item.id},
            duration_ms=int((time.perf_counter() - start) * 1000),
            evidence={"memory_id": item.id, "scoped": True},
        )
