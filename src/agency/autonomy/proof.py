"""Hash-chained proof bundle.

Every consequential action appends a canonical-JSON record; each
record commits to prior hash. Export manifest = chain head +
count, verifiable offline. Tamper-evident, not just append-only.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class ProofEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    seq: int
    timestamp: str
    actor: str
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str = Field(min_length=64)
    event_hash: str = Field(min_length=64)


class ProofBundle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    events: list[ProofEvent] = Field(default_factory=list)
    head: str = Field(default="0" * 64)
    count: int = Field(default=0, ge=0)
    artifact: dict[str, Any] = Field(default_factory=dict)

    def append(self, actor: str, action: str, payload: dict[str, Any]) -> ProofEvent:
        seq = len(self.events)
        ts = datetime.now(UTC).isoformat()
        prev = self.head
        h = _sha(
            _canonical(
                {
                    "seq": seq,
                    "ts": ts,
                    "actor": actor,
                    "action": action,
                    "payload": payload,
                    "prev": prev,
                }
            )
        )
        ev = ProofEvent(
            seq=seq,
            timestamp=ts,
            actor=actor,
            action=action,
            payload=dict(payload),
            prev_hash=prev,
            event_hash=h,
        )
        self.events.append(ev)
        object.__setattr__(self, "head", h)
        object.__setattr__(self, "count", len(self.events))
        return ev

    def manifest(self) -> dict[str, Any]:
        return {
            "head": self.head,
            "count": self.count,
            "artifact_sha256": _sha(_canonical(self.artifact)) if self.artifact else "",
        }


def verify_chain(events: list[ProofEvent]) -> bool:
    prev = "0" * 64
    for i, ev in enumerate(events):
        if ev.seq != i or ev.prev_hash != prev:
            return False
        expected = _sha(
            _canonical(
                {
                    "seq": ev.seq,
                    "ts": ev.timestamp,
                    "actor": ev.actor,
                    "action": ev.action,
                    "payload": ev.payload,
                    "prev": ev.prev_hash,
                }
            )
        )
        if expected != ev.event_hash:
            return False
        prev = ev.event_hash
    return True
