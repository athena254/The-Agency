"""Per-user beta model budget guard (SPEC_L2_TELEGRAM_BETA contract line 25).

Small, dependency-free, in-process sliding-window request budget keyed only
by positive admitted Telegram user ID, with an injectable monotonic clock
for deterministic tests.

Scope notes (read before wiring):

- This component is NOT authorization by itself. It must sit *behind* the
  invite-only admission preflight (nonempty positive user-ID allowlist,
  private chat, fail-closed beta mode). Later handler integration owns that
  wiring and calls :meth:`BetaRateLimiter.allow` before any model use.
- State is in-process only and does not survive process restart. A restart
  resets every user's budget.
- State growth is bounded: expired user entries are evicted before every
  new call, and at most ``max_users`` users are tracked (default 1000;
  least-recently-seen user is dropped when full).
- No connection to config metadata or user-supplied chat text: the only
  input is the numeric user ID. Invalid identities (``0``, negatives,
  bools, non-ints) are denied without consuming budget or creating state.

Spec trace: rate/budget caps per invited identity are required by
``docs/SPEC_L2_TELEGRAM_BETA.md`` ("Contract, scopes and failures") and
tracked as ``docs/BETA_GAPS.md`` B-07. (B-08 there covers L2 sandbox
isolation, not rate limits.)
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable

RATE_LIMIT_EXCEEDED = "Rate limit reached. Please try again later."

_DEFAULT_MAX_USERS = 1000


class BetaRateLimiter:
    """Bounded sliding-window request budget keyed by Telegram user ID."""

    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        *,
        max_users: int = _DEFAULT_MAX_USERS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if type(max_requests) is not int:
            raise TypeError(f"max_requests must be an int, got {type(max_requests).__name__}")
        if max_requests <= 0:
            raise ValueError(f"max_requests must be positive, got {max_requests}")
        if isinstance(window_seconds, bool) or not isinstance(window_seconds, (int, float)):
            raise TypeError(f"window_seconds must be a number, got {type(window_seconds).__name__}")
        window = float(window_seconds)
        if not math.isfinite(window) or window <= 0:
            raise ValueError(f"window_seconds must be a finite positive duration, got {window!r}")
        if type(max_users) is not int:
            raise TypeError(f"max_users must be an int, got {type(max_users).__name__}")
        if max_users <= 0:
            raise ValueError(f"max_users must be positive, got {max_users}")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be a callable returning monotonic seconds")
        self._max_requests = max_requests
        self._window_seconds = window
        self._max_users = max_users
        self._clock: Callable[[], float] = clock if clock is not None else time.monotonic
        self._hits: dict[int, deque[float]] = {}
        self._last_seen: float | None = None

    @property
    def max_requests(self) -> int:
        return self._max_requests

    @property
    def window_seconds(self) -> float:
        return self._window_seconds

    @property
    def max_users(self) -> int:
        return self._max_users

    def __len__(self) -> int:
        return len(self._hits)

    def __contains__(self, user_id: object) -> bool:
        return user_id in self._hits

    def _evict_expired(self, now: float) -> None:
        """Drop expired timestamps and users with no live budget use."""
        cutoff = now - self._window_seconds
        for uid, stamps in list(self._hits.items()):
            live = deque((t for t in stamps if cutoff < t <= now), maxlen=self._max_requests)
            if live:
                self._hits[uid] = live
            else:
                del self._hits[uid]

    def allow(self, user_id: int) -> bool:
        """Consume one request of ``user_id``'s budget if any remains.

        Returns False for invalid identities and when the per-user cap is
        exhausted, without consuming another token in either case.
        """
        if type(user_id) is not int or user_id <= 0:
            return False
        now = self._clock()
        if isinstance(now, bool) or not isinstance(now, (int, float)):
            return False
        moment = float(now)
        if not math.isfinite(moment):
            return False
        # Clamp to the latest seen instant so a backwards clock step can
        # never grant extra budget; the limiter's view of time only moves
        # forward (real time.monotonic already guarantees this).
        if self._last_seen is not None and moment < self._last_seen:
            moment = self._last_seen
        else:
            self._last_seen = moment
        self._evict_expired(moment)
        stamps = self._hits.get(user_id)
        if stamps is not None:
            if len(stamps) >= self._max_requests:
                del self._hits[user_id]
                self._hits[user_id] = stamps
                return False
            stamps.append(moment)
            del self._hits[user_id]
            self._hits[user_id] = stamps
            return True
        if len(self._hits) >= self._max_users:
            oldest = next(iter(self._hits))
            del self._hits[oldest]
        self._hits[user_id] = deque([moment], maxlen=self._max_requests)
        return True
