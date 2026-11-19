"""Agent Factory v1 — governed identity creation (bounded slice)."""

from agency.factory.compose import ComposedPrompt, compose_system_prompt
from agency.factory.imports import (
    ImportedMarkdown,
    ImportError,
    MarkdownImportService,
    SimilarityMatch,
)
from agency.factory.prompts import (
    PromptKind,
    PromptRegistry,
    PromptSpec,
    PromptStatus,
)
from agency.factory.requested_agent import (
    ImprovementReport,
    RequestedAgentRequest,
    RequestedAgentResult,
    RequestedAgentService,
)
from agency.factory.service import AgentFactory, Blueprint, BlueprintStatus

__all__ = [
    "AgentFactory",
    "Blueprint",
    "BlueprintStatus",
    "ComposedPrompt",
    "ImportError",
    "ImportedMarkdown",
    "ImprovementReport",
    "MarkdownImportService",
    "PromptKind",
    "PromptRegistry",
    "PromptSpec",
    "PromptStatus",
    "RequestedAgentRequest",
    "RequestedAgentResult",
    "RequestedAgentService",
    "SimilarityMatch",
    "compose_system_prompt",
]
