"""Butler configuration — environment-driven runtime settings.

All values can be supplied via environment variables (prefixed with
``BUTLER_``) or a ``.env`` file in the working directory::

    BUTLER_HOST=127.0.0.1
    BUTLER_PORT=8001
    BUTLER_LLM_PROVIDER=ollama
    BUTLER_LLM_MODEL=llama3.1
    BUTLER_API_KEY_ENV_VAR=LLM_API_KEY
    BUTLER_MAX_MESSAGE_LENGTH=8000
    BUTLER_TIMEOUT=60.0
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ButlerConfig(BaseSettings):
    """Runtime configuration for the Butler service."""

    model_config = SettingsConfigDict(
        env_prefix="BUTLER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = Field(default="127.0.0.1", description="Interface the HTTP server binds to.")
    port: int = Field(default=8001, ge=1, le=65535, description="Port the HTTP server binds to.")
    llm_provider: str = Field(
        default="none",
        description="LLM provider for routing decisions (e.g. 'openai', 'ollama', 'none').",
    )
    llm_model: str = Field(default="", description="Model name used for routing decisions.")
    api_key_env_var: str = Field(
        default="LLM_API_KEY",
        description="Name of the env var holding the LLM API key (value is read lazily).",
    )
    max_message_length: int = Field(
        default=8000, ge=1, le=1_000_000, description="Max accepted message length in chars."
    )
    timeout: float = Field(
        default=60.0, gt=0, description="Per-message execution timeout in seconds."
    )

    @property
    def llm_enabled(self) -> bool:
        """Return whether an LLM provider is configured."""
        return self.llm_provider.strip().lower() not in ("", "none", "disabled", "off")


__all__ = ["ButlerConfig"]
