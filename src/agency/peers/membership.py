"""Versioned membership sets, epochs, and the approved validator policy.

A membership set is pinned by both an epoch number and a root digest; a citation
of one without the other is invalid. Membership is provisioned out-of-band
(D-02 genesis, then signed membership proposals); there is no open enrollment.

Nothing in this module is authority. It types and validates the set a caller
claims to operate under; it cannot make that set true.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Final

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agency.peers.canonical import (
    MAX_MEMBERS,
    SchemaError,
    digest_json,
    validate_closed,
    validate_digest,
    validate_integer,
    validate_list,
    validate_peer_id,
    validate_string,
    validate_u64,
)
from agency.peers.identities import derive_peer_id_from_raw, public_key_from_fingerprint

__all__ = [
    "APPROVAL_DENOMINATOR",
    "APPROVAL_NUMERATOR",
    "GENESIS_EPOCH",
    "GENESIS_PREV_DIGEST",
    "ZERO_DIGEST",
    "Member",
    "Membership",
    "Removal",
    "derive_genesis_membership",
    "derive_membership_root",
    "derive_next_membership",
    "eligible_voter_ids",
    "find_member",
    "membership_from_payload",
    "quorum_approvals_required",
    "validate_membership",
]

GENESIS_EPOCH: Final = 1
GENESIS_PREV_DIGEST: Final = "0" * 64
ZERO_DIGEST: Final = "0" * 64

# D-01: 4 validators, 1 Byzantine fault, 3/4 application approval quorum.
APPROVAL_NUMERATOR: Final = 3
APPROVAL_DENOMINATOR: Final = 4

_MAX_ROLES: Final = 8
_MAX_REMOVED: Final = MAX_MEMBERS
_MEMBER_KEYS: Final = frozenset({"peer_id", "public_key", "roles", "joined_epoch"})
_REMOVAL_KEYS: Final = frozenset({"peer_id", "removed_epoch", "reason_digest"})
_PAYLOAD_KEYS: Final = frozenset(
    {"epoch", "members", "removed", "policy_version", "prev_epoch_digest"}
)


@dataclass(frozen=True, slots=True)
class Member:
    """One peer in a membership set."""

    peer_id: str
    public_key: str
    roles: tuple[str, ...]
    joined_epoch: int

    def __post_init__(self) -> None:
        validate_peer_id(self.peer_id, "peer_id")
        validate_digest(self.public_key, "public_key")
        if type(self.roles) is not tuple:
            raise SchemaError("roles must be a tuple")
        if not 1 <= len(self.roles) <= _MAX_ROLES:
            raise SchemaError("member must declare at least one role")
        for index, role in enumerate(self.roles):
            validate_string(role, f"roles[{index}]", maximum=64)
        validate_u64(self.joined_epoch, "joined_epoch")

    def to_record(self) -> dict[str, Any]:
        return {
            "peer_id": self.peer_id,
            "public_key": self.public_key,
            "roles": list(self.roles),
            "joined_epoch": self.joined_epoch,
        }


@dataclass(frozen=True, slots=True)
class Removal:
    """A peer removed at an epoch boundary, with the reason digest."""

    peer_id: str
    removed_epoch: int
    reason_digest: str

    def __post_init__(self) -> None:
        validate_peer_id(self.peer_id, "peer_id")
        validate_u64(self.removed_epoch, "removed_epoch")
        validate_digest(self.reason_digest, "reason_digest")

    def to_record(self) -> dict[str, Any]:
        return {
            "peer_id": self.peer_id,
            "removed_epoch": self.removed_epoch,
            "reason_digest": self.reason_digest,
        }


@dataclass(frozen=True, slots=True)
class Membership:
    """A complete, versioned membership set.

    ``root`` is derived from the canonical projection and is immutable for the
    life of the set. ``prev_epoch_digest`` links to the prior epoch's root.
    """

    epoch: int
    members: tuple[Member, ...]
    removed: tuple[Removal, ...]
    policy_version: str
    prev_epoch_digest: str
    root: str

    def __post_init__(self) -> None:
        validate_u64(self.epoch, "epoch")
        if self.epoch < GENESIS_EPOCH:
            raise SchemaError("epoch must be >= 1")
        if type(self.members) is not tuple or type(self.removed) is not tuple:
            raise SchemaError("members and removed must be tuples")
        validate_membership(
            epoch=self.epoch,
            members=[member.to_record() for member in self.members],
            removed=[removal.to_record() for removal in self.removed],
            policy_version=self.policy_version,
            prev_epoch_digest=self.prev_epoch_digest,
        )
        expected = derive_membership_root(self.to_projection())
        if self.root != expected:
            raise SchemaError("membership root does not match its contents")

    @property
    def member_count(self) -> int:
        return len(self.members)

    def to_projection(self) -> dict[str, Any]:
        """Canonical projection hashed into ``root`` (§13.1)."""

        return {
            "epoch": self.epoch,
            "members": [member.to_record() for member in self.members],
            "removed": [removal.to_record() for removal in self.removed],
            "policy_version": self.policy_version,
            "prev_epoch_digest": self.prev_epoch_digest,
        }

    def to_payload(self) -> dict[str, Any]:
        """Wire payload for a ``membership`` message (§13)."""

        return self.to_projection()

    def has_member(self, peer_id: str) -> bool:
        return any(member.peer_id == peer_id for member in self.members)

    def role_of(self, peer_id: str) -> tuple[str, ...] | None:
        for member in self.members:
            if member.peer_id == peer_id:
                return member.roles
        return None


def derive_membership_root(projection: dict[str, Any]) -> str:
    """SHA-256 of the canonical membership projection (§13.1)."""

    return digest_json(projection)


def validate_membership(
    *,
    epoch: int,
    members: list[dict[str, Any]],
    removed: list[dict[str, Any]],
    policy_version: str,
    prev_epoch_digest: str,
) -> None:
    """Closed validation of a membership set, including key/identity binding."""

    validate_u64(epoch, "epoch")
    if epoch < GENESIS_EPOCH:
        raise SchemaError("epoch must be >= 1")
    validate_string(policy_version, "policy_version", maximum=128)
    validate_digest(prev_epoch_digest, "prev_epoch_digest")
    if len(members) > MAX_MEMBERS:
        raise SchemaError(f"membership exceeds the {MAX_MEMBERS}-peer bound")
    if not members:
        raise SchemaError("membership must contain at least one peer")
    seen: set[str] = set()
    for index, member in enumerate(members):
        label = f"members[{index}]"
        if not isinstance(member, dict):
            raise SchemaError(f"{label} must be an object")
        validate_closed(member, _MEMBER_KEYS, label)
        validate_peer_id(member["peer_id"], f"{label}.peer_id")
        validate_digest(member["public_key"], f"{label}.public_key")
        if member["peer_id"] in seen:
            raise SchemaError(f"duplicate member {member['peer_id']}")
        seen.add(member["peer_id"])
        # The peer_id must be the digest of the listed public key. A set that
        # claims an identity without the matching key is not a membership set.
        key = public_key_from_fingerprint(member["public_key"])
        raw = key.public_bytes(Encoding.Raw, PublicFormat.Raw)
        if derive_peer_id_from_raw(raw) != member["peer_id"]:
            raise SchemaError(f"{label}.peer_id does not match its public key")
        roles = validate_list(member["roles"], f"{label}.roles", maximum=_MAX_ROLES)
        if not roles:
            raise SchemaError(f"{label} must declare at least one role")
        for role_index, role in enumerate(roles):
            validate_string(role, f"{label}.roles[{role_index}]", maximum=64)
        validate_u64(member["joined_epoch"], f"{label}.joined_epoch")
        if member["joined_epoch"] > epoch:
            raise SchemaError(f"{label}.joined_epoch cannot exceed the membership epoch")
    if len(removed) > _MAX_REMOVED:
        raise SchemaError(f"removals exceed the {_MAX_REMOVED}-peer bound")
    for index, entry in enumerate(removed):
        label = f"removed[{index}]"
        if not isinstance(entry, dict):
            raise SchemaError(f"{label} must be an object")
        validate_closed(entry, _REMOVAL_KEYS, label)
        validate_peer_id(entry["peer_id"], f"{label}.peer_id")
        validate_u64(entry["removed_epoch"], f"{label}.removed_epoch")
        validate_digest(entry["reason_digest"], f"{label}.reason_digest")
        if entry["removed_epoch"] > epoch:
            raise SchemaError(f"{label}.removed_epoch cannot exceed the membership epoch")


def derive_genesis_membership(members: list[dict[str, Any]], *, policy_version: str) -> Membership:
    """Build the epoch-1 membership set from the operator genesis ceremony.

    ``prev_epoch_digest`` is the all-zero genesis sentinel: epoch 1 has no
    predecessor, and a zero digest can never be a real membership root.
    """

    if type(members) is not list:
        raise SchemaError("genesis members must be a list")
    return _build_membership(
        epoch=GENESIS_EPOCH,
        members=members,
        removed=[],
        policy_version=policy_version,
        prev_epoch_digest=GENESIS_PREV_DIGEST,
    )


def derive_next_membership(
    current: Membership,
    *,
    members: list[dict[str, Any]],
    removed: list[dict[str, Any]] | None = None,
    policy_version: str | None = None,
) -> Membership:
    """Build epoch ``current.epoch + 1`` chained to the current root (§13.2)."""

    return _build_membership(
        epoch=current.epoch + 1,
        members=members,
        removed=removed or [],
        policy_version=policy_version or current.policy_version,
        prev_epoch_digest=current.root,
    )


def _build_membership(
    *,
    epoch: int,
    members: list[dict[str, Any]],
    removed: list[dict[str, Any]],
    policy_version: str,
    prev_epoch_digest: str,
) -> Membership:
    validate_membership(
        epoch=epoch,
        members=members,
        removed=removed,
        policy_version=policy_version,
        prev_epoch_digest=prev_epoch_digest,
    )
    projection = {
        "epoch": epoch,
        "members": [dict(member) for member in members],
        "removed": [dict(removal) for removal in removed],
        "policy_version": policy_version,
        "prev_epoch_digest": prev_epoch_digest,
    }
    return Membership(
        epoch=epoch,
        members=tuple(
            Member(
                peer_id=member["peer_id"],
                public_key=member["public_key"],
                roles=tuple(member["roles"]),
                joined_epoch=member["joined_epoch"],
            )
            for member in members
        ),
        removed=tuple(
            Removal(
                peer_id=removal["peer_id"],
                removed_epoch=removal["removed_epoch"],
                reason_digest=removal["reason_digest"],
            )
            for removal in removed
        ),
        policy_version=policy_version,
        prev_epoch_digest=prev_epoch_digest,
        root=derive_membership_root(projection),
    )


def membership_from_payload(payload: Any) -> Membership:
    """Reconstruct and re-derive a membership set from its wire payload.

    The root is recomputed, never trusted from the payload; a payload whose
    contents do not hash to a claimed root is rejected by :class:`Membership`.
    """

    if not isinstance(payload, dict):
        raise SchemaError("membership payload must be an object")
    validate_closed(payload, _PAYLOAD_KEYS, "membership payload")
    return _build_membership(
        epoch=payload["epoch"],
        members=list(payload["members"]),
        removed=list(payload["removed"]),
        policy_version=payload["policy_version"],
        prev_epoch_digest=payload["prev_epoch_digest"],
    )


def eligible_voter_ids(membership: Membership) -> tuple[str, ...]:
    """The epoch's eligible voter set, in membership order."""

    return tuple(member.peer_id for member in membership.members)


def find_member(membership: Membership, peer_id: str) -> Member | None:
    for member in membership.members:
        if member.peer_id == peer_id:
            return member
    return None


def quorum_approvals_required(member_count: int) -> int:
    """Approvals required under the approved 3/4 application quorum (D-01).

    This is a pure arithmetic helper over a membership size. It is not a tally,
    not a vote counter, and not authority; the caller must still verify every
    ballot's signature, eligibility, and non-equivocation.
    """

    validate_integer(member_count, 1, MAX_MEMBERS, "member_count")
    return math.ceil(member_count * APPROVAL_NUMERATOR / APPROVAL_DENOMINATOR)
