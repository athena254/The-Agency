"""Default-deny dependency for HTTP routers without a peer-grant verifier.

This is deliberately not an owner-token switch. It must only be replaced by
an authenticated, independently validated peer execution grant and a scoped
read policy; a Telegram user or a single coordinator cannot bypass it.
"""

from __future__ import annotations

from fastapi import HTTPException, status


def require_peer_authority() -> None:
    """Refuse a sensitive HTTP route until peer authorization is implemented."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Peer authorization is unavailable.",
    )
