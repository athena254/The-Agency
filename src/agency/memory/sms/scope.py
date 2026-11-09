"""Principal-bound memory resource scope (R-04).

The legacy memory tools accept a caller/model-selected ``agent_id`` or run an
unscoped full-text search, so a shared runtime cannot prove that one principal
only reaches its own memories. :class:`PrincipalMemoryScope` is the trusted
seam that closes that gap for the owner pilot: it is constructed from an
authenticated :class:`PrincipalScope` and every read or write is SQL-bound to
that principal's memory ``agent_id``.

The scope never accepts a caller-supplied agent id, never falls back to an
unscoped ``list_all``/``search_fts`` and never exposes raw store errors. Legacy
single-user callers that still hold a ``MemoryStore`` directly are unchanged;
this module only adds the bound view used by the owner pilot.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from agency.memory.sms.models import MemoryItem, MemoryTier
from agency.memory.sms.retrieval import build_fts_match, tokenize
from agency.memory.sms.store import MemoryStore

_MAX_LIMIT = 100
_MAX_QUERY_CHARS = 200
_MAX_CONTENT_CHARS = 8000
_MAX_TITLE_CHARS = 200
_TRANSPORTS = ("local", "telegram")


def _identifier(value: str) -> str:
    if type(value) is not str or not value.strip() or len(value) > 128:
        raise ValueError("invalid principal identifier")
    return value.strip()


def _bound_limit(limit: int) -> int:
    if type(limit) is not int or isinstance(limit, bool):
        raise ValueError("invalid limit")
    return max(1, min(limit, _MAX_LIMIT))


@dataclass(frozen=True)
class PrincipalScope:
    """Trusted principal identity, never derived from caller arguments."""

    owner_id: str
    transport: str
    subject_id: str

    def __post_init__(self) -> None:
        _identifier(self.owner_id)
        _identifier(self.subject_id)
        if type(self.transport) is not str or self.transport not in _TRANSPORTS:
            raise ValueError("invalid principal transport")

    @property
    def agent_id(self) -> str:
        """Stable, non-reversible memory agent id bound to this principal."""
        raw = f"{self.transport}\x00{self.owner_id}\x00{self.subject_id}"
        return "principal:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def matches(self, *, owner_id: str, transport: str, subject_id: str) -> bool:
        return (self.owner_id, self.transport, self.subject_id) == (
            owner_id,
            transport,
            subject_id,
        )


class PrincipalMemoryScope:
    """A :class:`MemoryStore` view bound to exactly one :class:`PrincipalScope`.

    Every method is scoped by the principal's memory ``agent_id``. There is no
    method that accepts a caller-selected agent id and no unscoped enumeration.
    """

    def __init__(self, store: MemoryStore, principal: PrincipalScope) -> None:
        if type(store) is not MemoryStore or type(principal) is not PrincipalScope:
            raise TypeError("principal memory scope requires concrete types")
        self._store = store
        self._principal = principal

    @property
    def principal(self) -> PrincipalScope:
        return self._principal

    @property
    def agent_id(self) -> str:
        return self._principal.agent_id

    async def list_memories(self, *, limit: int = 10) -> list[MemoryItem]:
        """List this principal's memories, most recently accessed first."""
        return await self._store.list_by_agent(self.agent_id, _bound_limit(limit))

    async def query(self, text: str, *, limit: int = 10) -> list[MemoryItem]:
        """Full-text search restricted to this principal's memories.

        Falls back to a substring scan over the same principal's rows when FTS
        yields nothing or fails; the fallback never widens the scope.
        """
        bounded = _bound_limit(limit)
        if type(text) is not str or len(text) > _MAX_QUERY_CHARS:
            raise ValueError("invalid memory query")
        match = build_fts_match(text)
        if match:
            try:
                items = await self._store.search_fts(match, agent_id=self.agent_id, limit=bounded)
            except Exception:  # noqa: BLE001 — FTS failure falls back to the bound scan
                items = []
            if items:
                return items
        candidates = await self._store.list_by_agent(self.agent_id, limit=1000)
        tokens = tokenize(text)
        if not tokens and not text.strip():
            return []
        scored: list[tuple[int, MemoryItem]] = []
        for item in candidates:
            haystack = (item.content + " " + " ".join(item.tags)).lower()
            score = sum(1 for token in tokens if token in haystack)
            if score > 0:
                scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], -pair[1].importance))
        return [item for _, item in scored[:bounded]]

    async def write(self, content: str, *, title: str | None = None) -> MemoryItem:
        """Store a memory owned by this principal; caller cannot choose the owner."""
        if type(content) is not str or not content.strip() or len(content) > _MAX_CONTENT_CHARS:
            raise ValueError("invalid memory content")
        text = content.strip()
        if title is not None:
            if type(title) is not str or len(title) > _MAX_TITLE_CHARS:
                raise ValueError("invalid memory title")
            cleaned = title.strip()
            if cleaned:
                text = f"{cleaned}\n{text}"
        return await self._store.store(
            MemoryItem(agent_id=self.agent_id, content=text, tier=MemoryTier.NORMAL)
        )


__all__ = [
    "PrincipalMemoryScope",
    "PrincipalScope",
]
