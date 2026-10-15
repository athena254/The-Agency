"""One process-local Lattice generation with explicit resource ownership.

Leases account for lifetime only, not peer identity or security authority.
Legacy getters retain one pin until a quiescent test reset.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field

import structlog

from agency.lattice.api import Lattice
from agency.lattice.config import get_config
from agency.lattice.models import LatticeConfig

log = structlog.get_logger(__name__)


@dataclass(eq=False)
class _Generation:
    lattice: Lattice
    config: LatticeConfig
    key: object = field(default_factory=object)
    tokens: set[object] = field(default_factory=set)
    legacy_pin: bool = False
    ready: bool = False


@dataclass(eq=False)
class _Manager:
    loop: asyncio.AbstractEventLoop
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    generation: _Generation | None = None
    initializing: bool = False
    users: int = 0


_manager: _Manager | None = None


def _check_loop(manager: _Manager) -> None:
    if manager.loop is not asyncio.get_running_loop():
        raise RuntimeError("Lattice ownership belongs to a different event loop")


@asynccontextmanager
async def _guard(manager: _Manager | None = None) -> AsyncIterator[_Manager]:
    global _manager
    if manager is None:
        if _manager is None:
            _manager = _Manager(asyncio.get_running_loop())
        manager = _manager
    _check_loop(manager)
    manager.users += 1
    try:
        async with manager.lock:
            yield manager
    finally:
        manager.users -= 1
        if manager is _manager and manager.users == 0 and manager.generation is None:
            _manager = None


class LatticeLease:
    """Opaque manager-issued lifetime claim; copies share the same token."""

    __slots__ = ("_generation", "_manager", "_token")

    _generation: _Generation
    _manager: _Manager
    _token: object

    def __init__(self) -> None:
        raise TypeError("LatticeLease must be issued by acquire_lattice()")

    def __copy__(self) -> LatticeLease:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> LatticeLease:
        return self

    @classmethod
    def _issue(cls, manager: _Manager, generation: _Generation) -> LatticeLease:
        lease = object.__new__(cls)
        lease._manager = manager
        lease._generation = generation
        lease._token = object()
        generation.tokens.add(lease._token)
        return lease

    @property
    def lattice(self) -> Lattice:
        return self._generation.lattice

    async def release(self) -> None:
        """Consume this generation's token once, closing only its final owner."""
        async with _guard(self._manager) as manager:
            generation = self._generation
            if self._token not in generation.tokens:
                return
            generation.tokens.remove(self._token)
            if not generation.tokens and not generation.legacy_pin:
                await _retire(manager, generation)


async def _finish_cleanup(task: asyncio.Task[None], *, propagate_cancel: bool) -> None:
    """Own and drain cleanup despite repeated cancellation of its caller."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled and propagate_cancel:
        raise asyncio.CancelledError


async def _retire(manager: _Manager, generation: _Generation) -> None:
    generation.ready = False
    await _finish_cleanup(
        asyncio.create_task(generation.lattice._close_managed(generation.key)),
        propagate_cancel=True,
    )
    manager.generation = None


async def _ready(manager: _Manager, config: LatticeConfig | None) -> _Generation:
    generation = manager.generation
    if generation is not None:
        if not generation.ready:
            raise RuntimeError("Lattice cleanup required; use quiescent reset_lattice()")
        if config is not None and deepcopy(config) != generation.config:
            raise ValueError("Lattice config differs from the active generation")
        return generation
    snapshot = deepcopy(config if config is not None else get_config())
    lattice = Lattice(deepcopy(snapshot))
    generation = _Generation(lattice, snapshot)
    manager.generation = generation
    manager.initializing = True
    try:
        await lattice.initialize()
        lattice._managed_by = generation.key
        generation.ready = True
    except BaseException:
        try:
            await _finish_cleanup(asyncio.create_task(lattice.close()), propagate_cancel=False)
        except BaseException as cleanup:  # noqa: BLE001 - preserve primary startup failure
            lattice._managed_by = generation.key
            log.warning(
                "lattice.factory.startup_cleanup_failed", error_class=type(cleanup).__name__
            )
        else:
            manager.generation = None
        raise
    finally:
        manager.initializing = False
    return generation


async def acquire_lattice(config: LatticeConfig | None = None) -> LatticeLease:
    """Acquire one lease on the ready process-local generation."""
    async with _guard() as manager:
        generation = await _ready(manager, config)
        return LatticeLease._issue(manager, generation)


async def get_lattice(config: LatticeConfig | None = None) -> Lattice:
    """Borrow a ready Lattice, retaining one explicit legacy pin until reset."""
    async with _guard() as manager:
        generation = await _ready(manager, config)
        generation.legacy_pin = True
        return generation.lattice


async def reset_lattice() -> None:
    """Test-only reset; never invalidate an active lease or initializer."""
    if _manager is not None:
        _check_loop(_manager)
        generation = _manager.generation
        if _manager.initializing or (generation is not None and generation.tokens):
            raise RuntimeError("Cannot reset Lattice with active leases or initialization")
    async with _guard() as manager:
        generation = manager.generation
        if manager.initializing or (generation is not None and generation.tokens):
            raise RuntimeError("Cannot reset Lattice with active leases or initialization")
        if generation is not None:
            generation.legacy_pin = False
            await _retire(manager, generation)


__all__ = ["LatticeLease", "acquire_lattice", "get_lattice", "reset_lattice"]
