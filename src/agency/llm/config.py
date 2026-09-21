"""Validated LLM configuration loaded from ``.env`` / environment variables.

Supports multiple named providers (``claude``, ``gpt``, ``local``, ...)
via prefixed env vars, e.g. ``CLAUDE_MODEL`` / ``GPT_API_KEY``, as well as
the well-known provider keys (``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``,
``OPENROUTER_API_KEY``, ``OLLAMA_HOST``).
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field, SecretStr

logger = structlog.get_logger(__name__)


class ProviderKind(str, Enum):
    """Supported LLM provider backends."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OPENROUTER = "openrouter"
    OLLAMA = "ollama"
    LOCAL = "local"
    ECHO = "echo"

    def __str__(self) -> str:
        return self.value


class LLMConfig(BaseModel):
    """Validated configuration for a single LLM provider.

    This is a Pydantic v2 model (frozen dataclass semantics with validation).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: ProviderKind = Field(description="Backend provider to use.")
    model: str = Field(description="Model identifier for the provider.")
    api_key: SecretStr | None = Field(default=None, repr=False, description="API key, if required.")
    base_url: str | None = Field(
        default=None, description="Custom endpoint (proxies, Ollama, local servers)."
    )
    max_tokens: int = Field(default=1024, gt=0, description="Max completion tokens.")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="Sampling temperature.")
    timeout: float = Field(default=60.0, gt=0, description="Per-request timeout in seconds.")

    def redacted(self) -> dict[str, Any]:
        """Return config as a dict with the API key masked."""
        data = self.model_dump()
        data["api_key"] = "***" if self.api_key is not None else None
        return data


# --------------------------------------------------------------------------- #
# Defaults per provider
# --------------------------------------------------------------------------- #

_DEFAULT_MODELS: dict[ProviderKind, str] = {
    ProviderKind.OPENAI: "gpt-4o-mini",
    ProviderKind.ANTHROPIC: "claude-sonnet-4-20250514",
    ProviderKind.OPENROUTER: "openrouter/auto",
    ProviderKind.OLLAMA: "llama3.1",
    ProviderKind.LOCAL: "local-model",
    ProviderKind.ECHO: "echo",
}

# Named presets: ``load_named_provider("claude")`` etc.
_NAMED_PRESETS: dict[str, tuple[ProviderKind, str]] = {
    "claude": (ProviderKind.ANTHROPIC, "claude-sonnet-4-20250514"),
    "claude-opus": (ProviderKind.ANTHROPIC, "claude-opus-4-20250514"),
    "claude-haiku": (ProviderKind.ANTHROPIC, "claude-haiku-3-5-20241022"),
    "gpt": (ProviderKind.OPENAI, "gpt-4o-mini"),
    "gpt4": (ProviderKind.OPENAI, "gpt-4o"),
    "gpt35": (ProviderKind.OPENAI, "gpt-3.5-turbo"),
    "openrouter": (ProviderKind.OPENROUTER, "openrouter/auto"),
    "local": (ProviderKind.OLLAMA, "llama3.1"),
    "ollama": (ProviderKind.OLLAMA, "llama3.1"),
    "echo": (ProviderKind.ECHO, "echo"),
}


def _load_dotenv(path: str | Path | None = None) -> None:
    """Minimal ``.env`` loader (no extra dependency).

    Only sets variables that are not already present in the environment.
    Existing env vars always win over the ``.env`` file.
    """
    candidates: list[Path]
    if path is not None:
        candidates = [Path(path)]
    else:
        candidates = [Path.cwd() / ".env", Path(__file__).resolve().parents[3] / ".env"]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            for raw_line in candidate.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError as exc:
            logger.warning("dotenv_read_failed", path=str(candidate), error=str(exc))
            continue
        else:
            logger.debug("dotenv_loaded", path=str(candidate))
            return


def _get(env: dict[str, str], *names: str, default: str | None = None) -> str | None:
    for name in names:
        if env.get(name):
            return env[name]
    return default


def detect_provider(env: dict[str, str] | None = None) -> ProviderKind:
    """Auto-select a provider from available credentials."""
    env = dict(os.environ) if env is None else env
    if _get(env, "OPENAI_API_KEY"):
        return ProviderKind.OPENAI
    if _get(env, "ANTHROPIC_API_KEY"):
        return ProviderKind.ANTHROPIC
    if _get(env, "OPENROUTER_API_KEY"):
        return ProviderKind.OPENROUTER
    if _get(env, "OLLAMA_HOST", "OLLAMA_BASE_URL"):
        return ProviderKind.OLLAMA
    if _get(env, "OPENAI_BASE_URL", "LLM_BASE_URL", "LOCAL_LLM_URL"):
        return ProviderKind.LOCAL
    return ProviderKind.ECHO


def load_config(
    provider: ProviderKind | str | None = None,
    model: str | None = None,
    env_file: str | Path | None = None,
    load_dotenv: bool = True,
) -> LLMConfig:
    """Build an :class:`LLMConfig` from ``.env`` / environment variables.

    Resolution order: explicit args > generic ``LLM_*`` vars > provider
    specific vars > built-in defaults. Falls back to ``echo`` when no
    credentials are found.
    """
    if load_dotenv:
        _load_dotenv(env_file)
    env = dict(os.environ)

    kind: ProviderKind
    if provider is None:
        raw = _get(env, "LLM_PROVIDER")
        kind = ProviderKind(raw.lower()) if raw else detect_provider(env)
    else:
        kind = (
            provider if isinstance(provider, ProviderKind) else ProviderKind(str(provider).lower())
        )

    api_key = _get(env, "LLM_API_KEY")
    base_url = _get(env, "LLM_BASE_URL")
    resolved_model = model or _get(env, "LLM_MODEL")

    if kind is ProviderKind.OPENAI:
        api_key = api_key or _get(env, "OPENAI_API_KEY")
        base_url = base_url or _get(env, "OPENAI_BASE_URL")
        resolved_model = resolved_model or _get(env, "OPENAI_MODEL") or _DEFAULT_MODELS[kind]
    elif kind is ProviderKind.ANTHROPIC:
        api_key = api_key or _get(env, "ANTHROPIC_API_KEY")
        base_url = base_url or _get(env, "ANTHROPIC_BASE_URL")
        resolved_model = resolved_model or _get(env, "ANTHROPIC_MODEL") or _DEFAULT_MODELS[kind]
    elif kind is ProviderKind.OPENROUTER:
        api_key = api_key or _get(env, "OPENROUTER_API_KEY")
        base_url = base_url or _get(env, "OPENROUTER_BASE_URL", "OPENAI_BASE_URL")
        resolved_model = resolved_model or _get(env, "OPENROUTER_MODEL") or _DEFAULT_MODELS[kind]
    elif kind in (ProviderKind.OLLAMA, ProviderKind.LOCAL):
        base_url = base_url or _get(env, "OLLAMA_HOST", "OLLAMA_BASE_URL", "LOCAL_LLM_URL")
        resolved_model = (
            resolved_model or _get(env, "OLLAMA_MODEL", "LOCAL_LLM_MODEL") or _DEFAULT_MODELS[kind]
        )
    else:  # echo
        resolved_model = resolved_model or _DEFAULT_MODELS[ProviderKind.ECHO]

    max_tokens = int(_get(env, "LLM_MAX_TOKENS") or 1024)
    temperature = float(_get(env, "LLM_TEMPERATURE") or 0.7)
    timeout = float(_get(env, "LLM_TIMEOUT", "LLM_TIMEOUT_S") or 60.0)

    config = LLMConfig(
        provider=kind,
        model=resolved_model or _DEFAULT_MODELS[ProviderKind.ECHO],
        api_key=SecretStr(api_key) if api_key else None,
        base_url=base_url,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
    )
    logger.debug("llm_config_loaded", **config.redacted())
    return config


def load_named_provider(
    name: str,
    env_file: str | Path | None = None,
    load_dotenv: bool = True,
    **overrides: Any,
) -> LLMConfig:
    """Load config for a named preset such as ``'claude'``, ``'gpt'`` or ``'local'``.

    Preset-specific env vars take precedence, e.g. for ``name="claude"``:
    ``CLAUDE_API_KEY``, ``CLAUDE_MODEL``, ``CLAUDE_BASE_URL``.
    """
    if load_dotenv:
        _load_dotenv(env_file)
    env = dict(os.environ)
    key = name.strip().lower()
    if key not in _NAMED_PRESETS:
        raise ValueError(f"Unknown provider preset {name!r}. Known: {sorted(_NAMED_PRESETS)}")
    kind, default_model = _NAMED_PRESETS[key]
    prefix = key.replace("-", "_").upper()

    model = str(overrides.pop("model", None) or _get(env, f"{prefix}_MODEL") or default_model)
    api_key = overrides.pop("api_key", None) or _get(env, f"{prefix}_API_KEY")
    base_url = overrides.pop("base_url", None) or _get(env, f"{prefix}_BASE_URL")

    base = load_config(provider=kind, model=model, load_dotenv=False)
    data: dict[str, Any] = base.model_dump()
    if api_key:
        data["api_key"] = SecretStr(str(api_key))
    if base_url:
        data["base_url"] = str(base_url)
    data.update(overrides)
    return LLMConfig(**data)
