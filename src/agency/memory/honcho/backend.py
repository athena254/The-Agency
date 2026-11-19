"""Honcho mirror over the durable SMS store.

SMS remains the source of truth. A write always lands in SQLite first and is
mirrored to Honcho best-effort; a read prefers Honcho when it is healthy and
falls back to the local store on timeout or error. No caller-visible behaviour
changes when Honcho is disabled or unavailable.
"""

from __future__ import annotations

from uuid import uuid4

import structlog
from pydantic import ValidationError

from agency.memory.honcho.client import HonchoClient
from agency.memory.sms.models import MemoryItem, MemoryTier
from agency.memory.sms.store import MemoryStore

log = structlog.get_logger(__name__)


def _item_from_honcho(raw: dict[str, object], agent_id: str) -> MemoryItem | None:
    content = raw.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    metadata = raw.get("metadata")
    meta = metadata if isinstance(metadata, dict) else {}
    item_id = raw.get("id")
    tier_raw = meta.get("tier", MemoryTier.NORMAL.value)
    try:
        tier = MemoryTier(tier_raw) if isinstance(tier_raw, str) else MemoryTier.NORMAL
        return MemoryItem(
            id=str(item_id) if isinstance(item_id, str) else uuid4().hex,
            agent_id=agent_id,
            content=content,
            tier=tier,
            importance=float(meta.get("importance", 0.5)),
            tags=[str(t) for t in meta.get("tags", [])]
            if isinstance(meta.get("tags"), list)
            else [],
        )
    except (ValidationError, ValueError, TypeError):
        return None


class HonchoMemoryBackend(MemoryStore):
    """SQLite-backed memory with an optional Honcho mirror/retrieval layer."""

    def __init__(self, db_path: str, *, client: HonchoClient | None) -> None:
        super().__init__(db_path)
        self._honcho = client

    @property
    def honcho_enabled(self) -> bool:
        return self._honcho is not None

    async def store(self, item: MemoryItem) -> MemoryItem:
        stored = await super().store(item)
        if self._honcho is not None:
            try:
                await self._honcho.add_message(peer=stored.agent_id, content=stored.content)
            except Exception:  # noqa: BLE001 - mirror never blocks durable write
                log.warning("honcho.mirror_failed", agent_id=stored.agent_id)
        return stored

    async def search_text(
        self, text: str, *, agent_id: str | None = None, limit: int = 10
    ) -> list[MemoryItem]:
        if self._honcho is not None and agent_id is not None:
            try:
                results = await self._honcho.search(peer=agent_id, query=text, limit=limit)
                items = [item for raw in results if (item := _item_from_honcho(raw, agent_id))]
                if items:
                    return items
            except Exception:  # noqa: BLE001 - fall back to the source of truth
                log.warning("honcho.search_failed", agent_id=agent_id)
        return await super().search_text(text, agent_id=agent_id, limit=limit)

    async def honcho_ready(self) -> bool:
        if self._honcho is None:
            return True
        try:
            return await self._honcho.ready()
        except Exception:  # noqa: BLE001 - readiness is a boolean
            return False


__all__ = ["HonchoMemoryBackend"]
