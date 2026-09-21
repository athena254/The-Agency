"""Agency runtime settings.

Configuration is loaded from (in increasing order of precedence):

1. Built-in defaults defined on :class:`AgencySettings`.
2. The ``.env`` file at the project root (loaded via :mod:`dotenv`).
3. Real environment variables, which always win over ``.env`` values.

All variables use the ``AGENCY_`` prefix, e.g. ``AGENCY_LLM_PROVIDER``.
Empty values (``KEY=``) are treated as unset so built-in defaults apply.
Secret values are never written to logs — use :func:`mask_secret` when a
key-adjacent value must appear in diagnostics.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlparse

import structlog
from dotenv import load_dotenv, set_key
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = structlog.get_logger(__name__)

ENV_PREFIX: str = "AGENCY_"
ENV_FILE_NAME: str = ".env"

#: Providers recognised by name. Unknown names are still accepted (custom
#: OpenAI-compatible endpoints) as long as they are well-formed, but anything
#: outside this set will not get provider-specific key-format checks.
SUPPORTED_PROVIDERS: frozenset[str] = frozenset(
    {
        "openai",
        "anthropic",
        "google",
        "gemini",
        "mistral",
        "ollama",
        "azure",
        "cohere",
        "groq",
        "together",
        "deepseek",
        "xai",
        "perplexity",
        "openrouter",
    }
)

#: Providers that can operate without an API key (local runtimes).
KEYLESS_PROVIDERS: frozenset[str] = frozenset({"ollama"})

VALID_LOG_LEVELS: frozenset[str] = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


def mask_secret(value: str | None, *, keep: int = 4) -> str:
    """Return a log/display-safe rendering of a secret.

    Shows only the last ``keep`` characters, e.g. ``"•••abcd"``.
    Empty or missing values render as ``"<unset>"`` — never the value itself.
    """
    if not value:
        return "<unset>"
    if len(value) <= keep:
        return "•••"
    return f"•••{value[-keep:]}"


def resolve_env_file(explicit: str | Path | None = None) -> Path:
    """Resolve which ``.env`` file to read/write.

    Order: explicit argument, ``AGENCY_ENV_FILE`` env var, ``.env`` in the
    current directory or any parent, falling back to ``./.env``.
    """
    if explicit is not None:
        return Path(explicit).expanduser()
    override = os.environ.get(f"{ENV_PREFIX}ENV_FILE")
    if override:
        return Path(override).expanduser()
    candidate = Path.cwd() / ENV_FILE_NAME
    for parent in (Path.cwd(), *Path.cwd().parents):
        probe = parent / ENV_FILE_NAME
        if probe.is_file():
            return probe
    return candidate


class AgencySettings(BaseSettings):
    """Runtime configuration for The Agency.

    Attributes mirror ``AGENCY_*`` environment variables. Construct via
    :meth:`load` so the ``.env`` file is honoured; direct construction reads
    only defaults + process environment.
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=ENV_FILE_NAME,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    llm_provider: str = Field(default="openai", description="LLM provider name.")
    llm_model: str = Field(default="gpt-4o-mini", description="Model identifier.")
    llm_api_key: str | None = Field(default=None, description="API key for llm_provider.")
    llm_base_url: str | None = Field(default=None, description="Custom API base URL.")

    butler_host: str = Field(default="127.0.0.1", description="API server bind host.")
    butler_port: int = Field(default=8000, description="API server bind port.")

    memory_db_path: str = Field(default="data/memory.db", description="Memory SQLite path.")
    evidence_db_path: str = Field(default="data/evidence.db", description="Evidence SQLite path.")
    audit_db_path: str = Field(default="data/audit.db", description="Audit SQLite path.")

    log_level: str = Field(default="INFO", description="Logging verbosity.")

    @model_validator(mode="before")
    @classmethod
    def _drop_empty_strings(cls, data: Any) -> Any:
        """Treat empty-string values as unset so field defaults apply.

        This keeps a freshly scaffolded ``.env`` (``KEY=`` with no value)
        usable: blanks fall back to defaults instead of crashing parsing
        (e.g. ``butler_port``) or silently blanking required fields.
        """
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v != ""}
        return data

    # ------------------------------------------------------------------ #
    # Loading / persistence
    # ------------------------------------------------------------------ #

    @classmethod
    def load(cls, env_file: str | Path | None = None) -> AgencySettings:
        """Load settings from ``.env`` + environment.

        ``python-dotenv`` populates ``os.environ`` from the ``.env`` file
        *without* overriding real environment variables, so env var
        overrides keep working. Afterwards :class:`AgencySettings` field
        binding applies on top of the merged environment.
        """
        path = resolve_env_file(env_file)
        if path.is_file():
            load_dotenv(dotenv_path=path, override=False, encoding="utf-8")
            log.info("config.settings.loaded", env_file=str(path))
        else:
            log.info("config.settings.no_env_file", env_file=str(path))
        settings = cls()
        log.info(
            "config.settings.active",
            llm_provider=settings.llm_provider,
            llm_model=settings.llm_model,
            llm_api_key=mask_secret(settings.llm_api_key),
            llm_base_url=settings.llm_base_url,
            butler_host=settings.butler_host,
            butler_port=settings.butler_port,
            log_level=settings.log_level,
        )
        return settings

    def save(self, env_file: str | Path | None = None) -> None:
        """Persist current field values back to the ``.env`` file.

        ``None`` values are skipped so unset optionals do not clobber
        provider keys managed by :class:`APIKeyManager`. Secret values are
        written to disk but never logged — only their masked form appears.
        """
        path = resolve_env_file(env_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.touch()
        values: dict[str, str | None] = {
            "AGENCY_LLM_PROVIDER": self.llm_provider,
            "AGENCY_LLM_MODEL": self.llm_model,
            "AGENCY_LLM_API_KEY": self.llm_api_key,
            "AGENCY_LLM_BASE_URL": self.llm_base_url,
            "AGENCY_BUTLER_HOST": self.butler_host,
            "AGENCY_BUTLER_PORT": str(self.butler_port),
            "AGENCY_MEMORY_DB_PATH": self.memory_db_path,
            "AGENCY_EVIDENCE_DB_PATH": self.evidence_db_path,
            "AGENCY_AUDIT_DB_PATH": self.audit_db_path,
            "AGENCY_LOG_LEVEL": self.log_level.upper(),
        }
        for name, value in values.items():
            if value is None:
                continue
            set_key(str(path), name, value)
        log.info(
            "config.settings.saved",
            env_file=str(path),
            llm_api_key=mask_secret(self.llm_api_key),
        )

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    def validate(self) -> list[str]:  # type: ignore[override]
        """Check configuration coherence; return a list of problems.

        An empty list means the settings are valid. This performs cheap
        local checks only — no network calls, no key-validity probes.

        Note: the name intentionally shadows pydantic's deprecated
        ``BaseModel.validate`` classmethod; the ``type: ignore`` above
        records that as deliberate (the spec'd ``validate()`` API wins).
        """
        errors: list[str] = []

        provider = (self.llm_provider or "").strip().lower()
        if not provider:
            errors.append("llm_provider must not be empty.")
        elif provider not in SUPPORTED_PROVIDERS:
            log.info("config.settings.unknown_provider", llm_provider=self.llm_provider)
            errors.append(
                f"llm_provider {self.llm_provider!r} is not in the supported set: "
                f"{sorted(SUPPORTED_PROVIDERS)}."
            )

        if not (self.llm_model or "").strip():
            errors.append("llm_model must not be empty.")

        if provider and provider not in KEYLESS_PROVIDERS and not (self.llm_api_key or "").strip():
            errors.append(
                f"llm_api_key is required when llm_provider is {self.llm_provider!r} "
                "(set AGENCY_LLM_API_KEY or run "
                f"`agency config set-key {self.llm_provider} <key>`)."
            )

        if self.llm_base_url:
            parsed = urlparse(self.llm_base_url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                errors.append(f"llm_base_url {self.llm_base_url!r} must be an http(s) URL.")

        if not (self.butler_host or "").strip():
            errors.append("butler_host must not be empty.")
        if not 1 <= self.butler_port <= 65535:
            errors.append(f"butler_port {self.butler_port} must be within 1-65535.")

        for field_name in ("memory_db_path", "evidence_db_path", "audit_db_path"):
            if not (getattr(self, field_name) or "").strip():
                errors.append(f"{field_name} must not be empty.")

        if self.log_level.upper() not in VALID_LOG_LEVELS:
            errors.append(
                f"log_level {self.log_level!r} is invalid; "
                f"expected one of {sorted(VALID_LOG_LEVELS)}."
            )

        if errors:
            log.info("config.settings.invalid", error_count=len(errors))
        else:
            log.info("config.settings.valid")
        return errors
