"""Shared pytest fixtures for the Lattice test suite."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from agency.lattice.config import reset_config
from agency.lattice.models import LatticeConfig


@pytest.fixture
def lattice_config() -> LatticeConfig:
    """A default SQLite-backed config pointing at an in-memory database."""

    return LatticeConfig(backend="sqlite", sqlite_path=":memory:")


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Isolate config file + env lookups per test for a clean state."""

    for name in list(os.environ):
        if name == "LATTICE_BACKEND" or name.startswith("LATTICE_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    reset_config()
    yield
    reset_config()


@pytest.fixture
def sample_config_dict() -> dict:
    """Nested ``config/lattice.yaml`` layout per SPEC_LATTICE.md section 8."""

    return {
        "lattice": {
            "backend": "sqlite",
            "sqlite": {"path": ":memory:"},
            "vectors": {"model": "sentence-transformers/all-MiniLM-L6-v2", "dimension": 384},
            "events": {"retention_days": 365, "prune_enabled": False},
            "governance": {
                "default_quorum": 0.66,
                "proposal_ttl_seconds": 3600,
                "reputation_window": 20,
            },
        }
    }
