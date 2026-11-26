"""Regression: filter keys are data, never SQL text (JSON path injection)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agency.lattice.backends.sqlite import SQLiteLattice
from agency.lattice.models import NodeType


@pytest.fixture()
def lattice(tmp_path: Path) -> SQLiteLattice:
    return SQLiteLattice(str(tmp_path / "lattice.db"))


def test_filter_keys_are_bound_not_interpolated(lattice: SQLiteLattice) -> None:
    async def run() -> None:
        await lattice.initialize()
        await lattice.create_node(next(iter(NodeType)), {"safe": "yes"}, actor="test")
        assert len(await lattice.find_nodes(filters={"safe": "yes"})) == 1
        assert len(await lattice.find_nodes(filters={"missing": "yes"})) == 0

        # Before the fix this key rewrote the WHERE clause and raised a
        # binding-count error (or could match rows that did not have the key).
        injected = "x') OR 1=1 --"
        assert len(await lattice.find_nodes(filters={injected: "anything"})) == 0
        # IS NULL on a missing path is true by design; the point is that the
        # key stays a bound value and the query does not change shape.
        assert len(await lattice.find_nodes(filters={injected: None})) == 1
        await lattice.close()

    asyncio.run(run())
