"""Deterministic Slice B application state machine for the Lattice peer protocol.

Pure and ordering-engine agnostic: it processes an ordered block of signed peer
records and derives a byte-stable app hash. No wall clock, no floats, no
randomness, no network and no hash-map iteration order is used. BFT time comes
only from :class:`BlockContext`. ABCI++ integration is deliberately deferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, cast

from agency.peer_envelope import EnvelopeError, decode_envelope
from agency.peers.authority import AuthorityError, PeerAuthority, PeerRegistry
from agency.peers.canonical import canonical_json, digest_hex
from agency.peers.identities import PeerIdentity, PeerPublicKey
from agency.peers.membership import Membership, eligible_voter_ids

__all__ = [
    "ZERO_STATE_ROOT",
    "BlockContext",
    "BlockResult",
    "Decision",
    "LatticeAppState",
    "Rejection",
    "RejectionReason",
]

ZERO_STATE_ROOT: Final = "0" * 64
_BALLOT_KIND: Final = "ballot"


class RejectionReason(str, Enum):
    """Closed set of deterministic record-level rejection reasons."""

    INVALID_SIGNATURE = "invalid_signature"
    UNKNOWN_PEER = "unknown_peer"
    WRONG_EPOCH = "wrong_epoch"
    REPLAY = "replay"
    EQUIVOCATION = "equivocation"
    EXPIRED = "expired"
    MALFORMED = "malformed"
    UNSUPPORTED_RECORD_KIND = "unsupported_record_kind"


@dataclass(frozen=True, slots=True)
class BlockContext:
    """Committed-header inputs. ``bft_time_ms`` is never a wall-clock read."""

    height: int
    chain_id: str
    bft_time_ms: int
    membership_epoch: int
    membership_root: str


@dataclass(frozen=True, slots=True)
class Rejection:
    """One record the state machine refused, with a closed reason string."""

    payload_digest: str
    reason: str


@dataclass(frozen=True, slots=True)
class Decision:
    """Deterministic, derived governance outcome for one proposal."""

    proposal_id: str
    outcome: str
    approve: int
    deny: int
    abstain: int


@dataclass(frozen=True, slots=True)
class BlockResult:
    """Byte-stable output of applying one ordered block."""

    app_hash: str
    accepted: tuple[str, ...]
    rejected: tuple[Rejection, ...]
    decisions: tuple[Decision, ...]


@dataclass(frozen=True, slots=True)
class _PinnedKey:
    """Adapter exposing only the public half ``PeerRegistry.register`` reads."""

    public: PeerPublicKey


class LatticeAppState:
    """Pure, deterministic application state machine.

    Owns the only ``PeerAuthority``/``PeerRegistry`` used to write ballot state,
    so no external caller can mutate the tallies it derives app hashes from.
    """

    def __init__(self, membership: Membership) -> None:
        self._membership = membership
        registry = PeerRegistry()
        for member in membership.members:
            key = PeerPublicKey.from_fingerprint(member.public_key)
            registry.register(cast(PeerIdentity, _PinnedKey(public=key)))
        self._authority = PeerAuthority(membership, registry)
        self._seen_proposals: set[str] = set()
        self._state_root = ZERO_STATE_ROOT

    @property
    def state_root(self) -> str:
        """App hash of the last applied block (the chain head)."""

        return self._state_root

    def apply_block(self, ctx: BlockContext, records: list[bytes]) -> BlockResult:
        """Execute one ordered block and advance the state root."""

        accepted: list[str] = []
        rejected: list[Rejection] = []
        record_order: list[str] = []
        for record in records:
            self._process_record(ctx, record, accepted, rejected, record_order)
        decisions = self._compute_decisions()
        app_hash = self._compute_app_hash(ctx, accepted, rejected, decisions, record_order)
        self._state_root = app_hash
        return BlockResult(
            app_hash=app_hash,
            accepted=tuple(accepted),
            rejected=tuple(rejected),
            decisions=tuple(decisions),
        )

    def _process_record(
        self,
        ctx: BlockContext,
        record: bytes,
        accepted: list[str],
        rejected: list[Rejection],
        record_order: list[str],
    ) -> None:
        try:
            env = decode_envelope(record)
        except EnvelopeError:
            handle = digest_hex(record)
            record_order.append(handle)
            rejected.append(Rejection(handle, RejectionReason.MALFORMED.value))
            return

        payload_digest = str(env["payload_digest"])
        record_order.append(payload_digest)
        if env["kind"] != _BALLOT_KIND:
            rejected.append(
                Rejection(payload_digest, RejectionReason.UNSUPPORTED_RECORD_KIND.value)
            )
            return

        precheck_reason = self._precheck(ctx, env)
        if precheck_reason is not None:
            rejected.append(Rejection(payload_digest, precheck_reason.value))
            return

        try:
            msg = self._authority.submit_ballot(record)
        except AuthorityError as exc:
            reason = self._classify(str(exc))
            rejected.append(Rejection(payload_digest, reason.value))
            return

        accepted.append(msg.payload_digest)
        self._seen_proposals.add(str(msg.payload["proposal_id"]))

    def _precheck(self, ctx: BlockContext, env: dict[str, Any]) -> RejectionReason | None:
        """Classify failures the authority collapses into one prerequisite error."""

        if (
            int(env["membership_epoch"]) != self._membership.epoch
            or str(env["membership_root"]) != self._membership.root
        ):
            return RejectionReason.WRONG_EPOCH
        sender = str(env["sender_peer_id"])
        if sender not in eligible_voter_ids(self._membership):
            return RejectionReason.UNKNOWN_PEER
        issued = int(env["issued_at_ms"])
        expires = int(env["expires_at_ms"])
        if expires <= issued or expires <= ctx.bft_time_ms:
            return RejectionReason.EXPIRED
        if int(env["monotonic_seq"]) <= self._authority.registry.last_seq(sender):
            return RejectionReason.REPLAY
        return None

    def _classify(self, text: str) -> RejectionReason:
        """Map the authority's fail-closed error text onto a closed reason."""

        if "equivocation" in text:
            return RejectionReason.EQUIVOCATION
        if "unknown peer" in text or "not eligible" in text:
            return RejectionReason.UNKNOWN_PEER
        if "epoch" in text:
            return RejectionReason.WRONG_EPOCH
        if "external prerequisite failed" in text:
            return RejectionReason.REPLAY
        lowered = text.lower()
        if any(
            marker in lowered
            for marker in ("signature", "fingerprint", "sender does not match", "digest mismatch")
        ):
            return RejectionReason.INVALID_SIGNATURE
        return RejectionReason.MALFORMED

    def _compute_decisions(self) -> list[Decision]:
        decisions: list[Decision] = []
        for proposal_id in sorted(self._seen_proposals):
            tally = self._authority.tally(proposal_id)
            decisions.append(
                Decision(
                    proposal_id=proposal_id,
                    outcome=self._authority.decide(proposal_id),
                    approve=tally["approve"],
                    deny=tally["deny"],
                    abstain=tally["abstain"],
                )
            )
        return decisions

    def _compute_app_hash(
        self,
        ctx: BlockContext,
        accepted: list[str],
        rejected: list[Rejection],
        decisions: list[Decision],
        record_order: list[str],
    ) -> str:
        structure: dict[str, Any] = {
            "height": ctx.height,
            "chain_id": ctx.chain_id,
            "bft_time_ms": ctx.bft_time_ms,
            "membership_epoch": ctx.membership_epoch,
            "membership_root": ctx.membership_root,
            "state_root": self._state_root,
            "accepted": sorted(accepted),
            "rejected": sorted((item.payload_digest, item.reason) for item in rejected),
            "decisions": sorted(
                (d.proposal_id, d.outcome, d.approve, d.deny, d.abstain) for d in decisions
            ),
            "ordered_payload_digests": record_order,
        }
        return digest_hex(canonical_json(structure))
