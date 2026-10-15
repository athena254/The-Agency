"""Spawn-safe admission experiment: stdlib only until explicit bootstrap.

The separate aggregate 60s bootstrap allowance is intentional. Actual ready /
observation waits remain 15s, checked start/release gates 10s, old-gap barrier 5s.
No early bootstrap notification is an acknowledgement of registry readiness.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
import queue
import sys
import time
from pathlib import Path
from typing import Any

BOOTSTRAP_SECONDS = 60
OBSERVATION_SECONDS = 15
GATE_SECONDS = 10
GAP_SECONDS = 5


class AdmissionProtocolError(AssertionError):
    def __init__(self, message: str, observations: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.observations = list(observations)


def admission_worker(
    path: str,
    arm: Any,
    start: Any,
    release: Any,
    barrier: Any,
    messages: Any,
    bootstrap_deadline: float,
    *,
    mutant: bool = False,
) -> None:
    """Initialize actual dependencies before arming; never dispatch on expiry."""
    phase = "bootstrap"

    def emit(event: str, **data: Any) -> None:
        messages.put({"event": event, "pid": os.getpid(), "phase": phase, **data})

    async def run() -> None:
        nonlocal phase
        emit("bootstrap_started")
        # Windows spawn imports this module, not the heavyweight pytest module.
        from agency.kernel import audit as audit_module
        from agency.kernel.audit import BetaAuditLog
        from agency.tools import registry as registry_module
        from agency.tools.base import (
            BetaPrincipal,
            BetaToolPolicy,
            ToolContext,
            ToolResult,
            ToolRisk,
            ToolSpec,
        )
        from agency.tools.registry import ToolRegistry

        log = BetaAuditLog(path)
        original_pending = BetaAuditLog.has_pending_calls
        original_claim = BetaAuditLog.claim_intent
        release_expired = False

        async def synchronized_check(self: Any) -> bool:
            pending = await original_pending(self)
            if not pending:
                await asyncio.to_thread(barrier.wait, GAP_SECONDS)
            return pending

        async def split_claim(self: Any, entry: Any) -> bool:
            # Deliberately unsafe TEST MUTANT: no transaction spans read + append.
            if await self.has_pending_calls():
                return False
            await BetaAuditLog.append_beta(self, entry)
            return True

        class BlockingTool:
            spec = ToolSpec(
                name="web_search",
                description="Synthetic offline search",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
                risk=ToolRisk.READ_ONLY,
            )

            async def run(self, args: Any, ctx: Any) -> Any:
                nonlocal phase, release_expired
                phase = "release"
                emit("ran")
                if await asyncio.to_thread(release.wait, GATE_SECONDS) is not True:
                    release_expired = True
                    raise AssertionError("release gate timed out")
                return ToolResult(tool=self.spec.name, ok=True, output="ok")

        try:
            await log.initialize()
            registry = ToolRegistry(audit=log, beta_policy=BetaToolPolicy())
            registry.register(BlockingTool())
            context = ToolContext(
                agent_id="agent-1",
                task_id="task-1",
                beta_principal=BetaPrincipal(telegram_user_id=12345, private_chat=True),
            )
            BetaAuditLog.has_pending_calls = synchronized_check
            if mutant:
                BetaAuditLog.claim_intent = split_claim
            emit(
                "bootstrap_complete",
                support=str(Path(__file__).resolve()),
                audit=str(Path(audit_module.__file__).resolve()),
                registry=str(Path(registry_module.__file__).resolve()),
                prefix=sys.prefix,
            )
            phase = "arm"
            remaining = bootstrap_deadline - time.monotonic()
            if remaining <= 0 or await asyncio.to_thread(arm.wait, remaining) is not True:
                raise AssertionError("arm gate timed out")
            phase = "start"
            emit("ready")
            if await asyncio.to_thread(start.wait, GATE_SECONDS) is not True:
                raise AssertionError("start gate timed out")
            phase = "claim"
            result = await registry.call("web_search", {"query": "private"}, context)
            emit(
                "result",
                ok=result.ok,
                error=result.error,
                output=result.output,
                status=result.evidence.get("status"),
            )
            if release_expired:
                raise AssertionError("release gate timed out")
        finally:
            # In-process negative tests must not leak worker-local class patches.
            BetaAuditLog.has_pending_calls = original_pending
            BetaAuditLog.claim_intent = original_claim
            await log.close()

    try:
        asyncio.run(run())
    except BaseException as exc:
        # Never serialize exception text / private inputs into protocol diagnostics.
        emit("worker_error", error_type=type(exc).__name__)
        raise


def run_admission_race(path: str, *, mutant: bool = False, context: Any = None) -> dict[str, Any]:
    """Parent assertions shared by the real race and its two-ran mutation test."""
    ctx = context if context is not None else multiprocessing.get_context("spawn")
    arm, start, release = ctx.Event(), ctx.Event(), ctx.Event()
    barrier, messages = ctx.Barrier(2), ctx.Queue()
    workers: list[Any] = []
    observations: list[dict[str, Any]] = []
    begun = time.monotonic()
    bootstrap_deadline = begun + BOOTSTRAP_SECONDS

    def require(condition: bool, detail: str) -> None:
        if not condition:
            raise AdmissionProtocolError(detail, observations)

    def receive(deadline: float, phase: str) -> dict[str, Any]:
        while True:
            remaining = deadline - time.monotonic()
            require(remaining > 0, f"{phase} deadline expired; pids={[w.pid for w in workers]}")
            try:
                item = messages.get(timeout=min(0.1, remaining))
            except queue.Empty:
                failed = [(w.pid, w.exitcode) for w in workers if w.exitcode not in (None, 0)]
                require(not failed, f"{phase} worker exited: {failed}")
                continue
            require(isinstance(item, dict), f"{phase} malformed worker message")
            observations.append(item)
            require(time.monotonic() <= deadline, f"{phase} absolute deadline expired")
            require(item.get("pid") in {w.pid for w in workers}, f"{phase} unknown worker PID")
            require(
                item.get("event") != "worker_error",
                f"worker pid={item.get('pid')} phase={item.get('phase')} "
                f"error={item.get('error_type')}",
            )
            return item

    def collect(event: str, deadline: float, phase: str) -> None:
        seen: set[int] = set()
        begun_pids: set[int] = set()
        while len(seen) < 2:
            item = receive(deadline, phase)
            pid = item["pid"]
            if event == "bootstrap_complete" and item.get("event") == "bootstrap_started":
                require(pid not in begun_pids and pid not in seen, "duplicate bootstrap start")
                require(item.get("phase") == "bootstrap", "invalid bootstrap-start phase")
                begun_pids.add(pid)
                continue
            require(item.get("event") == event, f"{phase} unexpected event={item.get('event')}")
            require(pid not in seen, f"{phase} duplicate {event} pid={pid}")
            if event == "bootstrap_complete":
                require(pid in begun_pids, f"bootstrap completion without start pid={pid}")
                require(item.get("phase") == "bootstrap", f"invalid bootstrap phase pid={pid}")
                root = Path(__file__).resolve().parents[2]
                for field, expected in (
                    ("support", Path(__file__).resolve()),
                    ("audit", root / "src/agency/kernel/audit.py"),
                    ("registry", root / "src/agency/tools/registry.py"),
                    ("prefix", Path(sys.prefix)),
                ):
                    require(
                        Path(item.get(field, "")).resolve() == expected.resolve(),
                        f"bootstrap {field} provenance mismatch pid={pid}",
                    )
            else:
                require(item.get("phase") == "start", f"invalid ready phase pid={pid}")
            seen.add(pid)

    primary_error: BaseException | None = None
    try:
        # Register ownership BEFORE start: start may launch and then raise.
        for _ in range(2):
            worker = ctx.Process(
                target=admission_worker,
                args=(path, arm, start, release, barrier, messages, bootstrap_deadline),
                kwargs={"mutant": mutant},
            )
            workers.append(worker)
            worker.start()
        collect("bootstrap_complete", bootstrap_deadline, "bootstrap")
        bootstrap_s = time.monotonic() - begun
        arm.set()
        collect("ready", time.monotonic() + OBSERVATION_SECONDS, "readiness")
        start.set()
        deadline = time.monotonic() + OBSERVATION_SECONDS
        racing: list[dict[str, Any]] = []
        while len(racing) < 2:
            item = receive(deadline, "observation")
            require(item.get("event") in {"ran", "result"}, "unexpected race event")
            racing.append(item)
        ran = [m for m in racing if m["event"] == "ran"]
        require(len(ran) == 1, "two tools ran before release" if len(ran) == 2 else "no tool ran")
        denied = [m for m in racing if m["event"] == "result"]
        require(len(denied) == 1, "missing denied competitor")
        require(
            denied[0].get("ok") is False
            and denied[0].get("error") == "beta audit unavailable"
            and denied[0].get("output") is None
            and denied[0].get("status") == "INDETERMINATE"
            and denied[0].get("phase") == "claim"
            and denied[0]["pid"] != ran[0]["pid"]
            and ran[0].get("phase") == "release",
            "competitor was not explicitly denied while admitted call was pending",
        )
        require(not release.is_set(), "release was set before overlap assertions")
        release.set()  # Exactly once, only after one held call + one proper denial.
        completed = receive(time.monotonic() + OBSERVATION_SECONDS, "success drain")
        require(
            completed.get("event") == "result"
            and completed["pid"] == ran[0]["pid"]
            and completed.get("phase") == "release"
            and completed.get("ok") is True
            and completed.get("error") is None
            and completed.get("output") == "ok",
            "admitted call did not drain a successful result after release",
        )
        for worker in workers:
            worker.join(timeout=OBSERVATION_SECONDS)
        require(
            all(w.exitcode == 0 for w in workers),
            f"worker exit codes={[(w.pid, w.exitcode) for w in workers]}",
        )
        # All producers have exited / flushed: no hidden extra run or error.
        try:
            extra = messages.get_nowait()
        except queue.Empty:
            pass
        else:
            observations.append(extra)
            require(False, "unexpected trailing worker message")
        return {
            "bootstrap_s": bootstrap_s,
            "observations": observations,
            "exitcodes": [w.exitcode for w in workers],
        }
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        # Do not set arm/start/release on failure; that would authorize late work.
        # Each independent phase must run even if a previous cleanup call failed.
        cleanup_errors: list[str] = []

        def attempt(label: str, operation: Any, fallback: Any = None) -> Any:
            try:
                return operation()
            except BaseException as exc:
                cleanup_errors.append(f"{label}: {type(exc).__name__}")
                # Log the phase/type, never private exception text or traceback.
                logging.getLogger(__name__).exception(
                    "cleanup %s failed: %s", label, type(exc).__name__, exc_info=False
                )
                return fallback

        owned = [(worker, worker.pid) for worker in workers]
        for worker, pid in owned:
            if pid is not None and attempt(f"pid={pid} is_alive", worker.is_alive, True):
                attempt(f"pid={pid} terminate", worker.terminate)
        for worker, pid in owned:
            if pid is not None:
                attempt(f"pid={pid} join", lambda worker=worker: worker.join(timeout=GAP_SECONDS))
                if attempt(f"pid={pid} is_alive", worker.is_alive, True):
                    attempt(f"pid={pid} kill", worker.kill)
                    attempt(
                        f"pid={pid} join after kill",
                        lambda worker=worker: worker.join(timeout=GAP_SECONDS),
                    )
                if attempt(f"pid={pid} is_alive", worker.is_alive, True):
                    cleanup_errors.append(f"could not reap worker pid={pid}")
            attempt(f"pid={pid} close", worker.close)
        attempt("queue.close", messages.close)
        attempt("queue.join_thread", messages.join_thread)
        if cleanup_errors:
            detail = "admission cleanup failed: " + "; ".join(cleanup_errors)
            if primary_error is not None:
                primary_error.add_note(detail)
            else:
                raise AdmissionProtocolError(detail, observations)
