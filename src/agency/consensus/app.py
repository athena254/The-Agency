"""Transport-agnostic ABCI++ application logic for the Lattice peer protocol.

Pure app logic to be hosted by a CometBFT ABCI server: no sockets, no wire codec,
no wall clock, no floats and no randomness. The only time input is ``ctx.bft_time_ms``.

Determinism contract (``docs/RESEARCH_LATTICE_FULL_BUILD.md`` §3.1):
- ``prepare_proposal`` is proposer-only, MAY be non-deterministic and never touches
  committed state.
- ``process_proposal`` and ``verify_vote_extension`` MUST be deterministic and
  read-only, and MUST return ACCEPT/REJECT only (UNKNOWN crashes CometBFT).
- ``finalize_block`` is deterministic and executes through ``LatticeAppState`` but
  must not persist; ``commit`` is the only persistence step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from agency.consensus.app_state import BlockContext, BlockResult, LatticeAppState
from agency.peer_envelope import EnvelopeError, decode_envelope
from agency.peers.membership import Membership, eligible_voter_ids

__all__ = ["AgencyApplication", "AppInfo"]

_ACCEPT: Final = "ACCEPT"
_REJECT: Final = "REJECT"
_BALLOT_KIND: Final = "ballot"
_APP_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class AppInfo:
    """Deterministic snapshot reported by ``Info`` during replay/startup."""

    last_block_height: int
    last_block_app_hash: str
    app_version: int = _APP_VERSION


def _is_valid_proposal_ballot(
    tx: bytes,
    ctx: BlockContext,
    membership: Membership,
    committed_seq: dict[str, int],
) -> bool:
    """Deterministic, read-only precheck for one ProcessProposal transaction.

    This mirrors the record-level checks in ``LatticeAppState._precheck`` without
    importing that private helper: a decodable ballot, pinned epoch/root, known
    voter, unexpired against BFT time, and a positive monotonic sequence that
    advances the committed per-sender replay state. Cross-block equivocation and
    replay against uncommitted ballots are FinalizeBlock's job via ``apply_block``;
    this check must not mutate state or track block-local order.
    """
    try:
        env = decode_envelope(tx)
    except EnvelopeError:
        return False
    if env["kind"] != _BALLOT_KIND:
        return False
    if (
        int(env["membership_epoch"]) != membership.epoch
        or str(env["membership_root"]) != membership.root
    ):
        return False
    sender = str(env["sender_peer_id"])
    if sender not in eligible_voter_ids(membership):
        return False
    issued = int(env["issued_at_ms"])
    expires = int(env["expires_at_ms"])
    if expires <= issued or expires <= ctx.bft_time_ms:
        return False
    seq = int(env["monotonic_seq"])
    return seq > 0 and seq > committed_seq.get(sender, 0)


class AgencyApplication:
    """Pure, transport-agnostic ABCI application logic (one instance per peer)."""

    def __init__(self, membership: Membership) -> None:
        self._membership = membership
        self._state = LatticeAppState(membership)
        self._height = 0
        self._dirty = False
        self._committed_seq: dict[str, int] = {}
        self._pending_seq: dict[str, int] = {}

    def info(self) -> AppInfo:
        """Report the committed height and app hash (never uncommitted work)."""

        return AppInfo(
            last_block_height=self._height,
            last_block_app_hash=self._state.state_root,
        )

    def check_tx(self, tx: bytes) -> tuple[int, str]:
        """Deterministic mempool admission precheck (CheckTx).

        Mirrors ``process_proposal``'s record-level checks against committed
        state only. Expiry is intentionally NOT checked here: it depends on
        block BFT time, which a CheckTx request does not carry; expiry is
        enforced deterministically at ProcessProposal/FinalizeBlock. A
        committed replay is refused here, so a rebroadcast can neither enter
        the mempool nor reach a block. Return is ``(code, log)`` with
        ``code == 0`` meaning admitted for the mempool.
        """
        ctx = BlockContext(
            height=self._height,
            chain_id="agency",
            bft_time_ms=0,
            membership_epoch=self._membership.epoch,
            membership_root=self._membership.root,
        )
        if _is_valid_proposal_ballot(tx, ctx, self._membership, self._committed_seq):
            return 0, "ok"
        return 1, "invalid ballot: rejected by deterministic precheck"

    def prepare_proposal(self, txs: list[bytes], max_tx_bytes: int) -> list[bytes]:
        """Proposer-only selection: whole txs in order up to the byte cap."""

        selected: list[bytes] = []
        total = 0
        for tx in txs:
            size = len(tx)
            if total + size > max_tx_bytes:
                continue
            selected.append(tx)
            total += size
        return selected

    def process_proposal(self, txs: list[bytes], ctx: BlockContext) -> str:
        """Deterministic, read-only ACCEPT/REJECT for the exact ordered block."""

        for tx in txs:
            if not _is_valid_proposal_ballot(tx, ctx, self._membership, self._committed_seq):
                return _REJECT
        return _ACCEPT

    def extend_vote(self, height: int, app_hash: str) -> bytes:
        """No vote extension is emitted in this increment."""

        return b""

    def verify_vote_extension(self, height: int, app_hash: str, extension: bytes) -> str:
        """Deterministic accept: this increment emits no extensions to verify."""

        return _ACCEPT

    def finalize_block(self, txs: list[bytes], ctx: BlockContext) -> BlockResult:
        """Execute one block deterministically, without persisting it."""

        result = self._state.apply_block(ctx, txs)
        accepted = set(result.accepted)
        staged: dict[str, int] = {}
        for tx in txs:
            try:
                env = decode_envelope(tx)
            except EnvelopeError:
                continue
            if str(env["payload_digest"]) not in accepted:
                continue
            sender = str(env["sender_peer_id"])
            seq = int(env["monotonic_seq"])
            if seq > staged.get(sender, 0):
                staged[sender] = seq
        self._pending_seq = staged
        self._dirty = True
        return result

    def commit(self) -> str:
        """Persist the finalized block exactly once and return its app hash."""

        if self._dirty:
            for sender, seq in self._pending_seq.items():
                if seq > self._committed_seq.get(sender, 0):
                    self._committed_seq[sender] = seq
            self._height += 1
            self._dirty = False
        return self._state.state_root
