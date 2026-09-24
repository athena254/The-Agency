"""Governed Agent Factory v1: versioned blueprints to runtime identities.

Bounded slice per docs/SPEC_AGENT_FACTORY_V1.md. SQLite-backed versioned
blueprint records keyed by (blueprint_id, version); metadata only, no
executable code, credentials, model selection, evaluation, sandboxing, or
deployment. No LLM calls; deterministic validation only.

Injected trust boundaries: authorizer(actor, permission) for agent.approve,
is_skill_published / is_workflow_published callbacks for exact pinned
references, plus the existing kernel and runtime registries. Missing
callbacks for requested references fail closed. Activated identities use
TrustLevel.UNKNOWN with L0-only claims; this module never issues Permission.

Restart limitation (fail closed): kernel and runtime registries are ephemeral,
so a restart loses live identities while SQLite retains ACTIVE provenance.
The factory never auto-recreates them; rollout needs a new version and approval.
Activation and revocation serialize via SQLite write transactions before
reading state; failed activation revokes any registered identity.
"""

from __future__ import annotations

import copy
import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import structlog

from agency.kernel.identity import Agent, Capability, TrustLevel
from agency.kernel.policies import ActionClass

logger = structlog.get_logger(__name__)

APPROVE_PERMISSION = "agent.approve"

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_CAP_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")

MAX_ID_LEN = 128
MAX_NAME_LEN = 200
MAX_EVIDENCE_LEN = 4096
MAX_CAPS = 32
MAX_REFS = 32
MAX_JSON_BYTES = 64 * 1024


class BlueprintStatus(str, Enum):
    """Lifecycle states for a versioned agent blueprint."""

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _is_nonblank(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _check_id(value: str, kind: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{kind} must be a nonblank string.")
    value = value.strip()
    if len(value) > MAX_ID_LEN:
        raise ValueError(f"{kind} exceeds {MAX_ID_LEN} chars.")
    if _ID_RE.match(value) is None:
        raise ValueError(f"invalid {kind} {value!r}.")
    return value


def _check_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("name must be a nonblank string.")
    value = value.strip()
    if len(value) > MAX_NAME_LEN:
        raise ValueError(f"name exceeds {MAX_NAME_LEN} chars.")
    return value


def _check_version(value: str) -> str:
    if not isinstance(value, str) or _VERSION_RE.match(value) is None:
        raise ValueError(f"invalid version {value!r}: must be exact X.Y.Z.")
    return value


def _check_evidence(value: str, kind: str = "evidence") -> str:
    if not _is_nonblank(value):
        raise ValueError(f"{kind} must be a nonblank reference.")
    value = value.strip()
    if len(value) > MAX_EVIDENCE_LEN:
        raise ValueError(f"{kind} exceeds {MAX_EVIDENCE_LEN} chars.")
    return value


def _normalize_caps(caps: Any, allowed: frozenset[str] | None) -> tuple[str, ...]:
    if caps is None:
        return ()
    if isinstance(caps, (str, Capability)):
        caps = [caps]
    if not isinstance(caps, (list, tuple)):
        raise TypeError("capabilities must be a list of names.")
    if len(caps) > MAX_CAPS:
        raise ValueError(f"too many capabilities: {len(caps)} > {MAX_CAPS}.")
    out: list[str] = []
    for entry in caps:
        if isinstance(entry, Capability):
            if entry.max_level != ActionClass.L0_OBSERVATION:
                raise ValueError(f"capability {entry.name!r} exceeds L0 (L0 only).")
            name = entry.name.strip().lower()
        elif isinstance(entry, str):
            name = entry.strip().lower()
        else:
            raise TypeError("capability entries must be strings or Capability.")
        if not name or _CAP_RE.match(name) is None:
            raise ValueError(f"invalid capability {entry!r}.")
        if allowed is not None and name not in allowed:
            raise ValueError(f"unrecognized capability: {name!r}.")
        if name not in out:
            out.append(name)
    return tuple(out)


def _normalize_refs(value: Any, kind: str) -> tuple[tuple[str, str], ...]:
    """Normalize pinned (id, version) refs; unpinned refs raise ValueError."""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{kind}_refs must be a list of pinned (id, version).")
    if len(value) > MAX_REFS:
        raise ValueError(f"too many {kind} refs: {len(value)} > {MAX_REFS}.")
    out: list[tuple[str, str]] = []
    for entry in value:
        rid: object
        ver: object
        if isinstance(entry, str):
            if "@" not in entry:
                raise ValueError(f"unpinned {kind} ref {entry!r}: use id@X.Y.Z.")
            rid, _, ver = entry.partition("@")
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            rid, ver = entry
        elif isinstance(entry, dict):
            rid = entry.get("id", entry.get(kind + "_id"))
            ver = entry.get("version")
            if rid is None or ver is None:
                raise ValueError(f"unpinned {kind} ref {entry!r}: id+version needed.")
        else:
            raise ValueError(f"invalid {kind} ref {entry!r}: pin id and version.")
        if not isinstance(rid, str) or not isinstance(ver, str):
            raise TypeError(f"invalid {kind} ref {entry!r}: pin id and version.")
        out.append((_check_id(rid, kind + " id"), _check_version(ver)))
    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str]] = []
    for ref in out:
        if ref not in seen:
            seen.add(ref)
            deduped.append(ref)
    return tuple(deduped)


def _check_json_bounded(payload: Any) -> None:
    try:
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"value is not JSON serializable: {exc}") from None
    if len(text.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError(f"blueprint payload exceeds {MAX_JSON_BYTES} bytes.")


@dataclass(frozen=True)
class Blueprint:
    """Immutable snapshot of one versioned agent blueprint."""

    blueprint_id: str
    version: str
    name: str
    domain: str
    capabilities: tuple[str, ...] = ()
    skill_refs: tuple[tuple[str, str], ...] = ()
    workflow_refs: tuple[tuple[str, str], ...] = ()
    creator: str = ""
    status: BlueprintStatus = BlueprintStatus.DRAFT
    approver: str | None = None
    evidence: str = ""
    runtime_agent_id: str | None = None
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a defensive deep copy as plain data."""
        return {
            "blueprint_id": self.blueprint_id,
            "version": self.version,
            "name": self.name,
            "domain": self.domain,
            "capabilities": list(self.capabilities),
            "skill_refs": [list(r) for r in self.skill_refs],
            "workflow_refs": [list(r) for r in self.workflow_refs],
            "creator": self.creator,
            "status": self.status.value,
            "approver": self.approver,
            "evidence": self.evidence,
            "runtime_agent_id": self.runtime_agent_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": copy.deepcopy(self.metadata),
        }


class AgentFactory:
    """SQLite-backed governed factory for agent identities (bounded v1)."""

    def __init__(
        self,
        db_path: str,
        *,
        authorizer: Callable[[str, str], bool] | None = None,
        is_skill_published: Callable[[str, str], bool] | None = None,
        is_workflow_published: Callable[[str, str], bool] | None = None,
        kernel_registry: Any | None = None,
        runtime_registry: Any | None = None,
        allowed_capabilities: frozenset[str] | set[str] | None = None,
    ) -> None:
        self._db_path = db_path
        self._authorizer = authorizer
        self._is_skill_published = is_skill_published
        self._is_workflow_published = is_workflow_published
        if kernel_registry is None:
            from agency.kernel.registry import AgentRegistry as KernelRegistry

            kernel_registry = KernelRegistry()
        if runtime_registry is None:
            from agency.agents.registry import AgentRegistry as RuntimeRegistry

            runtime_registry = RuntimeRegistry()
        self._kernels = kernel_registry
        self._runtimes = runtime_registry
        self._allowed = frozenset(c.strip().lower() for c in (allowed_capabilities or ()))
        if db_path != ":memory:":
            parent = Path(db_path).parent
            if str(parent) and not parent.exists():
                parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS blueprints ("
            "blueprint_id TEXT NOT NULL, version TEXT NOT NULL, "
            "name TEXT NOT NULL, domain TEXT NOT NULL, "
            "capabilities_json TEXT NOT NULL, skills_json TEXT NOT NULL, "
            "workflows_json TEXT NOT NULL, creator TEXT NOT NULL, "
            "status TEXT NOT NULL, approver TEXT, evidence TEXT NOT NULL, "
            "runtime_agent_id TEXT, created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, "
            "PRIMARY KEY (blueprint_id, version))"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS factory_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, blueprint_id TEXT NOT NULL, "
            "version TEXT NOT NULL, from_status TEXT NOT NULL, "
            "to_status TEXT NOT NULL, actor TEXT NOT NULL, "
            "evidence TEXT NOT NULL, at TEXT NOT NULL)"
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection (idempotent)."""
        conn, self._conn = self._conn, None  # type: ignore[assignment]
        if conn is not None:
            conn.close()

    def create(
        self,
        blueprint_id: str,
        version: str,
        name: str,
        *,
        domain: str = "general",
        capabilities: Any = (),
        skill_refs: Any = (),
        workflow_refs: Any = (),
        creator: str,
    ) -> Blueprint:
        """Store a new blueprint in DRAFT; duplicates raise ValueError."""
        bid = _check_id(blueprint_id, "blueprint_id")
        ver = _check_version(version)
        nm = _check_name(name)
        dom = _check_id(domain, "domain")
        if not _is_nonblank(creator):
            raise ValueError("creator must be a nonblank string.")
        creator = creator.strip()
        if len(creator) > MAX_ID_LEN:
            raise ValueError("creator exceeds 128 chars.")
        caps = _normalize_caps(capabilities, self._allowed)
        skills = _normalize_refs(skill_refs, "skill")
        workflows = _normalize_refs(workflow_refs, "workflow")
        _check_json_bounded({"caps": list(caps), "sk": skills, "wf": workflows})
        now = _now_iso()
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO blueprints (blueprint_id, version, name, domain,"
                    " capabilities_json, skills_json, workflows_json, creator,"
                    " status, approver, evidence, runtime_agent_id,"
                    " created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        bid,
                        ver,
                        nm,
                        dom,
                        json.dumps(list(caps)),
                        json.dumps([list(r) for r in skills]),
                        json.dumps([list(r) for r in workflows]),
                        creator,
                        BlueprintStatus.DRAFT.value,
                        None,
                        "",
                        None,
                        now,
                        now,
                    ),
                )
                self._conn.execute(
                    "INSERT INTO factory_events (blueprint_id, version,"
                    " from_status, to_status, actor, evidence, at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (bid, ver, "", BlueprintStatus.DRAFT.value, creator, "", now),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"duplicate blueprint version: {(bid, ver)!r}.") from exc
        return self.get(bid, ver)

    def validate(self, blueprint_id: str, version: str) -> Blueprint:
        """Validate a blueprint; failures raise ValueError (no state change).

        Checks syntactic bounds plus exact pinned references against the
        injected published callbacks. Missing callbacks for requested
        references fail closed. No LLM involved.
        """
        bp = self.get(blueprint_id, version)
        _check_name(bp.name)
        _check_version(bp.version)
        _normalize_caps(list(bp.capabilities), self._allowed)
        self._check_published_refs(bp)
        return bp

    def _check_published_refs(self, bp: Blueprint) -> None:
        for sid, sver in bp.skill_refs:
            if self._is_skill_published is None:
                raise ValueError(f"skill ref {(sid, sver)!r} unverifiable: no skill callback.")
            try:
                ok = self._is_skill_published(sid, sver)
            except Exception as exc:
                raise ValueError(f"skill ref {(sid, sver)!r} failed closed.") from exc
            if not ok:
                raise ValueError(f"skill ref {(sid, sver)!r} is not published.")
        for wid, wver in bp.workflow_refs:
            if self._is_workflow_published is None:
                raise ValueError(
                    f"workflow ref {(wid, wver)!r} unverifiable: no workflow callback."
                )
            try:
                ok = self._is_workflow_published(wid, wver)
            except Exception as exc:
                raise ValueError(f"workflow ref {(wid, wver)!r} failed closed.") from exc
            if not ok:
                raise ValueError(f"workflow ref {(wid, wver)!r} is not published.")

    def approve(
        self, blueprint_id: str, version: str, *, approver: str, evidence: str
    ) -> Blueprint:
        """Approve a DRAFT blueprint via the trusted authorizer callback.

        Calls authorizer(approver, "agent.approve"); caller-controlled
        permission lists are never consulted. Self-approval is rejected and
        pinned references are re-validated so approval cannot drift.
        """
        if not _is_nonblank(approver):
            raise ValueError("approver must be a nonblank string.")
        approver = approver.strip()
        ev = _check_evidence(evidence, "approval evidence")
        bp = self.get(blueprint_id, version)
        if bp.status is not BlueprintStatus.DRAFT:
            raise ValueError(f"approve requires DRAFT, found {bp.status.value}.")
        if approver == bp.creator:
            raise ValueError("approver must differ from creator (no self-approval).")
        if self._authorizer is None:
            raise PermissionError("no authorizer configured (fail closed).")
        try:
            allowed = self._authorizer(approver, APPROVE_PERMISSION)
        except PermissionError:
            raise
        except Exception as exc:
            raise PermissionError(f"authorizer failed closed: {exc}") from exc
        if not allowed:
            raise PermissionError(f"approver {approver!r} denied for agent.approve.")
        self._check_published_refs(bp)
        now = _now_iso()
        with self._conn:
            cur = self._conn.execute(
                "UPDATE blueprints SET status = ?, approver = ?, evidence = ?,"
                " updated_at = ? WHERE blueprint_id = ? AND version = ?"
                " AND status = ?",
                (
                    BlueprintStatus.APPROVED.value,
                    approver,
                    ev,
                    now,
                    bp.blueprint_id,
                    bp.version,
                    BlueprintStatus.DRAFT.value,
                ),
            )
            if cur.rowcount != 1:
                raise ValueError("approve lost a concurrent transition (fail closed).")
            self._conn.execute(
                "INSERT INTO factory_events (blueprint_id, version, from_status,"
                " to_status, actor, evidence, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    bp.blueprint_id,
                    bp.version,
                    BlueprintStatus.DRAFT.value,
                    BlueprintStatus.APPROVED.value,
                    approver,
                    ev,
                    now,
                ),
            )
        return self.get(bp.blueprint_id, bp.version)

    def activate(self, blueprint_id: str, version: str) -> Agent:
        """Activate an APPROVED blueprint into a runtime identity.

        Creates an Agent with TrustLevel.UNKNOWN and L0-only claims,
        registers it via kernel then runtime registries, and records the
        runtime id. Issues no Permission. Fails closed unless the row is
        still APPROVED with no recorded runtime id. The write lock covers
        the state read, registration, and audit commit so revoke cannot read
        a stale APPROVED snapshot during registration.
        """
        agent: Agent | None = None
        registration_attempted = False
        try:
            with self._conn:
                self._conn.execute("BEGIN IMMEDIATE")
                bp = self.get(blueprint_id, version)
                if bp.status is not BlueprintStatus.APPROVED:
                    raise ValueError(
                        f"activate requires APPROVED, found {bp.status.value} "
                        "(no pre-approval activation)."
                    )
                if bp.runtime_agent_id:
                    raise ValueError("blueprint already active (race/restart ambiguity).")
                self.validate(bp.blueprint_id, bp.version)
                agent = Agent(
                    name=bp.name,
                    domain=bp.domain,
                    capabilities=[Capability(name=c) for c in bp.capabilities],
                    trust_level=TrustLevel.UNKNOWN,
                    metadata={
                        "blueprint_id": bp.blueprint_id,
                        "blueprint_version": bp.version,
                        "factory": "agent-factory-v1",
                    },
                )
                for cap in agent.capabilities:
                    if cap.max_level != ActionClass.L0_OBSERVATION:
                        raise ValueError("factory issues L0-only capability claims.")
                registration_attempted = True
                try:
                    self._kernels.register(agent)
                except ValueError as exc:
                    raise ValueError(f"kernel registration failed closed: {exc}") from exc
                try:
                    self._runtimes.register(agent)
                except Exception as exc:
                    raise ValueError(f"runtime registration failed closed: {exc}") from exc
                now = _now_iso()
                cur = self._conn.execute(
                    "UPDATE blueprints SET status = ?, runtime_agent_id = ?,"
                    " updated_at = ? WHERE blueprint_id = ? AND version = ?"
                    " AND status = ? AND runtime_agent_id IS NULL",
                    (
                        BlueprintStatus.ACTIVE.value,
                        agent.id,
                        now,
                        bp.blueprint_id,
                        bp.version,
                        BlueprintStatus.APPROVED.value,
                    ),
                )
                if cur.rowcount != 1:
                    raise ValueError("activate lost a race (fail closed; orphan revoked).")
                self._conn.execute(
                    "INSERT INTO factory_events (blueprint_id, version, from_status,"
                    " to_status, actor, evidence, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        bp.blueprint_id,
                        bp.version,
                        BlueprintStatus.APPROVED.value,
                        BlueprintStatus.ACTIVE.value,
                        bp.creator,
                        bp.evidence,
                        now,
                    ),
                )
        except Exception:
            if registration_attempted and agent is not None:
                try:
                    self._runtimes.deregister(agent.id)
                finally:
                    try:
                        self._kernels.revoke(agent.id)
                    except KeyError:
                        logger.warning(
                            "factory.kernel_identity_absent_on_rollback", agent_id=agent.id
                        )
            raise
        assert agent is not None
        return agent

    def revoke(self, blueprint_id: str, version: str, *, evidence: str) -> Blueprint:
        """Revoke a blueprint; idempotent once REVOKED; provenance retained.

        Records revocation evidence, marks the row REVOKED, and revokes the
        recorded runtime identity via the kernel registry when present. A
        missing kernel identity (e.g. after restart) still revokes the row.
        """
        ev = _check_evidence(evidence, "revocation evidence")
        with self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            bp = self.get(blueprint_id, version)
            if bp.status is BlueprintStatus.REVOKED:
                return bp
            now = _now_iso()
            self._conn.execute(
                "UPDATE blueprints SET status = ?, evidence = ?, updated_at = ?"
                " WHERE blueprint_id = ? AND version = ?",
                (BlueprintStatus.REVOKED.value, ev, now, bp.blueprint_id, bp.version),
            )
            self._conn.execute(
                "INSERT INTO factory_events (blueprint_id, version, from_status,"
                " to_status, actor, evidence, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    bp.blueprint_id,
                    bp.version,
                    bp.status.value,
                    BlueprintStatus.REVOKED.value,
                    bp.creator,
                    ev,
                    now,
                ),
            )
        # Persist the audit and tombstone before mutating ephemeral registries.
        # If either SQL write fails, the existing identity remains intact and
        # the database stays ACTIVE; the caller can retry revocation.
        if bp.runtime_agent_id:
            try:
                self._kernels.revoke(bp.runtime_agent_id)
            except KeyError:
                logger.warning(
                    "factory.kernel_identity_absent_on_revoke", agent_id=bp.runtime_agent_id
                )
            try:
                self._runtimes.deregister(bp.runtime_agent_id)
            except KeyError:
                # Runtime identities do not survive process restarts.
                logger.info(
                    "factory.runtime_identity_absent_on_revoke", agent_id=bp.runtime_agent_id
                )
        return self.get(bp.blueprint_id, bp.version)

    def get(self, blueprint_id: str, version: str) -> Blueprint:
        """Return one blueprint snapshot; unknown raises KeyError."""
        row = self._conn.execute(
            "SELECT blueprint_id, version, name, domain, capabilities_json,"
            " skills_json, workflows_json, creator, status, approver, evidence,"
            " runtime_agent_id, created_at, updated_at"
            " FROM blueprints WHERE blueprint_id = ? AND version = ?",
            (blueprint_id, version),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown blueprint {(blueprint_id, version)!r}.")
        return self._row_to_blueprint(row)

    def list_versions(self, blueprint_id: str) -> list[Blueprint]:
        """Return all versions for blueprint_id sorted by semver."""
        rows = self._conn.execute(
            "SELECT blueprint_id, version, name, domain, capabilities_json,"
            " skills_json, workflows_json, creator, status, approver, evidence,"
            " runtime_agent_id, created_at, updated_at"
            " FROM blueprints WHERE blueprint_id = ?",
            (blueprint_id,),
        ).fetchall()
        bps = [self._row_to_blueprint(r) for r in rows]
        bps.sort(key=lambda b: tuple(int(p) for p in b.version.split(".")))
        return bps

    def list_events(self, blueprint_id: str, version: str) -> list[dict[str, str]]:
        """Return durable lifecycle evidence in order; unknown raises KeyError."""
        self.get(blueprint_id, version)
        rows = self._conn.execute(
            "SELECT from_status, to_status, actor, evidence, at FROM factory_events"
            " WHERE blueprint_id = ? AND version = ? ORDER BY id",
            (blueprint_id, version),
        ).fetchall()
        return [dict(zip(("from_status", "to_status", "actor", "evidence", "at"), r)) for r in rows]

    @staticmethod
    def _row_to_blueprint(row: tuple[Any, ...]) -> Blueprint:
        (
            bid,
            ver,
            name,
            domain,
            caps_j,
            sk_j,
            wf_j,
            creator,
            status,
            approver,
            evidence,
            runtime_id,
            created_at,
            updated_at,
        ) = row
        try:
            caps = tuple(json.loads(caps_j))
            skills = tuple(tuple(r) for r in json.loads(sk_j))
            wfs = tuple(tuple(r) for r in json.loads(wf_j))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"corrupt blueprint record: {exc}") from None
        try:
            norm_caps = _normalize_caps(list(caps), None)
        except ValueError as exc:
            raise ValueError(f"corrupt blueprint capabilities: {exc}") from None
        return Blueprint(
            blueprint_id=bid,
            version=ver,
            name=name,
            domain=domain,
            capabilities=norm_caps,
            skill_refs=skills,
            workflow_refs=wfs,
            creator=creator,
            status=BlueprintStatus(status),
            approver=approver,
            evidence=evidence,
            runtime_agent_id=runtime_id,
            created_at=created_at,
            updated_at=updated_at,
        )


__all__ = ["APPROVE_PERMISSION", "AgentFactory", "Blueprint", "BlueprintStatus"]
