"""Agent reputation scoring (Lattice Brief 3 / SPEC_LATTICE.md section 7.2).

Reputation blends task outcomes (60%) with peer ratings (40%) over a
rolling window. Butler and the human user always vote at weight 1.0;
all other agents vote at their reputation score.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from agency.lattice.backends.base import LatticeBackend
from agency.lattice.models import Reputation, utc_now

#: Number of peer ratings kept per agent (SPEC_LATTICE.md section 8).
REPUTATION_WINDOW = 20

#: Voters that always carry full weight regardless of reputation.
TRUSTED_VOTERS: frozenset[str] = frozenset({"butler", "user"})

log = structlog.get_logger(__name__)


class ReputationEngine:
    """Agent reputation scoring based on task history and peer ratings."""

    def __init__(self, lattice: LatticeBackend) -> None:
        self.lattice = lattice
        self._reputations: dict[str, Reputation] = {}
        self._lock = asyncio.Lock()

    async def record_task_completion(
        self,
        agent_id: str,
        success: bool,
        peer_rating: float | None = None,
    ) -> Reputation:
        """Record a task outcome and return the updated reputation.

        Increments the success/failure counter, optionally folds in a
        peer rating, recomputes the score, and best-effort mirrors the
        update to the lattice backend.
        """
        if not agent_id:
            raise ValueError("agent_id must not be empty.")
        if peer_rating is not None and not 0.0 <= peer_rating <= 1.0:
            raise ValueError(f"peer_rating {peer_rating} must be within 0.0-1.0.")
        async with self._lock:
            rep = self._stored_or_baseline(agent_id)
            ratings = list(rep.peer_ratings)
            if peer_rating is not None:
                ratings.append(float(peer_rating))
                ratings = ratings[-REPUTATION_WINDOW:]
            updated = Reputation(
                agent_id=agent_id,
                score=0.5,  # placeholder; recomputed below
                tasks_completed=rep.tasks_completed + (1 if success else 0),
                tasks_failed=rep.tasks_failed + (0 if success else 1),
                peer_ratings=ratings,
                last_updated=utc_now(),
            )
            updated.score = self.compute_score(updated)
            self._reputations[agent_id] = updated
        await self._mirror_to_backend(updated)
        return updated

    async def get_reputation(self, agent_id: str) -> Reputation:
        """Return current reputation, or a neutral 0.5 baseline if unknown."""
        if not agent_id:
            raise ValueError("agent_id must not be empty.")
        async with self._lock:
            rep = self._reputations.get(agent_id)
            if rep is None:
                return Reputation(agent_id=agent_id)
            return Reputation(
                agent_id=rep.agent_id,
                score=rep.score,
                tasks_completed=rep.tasks_completed,
                tasks_failed=rep.tasks_failed,
                peer_ratings=list(rep.peer_ratings),
                last_updated=rep.last_updated,
            )

    async def get_leaderboard(self, limit: int = 20) -> list[Reputation]:
        """Return the top agents by reputation score, highest first."""
        if limit <= 0:
            raise ValueError("limit must be > 0.")
        async with self._lock:
            ranked = sorted(
                self._reputations.values(), key=lambda r: r.score, reverse=True
            )
            return list(ranked[:limit])

    @staticmethod
    def compute_score(rep: Reputation) -> float:
        """Compute a 0.0-1.0 score: 60% task success + 40% peer average.

        Agents with no history get the neutral 0.5 baseline. When only
        one signal exists, that signal alone determines the score.
        """
        total = rep.tasks_completed + rep.tasks_failed
        window = rep.peer_ratings[-REPUTATION_WINDOW:]
        if total == 0 and not window:
            return 0.5
        if total == 0:
            return sum(window) / len(window)
        success_rate = rep.tasks_completed / total
        if not window:
            return success_rate
        peer_avg = sum(window) / len(window)
        return 0.6 * success_rate + 0.4 * peer_avg

    async def vote_weight(self, voter_id: str) -> float:
        """Return vote weight: 1.0 for butler/user, reputation otherwise."""
        if not voter_id:
            raise ValueError("voter_id must not be empty.")
        if voter_id.strip().lower() in TRUSTED_VOTERS:
            return 1.0
        rep = await self.get_reputation(voter_id)
        return rep.score

    async def submit_peer_rating(
        self,
        rater_id: str,
        ratee_id: str,
        rating: float,
        context: str | None = None,
    ) -> bool:
        """Record one agent's rating of another agent's work."""
        if not rater_id:
            raise ValueError("rater_id must not be empty.")
        if not ratee_id:
            raise ValueError("ratee_id must not be empty.")
        if not 0.0 <= rating <= 1.0:
            raise ValueError(f"rating {rating} must be within 0.0-1.0.")
        async with self._lock:
            rep = self._stored_or_baseline(ratee_id)
            ratings = [*rep.peer_ratings, float(rating)][-REPUTATION_WINDOW:]
            updated = Reputation(
                agent_id=ratee_id,
                score=0.5,  # recomputed below
                tasks_completed=rep.tasks_completed,
                tasks_failed=rep.tasks_failed,
                peer_ratings=ratings,
                last_updated=utc_now(),
            )
            updated.score = self.compute_score(updated)
            self._reputations[ratee_id] = updated
        await self._mirror_to_backend(updated, extra={"rater_id": rater_id, "context": context})
        return True

    # -- internals ---------------------------------------------------- #

    def _stored_or_baseline(self, agent_id: str) -> Reputation:
        rep = self._reputations.get(agent_id)
        if rep is None:
            return Reputation(agent_id=agent_id)
        return rep

    async def _mirror_to_backend(
        self, rep: Reputation, extra: dict[str, Any] | None = None
    ) -> None:
        """Best-effort mirror of reputation state to the lattice backend.

        Never raises: the in-memory store is the source of truth and the
        backend may not have a matching agent node (or any backend at all
        in tests with stub backends).
        """
        update = getattr(self.lattice, "update_node", None)
        if update is None:
            return
        payload: dict[str, Any] = {
            "reputation_score": rep.score,
            "tasks_completed": rep.tasks_completed,
            "tasks_failed": rep.tasks_failed,
            "peer_ratings": list(rep.peer_ratings[-REPUTATION_WINDOW:]),
        }
        if extra:
            payload.update({k: v for k, v in extra.items() if v is not None})
        try:
            await update(rep.agent_id, payload, actor="governance")
        except Exception as exc:  # noqa: BLE001 - best-effort backend mirror
            log.debug("lattice.reputation.mirror_skipped", error=str(exc))


__all__ = ["REPUTATION_WINDOW", "TRUSTED_VOTERS", "ReputationEngine"]
