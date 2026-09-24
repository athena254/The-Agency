"""Memory tools: query and write against the SMS MemoryStore.

Note: ``MemoryQuery`` has no free-text field, so text queries go through
``MemoryStore.search_fts`` (the full-text path) with a metadata-filter
fallback; ``agent_id``-only lookups use ``list_by_agent``.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from agency.memory.sms.models import MemoryItem, MemoryQuery, MemoryTier
from agency.tools.base import ToolContext, ToolResult, ToolRisk, ToolSpec

logger = structlog.get_logger(__name__)


def _item_to_dict(item: MemoryItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "content": item.content,
        "tier": item.tier.value,
        "agent_id": item.agent_id,
        "created_at": item.created_at.isoformat(),
    }


class MemoryQueryTool:
    """Search stored memories by text and/or owning agent."""

    def __init__(self) -> None:
        self.spec = ToolSpec(
            name="memory_query",
            description=(
                "Search stored memories. Provide 'query' for full-text search, "
                "'agent_id' to list one agent's memories, or both."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
            },
            risk=ToolRisk.READ_ONLY,
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        start = time.perf_counter()
        store = ctx.memory_store
        if store is None:
            return ToolResult(tool="memory_query", ok=False, error="no memory store available")
        query = args.get("query")
        agent_id = args.get("agent_id")
        limit = args.get("limit", 10)
        if not isinstance(limit, int) or isinstance(limit, bool):
            return ToolResult(tool="memory_query", ok=False, error="'limit' must be an integer")
        limit = max(1, min(limit, 100))
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
                    logger.warning("tool.memory_query.fts_fallback", query=query)
                    items = await store.search(
                        MemoryQuery(
                            agent_id=normalized_agent_id,
                            limit=limit,
                        )
                    )
            else:
                assert isinstance(agent_id, str)
                items = await store.list_by_agent(agent_id.strip(), limit)
        except Exception as exc:  # noqa: BLE001 — store failure is a tool error
            return ToolResult(
                tool="memory_query",
                ok=False,
                error=f"memory query failed: {exc}",
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


class MemoryWriteTool:
    """Store a memory item for the calling agent."""

    def __init__(self) -> None:
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
            },
            risk=ToolRisk.MUTATES_STATE,
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        start = time.perf_counter()
        store = ctx.memory_store
        if store is None:
            return ToolResult(tool="memory_write", ok=False, error="no memory store available")
        content = args.get("content")
        if not isinstance(content, str) or not content.strip():
            return ToolResult(
                tool="memory_write",
                ok=False,
                error="missing required param: 'content'",
            )
        title = args.get("title")
        text = content.strip()
        if isinstance(title, str) and title.strip():
            text = f"{title.strip()}\n{text}"
        try:
            item = await store.store(
                MemoryItem(agent_id=ctx.agent_id, content=text, tier=MemoryTier.NORMAL)
            )
        except Exception as exc:  # noqa: BLE001 — store failure is a tool error
            return ToolResult(
                tool="memory_write",
                ok=False,
                error=f"memory write failed: {exc}",
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
