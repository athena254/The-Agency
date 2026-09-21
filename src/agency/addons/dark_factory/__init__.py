"""Dark Factory — Python-focused code generation through a full SDLC."""

from __future__ import annotations

from agency.addons.dark_factory.absorption import AbsorptionResult, RepoAbsorber
from agency.addons.dark_factory.factory import DarkFactory
from agency.addons.dark_factory.sdlc import SDLCArtifact, SDLCPhase, SDLCWorkflow

__all__ = [
    "AbsorptionResult",
    "DarkFactory",
    "RepoAbsorber",
    "SDLCArtifact",
    "SDLCPhase",
    "SDLCWorkflow",
]
