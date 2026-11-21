"""Blueprint tool_requirements: declaration, validation, persistence.

Phase 1 of the tool-provisioning plan: tool names are a pure declaration on
the blueprint (no catalog resolution here). Mirrors the capability_ceilings
A1 slice: create keyword, fail-closed validation, tools_json column via the
ALTER TABLE migration.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.factory.service import MAX_TOOL_REQUIREMENTS, AgentFactory
from agency.kernel.registry import AgentRegistry as KernelRegistry


def _allow(actor: str, perm: str) -> bool:
    return perm == "agent.approve" and actor == "lead"


def _factory(tmp_path: Path, **over) -> tuple[AgentFactory, KernelRegistry, RuntimeRegistry]:
    kernels = KernelRegistry()
    runtimes = RuntimeRegistry()
    params = {
        "authorizer": _allow,
        "allowed_capabilities": {"inspect"},
        "kernel_registry": kernels,
        "runtime_registry": runtimes,
    }
    params.update(over)
    fac = AgentFactory(str(tmp_path / "factory.db"), **params)
    return fac, kernels, runtimes


def _factory_at(db: str) -> AgentFactory:
    return AgentFactory(
        db,
        authorizer=_allow,
        allowed_capabilities={"inspect"},
        kernel_registry=KernelRegistry(),
        runtime_registry=RuntimeRegistry(),
    )


def _draft(fac: AgentFactory, **over):
    args = {
        "blueprint_id": "analyst",
        "version": "1.0.0",
        "name": "Analyst",
        "domain": "research",
        "capabilities": ["inspect"],
        "creator": "creator",
    }
    args.update(over)
    return fac.create(**args)


def test_tool_requirements_round_trip(tmp_path: Path) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        _draft(fac, tool_requirements=["web_search", "memory_query"])
        bp = fac.get("analyst", "1.0.0")
        assert bp.tool_requirements == ("web_search", "memory_query")
        assert bp.to_dict()["tool_requirements"] == ["web_search", "memory_query"]
    finally:
        fac.close()


def test_tool_requirements_default_empty(tmp_path: Path) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        _draft(fac)
        bp = fac.get("analyst", "1.0.0")
        assert bp.tool_requirements == ()
        assert bp.to_dict()["tool_requirements"] == []
    finally:
        fac.close()


@pytest.mark.parametrize("bad", ["Web_Search", "1tool", "web search", "web.search", "", "   "])
def test_invalid_tool_name_rejected(tmp_path: Path, bad: str) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        with pytest.raises(ValueError):
            _draft(fac, tool_requirements=[bad])
    finally:
        fac.close()


def test_non_string_tool_entry_rejected(tmp_path: Path) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        with pytest.raises(TypeError):
            _draft(fac, tool_requirements=[42])
    finally:
        fac.close()


def test_non_sequence_tool_requirements_rejected(tmp_path: Path) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        with pytest.raises(TypeError):
            _draft(fac, tool_requirements="web_search")
        with pytest.raises(TypeError):
            _draft(fac, tool_requirements={"web_search"})
    finally:
        fac.close()


def test_duplicate_tool_requirements_rejected(tmp_path: Path) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        with pytest.raises(ValueError, match="duplicate"):
            _draft(fac, tool_requirements=["web_search", "web_search"])
    finally:
        fac.close()


def test_tool_requirements_bounded_at_max(tmp_path: Path) -> None:
    fac, _, _ = _factory(tmp_path)
    try:
        _draft(fac, tool_requirements=[f"tool-{i:02d}" for i in range(MAX_TOOL_REQUIREMENTS)])
        assert len(fac.get("analyst", "1.0.0").tool_requirements) == MAX_TOOL_REQUIREMENTS
        with pytest.raises(ValueError, match="too many tool requirements"):
            _draft(
                fac,
                blueprint_id="overflow",
                tool_requirements=["t"] * (MAX_TOOL_REQUIREMENTS + 1),
            )
    finally:
        fac.close()


def test_tool_requirements_survive_restart(tmp_path: Path) -> None:
    db = str(tmp_path / "factory.db")
    fac = _factory_at(db)
    fac.create(
        blueprint_id="analyst",
        version="1.0.0",
        name="Analyst",
        capabilities=["inspect"],
        tool_requirements=["web_search", "memory_query"],
        creator="creator",
    )
    fac.close()
    fac2 = _factory_at(db)
    try:
        assert fac2.get("analyst", "1.0.0").tool_requirements == ("web_search", "memory_query")
    finally:
        fac2.close()


def _create_legacy_db(db: str) -> None:
    """Create a blueprints table with the pre-tools_json schema and one row."""
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "CREATE TABLE blueprints ("
            "blueprint_id TEXT NOT NULL, version TEXT NOT NULL, "
            "name TEXT NOT NULL, domain TEXT NOT NULL, "
            "capabilities_json TEXT NOT NULL, "
            "capability_ceilings_json TEXT NOT NULL DEFAULT '[]', "
            "skills_json TEXT NOT NULL, "
            "workflows_json TEXT NOT NULL, prompts_json TEXT NOT NULL DEFAULT '[]', "
            "prompt_params_json TEXT NOT NULL DEFAULT '{}', "
            "prompt_override TEXT NOT NULL DEFAULT '', "
            "creator TEXT NOT NULL, "
            "status TEXT NOT NULL, approver TEXT, evidence TEXT NOT NULL, "
            "runtime_agent_id TEXT, created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, "
            "PRIMARY KEY (blueprint_id, version))"
        )
        conn.execute(
            "INSERT INTO blueprints (blueprint_id, version, name, domain,"
            " capabilities_json, capability_ceilings_json, skills_json,"
            " workflows_json, prompts_json, prompt_params_json, prompt_override,"
            " creator, status, approver, evidence, runtime_agent_id,"
            " created_at, updated_at)"
            " VALUES ('legacy', '1.0.0', 'Legacy', 'general', '[]', '[]', '[]',"
            " '[]', '[]', '{}', '', 'owner', 'DRAFT', NULL, '', NULL,"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        conn.commit()
    finally:
        conn.close()


def _table_columns(db: str) -> set[str]:
    conn = sqlite3.connect(db)
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(blueprints)")}
    finally:
        conn.close()


def test_old_schema_db_migrates_tools_json(tmp_path: Path) -> None:
    db = str(tmp_path / "legacy.db")
    _create_legacy_db(db)
    assert "tools_json" not in _table_columns(db)
    fac = _factory_at(db)
    try:
        # Migration is applied on open; the legacy row reads back unchanged.
        assert fac.get("legacy", "1.0.0").tool_requirements == ()
        assert "tools_json" in _table_columns(db)
        _draft(fac, tool_requirements=["web_fetch"])
        assert fac.get("analyst", "1.0.0").tool_requirements == ("web_fetch",)
    finally:
        fac.close()


def test_old_schema_db_survives_reopen(tmp_path: Path) -> None:
    db = str(tmp_path / "legacy.db")
    _create_legacy_db(db)
    fac = _factory_at(db)
    fac.close()
    fac2 = _factory_at(db)
    try:
        bp = fac2.get("legacy", "1.0.0")
        assert bp.tool_requirements == ()
        assert bp.status.value == "DRAFT"
    finally:
        fac2.close()
