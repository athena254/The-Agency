"""Pure derivation helpers for proposal identity and decision input sets.

These functions are deterministic and side-effect free: the same inputs always
produce the same digest. They do not validate authority, admission, or
freshness, and they do not read or write any store.

§5.3 makes ``proposal_id`` a pure function of the immutable proposal body, so
that a version bump or fencing change cannot masquerade as the same proposal.
§8 makes ``eligible_set_digest`` the fixed input-set citation a verifier
re-derives against.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from agency.peers.canonical import (
    MAX_MEMBERS,
    SchemaError,
    digest_json,
    validate_consumer_id,
    validate_list,
    validate_peer_id,
    validate_proposal_id_format,
    validate_string,
)
from agency.peers.envelope import validate_payload

__all__ = [
    "PROPOSAL_ID_PREFIX",
    "derive_eligible_set_digest",
    "derive_proposal_id",
    "validate_proposal_id",
]

PROPOSAL_ID_PREFIX: Final = "prop_"

# §5.3: every immutable proposal field except the self-referential
# ``proposal_id`` is hashed into the identifier.
_PROPOSAL_BODY_FIELDS: Final = (
    "proposer_peer_id",
    "proposal_version",
    "membership_epoch",
    "membership_root",
    "action",
    "affected_snapshot",
    "policy_version",
    "evidence_manifest",
    "idempotency_key",
    "fencing_base",
    "expires_at_ms",
    "supersedes",
)

_MAX_AFFECTED: Final = MAX_MEMBERS


def derive_proposal_id(proposal_body: Mapping[str, Any]) -> str:
    """Derive ``proposal_id`` from the canonical proposal body (§5.3).

    The body is validated first, so an unknown or malformed field cannot be
    silently excluded from the digest. The self-referential ``proposal_id`` is
    excluded from the digest and from the closed-schema check.
    """

    if not isinstance(proposal_body, Mapping):
        raise SchemaError("proposal body must be an object")
    body = dict(proposal_body)
    supplied = body.pop("proposal_id", None)
    if supplied is not None:
        validate_proposal_id_format(supplied, "proposal_id")
    if set(body) != set(_PROPOSAL_BODY_FIELDS):
        raise SchemaError("proposal body fields do not match the closed schema")
    # Validate the complete closed schema with a syntactically valid
    # placeholder id; the real id is the digest of exactly these fields.
    validate_payload("proposal", {**body, "proposal_id": "prop_" + "0" * 32})
    return PROPOSAL_ID_PREFIX + digest_json(body)[:32]


def validate_proposal_id(proposal_body: Mapping[str, Any]) -> str:
    """Recompute and check ``proposal_id``; return it when it matches."""

    derived = derive_proposal_id(proposal_body)
    if proposal_body.get("proposal_id") != derived:
        raise SchemaError("proposal_id does not match its body")
    return derived


def derive_eligible_set_digest(
    eligible_peer_ids: Sequence[str],
    affected_members: Sequence[Mapping[str, Any]],
) -> str:
    """Digest of the exact eligible voter set plus affected set used (§8).

    The set is canonicalized by sorting, so two peers that derived the same
    set from different arrival orders produce the same digest. This is a digest
    of identities and kinds only; it is not a tally and not a decision.
    """

    eligible = validate_list(list(eligible_peer_ids), "eligible_peer_ids", maximum=_MAX_AFFECTED)
    for index, peer in enumerate(eligible):
        validate_peer_id(peer, f"eligible_peer_ids[{index}]")
    if len(set(eligible)) != len(eligible):
        raise SchemaError("eligible_peer_ids contains duplicates")
    members = validate_list(list(affected_members), "affected_members", maximum=_MAX_AFFECTED)
    canonical_members: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, member in enumerate(members):
        if not isinstance(member, Mapping):
            raise SchemaError(f"affected_members[{index}] must be an object")
        kind = member.get("kind")
        if kind not in ("peer", "human-consumer"):
            raise SchemaError(f"invalid affected member kind at index {index}")
        peer_id = member.get("peer_id")
        consumer_id = member.get("consumer_id")
        if kind == "peer":
            if consumer_id is not None:
                raise SchemaError(f"affected_members[{index}] carries both id kinds")
            validate_peer_id(peer_id, f"affected_members[{index}].peer_id")
            member_id = peer_id
        else:
            if peer_id is not None:
                raise SchemaError(f"affected_members[{index}] carries both id kinds")
            validate_consumer_id(consumer_id, f"affected_members[{index}].consumer_id")
            member_id = consumer_id
        assert isinstance(member_id, str)
        if member_id in seen:
            raise SchemaError(f"duplicate affected member {member_id}")
        seen.add(member_id)
        role = member.get("role")
        validate_string(role, f"affected_members[{index}].role", maximum=128)
        assert isinstance(role, str)
        canonical_members.append({"member_id": member_id, "kind": kind, "role": role})
    canonical_members.sort(key=lambda entry: entry["member_id"])
    return digest_json(
        {
            "eligible": sorted(eligible),
            "affected": canonical_members,
        }
    )
