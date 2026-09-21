"""The Agency LLM layer — multi-provider async generation with streaming.

Public surface::

    from agency.llm import LLMAdapter, LLMConfig, LLMRouter
    from agency.llm import EchoProvider

The adapter auto-selects a provider from environment variables and falls
back to echo mode when no key is configured, so tests and offline runs
never require network access.
"""

from __future__ import annotations

from agency.llm.adapter import LLMAdapter
from agency.llm.config import LLMConfig, ProviderKind, load_config, load_named_provider
from agency.llm.router import LLMRouter, TaskKind, classify_task

__all__ = [
    "LLMAdapter",
    "LLMConfig",
    "LLMRouter",
    "ProviderKind",
    "TaskKind",
    "classify_task",
    "load_config",
    "load_named_provider",
]
