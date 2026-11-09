"""Deterministic, rebuildable capability discovery index.

The index is a read model over adapter projections. It is never
authoritative for publication, revocation, permission, or exact
version content. Callers must re-resolve a ref through an adapter
or resolver before using it for composition, approval, evaluation,
or execution.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from agency.capability.adapters import CapabilityAdapter
from agency.capability.contract import (
    CapabilityContract,
    CapabilityRef,
    CapabilityType,
)

_UNVERIFIED_STATES = frozenset(
    {"PUBLISHED_UNATTESTED", "REGISTERED_UNVERIFIED", "DRAFT", "TESTING"}
)


@dataclass(frozen=True)
class CapabilityQuery:
    text: str = ""
    capability_type: CapabilityType | None = None
    tags: tuple[str, ...] = ()
    include_unverified: bool = False
    limit: int = 50

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError(f"invalid text: {self.text!r}")
        if len(self.text) > 500:
            raise ValueError(f"invalid text length: {len(self.text)}")
        raw_type: object = self.capability_type
        if raw_type is None:
            resolved_type = None
        elif isinstance(raw_type, CapabilityType):
            resolved_type = raw_type
        elif isinstance(raw_type, str):
            resolved_type = CapabilityType.from_value(raw_type)
        else:
            raise TypeError(f"invalid capability_type: {raw_type!r}")
        object.__setattr__(self, "capability_type", resolved_type)
        tags = tuple(self.tags) if self.tags is not None else ()
        for tag in tags:
            if not isinstance(tag, str):
                raise TypeError(f"invalid tag: {tag!r}")
        object.__setattr__(self, "tags", tags)
        if not isinstance(self.include_unverified, bool):
            raise TypeError(f"invalid include_unverified: {self.include_unverified!r}")
        if type(self.limit) is not int or not 1 <= self.limit <= 500:
            raise ValueError(f"invalid limit: {self.limit!r}")


@dataclass(frozen=True)
class CapabilitySearchResult:
    contract: CapabilityContract

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.contract.ref.to_dict(),
            "name": self.contract.name,
            "lifecycle_state": self.contract.lifecycle_state,
            "native_state": self.contract.native_state,
            "content_digest": self.contract.content_digest,
            "requested_permissions": list(self.contract.requested_permissions),
            "tags": list(self.contract.tags),
        }


@dataclass(frozen=True)
class IndexBuildReport:
    count: int
    types: tuple[str, ...]


class CapabilityIndex:
    """Rebuildable read model over capability adapters.

    Rebuild from native registries via adapters whenever native state
    may have changed. Stale results are a discovery aid only; re-resolve
    through an adapter or resolver before use.
    """

    def __init__(self, adapters: Iterable[CapabilityAdapter]) -> None:
        self._adapters = tuple(adapters)
        self._index: dict[tuple[CapabilityType, str, str], CapabilityContract] = {}
        self._built = False

    def rebuild(self) -> IndexBuildReport:
        built: dict[tuple[CapabilityType, str, str], CapabilityContract] = {}
        for adapter in self._adapters:
            for ref in adapter.list_refs():
                contract = adapter.get(ref.capability_id, ref.version)
                built[
                    (contract.ref.capability_type, contract.ref.capability_id, contract.ref.version)
                ] = contract
        self._index = built
        self._built = True
        types = tuple(sorted({key[0].value for key in built}))
        return IndexBuildReport(count=len(built), types=types)

    def search(self, query: CapabilityQuery) -> list[CapabilitySearchResult]:
        if not self._built:
            return []
        text = query.text.casefold()
        results: list[CapabilitySearchResult] = []
        for contract in sorted(
            self._index.values(),
            key=lambda c: (c.ref.capability_type.value, c.ref.capability_id, c.ref.version),
        ):
            if query.capability_type is not None and (
                contract.ref.capability_type is not query.capability_type
            ):
                continue
            if not query.include_unverified and contract.lifecycle_state in _UNVERIFIED_STATES:
                continue
            if text:
                haystack = (
                    f"{contract.name} {contract.description} {contract.ref.capability_id}"
                ).casefold()
                if text not in haystack:
                    continue
            if query.tags and not set(query.tags).issubset(set(contract.tags)):
                continue
            results.append(CapabilitySearchResult(contract))
            if len(results) >= query.limit:
                break
        return results

    def get(self, ref: CapabilityRef) -> CapabilityContract | None:
        """Return the indexed contract for ``ref``, or ``None``.

        Index lookup only: the indexed record may be stale. Re-resolve
        via adapter before use.
        """
        return self._index.get((ref.capability_type, ref.capability_id, ref.version))

    def is_stale(self, contract: CapabilityContract) -> bool:
        stored = self.get(contract.ref)
        if stored is None:
            return True
        return stored.content_digest != contract.content_digest

    def __len__(self) -> int:
        return len(self._index)


__all__ = [
    "CapabilityIndex",
    "CapabilityQuery",
    "CapabilitySearchResult",
    "IndexBuildReport",
]
