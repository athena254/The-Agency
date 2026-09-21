"""OpenClaw bridge (HTTP agent API).

Submits tasks via ``POST {base_url}/api/agent`` with a bearer token, then
polls ``GET {base_url}/api/agent/{run_id}`` until the run reaches a terminal
state (``completed`` / ``failed`` / ``cancelled``) or the timeout expires.
Streaming replays result deltas by long-polling the run-status endpoint.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from time import perf_counter
from typing import Any

import httpx
from pydantic import Field, SecretStr

from agency.bridges.base import Bridge, BridgeConfig, BridgeResult, BridgeStatus

_SUBMIT_PATH = "/api/agent"
_TERMINAL_STATES = {"completed", "failed", "cancelled", "timeout"}


class OpenClawConfig(BridgeConfig):
    """Configuration for :class:`OpenClawBridge`."""

    name: str = Field(default="openclaw")
    base_url: str = Field(description="OpenClaw server URL, e.g. http://localhost:8787.")
    token: SecretStr | None = Field(default=None, description="Defaults to OPENCLAW_TOKEN.")
    poll_interval_s: float = Field(default=1.0, gt=0)
    agent_id: str | None = Field(default=None, description="Target agent when server hosts many.")


class OpenClawBridge(Bridge):
    """HTTP API bridge to an OpenClaw agent server."""

    def __init__(
        self,
        config: OpenClawConfig | None = None,
        client: httpx.AsyncClient | None = None,
        **kwargs: Any,
    ) -> None:
        if config is None:
            config = OpenClawConfig(**kwargs)
        super().__init__(config)
        self._cfg: OpenClawConfig = self._config  # type: ignore[assignment]
        self._client = client
        self._owns_client = client is None

    # ------------------------------------------------------------------ #
    # Bridge interface
    # ------------------------------------------------------------------ #

    async def execute(self, task: str, context: dict[str, Any] | None = None) -> BridgeResult:
        """Submit ``task`` and poll until a terminal state is reached."""
        start = perf_counter()
        try:
            client = await self._get_client()
            run_id = await self._submit(client, task, context)
            final = await self._poll_until_done(client, run_id, start)
        except TimeoutError as exc:
            return BridgeResult.failure(
                str(exc), status=BridgeStatus.TIMEOUT, duration_s=perf_counter() - start
            )
        except httpx.HTTPError as exc:
            return BridgeResult.failure(
                f"openclaw transport error: {exc}",
                status=BridgeStatus.UNAVAILABLE,
                duration_s=perf_counter() - start,
            )
        except (ValueError, KeyError) as exc:
            return BridgeResult.failure(
                f"openclaw protocol error: {exc}", duration_s=perf_counter() - start
            )
        duration = perf_counter() - start
        state = str(final.get("status", "")).lower()
        if state == "completed":
            return BridgeResult(
                output=str(final.get("output", "") or ""),
                artifacts=self._artifacts_of(final),
                status=BridgeStatus.SUCCESS,
                duration_s=duration,
            )
        return BridgeResult.failure(
            str(final.get("error") or f"openclaw run {state or 'failed'}"),
            status=BridgeStatus.FAILED,
            duration_s=duration,
            artifacts=self._artifacts_of(final),
        )

    async def stream(
        self, task: str, context: dict[str, Any] | None = None
    ) -> AsyncGenerator[str, None]:
        """Yield incremental ``output`` deltas by polling the run endpoint."""
        client = await self._get_client()
        run_id = await self._submit(client, task, context)
        seen = 0
        start = perf_counter()
        while True:
            if perf_counter() - start > self._cfg.timeout_s:
                raise TimeoutError(f"openclaw run {run_id} timed out")
            status = await self._get_status(client, run_id)
            output = str(status.get("output", "") or "")
            if len(output) > seen:
                yield output[seen:]
                seen = len(output)
            if str(status.get("status", "")).lower() in _TERMINAL_STATES:
                return
            await asyncio.sleep(self._cfg.poll_interval_s)

    def capabilities(self) -> dict[str, Any]:
        return {
            "name": "openclaw",
            "streaming": True,
            "tools": ["browser", "shell", "workflow"],
            "modalities": ["text", "code", "web"],
            "transport": "http-polling",
            "notes": "POST /api/agent then poll /api/agent/{run_id}.",
        }

    async def health_check(self) -> bool:
        """Probe ``GET {base_url}/api/health`` (falls back to ``/api/agent``)."""
        try:
            client = await self._get_client()
            for path in ("/api/health", "/api/agent"):
                response = await client.get(path, timeout=10.0)
                if response.status_code < 500:
                    return response.status_code < 400 or response.status_code == 405
            return False
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        """Close the owned HTTP client (no-op for injected clients)."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            import os

            token = self._cfg.token.get_secret_value() if self._cfg.token else os.environ.get(
                "OPENCLAW_TOKEN", ""
            )
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            else:
                self._log.warning("bridge.missing_auth", env_var="OPENCLAW_TOKEN")
            self._client = httpx.AsyncClient(
                base_url=self._cfg.base_url.rstrip("/"), headers=headers
            )
        return self._client

    def _submit_payload(self, task: str, context: dict[str, Any] | None) -> dict[str, Any]:
        payload: dict[str, Any] = {"task": self._build_prompt(task, context)}
        if self._cfg.agent_id:
            payload["agent_id"] = self._cfg.agent_id
        if context:
            payload["context"] = {k: v for k, v in context.items() if k != "system"}
        return payload

    async def _submit(
        self, client: httpx.AsyncClient, task: str, context: dict[str, Any] | None
    ) -> str:
        last_error = "unknown error"
        for attempt in range(self._cfg.max_retries + 1):
            try:
                response = await client.post(
                    _SUBMIT_PATH,
                    json=self._submit_payload(task, context),
                    timeout=self._cfg.timeout_s,
                )
                response.raise_for_status()
                data = response.json()
                run_id = data.get("run_id") or data.get("id")
                if not run_id:
                    raise ValueError(f"submit response missing run_id: {data!r}")
                self._log.info("bridge.submitted", bridge="openclaw", run_id=run_id)
                return str(run_id)
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                last_error = str(exc)
                if attempt < self._cfg.max_retries:
                    await asyncio.sleep(self._cfg.backoff_for(attempt))
        raise httpx.HTTPError(f"openclaw submit failed after retries: {last_error}")

    async def _get_status(self, client: httpx.AsyncClient, run_id: str) -> dict[str, Any]:
        response = await client.get(f"{_SUBMIT_PATH}/{run_id}", timeout=30.0)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError(f"unexpected status payload: {data!r}")
        return data

    async def _poll_until_done(
        self, client: httpx.AsyncClient, run_id: str, start: float
    ) -> dict[str, Any]:
        while True:
            if perf_counter() - start > self._cfg.timeout_s:
                raise TimeoutError(f"openclaw run {run_id} timed out after {self._cfg.timeout_s}s")
            status = await self._get_status(client, run_id)
            if str(status.get("status", "")).lower() in _TERMINAL_STATES:
                return status
            await asyncio.sleep(self._cfg.poll_interval_s)

    def _artifacts_of(self, final: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": final.get("run_id") or final.get("id"),
            "status": final.get("status"),
            "files": final.get("files", []),
            "tool_calls": final.get("tool_calls", []),
        }
