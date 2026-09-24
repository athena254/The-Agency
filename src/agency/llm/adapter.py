"""LLM adapter — multi-provider support with free-tier defaults."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import structlog
from dotenv import load_dotenv

load_dotenv()

logger = structlog.get_logger(__name__)


def _response_text(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("LLM response content must be text")
    return value


class LLMAdapter:
    """Async LLM adapter with multi-provider support.

    Free-tier defaults (no API key needed):
    - Nous Portal: meituan/longcat-2.0:free, qwen-3.5-plus, mistral-large
    - OpenRouter: Various free models
    - Ollama: Local models

    Priority:
    1. Explicit provider/model from config
    2. Provider from env vars (AGENCY_LLM_PROVIDER)
    3. Auto-detect from available API keys
    4. Fallback to Nous Portal free tier
    """

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        timeout: float = 60.0,
        config: Any = None,
    ) -> None:
        # Compatibility: accept an LLMConfig instance (from agency.llm.config)
        if config is not None:
            self._provider = str(config.provider)
            self._model = config.model
            self._api_key = config.api_key.get_secret_value() if config.api_key else ""
            self._base_url = config.base_url or ""
            self._max_tokens = config.max_tokens
            self._temperature = config.temperature
            self._timeout = config.timeout
        else:
            self._provider = provider or os.environ.get("AGENCY_LLM_PROVIDER", "")
            self._model = model or os.environ.get("AGENCY_LLM_MODEL", "")
            self._api_key = api_key or os.environ.get("AGENCY_LLM_API_KEY", "")
            self._base_url = base_url or os.environ.get("AGENCY_LLM_BASE_URL", "")
            self._max_tokens = max_tokens
            self._temperature = temperature
            self._timeout = timeout

            # Auto-detect provider if not set
            if not self._provider:
                self._provider = self._detect_provider()

            # Auto-detect model if not set
            if not self._model:
                self._model = self._default_model()

        self._log = structlog.get_logger(__name__)
        self._log.info(
            "llm_adapter_initialized",
            provider=self._provider,
            model=self._model,
            has_api_key=bool(self._api_key),
        )

    def _detect_provider(self) -> str:
        """Auto-detect provider from available credentials."""
        if os.environ.get("OPENAI_API_KEY"):
            return "openai"
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic"
        if os.environ.get("OPENROUTER_API_KEY"):
            return "openrouter"
        if os.environ.get("OLLAMA_HOST") or os.environ.get("OLLAMA_BASE_URL"):
            return "ollama"
        # Default to Pollinations — keyless and free
        return "pollinations"

    def _default_model(self) -> str:
        """Get default model for the detected provider."""
        defaults = {
            "nous": "meituan/longcat-2.0:free",
            "openai": "gpt-4o-mini",
            "anthropic": "claude-3-5-haiku-20241022",
            "openrouter": "qwen/qwen3.8-27b:free",
            "pollinations": "openai-fast",
            "ollama": "llama3.1",
            "echo": "echo",
        }
        return defaults.get(self._provider, "openai-fast")

    async def generate(self, prompt: str, context: dict[str, Any] | None = None) -> str:
        """Generate a response from the LLM."""
        ctx = context or {}

        # Override model/provider from context if specified
        model = ctx.get("model", self._model)
        provider = ctx.get("provider", self._provider)

        self._log.debug("llm_generate", provider=provider, model=model)

        try:
            if provider == "nous":
                return await self._nous_generate(prompt, model)
            elif provider == "openai":
                return await self._openai_generate(prompt, model)
            elif provider == "anthropic":
                return await self._anthropic_generate(prompt, model)
            elif provider == "openrouter":
                return await self._openrouter_generate(prompt, model)
            elif provider == "pollinations":
                return await self._pollinations_generate(prompt, model)
            elif provider == "ollama":
                return await self._ollama_generate(prompt, model)
            elif provider == "echo":
                return self._echo_generate(prompt)
            else:
                raise ValueError(f"Unsupported LLM provider: {provider!r}")
        except Exception as e:
            self._log.error("llm_error", provider=provider, model=model, error=str(e))
            # A failed model call is never a successful echo reply, even when
            # the caller requests strict=False. Echo is an explicit provider.
            raise

    async def stream(
        self, prompt: str, context: dict[str, Any] | None = None
    ) -> AsyncIterator[str]:
        """Stream a response from the LLM."""
        response = await self.generate(prompt, context)
        yield response

    async def _nous_generate(self, prompt: str, model: str) -> str:
        """Generate via Nous Portal (free tier available)."""
        import httpx

        # Nous Portal uses OpenAI-compatible API
        base_url = self._base_url or "https://api.nousresearch.com/v1"
        api_key = self._api_key or os.environ.get("NOUS_API_KEY", "")

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": self._max_tokens,
                    "temperature": self._temperature,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        return _response_text(data["choices"][0]["message"]["content"])

    async def _openai_generate(self, prompt: str, model: str) -> str:
        """Generate via OpenAI API."""
        import httpx

        base_url = self._base_url or "https://api.openai.com/v1"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": self._max_tokens,
                    "temperature": self._temperature,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        return _response_text(data["choices"][0]["message"]["content"])

    async def _anthropic_generate(self, prompt: str, model: str) -> str:
        """Generate via Anthropic API."""
        import httpx

        base_url = self._base_url or "https://api.anthropic.com/v1"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{base_url}/messages",
                headers={
                    "x-api-key": self._api_key,
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": model,
                    "max_tokens": self._max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            data = resp.json()

        return _response_text(data["content"][0]["text"])

    async def _openrouter_generate(self, prompt: str, model: str) -> str:
        """Generate via OpenRouter API."""
        import httpx

        base_url = self._base_url or "https://openrouter.ai/api/v1"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": self._max_tokens,
                    "temperature": self._temperature,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        return _response_text(data["choices"][0]["message"]["content"])

    async def _pollinations_generate(self, prompt: str, model: str) -> str:
        """Generate via Pollinations — keyless and free."""
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                "https://text.pollinations.ai/openai",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            data = resp.json()

        # Tolerate shape variations: some responses carry plain text,
        # some omit choices, some nest differently.
        if isinstance(data, str):
            return data
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content
            # Native function-calling models sometimes emit tool_calls
            # with empty content — translate to our JSON protocol so the
            # tool driver understands it.
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                first = tool_calls[0] or {}
                function = first.get("function", {}) if isinstance(first, dict) else {}
                name = function.get("name")
                if isinstance(name, str) and name.strip():
                    raw_args = function.get("arguments", "{}")
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except (TypeError, ValueError):
                        args = {}
                    return json.dumps({"action": {"name": name.strip(), "args": args}})
            # Reasoning-only responses: the model thought but emitted no
            # content. Fall back to the reasoning text — the driver's JSON
            # extractor can usually pull an action/final out of it.
            reasoning = message.get("reasoning")
            if isinstance(reasoning, str) and reasoning.strip():
                return reasoning
        content = data.get("content")
        if isinstance(content, str) and content.strip():
            return content
        raise ValueError(f"unexpected Pollinations response shape: {str(data)[:200]}")

    async def _ollama_generate(self, prompt: str, model: str) -> str:
        import httpx

        base_url = self._base_url or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        return _response_text(data.get("response", ""))

    def _echo_generate(self, prompt: str) -> str:
        """Echo fallback — returns the prompt."""
        return f"Echo: {prompt}"

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return str(self._model)

    @property
    def echo_mode(self) -> bool:
        """True when running in offline echo fallback mode."""
        return self._provider in ("echo", "")

    @property
    def is_free_tier(self) -> bool:
        """Check if using a keyless/free provider."""
        return self._provider == "pollinations" or (
            self._provider in ("openrouter", "nous") and ":free" in self._model
        )
