from __future__ import annotations

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
