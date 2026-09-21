"""Codex bridge (OpenAI Chat Completions API).

Executes tasks through the OpenAI ``/v1/chat/completions`` endpoint
(``model`` configurable, e.g. ``gpt-5-codex`` / ``o4-mini``). Streaming uses
SSE; rate limits (HTTP 429) and transient 5xx responses are retried with
exponential backoff. Authentication is via ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from time import perf_counter
from typing import Any

import httpx
from pydantic import Field, SecretStr

from agency.bridges.base import Bridge, BridgeConfig, BridgeResult, BridgeStatus, BridgeUsage

_CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}

# Rough per-1k-token prices keyed by model prefix (input, output) in USD.
_PRICING_PER_1K: dict[str, tuple[float, float]] = {
    "gpt-5": (0.005, 0.015),
    "gpt-4": (0.01, 0.03),
    "o4": (0.005, 0.02),
    "o3": (0.01, 0.04),
}


class CodexConfig(BridgeConfig):
    """Configuration for :class:`CodexBridge`."""

    name: str = Field(default="codex")
    base_url: str = Field(default="https://api.openai.com")
    api_key: SecretStr | None = Field(default=None, description="Defaults to OPENAI_API_KEY.")
    model: str = Field(default="gpt-5-codex")
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, gt=0)
    organization: str | None = Field(default=None)


class CodexBridge(Bridge):
    """OpenAI API-based bridge for Codex-class coding models."""

    def __init__(
        self,
        config: CodexConfig | None = None,
        client: httpx.AsyncClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(config or CodexConfig(**kwargs))
        self._cfg: CodexConfig = self._config  # type: ignore[assignment]
        self._client = client
        self._owns_client = client is None

    # ------------------------------------------------------------------ #
    # Bridge interface
    # ------------------------------------------------------------------ #

    async def execute(self, task: str, context: dict[str, Any] | None = None) -> BridgeResult:
        """POST a chat-completions request and return the assistant message."""
        start = perf_counter()
        messages = self._build_messages(task, context)
        last_error = "unknown error"
        status = BridgeStatus.FAILED
        for attempt in range(self._cfg.max_retries + 1):
            try:
                client = await self._get_client()
                response = await client.post(
                    _CHAT_COMPLETIONS_PATH,
                    json=self._payload(messages, stream=False),
                    timeout=self._cfg.timeout_s,
                )
                self._raise_for_status(response, attempt)
                data = response.json()
                output, usage, artifacts = self._parse_response(data)
                return BridgeResult(
                    output=output,
                    artifacts=artifacts,
                    usage=usage,
                    status=BridgeStatus.SUCCESS,
                    duration_s=perf_counter() - start,
                )
            except httpx.TimeoutException as exc:
                last_error, status = f"codex request timed out: {exc}", BridgeStatus.TIMEOUT
            except httpx.HTTPStatusError as exc:
                last_error = f"codex HTTP {exc.response.status_code}: {exc.response.text[:1000]}"
                status = BridgeStatus.UNAVAILABLE if exc.response.status_code == 429 else (
                    BridgeStatus.FAILED
                )
                if exc.response.status_code not in _RETRYABLE_STATUS:
                    break
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                last_error = f"codex request failed: {exc}"
                status = BridgeStatus.FAILED
            if attempt < self._cfg.max_retries:
                backoff = self._cfg.backoff_for(attempt)
                self._log.warning(
                    "bridge.retry", bridge="codex", attempt=attempt + 1, backoff=backoff
                )
                await asyncio.sleep(backoff)
        return BridgeResult.failure(last_error, status=status, duration_s=perf_counter() - start)

    async def stream(
        self, task: str, context: dict[str, Any] | None = None
    ) -> AsyncGenerator[str, None]:
        """Yield SSE-decoded content deltas from a streaming completion."""
        messages = self._build_messages(task, context)
        client = await self._get_client()
        async with client.stream(
            "POST",
            _CHAT_COMPLETIONS_PATH,
            json=self._payload(messages, stream=True),
            timeout=self._cfg.timeout_s,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                for chunk in self._parse_sse_line(line):
                    yield chunk

    def capabilities(self) -> dict[str, Any]:
        return {
            "name": "codex",
            "streaming": True,
            "tools": ["codegen", "reasoning", "function_calling"],
            "modalities": ["text", "code"],
            "max_context_tokens": 128_000,
            "transport": "https-openai-chat-completions",
            "model": self._cfg.model,
        }

    async def health_check(self) -> bool:
        """Probe ``GET /v1/models`` with the configured credentials."""
        try:
            client = await self._get_client()
            response = await client.get("/v1/models", timeout=10.0)
            return response.status_code == 200
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

            api_key = self._cfg.api_key.get_secret_value() if self._cfg.api_key else os.environ.get(
                "OPENAI_API_KEY", ""
            )
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            else:
                self._log.warning("bridge.missing_auth", env_var="OPENAI_API_KEY")
            if self._cfg.organization:
                headers["OpenAI-Organization"] = self._cfg.organization
            self._client = httpx.AsyncClient(base_url=self._cfg.base_url, headers=headers)
        return self._client

    def _build_messages(self, task: str, context: dict[str, Any] | None) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        system = (context or {}).get("system")
        if system:
            messages.append({"role": "system", "content": str(system)})
        messages.append({"role": "user", "content": self._build_prompt(task, context)})
        return messages

    def _payload(self, messages: list[dict[str, str]], *, stream: bool) -> dict[str, Any]:
        return {
            "model": self._cfg.model,
            "messages": messages,
            "temperature": self._cfg.temperature,
            "max_tokens": self._cfg.max_tokens,
            "stream": stream,
        }

    def _raise_for_status(self, response: httpx.Response, attempt: int) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in _RETRYABLE_STATUS and attempt >= self._cfg.max_retries:
                pass
            raise

    def _parse_sse_line(self, line: str) -> list[str]:
        line = line.strip()
        if not line or line.startswith(":") or line == "[DONE]" :
            return []
        if line.startswith("data:"):
            line = line[len("data:") :].strip()
        if line == "[DONE]" or not line:
            return []
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return []
        try:
            delta = event["choices"][0].get("delta", {})
            content = delta.get("content")
        except (KeyError, IndexError, AttributeError):
            return []
        return [content] if isinstance(content, str) and content else []

    def _parse_response(self, data: dict[str, Any]) -> tuple[str, BridgeUsage, dict[str, Any]]:
        choices = data.get("choices", [])
        if not choices:
            raise ValueError("codex response contained no choices")
        message = choices[0].get("message", {})
        output = str(message.get("content", "") or "")
        raw_usage = data.get("usage", {})
        usage = BridgeUsage(
            input_tokens=int(raw_usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(raw_usage.get("completion_tokens", 0) or 0),
            total_tokens=int(raw_usage.get("total_tokens", 0) or 0),
            estimated_cost_usd=self._estimate_cost(
                int(raw_usage.get("prompt_tokens", 0) or 0),
                int(raw_usage.get("completion_tokens", 0) or 0),
            ),
        )
        artifacts: dict[str, Any] = {
            "model": data.get("model", self._cfg.model),
            "finish_reason": choices[0].get("finish_reason"),
            "response_id": data.get("id"),
            "tool_calls": message.get("tool_calls", []),
        }
        return output, usage, artifacts

    def _estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        for prefix, (inp, out) in _PRICING_PER_1K.items():
            if self._cfg.model.startswith(prefix):
                return (input_tokens / 1000) * inp + (output_tokens / 1000) * out
        return 0.0
