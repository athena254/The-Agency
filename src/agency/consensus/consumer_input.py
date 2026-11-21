"""Attributable affected-consumer input for proposals.

A proposal's ``affected_snapshot`` (envelope.py) lists ``human-consumer``
members. Each requires an authenticated, identity-bound attestation before
the decision can lead to execution. This module validates coverage; it does
not introduce a new wire kind — it reuses the ballot's
``affected_role="affected-consumer"`` + ``human_input_ref`` fields.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agency.peers.canonical import validate_digest, validate_string


@dataclass(frozen=True, slots=True)
class ConsumerInput:
    consumer_id: str
    proposal_id: str
    attestation_digest: str
    gateway_peer_id: str


def validate_consumer_input(inp: ConsumerInput, *, affected_snapshot: Mapping[str, Any]) -> None:
    members = affected_snapshot.get("members", [])
    consumer_members = {m.get("consumer_id") for m in members if m.get("kind") == "human-consumer"}
    if inp.consumer_id not in consumer_members:
        raise ValueError(f"consumer {inp.consumer_id} not in affected snapshot")
    validate_digest(inp.attestation_digest, "attestation_digest")
    validate_string(inp.gateway_peer_id, "gateway_peer_id", maximum=128)


def required_consumers(affected_snapshot: Mapping[str, Any]) -> set[str]:
    return {
        str(m.get("consumer_id"))
        for m in affected_snapshot.get("members", [])
        if m.get("kind") == "human-consumer" and m.get("consumer_id")
    }


def check_coverage(
    inputs: Sequence[ConsumerInput],
    affected_snapshot: Mapping[str, Any],
) -> tuple[bool, set[str]]:
    """Return (all_covered, missing_consumer_ids)."""
    required = required_consumers(affected_snapshot)
    provided = {i.consumer_id for i in inputs}
    missing = required - provided
    return (len(missing) == 0, missing)
