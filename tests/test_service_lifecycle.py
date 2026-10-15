"""Owned service cleanup, using real temporary SQLite and no global Lattice.

The fresh per-start Lattice double deliberately does not certify the factory's
cached-closed singleton or multi-consumer ownership; those remain follow-ups.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

import agency.butler.service as butler_module
import agency.orchestrator as orchestrator_module
from agency.butler.service import ButlerService
from agency.butler.threads import ThreadStore
from agency.evidence.store.store import EvidenceStore
from agency.kernel.audit import AuditLog
from agency.memory.sms.store import MemoryStore
from agency.orchestrator import AgencyOrchestrator


@dataclass
class StoreProbe:
    store: Any
    initialize_calls: int = 0
    allocations: int = 0
    close_calls: int = 0
    releases: int = 0

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        initialize = self.store.initialize
        close = self.store.close

        async def counted_initialize() -> Any:
            self.initialize_calls += 1
            before = self.store._conn
            result = await initialize()
            self.allocations += int(before is None and self.store._conn is not None)
            return result

        async def counted_close() -> None:
            self.close_calls += 1
            before = self.store._conn
            await close()
            self.releases += int(before is not None and self.store._conn is None)

        monkeypatch.setattr(self.store, "initialize", counted_initialize)
        monkeypatch.setattr(self.store, "close", counted_close)


@dataclass
class LifecycleRig:
    orchestrator: AgencyOrchestrator
    probes: dict[str, StoreProbe]
    lattices: list[Any] = field(default_factory=list)


@pytest_asyncio.fixture
async def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    audit = AuditLog(tmp_path / "orchestrator-audit.db")
    evidence = EvidenceStore(tmp_path / "evidence.db")
    butler_audit = AuditLog(tmp_path / "butler-audit.db")
    monkeypatch.setattr(orchestrator_module, "AuditLog", lambda *_: audit)
    monkeypatch.setattr(orchestrator_module, "EvidenceStore", lambda: evidence)
    monkeypatch.setattr(butler_module, "AuditLog", lambda *_: butler_audit)
    orch = AgencyOrchestrator(memory_db_path=str(tmp_path / "memory.db"))
    probes = {
        "memory": StoreProbe(orch._memory_store),
        "evidence": StoreProbe(evidence),
        "audit": StoreProbe(audit),
        "butler_audit": StoreProbe(butler_audit),
    }
    # Original closers also run after failed assertions: RED must not leak.
    closers = [probe.store.close for probe in probes.values()]
    for probe in probes.values():
        probe.install(monkeypatch)
    result = LifecycleRig(orch, probes)

    class FreshLattice:
        closes = 0

        async def close(self) -> None:
            self.closes += 1

        async def register_agent(self, **_kwargs: Any) -> None:
            pass

    async def fresh_lattice() -> FreshLattice:
        lattice = FreshLattice()
        result.lattices.append(lattice)
        return lattice

    monkeypatch.setattr(orchestrator_module, "get_lattice", fresh_lattice)
    try:
        yield result
    finally:
        for close in closers:
            await close()


@pytest.mark.asyncio
async def test_orchestrator_closes_owned_sqlite_once_per_allocation(rig: LifecycleRig) -> None:
    orch = rig.orchestrator
    await orch.start()
    try:
        assert all(rig.probes[name].allocations == 1 for name in ("memory", "evidence", "audit"))
        await orch.stop()
        await orch.stop()
        for name in ("memory", "evidence", "audit"):
            probe = rig.probes[name]
            assert probe.releases == probe.allocations == 1, name
            assert probe.store._conn is None, name
        assert rig.lattices[0].closes == 1
        assert not orch._started
        assert orch._lattice is None
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_orchestrator_repeated_start_and_supported_restart(rig: LifecycleRig) -> None:
    orch = rig.orchestrator
    await orch.start()
    first_tools = orch._tool_registry
    await orch.start()
    assert len(rig.lattices) == 1
    for name in ("memory", "evidence", "audit"):
        assert rig.probes[name].initialize_calls == 1
    await orch.stop()
    assert orch._tool_registry is None
    assert orch._tool_driver is None
    await orch.start()
    assert orch._tool_registry is not first_tools
    assert len(rig.lattices) == 2
    await orch.stop()
    for name in ("memory", "evidence", "audit"):
        assert rig.probes[name].allocations == rig.probes[name].releases == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["memory", "evidence", "audit", "lattice", "tools"])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_orchestrator_startup_rolls_back(
    rig: LifecycleRig, monkeypatch: pytest.MonkeyPatch, boundary: str, cancelled: bool
) -> None:
    import asyncio

    orch = rig.orchestrator
    primary = asyncio.CancelledError("startup") if cancelled else RuntimeError("startup")
    if boundary in rig.probes:
        probe = rig.probes[boundary]
        initialize = probe.store.initialize

        async def fail_initialize() -> None:
            await initialize()
            raise primary

        monkeypatch.setattr(probe.store, "initialize", fail_initialize)
    elif boundary == "lattice":

        async def fail_lattice() -> None:
            raise primary

        monkeypatch.setattr(orchestrator_module, "get_lattice", fail_lattice)
    else:

        def fail_tools() -> None:
            orch._tool_registry = object()  # type: ignore[assignment]
            raise primary

        monkeypatch.setattr(orch, "_build_tool_layer", fail_tools)
    # A rollback failure must not replace the startup failure, or skip memory.
    close = orch._evidence_store.close

    async def fail_close() -> None:
        await close()
        raise ValueError("cleanup")

    monkeypatch.setattr(orch._evidence_store, "close", fail_close)
    with pytest.raises(type(primary)) as caught:
        await orch.start()
    assert caught.value is primary
    assert not orch._started
    assert orch._tool_registry is None
    assert orch._tool_driver is None
    assert orch._lattice is None
    for name in ("memory", "evidence", "audit"):
        assert rig.probes[name].store._conn is None, name
        assert rig.probes[name].allocations == rig.probes[name].releases, name
    assert all(lattice.closes == 1 for lattice in rig.lattices)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["periodic", "lattice", "audit", "evidence", "memory"])
async def test_orchestrator_stop_attempts_all_cleanup(
    rig: LifecycleRig, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    orch = rig.orchestrator
    await orch.start()
    target = {
        "periodic": (orch._memory_lifecycle, "stop_periodic"),
        "lattice": (rig.lattices[0], "close"),
        "audit": (orch._audit_log, "close"),
        "evidence": (orch._evidence_store, "close"),
        "memory": (orch._memory_store, "close"),
    }[boundary]
    original = getattr(*target)
    primary = RuntimeError("cleanup")

    async def fail_once() -> None:
        monkeypatch.setattr(*target, original)
        await original()
        raise primary

    monkeypatch.setattr(*target, fail_once)
    with pytest.raises(RuntimeError) as caught:
        await orch.stop()
    assert caught.value is primary
    assert not orch._started
    assert orch._tool_registry is orch._tool_driver is None
    for name in ("memory", "evidence", "audit"):
        assert rig.probes[name].releases == 1, name
    await orch.stop()


@pytest.mark.asyncio
async def test_butler_owned_resources_restart_without_stale_helpers(rig: LifecycleRig) -> None:
    service = ButlerService(orchestrator=rig.orchestrator)
    await service.start()
    await service.start()
    commands = service.commands
    engine = service.thread_context_engine
    threads = service.thread_store
    await service.stop()
    await service.stop()
    assert rig.probes["butler_audit"].releases == 1
    assert rig.probes["memory"].releases == 1
    assert rig.probes["memory"].initialize_calls == 1
    assert service._threads is service._commands is service._engine is service._lattice is None
    with pytest.raises(RuntimeError, match="ThreadStore is closed"):
        threads.create_workspace("owner", "closed")
    await service.start()
    assert service.commands is not commands
    assert service.thread_context_engine is not engine
    service.create_workspace("owner", "restarted")
    await service.stop()
    assert rig.probes["butler_audit"].releases == 2
    assert rig.probes["memory"].releases == 2


@pytest.mark.asyncio
async def test_butler_borrowed_stores_remain_usable(rig: LifecycleRig, tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "borrowed-audit.db")
    memory = MemoryStore(str(tmp_path / "borrowed-memory.db"))
    threads = ThreadStore(str(tmp_path / "borrowed-threads.db"))
    service = ButlerService(
        orchestrator=rig.orchestrator, audit_log=audit, memory_store=memory, thread_store=threads
    )
    try:
        await service.start()
        await service.stop()
        assert audit._conn is not None
        assert memory._conn is not None
        assert await audit.query() == []
        assert await memory.list_all() == []
        threads.create_workspace("owner", "still usable")
        assert rig.probes["memory"].releases == 1
    finally:
        await audit.close()
        await memory.close()
        threads.close()


@pytest.mark.asyncio
async def test_butler_stop_before_start_closes_lazy_threads(rig: LifecycleRig) -> None:
    service = ButlerService(orchestrator=rig.orchestrator)
    threads = service.thread_store
    await service.stop()
    assert service._threads is None
    with pytest.raises(RuntimeError, match="ThreadStore is closed"):
        threads.create_workspace("owner", "closed")


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["audit", "memory", "orchestrator", "seed"])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_butler_startup_rollback_preserves_primary(
    rig: LifecycleRig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    boundary: str,
    cancelled: bool,
) -> None:
    import asyncio

    memory = MemoryStore(str(tmp_path / "borrowed.db"))
    service = ButlerService(orchestrator=rig.orchestrator, memory_store=memory)
    threads = service.thread_store
    primary = asyncio.CancelledError("startup") if cancelled else RuntimeError("startup")
    target, method = {
        "audit": (service._audit, "initialize"),
        "memory": (memory, "initialize"),
        "orchestrator": (rig.orchestrator, "start"),
        "seed": (service, "_ensure_default_agents"),
    }[boundary]
    original = getattr(target, method)

    async def fail_start() -> None:
        await original()
        raise primary

    monkeypatch.setattr(target, method, fail_start)
    # Cleanup failure must not mask the primary or skip owned audit/thread cleanup.
    stop = rig.orchestrator.stop

    async def fail_stop() -> None:
        await stop()
        raise ValueError("cleanup")

    monkeypatch.setattr(rig.orchestrator, "stop", fail_stop)
    try:
        with pytest.raises(type(primary)) as caught:
            await service.start()
        assert caught.value is primary
        assert not service._started
        assert service._lattice is service._threads is service._commands is service._engine is None
        assert service._audit._conn is None
        assert rig.probes["memory"].store._conn is None
        with pytest.raises(RuntimeError, match="ThreadStore is closed"):
            threads.create_workspace("owner", "closed")
        if boundary in ("memory", "seed"):
            assert memory._conn is not None  # Borrowed; no rollback ownership transfer.
    finally:
        await memory.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["orchestrator", "threads", "audit"])
async def test_butler_stop_attempts_all_owned_cleanup(
    rig: LifecycleRig, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    service = ButlerService(orchestrator=rig.orchestrator)
    await service.start()
    threads = service.thread_store
    target, method = {
        "orchestrator": (rig.orchestrator, "stop"),
        "threads": (threads, "close"),
        "audit": (service._audit, "close"),
    }[boundary]
    original = getattr(target, method)
    primary = RuntimeError("first cleanup")

    async def fail_async() -> None:
        await original()
        raise primary

    def fail_sync() -> None:
        original()
        raise primary

    monkeypatch.setattr(target, method, fail_sync if boundary == "threads" else fail_async)
    with pytest.raises(RuntimeError) as caught:
        await service.stop()
    assert caught.value is primary
    assert not service._started
    assert service._lattice is service._engine is service._commands is None
    assert rig.probes["butler_audit"].releases == 1
    assert rig.probes["memory"].releases == 1
    with pytest.raises(RuntimeError, match="ThreadStore is closed"):
        threads.create_workspace("owner", "closed")
