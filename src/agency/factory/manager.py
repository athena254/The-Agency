"""Factory manager: agent selection data and manifest of installed agents.

``AgentManager`` loads ``data/agents/agents.yaml`` and exposes the agent
selector view (``list_agents`` / ``list_active_agents`` / ``get_agent_by_id``
/ ``get_agent_paths``) that bootstrap/install flows use. ``InstalledAgent``
records may reference a factory blueprint id via ``prompt_id`` +
``version``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from agency.factory.service import AgentFactory, Blueprint, BlueprintStatus

DEFAULT_AGENTS_DIR = Path("data/agents")
AGENTS_FILE = DEFAULT_AGENTS_DIR / "agents.yaml"


@dataclass(frozen=True)
class InstalledAgent:
    name: str
    id: str
    version: str
    domain: str
    prompt_id: str
    description: str = ""
    path: str = ""
    modified_date: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "domain": self.domain,
            "prompt_id": self.prompt_id,
            "description": self.description,
            "path": self.path,
            "modified_date": self.modified_date,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InstalledAgent:
        return cls(
            name=str(data["name"]),
            id=str(data["id"]),
            version=str(data["version"]),
            domain=str(data["domain"]),
            prompt_id=str(data["prompt_id"]),
            description=str(data.get("description", "")),
            path=str(data.get("path", "")),
            modified_date=str(data.get("modified_date", "")),
        )


def parse_version(text: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", text)
    if match is None:
        raise ValueError(f"invalid version {text!r}")
    first, second, third = (int(part) for part in match.groups())
    return (first, second, third)


def format_version(value: tuple[int, int, int]) -> str:
    return f"{value[0]}.{value[1]}.{value[2]}"


def _newer(a: str, b: str) -> bool:
    if not a or not b:
        return False
    try:
        return datetime.fromisoformat(a) > datetime.fromisoformat(b)
    except ValueError:
        return False


class AgentManager:
    """Durable installed-agent registry persisted as ``data/agents/agents.yaml``."""

    def __init__(
        self,
        factory: AgentFactory | None = None,
        path: Path = AGENTS_FILE,
    ) -> None:
        self._factory = factory
        self.path = Path(path)

    @staticmethod
    def parse_yaml(raw: str) -> list[InstalledAgent]:
        data = yaml.safe_load(raw) or {}
        entries = data.get("agents", [])
        if not isinstance(entries, list):
            raise TypeError("agents.yaml must define an 'agents' list")
        return [InstalledAgent.from_dict(entry) for entry in entries]

    def load(self) -> list[InstalledAgent]:
        if not self.path.exists():
            return []
        return self.parse_yaml(self.path.read_text(encoding="utf-8"))

    def installed(self) -> list[InstalledAgent]:
        return self.load()

    def save(self, agents: Iterable[InstalledAgent]) -> None:
        payload = {
            "agents": [
                {
                    "id": agent.id,
                    "name": agent.name,
                    "version": agent.version,
                    "domain": agent.domain,
                    "prompt_id": agent.prompt_id,
                    "description": agent.description,
                    "path": agent.path,
                    "modified_date": agent.modified_date,
                }
                for agent in agents
            ]
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")

    def get_agent_by_id(self, agent_id: str) -> InstalledAgent | None:
        return next((agent for agent in self.load() if agent.id == agent_id), None)

    def get_agent_paths(self) -> list[tuple[str, str]]:
        """Return ``(agent_id, blueprint_version)`` paths of installed agents."""
        out: list[tuple[str, str]] = []
        for agent in self.load():
            if self._factory is not None:
                versions = self._factory.list_versions(agent.id)
                if versions:
                    out.append((agent.id, versions[-1].version))
                    continue
            out.append((agent.id, agent.version))
        return out

    def list_agents(self) -> list[Blueprint]:
        """ACTIVE factory blueprints for installed agents (agent selector)."""
        if self._factory is None:
            return []
        active: list[Blueprint] = []
        for agent in self.load():
            for version in self._factory.list_versions(agent.id):
                if version.status is BlueprintStatus.ACTIVE:
                    active.append(version)
        return sorted(active, key=lambda b: (b.updated_at, b.name))

    def list_active_agents(self) -> list[Blueprint]:
        return self.list_agents()

    def register_or_raise(self, agent: InstalledAgent) -> None:
        agents = self.load()
        if any(existing.id == agent.id for existing in agents):
            raise ValueError(f"agent '{agent.id}' already registered in agents.yaml")
        agents.append(agent)
        agents.sort(key=lambda a: a.name)
        self.save(agents)

    def remove(self, agent_id: str) -> None:
        self.save([agent for agent in self.load() if agent.id != agent_id])


def _json_safe(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "AGENTS_FILE",
    "DEFAULT_AGENTS_DIR",
    "AgentManager",
    "Blueprint",
    "BlueprintStatus",
    "InstalledAgent",
]
