"""Agency Kernel — deterministic governance layer for autonomous agents.

Modules
-------
identity:
    Agent identity, capabilities and trust levels (Pydantic models).
policies:
    Action classes, permission model and the policy engine.
tasks:
    Task lifecycle and message-passing between agents.
audit:
    Immutable, SQLite-backed audit log.
registry:
    Agent life-cycle registry (register / get / list / revoke).
"""

from __future__ import annotations

from agency.kernel.audit import AuditEntry, AuditFilter, AuditLog
from agency.kernel.identity import (
    Agent,
    Capability,
    TrustLevel,
)
from agency.kernel.policies import (
    ActionClass,
    FilesystemPolicy,
    NetworkPolicy,
    NetworkScope,
    Permission,
    PolicyEngine,
    ResourceLimits,
)
from agency.kernel.registry import AgentRegistry
from agency.kernel.tasks import (
    Task,
    TaskManager,
    TaskMessage,
    TaskStatus,
)

__all__ = [
    "ActionClass",
    "Agent",
    "AgentRegistry",
    "AuditEntry",
    "AuditFilter",
    "AuditLog",
    "Capability",
    "FilesystemPolicy",
    "NetworkPolicy",
    "NetworkScope",
    "Permission",
    "PolicyEngine",
    "ResourceLimits",
    "Task",
    "TaskManager",
    "TaskMessage",
    "TaskStatus",
    "TrustLevel",
]
