"""Per-provider LLM API key management.

Keys are persisted in the ``.env`` file as ``AGENCY_<PROVIDER>_API_KEY``
(e.g. ``AGENCY_OPENAI_API_KEY``) and are also honoured from real environment
variables, which take precedence over the file.

Format validation here is purely syntactic (prefix / length / charset) — it
never performs network calls and never proves a key is live.

Security: key material is never emitted to logs, exceptions, or ``repr``.
Only :func:`agency.config.settings.mask_secret` renderings leave this module.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import structlog
from dotenv import dotenv_values, load_dotenv, set_key, unset_key

from agency.config.settings import ENV_PREFIX, mask_secret, resolve_env_file

log = structlog.get_logger(__name__)

#: Generic settings key owned by :class:`AgencySettings`, not a provider key.
_GENERIC_API_KEY_VAR: str = f"{ENV_PREFIX}LLM_API_KEY"

_KEY_VAR_PATTERN: re.Pattern[str] = re.compile(r"^AGENCY_([A-Z0-9_]+)_API_KEY$")

#: Minimum length accepted for providers without a known prefix.
MIN_GENERIC_KEY_LENGTH: int = 8

#: provider -> (accepted prefixes, minimum length). Empty prefixes means
#: "any printable value of sufficient length".
_KEY_FORMATS: dict[str, tuple[tuple[str, ...], int]] = {
    "openai": (("sk-",), 20),
    "anthropic": (("sk-ant-",), 20),
    "google": (("AIza",), 20),
    "gemini": (("AIza",), 20),
    "mistral": ((), 20),
    "azure": ((), 20),
    "cohere": ((), 20),
    "groq": (("gsk_",), 20),
    "together": ((), 20),
    "deepseek": (("sk-",), 20),
    "xai": (("xai-",), 20),
    "perplexity": (("pplx-",), 20),
    "openrouter": (("sk-or-",), 20),
    "ollama": ((), 0),  # Local runtime; a key is optional.
}


def normalize_provider(provider: str) -> str:
    """Canonicalise a provider name (``" OpenAI "`` -> ``"openai"``)."""
    return provider.strip().lower()


def key_var_name(provider: str) -> str:
    """Map a provider to its ``.env`` / environment variable name."""
    slug = re.sub(r"[^A-Z0-9]+", "_", provider.strip().upper()).strip("_")
    if not slug:
        raise ValueError("provider must not be empty.")
    return f"{ENV_PREFIX}{slug}_API_KEY"


def check_key_format(provider: str, key: str) -> tuple[bool, str]:
    """Syntactic format check; returns ``(ok, reason)``.

    The key value itself never appears in ``reason``.
    """
    candidate = key.strip()
    if not candidate:
        return False, "key is empty."
    name = normalize_provider(provider)
    prefixes, min_len = _KEY_FORMATS.get(name, ((), MIN_GENERIC_KEY_LENGTH))
    if len(candidate) < min_len:
        return False, f"key is too short ({len(candidate)} chars; minimum {min_len})."
    if any(ch.isspace() for ch in candidate):
        return False, "key must not contain whitespace."
    if not candidate.isascii() or not candidate.isprintable():
        return False, "key must be printable ASCII."
    if prefixes and not candidate.startswith(prefixes):
        expected = ", ".join(f"{p!r}" for p in prefixes)
        return False, f"key does not start with an expected prefix ({expected})."
    return True, "format ok."


class APIKeyManager:
    """Read/write LLM API keys backed by the ``.env`` file + environment."""

    def __init__(self, env_file: str | Path | None = None) -> None:
        self._env_file: Path = resolve_env_file(env_file)

    @property
    def env_file(self) -> Path:
        """The ``.env`` file this manager persists to."""
        return self._env_file

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _ensure_loaded(self) -> None:
        """Merge ``.env`` values into ``os.environ`` without overrides."""
        if self._env_file.is_file():
            load_dotenv(dotenv_path=self._env_file, override=False, encoding="utf-8")

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def set_key(self, provider: str, key: str) -> None:
        """Store ``key`` for ``provider`` in the ``.env`` file.

        Raises:
            ValueError: if the provider name is empty, the key is empty, or
                the key fails the syntactic format check.
        """
        name = normalize_provider(provider)
        if not name:
            raise ValueError("provider must not be empty.")
        var = key_var_name(name)
        candidate = key.strip()
        if not candidate:
            raise ValueError(f"key for provider {name!r} must not be empty.")
        ok, reason = check_key_format(name, candidate)
        if not ok:
            raise ValueError(f"refusing to store key for provider {name!r}: {reason}")
        self._env_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._env_file.exists():
            self._env_file.touch()
        set_key(str(self._env_file), var, candidate)
        # Mirror into the live process so reads in this session observe it.
        # A real environment variable set outside this process still wins on
        # the next fresh interpreter start (file values never override env).
        os.environ[var] = candidate
        log.info(
            "config.keys.set",
            provider=name,
            var=var,
            env_file=str(self._env_file),
            key=mask_secret(candidate),
        )

    def get_key(self, provider: str) -> str | None:
        """Return the stored key for ``provider``, or ``None`` if unset.

        Real environment variables take precedence over ``.env`` values.
        Callers must treat the return value as secret: never log or print it.
        """
        name = normalize_provider(provider)
        if not name:
            raise ValueError("provider must not be empty.")
        self._ensure_loaded()
        return os.environ.get(key_var_name(name))

    def list_keys(self) -> list[str]:
        """Return sorted provider names that have a key configured.

        Only provider names are returned — never key values.
        """
        self._ensure_loaded()
        found: set[str] = set()
        sources: list[dict[str, str | None]] = [dict(os.environ)]
        if self._env_file.is_file():
            sources.append(dotenv_values(str(self._env_file)))
        for source in sources:
            for var in source:
                if var == _GENERIC_API_KEY_VAR:
                    continue  # Owned by AgencySettings, not a provider key.
                match = _KEY_VAR_PATTERN.match(var)
                if match and source.get(var):
                    found.add(match.group(1).lower())
        providers = sorted(found)
        log.info("config.keys.list", count=len(providers), providers=providers)
        return providers

    def remove_key(self, provider: str) -> bool:
        """Delete the stored key for ``provider``; ``True`` if one existed."""
        name = normalize_provider(provider)
        if not name:
            raise ValueError("provider must not be empty.")
        var = key_var_name(name)
        existed = False
        if self._env_file.is_file():
            # unset_key returns (success, key); coerce to a real bool.
            success, _ = unset_key(str(self._env_file), var)
            existed = existed or bool(success)
        if var in os.environ:
            del os.environ[var]
            existed = True
        log.info("config.keys.removed", provider=name, var=var, existed=existed)
        return existed

    def validate_key(self, provider: str) -> bool:
        """Check the stored key's *format* (not its live validity).

        Returns ``False`` when no key is configured or the value fails the
        syntactic check. Providers that do not require a key (e.g. ollama)
        validate as ``True`` even when unset.
        """
        name = normalize_provider(provider)
        if not name:
            raise ValueError("provider must not be empty.")
        key = self.get_key(name)
        if not key:
            # Keyless local runtimes are valid without a key.
            valid = name in ("ollama",)
            log.info(
                "config.keys.validated",
                provider=name,
                valid=valid,
                reason="keyless provider" if valid else "no key configured",
            )
            return valid
        ok, reason = check_key_format(name, key)
        log.info(
            "config.keys.validated",
            provider=name,
            valid=ok,
            reason=reason,
            key=mask_secret(key),
        )
        return ok
