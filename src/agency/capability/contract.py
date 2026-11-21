from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from agency.factory.coding_models import canonical_sha256 as _canonical_sha256

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SOURCE_KINDS = frozenset(
    {"agency_native", "human_authored", "generated_candidate", "reviewed_import"}
)
_PLACEHOLDER_DIGEST = "sha256:" + "0" * 64


class CapabilityError(ValueError):
    pass


class UnsupportedCapabilityTypeError(CapabilityError):
    pass


class CapabilityNotFoundError(CapabilityError, KeyError):
    pass


class DigestMismatchError(CapabilityError):
    pass


class CorruptCapabilityError(CapabilityError):
    pass


class CapabilityType(str, Enum):
    AGENT = "agent"
    SKILL = "skill"
    WORKFLOW = "workflow"
    PROMPT = "prompt"
    TOOL_ADAPTER = "tool_adapter"
    EVALUATION_SUITE = "evaluation_suite"
    AGENT_TEAM = "agent_team"
    KNOWLEDGE_PACK = "knowledge_pack"

    @classmethod
    def from_value(cls, value: str) -> CapabilityType:
        if isinstance(value, CapabilityType):
            return value
        if not isinstance(value, str):
            raise UnsupportedCapabilityTypeError(f"unsupported capability type: {value!r}")
        try:
            return cls(value)
        except ValueError:
            raise UnsupportedCapabilityTypeError(
                f"unsupported capability type: {value!r}"
            ) from None


class DependencyRelation(str, Enum):
    REQUIRES = "requires"
    COMPOSES = "composes"
    CONFLICTS_WITH = "conflicts_with"
    SUPERSEDES = "supersedes"
    COMPATIBLE_WITH = "compatible_with"

    @classmethod
    def from_value(cls, value: str) -> DependencyRelation:
        if isinstance(value, DependencyRelation):
            return value
        if not isinstance(value, str):
            raise TypeError(f"invalid dependency relation: {value!r}")
        try:
            return cls(value)
        except ValueError:
            raise ValueError(f"invalid dependency relation: {value!r}") from None


class CapabilityLifecycleState(str, Enum):
    DRAFT = "DRAFT"
    TESTING = "TESTING"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    PUBLISHED_UNATTESTED = "PUBLISHED_UNATTESTED"
    REGISTERED_UNVERIFIED = "REGISTERED_UNVERIFIED"
    DEPRECATED = "DEPRECATED"
    RETIRED = "RETIRED"
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


def _validate_id(value: object, kind: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError(f"invalid {kind}: {value!r}")
    if _ID_RE.match(value) is None:
        raise ValueError(f"invalid {kind}: {value!r}")
    return value


def _validate_version(value: object) -> str:
    if not isinstance(value, str) or _VERSION_RE.match(value) is None:
        raise ValueError(f"invalid version: {value!r}")
    return value


def _validate_digest(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _DIGEST_RE.match(value) is None:
        raise ValueError(f"invalid digest: {value!r}")
    return value


def canonical_sha256(value: object) -> str:
    return "sha256:" + _canonical_sha256(value)


def _plain(value: object) -> object:
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return copy.deepcopy(value)


def _ref_dict(ref: CapabilityRef) -> dict[str, object]:
    return {
        "capability_type": ref.capability_type.value,
        "capability_id": ref.capability_id,
        "version": ref.version,
    }


@dataclass(frozen=True)
class CapabilityRef:
    capability_type: CapabilityType
    capability_id: str
    version: str
    digest: str | None = None

    def __post_init__(self) -> None:
        raw: object = self.capability_type
        if isinstance(raw, CapabilityType):
            resolved = raw
        elif isinstance(raw, str):
            resolved = CapabilityType.from_value(raw)
        else:
            raise UnsupportedCapabilityTypeError(f"unsupported capability type: {raw!r}")
        object.__setattr__(self, "capability_type", resolved)
        _validate_id(self.capability_id, "capability_id")
        _validate_version(self.version)
        _validate_digest(self.digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_type": self.capability_type.value,
            "capability_id": self.capability_id,
            "version": self.version,
            "digest": self.digest,
        }

    def __str__(self) -> str:
        return f"{self.capability_type.value}:{self.capability_id}@{self.version}"


@dataclass(frozen=True)
class DependencyEdge:
    relation: DependencyRelation
    target: CapabilityRef
    label: str = ""

    def __post_init__(self) -> None:
        raw: object = self.relation
        if isinstance(raw, DependencyRelation):
            resolved = raw
        elif isinstance(raw, str):
            resolved = DependencyRelation.from_value(raw)
        else:
            raise TypeError(f"invalid dependency relation: {raw!r}")
        object.__setattr__(self, "relation", resolved)
        if not isinstance(self.target, CapabilityRef):
            raise TypeError(f"invalid dependency target: {self.target!r}")
        if not isinstance(self.label, str) or len(self.label) > 128:
            raise ValueError(f"invalid dependency label: {self.label!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation": self.relation.value,
            "target": self.target.to_dict(),
            "label": self.label,
        }


@dataclass(frozen=True)
class ProvenanceRef:
    source_kind: str
    source_id: str = ""
    actor_id: str = "actor_unknown"
    created_at: str = ""
    source_digest: str | None = None
    upstream_url: str | None = None
    license_id: str | None = None
    parents: tuple[CapabilityRef, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.source_kind, str) or self.source_kind not in _SOURCE_KINDS:
            raise ValueError(f"invalid source_kind: {self.source_kind!r}")
        for field_name in ("source_id", "actor_id", "created_at"):
            field_value: object = getattr(self, field_name)
            if not isinstance(field_value, str):
                raise TypeError(f"invalid provenance {field_name}: {field_value!r}")
        for field_name in ("source_digest", "upstream_url", "license_id"):
            field_value = getattr(self, field_name)
            if field_value is not None and not isinstance(field_value, str):
                raise ValueError(f"invalid provenance {field_name}: {field_value!r}")
        if self.source_digest is not None:
            _validate_digest(self.source_digest)
        parents = tuple(self.parents) if self.parents is not None else ()
        for parent in parents:
            if not isinstance(parent, CapabilityRef):
                raise TypeError(f"invalid provenance parent: {parent!r}")
        object.__setattr__(self, "parents", parents)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "actor_id": self.actor_id,
            "created_at": self.created_at,
            "source_digest": self.source_digest,
            "upstream_url": self.upstream_url,
            "license_id": self.license_id,
            "parents": [parent.to_dict() for parent in self.parents],
        }


@dataclass(frozen=True)
class CapabilityContract:
    schema_version: int
    ref: CapabilityRef
    name: str
    description: str
    lifecycle_state: str
    native_state: str
    inputs: dict[str, object] | None
    outputs: dict[str, object] | None
    requested_permissions: tuple[str, ...]
    implementation_ref: str | None
    dependencies: tuple[DependencyEdge, ...]
    tags: tuple[str, ...]
    provenance: ProvenanceRef
    content_digest: str
    extra: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError(f"invalid schema_version: {self.schema_version!r}")
        if not isinstance(self.ref, CapabilityRef):
            raise TypeError(f"invalid ref: {self.ref!r}")
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 200:
            raise ValueError(f"invalid name: {self.name!r}")
        if not isinstance(self.description, str):
            raise TypeError(f"invalid description: {self.description!r}")
        for field_name in ("lifecycle_state", "native_state"):
            field_value: object = getattr(self, field_name)
            if not isinstance(field_value, str) or not field_value.strip():
                raise ValueError(f"invalid {field_name}: {field_value!r}")
        for field_name in ("inputs", "outputs", "extra"):
            field_value = getattr(self, field_name)
            if field_value is not None and not isinstance(field_value, dict):
                raise ValueError(f"invalid {field_name}: {field_value!r}")
            object.__setattr__(self, field_name, copy.deepcopy(field_value))
        permissions = tuple(self.requested_permissions) if self.requested_permissions else ()
        for permission in permissions:
            if not isinstance(permission, str) or not permission.strip():
                raise ValueError(f"invalid requested permission: {permission!r}")
        object.__setattr__(self, "requested_permissions", permissions)
        if self.implementation_ref is not None and not isinstance(self.implementation_ref, str):
            raise ValueError(f"invalid implementation_ref: {self.implementation_ref!r}")
        dependencies = tuple(self.dependencies) if self.dependencies else ()
        for dependency in dependencies:
            if not isinstance(dependency, DependencyEdge):
                raise TypeError(f"invalid dependency: {dependency!r}")
        object.__setattr__(self, "dependencies", dependencies)
        tags = tuple(self.tags) if self.tags else ()
        for tag in tags:
            if not isinstance(tag, str):
                raise TypeError(f"invalid tag: {tag!r}")
        object.__setattr__(self, "tags", tags)
        if not isinstance(self.provenance, ProvenanceRef):
            raise TypeError(f"invalid provenance: {self.provenance!r}")
        if not isinstance(self.content_digest, str):
            raise TypeError(f"invalid content_digest: {self.content_digest!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ref": self.ref.to_dict(),
            "name": self.name,
            "description": self.description,
            "lifecycle_state": self.lifecycle_state,
            "native_state": self.native_state,
            "inputs": copy.deepcopy(self.inputs),
            "outputs": copy.deepcopy(self.outputs),
            "requested_permissions": list(self.requested_permissions),
            "implementation_ref": self.implementation_ref,
            "dependencies": [dependency.to_dict() for dependency in self.dependencies],
            "tags": list(self.tags),
            "provenance": self.provenance.to_dict(),
            "content_digest": self.content_digest,
            "extra": copy.deepcopy(self.extra),
        }


@dataclass(frozen=True)
class PublicationAttestation:
    ref: CapabilityRef
    content_digest: str
    approver: str
    decision_ref: str
    evidence_ref: str
    schema_version: int = 1
    decided_at: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.ref, CapabilityRef):
            raise TypeError(f"invalid ref: {self.ref!r}")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError(f"invalid schema_version: {self.schema_version!r}")
        for field_name in ("content_digest", "approver", "decision_ref", "evidence_ref"):
            field_value: object = getattr(self, field_name)
            if not isinstance(field_value, str):
                raise TypeError(f"invalid {field_name}: {field_value!r}")
        if not isinstance(self.decided_at, str):
            raise TypeError(f"invalid decided_at: {self.decided_at!r}")


def _projection_dict(contract: CapabilityContract) -> dict[str, object]:
    return {
        "schema_version": contract.schema_version,
        "ref": _ref_dict(contract.ref),
        "name": contract.name,
        "description": contract.description,
        "inputs": _plain(contract.inputs),
        "outputs": _plain(contract.outputs),
        "requested_permissions": list(contract.requested_permissions),
        "implementation_ref": contract.implementation_ref,
        "dependencies": [
            {
                "relation": dependency.relation.value,
                "target": _ref_dict(dependency.target),
                "label": dependency.label,
            }
            for dependency in contract.dependencies
        ],
        "tags": list(contract.tags),
        "provenance": {
            "source_kind": contract.provenance.source_kind,
            "source_id": contract.provenance.source_id,
            "actor_id": contract.provenance.actor_id,
            "created_at": contract.provenance.created_at,
            "source_digest": contract.provenance.source_digest,
            "upstream_url": contract.provenance.upstream_url,
            "license_id": contract.provenance.license_id,
            "parents": [_ref_dict(parent) for parent in contract.provenance.parents],
        },
        "extra": _plain(contract.extra),
    }


def compute_content_digest(contract: CapabilityContract) -> str:
    return canonical_sha256(_projection_dict(contract))


content_digest = compute_content_digest


def verify_digest(contract: CapabilityContract) -> None:
    if compute_content_digest(contract) != contract.content_digest:
        raise DigestMismatchError(f"INTEGRITY_FAILURE: digest mismatch for {contract.ref}")


__all__ = [
    "CapabilityContract",
    "CapabilityError",
    "CapabilityLifecycleState",
    "CapabilityNotFoundError",
    "CapabilityRef",
    "CapabilityType",
    "CorruptCapabilityError",
    "DependencyEdge",
    "DependencyRelation",
    "DigestMismatchError",
    "ProvenanceRef",
    "PublicationAttestation",
    "UnsupportedCapabilityTypeError",
    "canonical_sha256",
    "compute_content_digest",
    "content_digest",
    "verify_digest",
]
