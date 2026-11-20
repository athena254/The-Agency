"""Install plans for externally-installed agents.

An ``InstalledAgent`` metadata record (from ``agency.factory.manager``) is turned
into runtime ``Agent`` identities via :class:`InstallPlan`:

- install: ensure the kernel/runtime identity exists (idempotent), grant the
  installed agent's name as an L0 capability.
- removals: deterministically deregister identities by id.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from agency.factory.manager import InstalledAgent
from agency.kernel.identity import Agent, Capability
from agency.kernel.policies import ActionClass

_METADATA_KEYS = (
    "name",
    "version",
    "domain",
    "prompt_id",
    "description",
    "path",
    "modified_date",
)


def _agent_for(entry: InstallPlanEntry) -> Agent:
    a = entry.agent
    return Agent(
        id=a.id,
        name=a.name,
        domain=a.domain,
        capabilities=[Capability(name=a.id, max_level=ActionClass.L0_OBSERVATION)],
        metadata={key: getattr(a, key) for key in _METADATA_KEYS},
    )


@dataclass(frozen=True)
class InstallPlanEntry:
    agent: InstalledAgent


@dataclass(frozen=True)
class InstallPlan:
    installs: tuple[InstallPlanEntry, ...]
    removals: tuple[str, ...] = ()

    def get(self) -> tuple[tuple[str, str], ...]:
        return tuple((entry.agent.id, entry.agent.version) for entry in self.installs)

    def invert(self) -> InstallPlan:
        return InstallPlan(installs=(), removals=tuple(agent_id for agent_id, _ in self.get()))

    def merge(self, other: InstallPlan) -> InstallPlan:
        return InstallPlan(
            installs=self.installs + other.installs,
            removals=self.removals + other.removals,
        )


@dataclass(frozen=True)
class PAKSyncInstallPlan:
    installs: tuple[InstallPlanEntry, ...]
    repo_root: str
    install_from_main: bool
    all_repo_root: str

    def get(self) -> InstallPlan:
        return InstallPlan(installs=self.installs)

    def invert(self) -> PAKSyncInstallPlan:
        return PAKSyncInstallPlan(
            installs=(),
            repo_root=self.repo_root,
            install_from_main=self.install_from_main,
            all_repo_root=self.all_repo_root,
        )

    def merge(self, other: PAKSyncInstallPlan) -> PAKSyncInstallPlan:
        return PAKSyncInstallPlan(
            installs=self.installs + other.installs,
            repo_root=self.repo_root,
            install_from_main=self.install_from_main or other.install_from_main,
            all_repo_root=self.all_repo_root or other.all_repo_root,
        )


def install(agents: Iterable[InstalledAgent]) -> InstallPlan:
    return InstallPlan(tuple(InstallPlanEntry(agent) for agent in agents))


def check(plan: InstallPlan) -> list[str]:
    return [agent_id for agent_id, _ in plan.get()]


def apply_install_plan(plan: InstallPlan, kernel_registry: Any) -> list[str]:
    applied: list[str] = []
    for entry in plan.installs:
        agent = _agent_for(entry)
        if kernel_registry.get(agent.id) is None:
            kernel_registry.register(agent)
        kernel_registry.grant_capability(agent.id, agent.capabilities[0])
        applied.append(agent.id)
    for agent_id in plan.removals:
        if kernel_registry.get(agent_id) is not None:
            kernel_registry.deregister(agent_id)
    return applied


__all__ = [
    "InstallPlan",
    "InstallPlanEntry",
    "PAKSyncInstallPlan",
    "apply_install_plan",
    "check",
    "install",
]
