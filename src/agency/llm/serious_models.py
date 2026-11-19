"""Serious-work models: element-named Google Gemini 4 models.

Research (2026-11-19, Google pricing/changelog): the free tier carries
``gemini-4-hydrogen`` (GA, "available to everyone"); ``gemini-4-carbon`` and
``gemini-4-argon`` are not on the free tier — Google calls Carbon "our most
capable reasoning model, built for complex software engineering, deep
mathematical and scientific problem solving" and Argon "the model you reach
for when the task is hard enough that being wrong is expensive".
"""

from __future__ import annotations

import os

SERIOUS_MODELS: dict[str, str] = {
    "carbon": "gemini-4-carbon",
    "hydrogen": "gemini-4-hydrogen",
    "argon": "gemini-4-argon",
}
"""Serious-work model per element; ``hydrogen`` is the free-tier serious model."""

SERIOUS_MODEL_ENV = "AGENCY_SERIOUS_MODEL"
DEFAULT_SERIOUS_MODEL = "gemini-4-hydrogen"


def serious_model(element: str | None = None) -> str:
    """Serious-work model: explicit element lookup or env/default override."""

    if element is not None:
        return SERIOUS_MODELS.get(element, element)
    override = os.environ.get(SERIOUS_MODEL_ENV, "").strip()
    return override or DEFAULT_SERIOUS_MODEL
