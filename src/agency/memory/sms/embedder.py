"""Embedding support for SMS memory using the Lattice free embedding model.

The free embedding model is ``sentence-transformers/all-MiniLM-L6-v2`` — the
Lattice default (``lattice/models.py:283``, ``config/lattice.yaml:16``;
overridable via ``LATTICE_VECTOR_MODEL``). ``sentence-transformers`` is an
optional dependency: :func:`default_embedder` returns ``None`` when it is not
installed, and callers (``MemoryStore``, ``RetrievalEngine``) fall back to FTS.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

Embedder = Callable[[str], Awaitable[list[float]]]
"""Async text → vector mapping used for SMS memory embeddings."""

DEFAULT_VECTOR_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
"""Matches the Lattice ``vector_model`` default (``lattice/models.py:283``)."""


def default_vector_model() -> str:
    """The embedding model name: Lattice default, ``LATTICE_VECTOR_MODEL`` overridable."""

    return os.environ.get("LATTICE_VECTOR_MODEL", "").strip() or DEFAULT_VECTOR_MODEL


class SentenceTransformerEmbedder:
    """Lazy ``sentence-transformers`` embedder using the Lattice free embedding model."""

    def __init__(self, model_name: str | None = None) -> None:
        self._model_name = model_name or default_vector_model()
        self._model: Any = None

    def _load(self) -> None:
        # Optional dependency (extra "memory"); default_embedder() gates on its presence.
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

        self._model = SentenceTransformer(self._model_name)

    def _embed(self, text: str) -> list[float]:
        vector = self._model.encode(text, normalize_embeddings=True)
        return [float(value) for value in vector]

    async def __call__(self, text: str) -> list[float]:
        if self._model is None:
            await asyncio.to_thread(self._load)
        return await asyncio.to_thread(self._embed, text)


def default_embedder() -> SentenceTransformerEmbedder | None:
    """Return the SMS embedder, or ``None`` when ``sentence-transformers`` is missing."""

    try:
        import sentence_transformers  # noqa: F401  # type: ignore[import-not-found]
    except ImportError:
        return None
    return SentenceTransformerEmbedder()
