"""Tests for wiring the coding agent into API startup."""

from __future__ import annotations

import agency.api.server as server_module


def test_server_exposes_callable_coding_agent_bootstrap_helper() -> None:
    assert hasattr(server_module, "bootstrap_coding_agent")
    assert callable(server_module.bootstrap_coding_agent)
