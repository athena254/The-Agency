from __future__ import annotations

import tomllib
from pathlib import Path

from agency.factory.manager import AgentManager, InstalledAgent


def _agent(agent_id: str, name: str) -> InstalledAgent:
    return InstalledAgent(
        name=name,
        id=agent_id,
        version="1.0.0",
        domain="coding",
        prompt_id=name.casefold().replace(" ", "-"),
        description="test agent",
        path=f"docs/agents/{agent_id}.md",
        modified_date="2026-11-21T00:00:00+00:00",
    )


def test_manager_load_save_and_lookup(tmp_path: Path) -> None:
    path = tmp_path / "agents" / "agents.yaml"
    manager = AgentManager(path=path)
    assert manager.installed() == []
    first, second = _agent("a-one", "Alpha"), _agent("b-two", "Beta")
    manager.save([first, second])
    loaded = AgentManager(path=path).installed()
    assert [a.id for a in loaded] == ["a-one", "b-two"]
    assert manager.get_agent_by_id("a-one") == first
    assert manager.get_agent_by_id("missing") is None
    names = [a.name for a in loaded]
    assert names == sorted(names)


def test_manager_register_duplicate_raises_and_remove(tmp_path: Path) -> None:
    path = tmp_path / "agents.yaml"
    manager = AgentManager(path=path)
    agent = _agent("a-one", "Alpha")
    manager.register_or_raise(agent)
    try:
        manager.register_or_raise(agent)
    except ValueError as exc:
        assert "already registered" in str(exc)
    else:
        raise AssertionError("duplicate registration accepted")
    manager.remove("a-one")
    assert manager.installed() == []


def test_get_agent_paths_prefers_factory_version(tmp_path: Path) -> None:
    path = tmp_path / "agents.yaml"
    AgentManager(path=path).save([_agent("a-one", "Alpha")])

    class _Factory:
        @staticmethod
        def list_versions(agent_id: str):
            class _V:
                version = "2.3.4"

            return [_V()] if agent_id == "a-one" else []

    manager = AgentManager(factory=_Factory(), path=path)  # type: ignore[arg-type]
    assert manager.get_agent_paths() == [("a-one", "2.3.4")]


# --- generated duplicate artifact: agents.toml (gaps.md #16) -------------------


def test_to_toml_renders_agent_tables(tmp_path: Path) -> None:
    path = tmp_path / "agents.yaml"
    manager = AgentManager(path=path)
    manager.save([_agent("a-one", "Alpha")])
    data = tomllib.loads(manager.to_toml())
    assert data == {
        "agents": {
            "a-one": {
                "name": "Alpha",
                "version": "1.0.0",
                "domain": "coding",
                "prompt_id": "alpha",
                "path": "docs/agents/a-one.md",
            }
        }
    }


def test_write_toml_round_trips_through_tomllib(tmp_path: Path) -> None:
    path = tmp_path / "agents.yaml"
    manager = AgentManager(path=path)
    manager.save([_agent("a-one", "Alpha"), _agent("b-two", "Beta")])
    out = tmp_path / "agents.toml"
    manager.write_toml(out)
    data = tomllib.loads(out.read_text(encoding="utf-8"))
    assert set(data["agents"]) == {"a-one", "b-two"}


def test_to_toml_quotes_table_keys_that_are_not_bare(tmp_path: Path) -> None:
    path = tmp_path / "agents.yaml"
    manager = AgentManager(path=path)
    manager.save([_agent("weird id!", "Weird")])
    rendered = manager.to_toml()
    assert '"weird id!"' in rendered
    data = tomllib.loads(rendered)
    assert "weird id!" in data["agents"]
