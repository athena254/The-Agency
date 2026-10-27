"""Agent Factory v1 — governed identity creation (bounded slice)."""

from agency.factory.compose import ComposedPrompt, compose_system_prompt
from agency.factory.prompts import (
    PromptKind,
    PromptRegistry,
    PromptSpec,
    PromptStatus,
)
from agency.factory.service import AgentFactory, Blueprint, BlueprintStatus

__all__ = [
    "AgentFactory",
    "Blueprint",
    "BlueprintStatus",
    "ComposedPrompt",
    "PromptKind",
    "PromptRegistry",
    "PromptSpec",
    "PromptStatus",
    "compose_system_prompt",
]
