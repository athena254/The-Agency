"""Ordering-engine-agnostic block adapter for the Lattice state machine (Slice C).

Boundary/evidence note: this is protocol foundation, not peer actuation and not
release authorization. The adapter is pure and local: no network, no real
CometBFT process, no wall clock, no randomness and no hash-map iteration order.
Committed time is supplied by :class:`OrderingPlan` inputs, never read from a
clock. ABCI++ and a real ordering engine remain follow-ups.

An ordering engine decides *which* signed records are committed. This adapter
then imposes a content-addressed total order over those records, so every peer
replays the same block sequence and derives the same state-root chain regardless
of the order in which records were delivered locally. Intra-engine ordering is
therefore not relied upon; this is an explicit, documented choice of a bounded,
engine-independent foundation rather than a claim about live BFT ordering.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from agency.consensus.app_state import BlockContext, BlockResult, LatticeAppState
from agency.peers.canonical import (
    digest_hex,
    validate_digest,
    validate_integer,
    validate_string,
)
from agency.peers.membership import Membership

__all__ = [
    "DEFAULT_BLOCK_SIZE",
    "DEFAULT_RUNTIME_COUNT",
    "BlockPoint",
    "DerivationResult",
    "OrderingAdapter",
    "OrderingPlan",
    "StateRootChain",
    "canonical_order",
    "group_into_blocks",
]

DEFAULT_BLOCK_SIZE: Final = 4
DEFAULT_RUNTIME_COUNT: Final = 4
_MAX_BLOCK_SIZE: Final = 4096
_MAX_RUNTIMES: Final = 64


def canonical_order(records: Sequence[bytes]) -> tuple[bytes, ...]:
    """Content-addressed total order over a set of committed wire records."""

    return tuple(sorted(records, key=lambda wire: (digest_hex(wire), wire)))


def group_into_blocks(
    records: Sequence[bytes], *, block_size: int = DEFAULT_BLOCK_SIZE
) -> tuple[tuple[bytes, ...], ...]:
    """Deterministic fixed-size batching of the canonical ordered sequence."""

    validate_integer(block_size, 1, _MAX_BLOCK_SIZE, "block_size")
    ordered = canonical_order(records)
    return tuple(
        ordered[start : start + block_size] for start in range(0, len(ordered), block_size)
    )


@dataclass(frozen=True, slots=True)
class OrderingPlan:
    """Pinned, deterministic per-block header inputs."""

    chain_id: str
    membership_epoch: int
    membership_root: str
    block_size: int = DEFAULT_BLOCK_SIZE
    genesis_time_ms: int = 1000
    time_step_ms: int = 1000

    def __post_init__(self) -> None:
        validate_string(self.chain_id, "chain_id", maximum=128)
        validate_integer(self.block_size, 1, _MAX_BLOCK_SIZE, "block_size")
        validate_integer(self.membership_epoch, 1, (1 << 64) - 1, "membership_epoch")
        validate_integer(self.genesis_time_ms, 0, (1 << 63) - 1, "genesis_time_ms")
        validate_integer(self.time_step_ms, 0, (1 << 63) - 1, "time_step_ms")
        validate_digest(self.membership_root, "membership_root")

    def context(self, height: int) -> BlockContext:
        """Derive a committed-header context from pinned inputs only."""

        validate_integer(height, 1, (1 << 64) - 1, "height")
        return BlockContext(
            height=height,
            chain_id=self.chain_id,
            bft_time_ms=self.genesis_time_ms + height * self.time_step_ms,
            membership_epoch=self.membership_epoch,
            membership_root=self.membership_root,
        )


@dataclass(frozen=True, slots=True)
class BlockPoint:
    """One applied block and the state root it produced."""

    height: int
    state_root: str
    result: BlockResult


@dataclass(frozen=True, slots=True)
class StateRootChain:
    """One runtime's replay of the whole committed block sequence."""

    runtime_index: int
    head: str
    points: tuple[BlockPoint, ...]

    @property
    def roots(self) -> tuple[str, ...]:
        return tuple(point.state_root for point in self.points)


@dataclass(frozen=True, slots=True)
class DerivationResult:
    """Independent per-runtime chains over one committed record set."""

    chains: tuple[StateRootChain, ...]
    head: str

    @property
    def deterministic(self) -> bool:
        return len({chain.head for chain in self.chains}) == 1


class OrderingAdapter:
    """Drive independent :class:`LatticeAppState` runtimes over canonical blocks.

    Each :meth:`derive` is a pure full replay: fresh runtimes are created, so the
    same committed records always yield the same chains with no carried state.
    """

    def __init__(self, plan: OrderingPlan, *, runtime_count: int = DEFAULT_RUNTIME_COUNT) -> None:
        validate_integer(runtime_count, 1, _MAX_RUNTIMES, "runtime_count")
        self._plan = plan
        self._runtime_count = runtime_count

    def derive(self, records: Sequence[bytes], *, membership: Membership) -> DerivationResult:
        """Replay ``records`` across runtimes and return their state-root chains.

        ``membership`` supplies the pinned validator set used to construct each
        runtime; it is an explicit argument so no runtime is implicitly shared.
        """

        blocks = group_into_blocks(records, block_size=self._plan.block_size)
        chains: list[StateRootChain] = []
        for index in range(self._runtime_count):
            state = LatticeAppState(membership)
            points: list[BlockPoint] = []
            for height, block in enumerate(blocks, start=1):
                result = state.apply_block(self._plan.context(height), list(block))
                points.append(BlockPoint(height, result.app_hash, result))
            chains.append(StateRootChain(index, state.state_root, tuple(points)))
        return DerivationResult(tuple(chains), chains[0].head)
