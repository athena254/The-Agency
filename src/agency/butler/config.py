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
    BUTLER_DECISION_PROVIDER=jev
    BUTLER_JEV_MODEL=jev-latest
    BUTLER_PERSONA_ENABLED=1
    BUTLER_PERSONA_MAX_OUTPUT=20000
"""

from __future__ import annotations

from pydantic import Field, field_validator
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
    decision_provider: str = Field(
        default="none",
        description="Routing decision-model provider ('none' or 'jev').",
    )
    jev_model: str = Field(default="jev-latest", description="Jev System-1 model name.")
    jev_signal_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Judgment probability that counts as a definite yes (route).",
    )
    jev_uncertain_low: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="Judgment probability below which the judgment is a definite no (defer).",
    )
    persona_enabled: bool = Field(
        default=True,
        description="Frame completed replies through the Butler persona (SOUL/IDENTITY docs).",
    )
    persona_max_output: int = Field(
        default=20000,
        ge=1,
        le=1_000_000,
        description="Hard cap on the framed reply length in chars (adds a truncation marker).",
    )

    @field_validator("decision_provider")
    @classmethod
    def _validate_decision_provider(cls, value: str) -> str:
        """Reject unknown providers; store the canonical lowercase form."""
        normalized = value.strip().lower()
        if normalized not in ("none", "jev"):
            raise ValueError("decision_provider must be one of: none, jev")
        return normalized

    @property
    def llm_enabled(self) -> bool:
        """Return whether an LLM provider is configured."""
        return self.llm_provider.strip().lower() not in ("", "none", "disabled", "off")

    @property
    def decision_enabled(self) -> bool:
        """Return whether a decision model is configured to route first."""
        return self.decision_provider.strip().lower() == "jev"


__all__ = ["ButlerConfig"]
