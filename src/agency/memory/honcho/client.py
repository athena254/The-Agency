"""Honcho client boundary.

The adapter never imports Honcho server code: it talks to a separate Honcho
service over HTTP through the pinned ``honcho-ai`` SDK. All SDK calls run in a
worker thread so a slow service cannot block the Agency event loop.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

import structlog

log = structlog.get_logger(__name__)


@runtime_checkable
class HonchoClient(Protocol):
    """Minimal operations the memory mirror needs from Honcho."""

    async def add_message(self, *, peer: str, content: str) -> None: ...

    async def search(self, *, peer: str, query: str, limit: int) -> list[dict[str, Any]]: ...

    async def ready(self) -> bool: ...


class SDKHonchoClient:
    """Thin adapter over a pinned ``honcho-ai`` SDK instance."""

    def __init__(self, *, base_url: str, api_key: str, workspace: str, timeout: float) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._workspace = workspace
        self._timeout = timeout
        self._client: Any | None = None
        self._session: Any | None = None

    def _sdk(self) -> Any:
        if self._client is None:
            from honcho import Honcho  # type: ignore[import-not-found]

            self._client = Honcho(
                workspace_id=self._workspace,
                api_key=self._api_key,
                base_url=self._base_url,
            )
            self._session = self._client.session("agency")
        return self._client

    async def _call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args, **kwargs), timeout=self._timeout)

    async def add_message(self, *, peer: str, content: str) -> None:
        client = self._sdk()
        session = self._session
        if session is None:
            raise RuntimeError("honcho session is not initialized")
        peer_obj = await self._call(client.peer, peer)
        message = await self._call(peer_obj.message, content)
        await self._call(session.add_messages, [message])

    async def search(self, *, peer: str, query: str, limit: int) -> list[dict[str, Any]]:
        client = self._sdk()
        peer_obj = await self._call(client.peer, peer)
        results = await self._call(peer_obj.search, query, limit=limit)
        out: list[dict[str, Any]] = []
        for item in results or []:
            if isinstance(item, dict):
                out.append(item)
            else:
                out.append(
                    {
                        "id": getattr(item, "id", None),
                        "content": getattr(item, "content", getattr(item, "message", "")),
                        "metadata": getattr(item, "metadata", {}),
                    }
                )
        return out

    async def ready(self) -> bool:
        try:
            self._sdk()
            return True
        except Exception:  # noqa: BLE001 - readiness must be a boolean
            log.warning("honcho.client.not_ready", workspace=self._workspace)
            return False


__all__ = ["HonchoClient", "SDKHonchoClient"]
