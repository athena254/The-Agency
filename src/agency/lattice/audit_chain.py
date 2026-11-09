"""Hash-chained audit log helper (minimal, stdlib only).

Each event is hashed as sha256(prev_hash + canonical_json(event)) where
canonical_json is sorted-keys compact JSON. ``verify_chain`` re-checks
linkage and hashes for a chronological list of events.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

GENESIS_HASH = "0" * 64

_HASHED_FIELDS = (
    "event_id",
    "event_type",
    "actor",
    "target_id",
    "payload",
    "prior_state",
    "timestamp",
)


def canonical_json(data: Any) -> str:
    """Deterministic JSON: sorted keys, compact separators."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def compute_event_hash(prev_hash: str, event: dict[str, Any]) -> str:
    """Hash one event given its predecessor hash."""
    body = {field: event.get(field) for field in _HASHED_FIELDS}
    return hashlib.sha256(f"{prev_hash}:{canonical_json(body)}".encode()).hexdigest()


def chain_event(prev_hash: str, event: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``event`` with prev_hash/event_hash set."""
    event_hash = compute_event_hash(prev_hash, event)
    return {**event, "prev_hash": prev_hash, "event_hash": event_hash}


def _event_to_dict(event: Any) -> dict[str, Any]:
    if hasattr(event, "to_dict"):
        return dict(event.to_dict())
    return dict(event)


def verify_chain(events: list[Any]) -> bool:
    """Verify chronological ``events`` form an intact hash chain.

    Accepts dicts or objects with ``to_dict()``. Timestamps are compared
    as stored (ISO strings); callers must pass oldest-first order.
    Events without hashes (legacy) are skipped — returns True only if
    every hashed event links correctly.
    """
    prev = GENESIS_HASH
    for raw in events:
        event = _event_to_dict(raw)
        prev_hash = str(event.get("prev_hash") or "")
        event_hash = str(event.get("event_hash") or "")
        if not event_hash and not prev_hash:
            continue
        if prev_hash != prev:
            return False
        if compute_event_hash(prev_hash, event) != event_hash:
            return False
        prev = event_hash
    return True


__all__ = ["GENESIS_HASH", "canonical_json", "chain_event", "compute_event_hash", "verify_chain"]
