"""Guard the bounded review retry delay (regression for ticket 85)."""

from __future__ import annotations

from agency.forge import reviewer


def test_review_retry_delay_is_bounded() -> None:
    # A 60 s sleep on each of two retries cost ~120 s of pure waiting on a
    # flaky review. Keep the delay small; keep the retry count unchanged.
    assert reviewer.REVIEW_RETRY_DELAY_S <= 5.0
    assert reviewer.REVIEW_MAX_PROVIDER_RETRIES == 2
