"""Pydantic models for the Sovereign Mind System (SMS).

These models are the on-the-wire and on-disk contract for every SMS node. They
are intentionally dependency-light: ``store``, ``lifecycle``, ``retrieval`` and
``secrets`` all speak in terms of :class:`MemoryItem`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""

    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Attach UTC to naive datetimes so comparisons never fail."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class MemoryTier(str, Enum):
    """The five tiers of the SMS lifecycle.

    Ordered from coldest to hottest in :data:`TIER_ORDER`. Hotter tiers are
    retrieved more aggressively and compressed less; colder tiers are archived.
    """

    HOT = "hot"
    WARM = "warm"
    NORMAL = "normal"
    COOL = "cool"
    COLD = "cold"

    @property
    def rank(self) -> int:
        """Index into :data:`TIER_ORDER` (higher == hotter)."""

        return TIER_ORDER.index(self)

    def warmer(self) -> MemoryTier | None:
        """Return the next-hotter tier, or ``None`` if already HOT."""

        index = self.rank
        if index + 1 >= len(TIER_ORDER):
            return None
        return TIER_ORDER[index + 1]

    def colder(self) -> MemoryTier | None:
        """Return the next-colder tier, or ``None`` if already COLD."""

        index = self.rank
        if index == 0:
            return None
        return TIER_ORDER[index - 1]


TIER_ORDER: tuple[MemoryTier, ...] = (
    MemoryTier.COLD,
    MemoryTier.COOL,
    MemoryTier.NORMAL,
    MemoryTier.WARM,
    MemoryTier.HOT,
)
"""Tiers from coldest (archived) to hottest (active)."""


class MemoryItem(BaseModel):
    """A single unit of remembered content."""

    model_config = ConfigDict(validate_assignment=True, str_strip_whitespace=True)

    id: str = Field(default_factory=lambda: uuid4().hex)
    agent_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    tier: MemoryTier = MemoryTier.NORMAL
    created_at: datetime = Field(default_factory=utc_now)
    accessed_at: datetime = Field(default_factory=utc_now)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)

    @field_validator("created_at", "accessed_at")
    @classmethod
    def _normalize_datetimes(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("tags")
    @classmethod
    def _normalize_tags(cls, value: list[str]) -> list[str]:
        seen: dict[str, None] = {}
        for tag in value:
            cleaned = tag.strip().lower()
            if cleaned:
                seen.setdefault(cleaned, None)
        return list(seen)


class MemoryQuery(BaseModel):
    """Filter for :meth:`MemoryStore.search`.

    All fields are optional. ``tags`` matches items carrying *any* of the
    supplied tags, ``limit`` bounds the number of rows returned.
    """

    model_config = ConfigDict(validate_assignment=True)

    agent_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    tier: MemoryTier | None = None
    limit: int = Field(default=100, ge=1, le=1000)

    @field_validator("tags")
    @classmethod
    def _normalize_tags(cls, value: list[str]) -> list[str]:
        seen: dict[str, None] = {}
        for tag in value:
            cleaned = tag.strip().lower()
            if cleaned:
                seen.setdefault(cleaned, None)
        return list(seen)
