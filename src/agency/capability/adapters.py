"""Read-only capability adapters projecting native records to contracts."""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Any, Protocol

from agency.capability.contract import (
    CapabilityContract,
    CapabilityNotFoundError,
    CapabilityRef,
    CapabilityType,
    CorruptCapabilityError,
    DependencyEdge,
    DependencyRelation,
    ProvenanceRef,
    PublicationAttestation,
    compute_content_digest,
)

AttestationLookup = Callable[[CapabilityRef, str], PublicationAttestation | None]


class CapabilityAdapter(Protocol):
    capability_type: CapabilityType

    def get(self, capability_id: str, version: str) -> CapabilityContract: ...

    def list_refs(self) -> list[CapabilityRef]: ...


def _attested(
    attestation_lookup: AttestationLookup | None,
    ref: CapabilityRef,
    digest: str,
) -> bool:
    if attestation_lookup is None:
        return False
    try:
        att = attestation_lookup(ref, digest)
    except Exception:  # noqa: BLE001
        return False
    if att is None:
        return False
    expected = CapabilityRef(ref.capability_type, ref.capability_id, ref.version)
    try:
        if att.ref != expected:
            return False
    except Exception:  # noqa: BLE001
        return False
    if att.content_digest != digest:
        return False
    if not bool(str(att.approver).strip()):
        return False
    return bool(str(att.decision_ref).strip())


def _published_state(native_status: str, attested: bool) -> str:
    if native_status == "PUBLISHED":
        return "PUBLISHED" if attested else "PUBLISHED_UNATTESTED"
    return native_status


def _native_status(status: Any) -> str:
    value = getattr(status, "value", status)
    return str(value)


def _finalize(
    contract: CapabilityContract,
    attestation_lookup: AttestationLookup | None,
    *,
    apply_attestation: bool,
) -> CapabilityContract:
    digest = compute_content_digest(contract)
    contract = replace(contract, content_digest=digest)
    if not apply_attestation:
        return contract
    attested = _attested(attestation_lookup, contract.ref, digest)
    lifecycle = _published_state(contract.native_state, attested)
    if lifecycle != contract.lifecycle_state:
        contract = replace(contract, lifecycle_state=lifecycle)
    return contract


def _fetch(registry: Any, capability_id: str, version: str) -> Any:
    try:
        return registry.get(capability_id, version)
    except KeyError as exc:
        raise CapabilityNotFoundError(f"unknown capability: {(capability_id, version)!r}.") from exc
    except Exception as exc:
        raise CorruptCapabilityError(
            f"corrupt capability record: {(capability_id, version)!r}: {exc}"
        ) from exc


class _BaseAdapter:
    capability_type: CapabilityType

    def __init__(
        self,
        registry: Any,
        attestation_lookup: AttestationLookup | None = None,
        list_ids: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        self._registry = registry
        self._attestation_lookup = attestation_lookup
        self._list_ids = list_ids

    def list_refs(self) -> list[CapabilityRef]:
        if self._list_ids is None:
            return []
        refs: list[CapabilityRef] = []
        for capability_id in self._list_ids():
            for native in self._registry.list_versions(capability_id):
                refs.append(CapabilityRef(self.capability_type, capability_id, native.version))
        return refs


class SkillCapabilityAdapter(_BaseAdapter):
    capability_type = CapabilityType.SKILL

    def get(self, capability_id: str, version: str) -> CapabilityContract:
        spec = _fetch(self._registry, capability_id, version)
        try:
            native_state = _native_status(spec.status)
            provisional = _published_state(native_state, False)
            contract = CapabilityContract(
                1,
                CapabilityRef(self.capability_type, capability_id, version),
                spec.name,
                spec.description,
                provisional,
                native_state,
                copy.deepcopy(spec.inputs),
                copy.deepcopy(spec.outputs),
                tuple(spec.permissions),
                spec.implementation or None,
                (),
                (),
                ProvenanceRef("agency_native", source_id=spec.provenance),
                "",
                None,
            )
            return _finalize(contract, self._attestation_lookup, apply_attestation=True)
        except Exception as exc:
            if isinstance(exc, (CapabilityNotFoundError, CorruptCapabilityError)):
                raise
            raise CorruptCapabilityError(f"corrupt skill record: {exc}") from exc


class PromptCapabilityAdapter(_BaseAdapter):
    capability_type = CapabilityType.PROMPT

    def get(self, capability_id: str, version: str) -> CapabilityContract:
        spec = _fetch(self._registry, capability_id, version)
        try:
            native_state = _native_status(spec.status)
            provisional = _published_state(native_state, False)
            contract = CapabilityContract(
                1,
                CapabilityRef(self.capability_type, capability_id, version),
                spec.name,
                spec.description,
                provisional,
                native_state,
                None,
                None,
                (),
                None,
                (),
                (),
                ProvenanceRef("agency_native", source_id=spec.provenance),
                "",
                {
                    "domain": spec.domain,
                    "kind": _native_status(spec.kind),
                    "body": spec.body,
                },
            )
            return _finalize(contract, self._attestation_lookup, apply_attestation=True)
        except Exception as exc:
            if isinstance(exc, (CapabilityNotFoundError, CorruptCapabilityError)):
                raise
            raise CorruptCapabilityError(f"corrupt prompt record: {exc}") from exc


class WorkflowCapabilityAdapter(_BaseAdapter):
    capability_type = CapabilityType.WORKFLOW

    def get(self, capability_id: str, version: str) -> CapabilityContract:
        definition = _fetch(self._registry, capability_id, version)
        try:
            permissions = tuple(sorted({p for s in definition.steps for p in s.permissions}))
            steps = [
                {
                    "step_id": s.step_id,
                    "operation": s.operation,
                    "depends_on": list(s.depends_on),
                    "permissions": list(s.permissions),
                }
                for s in definition.steps
            ]
            contract = CapabilityContract(
                1,
                CapabilityRef(self.capability_type, capability_id, version),
                definition.workflow_id,
                "",
                "REGISTERED_UNVERIFIED",
                "REGISTERED_UNVERIFIED",
                None,
                None,
                permissions,
                None,
                (),
                (),
                ProvenanceRef("agency_native"),
                "",
                {"steps": steps},
            )
            return _finalize(contract, self._attestation_lookup, apply_attestation=False)
        except Exception as exc:
            if isinstance(exc, (CapabilityNotFoundError, CorruptCapabilityError)):
                raise
            raise CorruptCapabilityError(f"corrupt workflow record: {exc}") from exc


class AgentBlueprintCapabilityAdapter(_BaseAdapter):
    capability_type = CapabilityType.AGENT

    def get(self, capability_id: str, version: str) -> CapabilityContract:
        bp = _fetch(self._registry, capability_id, version)
        try:
            dependencies: tuple[DependencyEdge, ...] = tuple(
                DependencyEdge(
                    DependencyRelation.COMPOSES,
                    CapabilityRef(kind, rid, ver),
                )
                for kind, refs in (
                    (CapabilityType.SKILL, bp.skill_refs),
                    (CapabilityType.WORKFLOW, bp.workflow_refs),
                    (CapabilityType.PROMPT, bp.prompt_refs),
                )
                for rid, ver in refs
            )
            native_state = _native_status(bp.status)
            contract = CapabilityContract(
                1,
                CapabilityRef(self.capability_type, capability_id, version),
                bp.name,
                "",
                native_state,
                native_state,
                None,
                None,
                tuple(bp.capabilities),
                None,
                dependencies,
                (),
                ProvenanceRef(
                    "agency_native",
                    source_id=bp.evidence or bp.creator,
                    actor_id=bp.creator or "actor_unknown",
                    created_at=bp.created_at,
                ),
                "",
                {
                    "domain": bp.domain,
                    "prompt_params": dict(bp.prompt_params),
                    "prompt_override": bp.prompt_override,
                },
            )
            return _finalize(contract, self._attestation_lookup, apply_attestation=False)
        except Exception as exc:
            if isinstance(exc, (CapabilityNotFoundError, CorruptCapabilityError)):
                raise
            raise CorruptCapabilityError(f"corrupt blueprint record: {exc}") from exc


__all__ = [
    "AgentBlueprintCapabilityAdapter",
    "AttestationLookup",
    "CapabilityAdapter",
    "PromptCapabilityAdapter",
    "SkillCapabilityAdapter",
    "WorkflowCapabilityAdapter",
]
