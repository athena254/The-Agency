"""Tests for SMS memory embeddings (Lattice free embedding model, KISS path)."""

from __future__ import annotations

from agency.memory.sms.embedder import SentenceTransformerEmbedder, default_embedder
from agency.memory.sms.models import MemoryItem
from agency.memory.sms.retrieval import RetrievalEngine
from agency.memory.sms.store import MemoryStore

_VECTORS: dict[str, list[float]] = {
    "alpha": [1.0, 0.0],
    "beta": [0.0, 1.0],
    "gamma": [1.0, 1.0],
}


class _StubEmbedder:
    """Deterministic stub: fixed 2-D vectors per known text."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, text: str) -> list[float]:
        self.calls += 1
        return list(_VECTORS.get(text, [0.0, 0.0]))


async def _store_items(store: MemoryStore, *contents: str) -> None:
    for content in contents:
        await store.store(MemoryItem(agent_id="tester", content=content))


async def test_search_vectors_ranks_by_cosine_similarity() -> None:
    store = MemoryStore(":memory:", embedder=_StubEmbedder())
    await _store_items(store, "alpha", "beta", "gamma")
    results = await store.search_vectors([1.0, 0.0], limit=3)
    assert [item.content for item in results] == ["alpha", "gamma", "beta"]


async def test_search_vectors_scopes_by_agent_id() -> None:
    store = MemoryStore(":memory:", embedder=_StubEmbedder())
    await store.store(MemoryItem(agent_id="alice", content="alpha"))
    await store.store(MemoryItem(agent_id="bob", content="beta"))
    results = await store.search_vectors([1.0, 0.0], agent_id="alice", limit=10)
    assert [item.agent_id for item in results] == ["alice"]


async def test_search_vectors_without_embeddings_is_empty() -> None:
    store = MemoryStore(":memory:")
    await _store_items(store, "alpha")
    assert await store.search_vectors([1.0, 0.0]) == []


async def test_semantic_search_prefers_embeddings_when_available() -> None:
    stub = _StubEmbedder()
    store = MemoryStore(":memory:", embedder=stub)
    await _store_items(store, "alpha", "beta")
    results = await RetrievalEngine(store).semantic_search("alpha")
    assert results[:1] and results[0].content == "alpha"
    assert stub.calls > 0


async def test_semantic_search_falls_back_to_fts_without_embedder() -> None:
    store = MemoryStore(":memory:")
    await _store_items(store, "alpha", "beta")
    results = await RetrievalEngine(store).semantic_search("alpha")
    assert any(item.content == "alpha" for item in results)


def test_default_embedder_present_only_when_package_installed() -> None:
    try:
        import sentence_transformers  # noqa: F401

        installed = True
    except ImportError:
        installed = False
    embedder = default_embedder()
    if installed:
        assert isinstance(embedder, SentenceTransformerEmbedder)
    else:
        assert embedder is None
