"""Per-user beta model budget guard (offline, deterministic).

Covers SPEC_L2_TELEGRAM_BETA contract line 25 ("rate/budget caps for each
invited identity ... before external model use") and BETA_GAPS B-07. Wiring
into the Telegram handler happens in a later integration slice; this module
has no Telegram/network side effects.
"""

from __future__ import annotations

from typing import Any

import pytest


class FakeClock:
    """Injectable monotonic clock for deterministic tests."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _beta_config(**over: Any) -> Any:
    from agency.telegram.config import TelegramConfig

    args: dict[str, Any] = {
        "bot_token": "test",
        "beta_mode": True,
        "allowed_user_ids": [101, 202],
    }
    args.update(over)
    return TelegramConfig(**args)


def test_first_request_allowed() -> None:
    from agency.telegram.rate_limit import BetaRateLimiter

    clock = FakeClock()
    limiter = BetaRateLimiter(max_requests=3, window_seconds=60.0, clock=clock)
    assert limiter.allow(101) is True


def test_exact_limit_allowed_then_denied() -> None:
    from agency.telegram.rate_limit import BetaRateLimiter

    clock = FakeClock()
    limiter = BetaRateLimiter(max_requests=3, window_seconds=60.0, clock=clock)
    assert limiter.allow(101) is True
    assert limiter.allow(101) is True
    assert limiter.allow(101) is True
    assert limiter.allow(101) is False
    # stays denied without time passing
    assert limiter.allow(101) is False


def test_reset_after_window() -> None:
    from agency.telegram.rate_limit import BetaRateLimiter

    clock = FakeClock()
    limiter = BetaRateLimiter(max_requests=2, window_seconds=60.0, clock=clock)
    assert limiter.allow(101) is True
    assert limiter.allow(101) is True
    assert limiter.allow(101) is False
    clock.advance(60.0)
    assert limiter.allow(101) is True


def test_independent_identities() -> None:
    from agency.telegram.rate_limit import BetaRateLimiter

    clock = FakeClock()
    limiter = BetaRateLimiter(max_requests=1, window_seconds=60.0, clock=clock)
    assert limiter.allow(101) is True
    assert limiter.allow(101) is False
    assert limiter.allow(202) is True
    assert limiter.allow(202) is False


def test_invalid_identities_denied_without_state() -> None:
    from agency.telegram.rate_limit import BetaRateLimiter

    clock = FakeClock()
    limiter = BetaRateLimiter(max_requests=2, window_seconds=60.0, clock=clock)
    for bad in (0, -5, True, False, "101", 101.5, None, (101,)):
        assert limiter.allow(bad) is False  # type: ignore[arg-type]
    assert len(limiter) == 0


def test_constructor_rejects_invalid_caps() -> None:
    from agency.telegram.rate_limit import BetaRateLimiter

    for bad in (0, -1, True, False, "3", 3.5, None):
        with pytest.raises((ValueError, TypeError)):
            BetaRateLimiter(max_requests=bad, window_seconds=60.0)  # type: ignore[arg-type]


def test_constructor_rejects_invalid_durations() -> None:
    from agency.telegram.rate_limit import BetaRateLimiter

    for bad in (0, -1.0, True, False, "60", None, float("inf"), float("nan")):
        with pytest.raises((ValueError, TypeError)):
            BetaRateLimiter(max_requests=3, window_seconds=bad)  # type: ignore[arg-type]


def test_stale_id_cleanup() -> None:
    from agency.telegram.rate_limit import BetaRateLimiter

    clock = FakeClock()
    limiter = BetaRateLimiter(max_requests=2, window_seconds=60.0, clock=clock)
    assert limiter.allow(101) is True
    assert len(limiter) == 1
    clock.advance(61.0)
    assert limiter.allow(202) is True
    # expired user 101 must have been evicted before applying the new call
    assert 101 not in limiter
    assert 202 in limiter


def test_no_unbounded_growth_on_expired_ids() -> None:
    from agency.telegram.rate_limit import BetaRateLimiter

    clock = FakeClock()
    limiter = BetaRateLimiter(max_requests=2, window_seconds=60.0, clock=clock)
    for user_id in range(1, 1001):
        assert limiter.allow(user_id) is True
        clock.advance(61.0)
    assert len(limiter) <= limiter.max_users
    assert len(limiter) <= 10


def test_monotonic_clock_boundary_and_repeats() -> None:
    from agency.telegram.rate_limit import BetaRateLimiter

    clock = FakeClock()
    limiter = BetaRateLimiter(max_requests=2, window_seconds=60.0, clock=clock)
    # repeated requests at the same instant consume the budget, never error
    assert limiter.allow(101) is True
    assert limiter.allow(101) is True
    assert limiter.allow(101) is False
    # clock stepping backwards must not crash and must not grant extra budget
    clock.now -= 30.0
    assert limiter.allow(101) is False
    clock.now += 90.0
    assert limiter.allow(101) is True


def test_user_facing_response_constant() -> None:
    from agency.telegram import rate_limit

    assert isinstance(rate_limit.RATE_LIMIT_EXCEEDED, str)
    assert rate_limit.RATE_LIMIT_EXCEEDED.strip() != ""
    lowered = rate_limit.RATE_LIMIT_EXCEEDED.lower()
    assert "try again" in lowered or "later" in lowered
    assert "token" not in lowered
