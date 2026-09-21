"""Tiered memory lifecycle for the Sovereign Mind System.

The engine is the policy layer on top of :class:`MemoryStore`. It decides which
of the five tiers an item belongs in based on how long ago it was last
accessed:

===========  ===================================
Tier         Maximum idle age
===========  ===================================
``HOT``      accessed within 1 day
``WARM``     accessed within 3 days
``NORMAL``   accessed within 7 days
``COOL``     accessed within 30 days
``COLD``     older than 30 days (archived)
===========  ===================================
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta

import structlog

from agency.memory.sms.models import MemoryItem, MemoryTier, utc_now
from agency.memory.sms.store import MemoryStore

logger = structlog.get_logger(__name__)

DEFAULT_SCAN_INTERVAL = 3600.0
"""Seconds between automatic aging passes (~1 hour)."""

TIER_MAX_AGE: dict[MemoryTier, timedelta] = {
    MemoryTier.HOT: timedelta(days=1),
    MemoryTier.WARM: timedelta(days=3),
    MemoryTier.NORMAL: timedelta(days=7),
    MemoryTier.COOL: timedelta(days=30),
}
"""Upper bound on idle age for each non-archived tier."""


def tier_for_age(age: timedelta) -> MemoryTier:
    """Return the tier an item of ``age`` belongs in (HOT for age <= 1 day)."""

    if age <= TIER_MAX_AGE[MemoryTier.HOT]:
        return MemoryTier.HOT
    if age <= TIER_MAX_AGE[MemoryTier.WARM]:
        return MemoryTier.WARM
    if age <= TIER_MAX_AGE[MemoryTier.NORMAL]:
        return MemoryTier.NORMAL
    if age <= TIER_MAX_AGE[MemoryTier.COOL]:
        return MemoryTier.COOL
    return MemoryTier.COLD


class TieredMemoryEngine:
    """Promote, demote and auto-age items across the five SMS tiers."""

    def __init__(
        self,
        store: MemoryStore,
        *,
        scan_interval: float = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        self._store = store
        self._scan_interval = scan_interval
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def scan_interval(self) -> float:
        return self._scan_interval

    async def promote(self, item_id: str) -> MemoryItem | None:
        """Move an item one tier hotter. No-op at HOT; ``None`` if missing."""

        item = await self._store.get(item_id)
        if item is None:
            logger.warning("tiered_engine.promote.missing", item_id=item_id)
            return None
        warmer = item.tier.warmer()
        if warmer is None:
            logger.debug("tiered_engine.promote.at_max", item_id=item_id)
            return item
        updated = await self._store.update_tier(item_id, warmer)
        logger.info("tiered_engine.promoted", item_id=item_id, tier=warmer.value)
        return updated

    async def demote(self, item_id: str) -> MemoryItem | None:
        """Move an item one tier colder. No-op at COLD; ``None`` if missing."""

        item = await self._store.get(item_id)
        if item is None:
            logger.warning("tiered_engine.demote.missing", item_id=item_id)
            return None
        colder = item.tier.colder()
        if colder is None:
            logger.debug("tiered_engine.demote.at_min", item_id=item_id)
            return item
        updated = await self._store.update_tier(item_id, colder)
        logger.info("tiered_engine.demoted", item_id=item_id, tier=colder.value)
        return updated

    async def auto_age(self) -> int:
        """Reconcile every item's tier with its idle age.

        Computes the *target* tier from ``now - accessed_at`` and applies it
        only when the target is colder than the current tier, so recent access
        (or an explicit :meth:`promote`) is never undone by an aging pass.
        Returns the number of items moved.
        """

        now = utc_now()
        items = await self._store.list_all()
        moved = 0
        for item in items:
            age = now - item.accessed_at
            age = max(age, timedelta(0))
            target = tier_for_age(age)
            if target.rank < item.tier.rank:
                await self._store.update_tier(item.id, target)
                moved += 1
        logger.info("tiered_engine.auto_age", scanned=len(items), moved=moved)
        return moved

    def start_periodic(self, interval: float | None = None) -> asyncio.Task[None]:
        """Start the background aging loop, returning its task."""

        if self._task is not None and not self._task.done():
            return self._task
        self._stop.clear()
        self._task = asyncio.create_task(self._run(interval or self._scan_interval))
        logger.info("tiered_engine.periodic_started", interval=interval or self._scan_interval)
        return self._task

    async def stop_periodic(self) -> None:
        """Stop the background aging loop. Safe to call when not running."""

        self._stop.set()
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        logger.info("tiered_engine.periodic_stopped")

    async def _run(self, interval: float) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                try:
                    await self.auto_age()
                except Exception:  # pragma: no cover - defensive background loop
                    logger.exception("tiered_engine.auto_age_failed")
