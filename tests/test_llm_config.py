"""Resolution-order tests for :func:`agency.llm.config.load_config`.

The repository ships ``AGENCY_``-prefixed LLM settings (``.env`` and
``agency.config.settings``), so ``load_config`` must honour them as a fallback
when the unprefixed ``LLM_*`` variables are absent. These tests are hermetic:
they never read or write the real ``.env``.
"""

from __future__ import annotations

import pytest

from agency.llm.config import ProviderKind, load_config

#: Every env var name ``load_config`` / ``detect_provider`` consults.
_ALL_LLM_ENV_VARS = (
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MAX_TOKENS",
    "LLM_TEMPERATURE",
    "LLM_TIMEOUT",
    "LLM_TIMEOUT_S",
    "LLM_PROXY",
    "AGENCY_LLM_PROVIDER",
    "AGENCY_LLM_MODEL",
    "AGENCY_LLM_BASE_URL",
    "AGENCY_LLM_API_KEY",
    "AGENCY_LLM_MAX_TOKENS",
    "AGENCY_LLM_TEMPERATURE",
    "AGENCY_LLM_TIMEOUT",
    "AGENCY_LLM_PROXY",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_BASE_URL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "OPENROUTER_BASE_URL",
    "OLLAMA_HOST",
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
    "LOCAL_LLM_URL",
    "LOCAL_LLM_MODEL",
    "INCEPTION_API_KEY",
    "INCEPTION_LABS_API_KEY",
    "INCEPTION_BASE_URL",
    "INCEPTION_LABS_BASE_URL",
    "INCEPTION_API_BASE_URL",
    "INCEPTION_MODEL",
    "INCEPTION_LABS_MODEL",
    "MERCURY_MODEL",
    "AGENCY_OPENAI_API_KEY",
    "AGENCY_OPENAI_MODEL",
    "AGENCY_OPENAI_BASE_URL",
    "AGENCY_ANTHROPIC_API_KEY",
    "AGENCY_ANTHROPIC_MODEL",
    "AGENCY_ANTHROPIC_BASE_URL",
    "AGENCY_OPENROUTER_API_KEY",
    "AGENCY_OPENROUTER_MODEL",
    "AGENCY_OPENROUTER_BASE_URL",
    "AGENCY_OLLAMA_HOST",
    "AGENCY_OLLAMA_BASE_URL",
    "AGENCY_OLLAMA_MODEL",
    "AGENCY_LOCAL_LLM_URL",
    "AGENCY_LOCAL_LLM_MODEL",
    "AGENCY_INCEPTION_API_KEY",
    "AGENCY_INCEPTION_LABS_API_KEY",
    "AGENCY_INCEPTION_BASE_URL",
    "AGENCY_INCEPTION_LABS_BASE_URL",
    "AGENCY_INCEPTION_API_BASE_URL",
    "AGENCY_INCEPTION_MODEL",
    "AGENCY_INCEPTION_LABS_MODEL",
    "AGENCY_MERCURY_MODEL",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blank every LLM env var so the developer's ``.env`` cannot leak in.

    ``load_dotenv=False`` throughout: the repository root ``.env`` must never be
    parsed by these tests, and the values under test are set explicitly.
    """
    for name in _ALL_LLM_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# ------------------------------------------------------------------ #
# (a) AGENCY_LLM_* is honoured when no unprefixed LLM_* is set
# ------------------------------------------------------------------ #


def test_agency_prefixed_vars_resolve_without_echo_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENCY_LLM_PROVIDER", "openai")
    monkeypatch.setenv("AGENCY_LLM_MODEL", "agency-model")
    monkeypatch.setenv("AGENCY_LLM_BASE_URL", "https://agency.example/v1")
    monkeypatch.setenv("AGENCY_LLM_API_KEY", "agency-key")

    config = load_config(load_dotenv=False)

    assert config.provider is ProviderKind.OPENAI
    assert config.model == "agency-model"
    assert config.base_url == "https://agency.example/v1"
    assert config.api_key is not None
    assert config.api_key.get_secret_value() == "agency-key"
    assert config.provider is not ProviderKind.ECHO


def test_agency_key_alone_avoids_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    """A prefixed credential alone must be enough to escape the echo default."""
    monkeypatch.setenv("AGENCY_LLM_API_KEY", "agency-only-key")

    config = load_config(load_dotenv=False)

    assert config.provider is not ProviderKind.ECHO
    assert config.api_key is not None
    assert config.api_key.get_secret_value() == "agency-only-key"


def test_agency_prefixed_vars_drive_provider_specific_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prefixed spelling reaches the provider-specific fallback branches."""
    monkeypatch.setenv("AGENCY_LLM_PROVIDER", "openai")
    monkeypatch.setenv("AGENCY_OPENAI_API_KEY", "prefixed-provider-key")

    config = load_config(load_dotenv=False)

    assert config.provider is ProviderKind.OPENAI
    assert config.api_key is not None
    assert config.api_key.get_secret_value() == "prefixed-provider-key"


# ------------------------------------------------------------------ #
# (b) unprefixed LLM_* still wins over AGENCY_LLM_*
# ------------------------------------------------------------------ #


def test_unprefixed_llm_vars_win_over_prefixed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "bare-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://bare.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "bare-key")
    monkeypatch.setenv("AGENCY_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("AGENCY_LLM_MODEL", "prefixed-model")
    monkeypatch.setenv("AGENCY_LLM_BASE_URL", "https://prefixed.example/v1")
    monkeypatch.setenv("AGENCY_LLM_API_KEY", "prefixed-key")

    config = load_config(load_dotenv=False)

    assert config.provider is ProviderKind.OPENAI
    assert config.model == "bare-model"
    assert config.base_url == "https://bare.example/v1"
    assert config.api_key is not None
    assert config.api_key.get_secret_value() == "bare-key"


def test_prefixed_value_used_only_when_bare_var_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A per-field fallback: bare provider, prefixed model."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("AGENCY_LLM_MODEL", "prefixed-model")

    config = load_config(load_dotenv=False)

    assert config.provider is ProviderKind.OPENAI
    assert config.model == "prefixed-model"


# ------------------------------------------------------------------ #
# (c) explicit arguments win over both spellings
# ------------------------------------------------------------------ #


def test_explicit_args_win_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    monkeypatch.setenv("AGENCY_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("AGENCY_LLM_MODEL", "prefixed-model")

    config = load_config(provider="anthropic", model="explicit-model", load_dotenv=False)

    assert config.provider is ProviderKind.ANTHROPIC
    assert config.model == "explicit-model"


def test_explicit_echo_provider_still_yields_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    """``provider='echo'`` must stay echo even when credentials are present."""
    monkeypatch.setenv("AGENCY_LLM_PROVIDER", "openai")
    monkeypatch.setenv("AGENCY_LLM_API_KEY", "prefixed-key")

    config = load_config(provider="echo", load_dotenv=False)

    assert config.provider is ProviderKind.ECHO
    assert config.model == "echo"


# ------------------------------------------------------------------ #
# (d) echo fallback remains when nothing is configured
# ------------------------------------------------------------------ #


def test_echo_fallback_only_when_no_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(load_dotenv=False)

    assert config.provider is ProviderKind.ECHO
    assert config.model == "echo"
    assert config.api_key is None


@pytest.mark.parametrize(
    ("prefixed_var", "expected_kind"),
    [
        ("AGENCY_OPENAI_API_KEY", ProviderKind.OPENAI),
        ("AGENCY_ANTHROPIC_API_KEY", ProviderKind.ANTHROPIC),
        ("AGENCY_OPENROUTER_API_KEY", ProviderKind.OPENROUTER),
        ("AGENCY_INCEPTION_API_KEY", ProviderKind.INCEPTION),
    ],
)
def test_detect_provider_honours_prefixed_credentials(
    monkeypatch: pytest.MonkeyPatch, prefixed_var: str, expected_kind: ProviderKind
) -> None:
    """Credential auto-detection must not report echo when AGENCY_* keys exist."""
    monkeypatch.setenv(prefixed_var, "prefixed-detect-key")

    assert load_config(load_dotenv=False).provider is expected_kind
