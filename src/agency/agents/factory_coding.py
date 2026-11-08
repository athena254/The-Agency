"""Trusted literal fixture bridge. No API admission or sandbox guarantees."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import stat
import subprocess
import threading
import weakref
from collections.abc import Callable
from concurrent.futures import Future as ConcurrentFuture
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar, cast

from agency.admission.models import OwnerExecutionGrant, OwnerMeteredBinding
from agency.agents.coding import AgencyCodingAgent
from agency.agents.coding_planner import plan_edits
from agency.agents.prompts import issued_system_prompt
from agency.agents.registry import AgentRecord, AgentStatus
from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.factory.coding_models import (
    CodingBinding,
    CodingEdit,
    CodingReceipt,
    FactoryCodingRequest,
    canonical_sha256,
    coding_branch,
    literal_patch,
)
from agency.factory.coding_verification import (
    _read_artifact,
    checker_source_sha256,
    criteria_document,
    validate_coding_receipt,
    verify_literal_artifact,
)
from agency.factory.service import AgentFactory
from agency.forge.coding_agent import CodingAgentReport
from agency.kernel.identity import Agent
from agency.kernel.registry import AgentRegistry as KernelRegistry
from agency.llm.metered import MeteredDenied, MeteredError, MeteredModel

_Phase = Literal["preflight", "planning", "mutation", "verification", "publication"]
_UncertainReason = Literal[
    "cancelled",
    "deadline",
    "stopping",
    "identity_changed",
    "model_unknown",
    "native_unknown",
    "evidence_invalid",
    "hook_unknown",
]

_T = TypeVar("_T")
_FutureT = TypeVar("_FutureT", bound=asyncio.Future[Any])


class NativeCodingDenied(Exception):
    """Known refusal before native mutation."""


class NativeCodingUncertain(Exception):
    """Unresolved effects; operator reconciliation is required."""


@dataclass(frozen=True)
class NativeLiteralProfile:
    owner_id: str
    workspace_id: str
    blueprint_id: str
    blueprint_version: str
    runtime_agent_id: str
    blueprint_json: str
    identity_json: str
    events_json: str
    repo: Path
    base_commit: str
    base_tree: str
    worktree_root: Path
    evidence_root: Path
    checker_sha256: str
    criteria_sha256: str
    provider: str
    model: str
    provider_config_id: str
    dispatch_json: str
    job_timeout_s: float
    exclusive_registries: bool
    profile_id: str = "literal-assignment-v1"
    checker_id: str = "literal-assignment"
    checker_version: str = "1"
    allowed_path: str = "m.py"
    max_edits: int = 1
    max_bytes: int = 8192
    max_model_calls: int = 1

    def __post_init__(self) -> None:
        if (
            type(self.job_timeout_s) not in (float, int)
            or not math.isfinite(self.job_timeout_s)
            or not 0 < self.job_timeout_s <= 120
            or self.exclusive_registries is not True
            or self.profile_id != "literal-assignment-v1"
            or self.allowed_path != "m.py"
            or self.checker_id != "literal-assignment"
            or self.checker_version != "1"
            or any(
                type(getattr(self, k)) is not int or getattr(self, k) != v
                for k, v in (("max_edits", 1), ("max_bytes", 8192), ("max_model_calls", 1))
            )
        ):
            raise ValueError("invalid fixed native profile")
        for path in (self.repo, self.worktree_root, self.evidence_root):
            if (
                not isinstance(path, Path)
                or not path.is_absolute()
                or ".." in path.parts
                or str(path).startswith(("\\\\", "//"))
            ):
                raise ValueError("invalid trusted native path")
        if (
            self.worktree_root != self.repo.parent / (self.repo.name + "-worktrees")
            or self.evidence_root.is_relative_to(self.repo)
            or self.evidence_root.is_relative_to(self.worktree_root)
        ):
            raise ValueError("invalid fixed worktree/evidence root")
        if (
            self.checker_sha256 != checker_source_sha256()
            or self.criteria_sha256 != canonical_sha256(criteria_document())
        ):
            raise ValueError("unapproved checker/criterion pin")
        facts = json.loads(self.dispatch_json)
        if type(facts) is not dict or set(facts) != {
            "provider",
            "model",
            "base_url",
            "max_tokens",
            "temperature",
            "timeout",
        }:
            raise ValueError("only nonsecret dispatch facts allowed")
        for text in (self.blueprint_json, self.identity_json, self.events_json, self.dispatch_json):
            if type(text) is not str or len(text.encode("utf-8")) > 65536:
                raise ValueError("invalid native snapshot")
            canonical_sha256(json.loads(text))
        _ = self.sha256

    def snapshot(self) -> dict[str, Any]:
        """Defensive JSON-compatible nonsecret facts, with no live lookups."""
        value = asdict(self)
        for name in ("repo", "worktree_root", "evidence_root"):
            value[name] = str(value[name])
        return value

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.snapshot())


@dataclass(frozen=True)
class NativeCodingRequest:
    request: FactoryCodingRequest
    binding: CodingBinding


@dataclass(frozen=True)
class NativeCodingOutcome:
    binding: CodingBinding
    edits: tuple[CodingEdit, ...]
    forge_report: CodingAgentReport
    artifact_text: str | None
    patch: str | None
    receipt: CodingReceipt


class NativeCodingHooks(Protocol):
    async def checkpoint(
        self, binding: CodingBinding, phase: Literal["planning", "mutation", "publication"]
    ) -> None: ...
    async def record_intent(
        self, binding: CodingBinding, edits: tuple[CodingEdit, ...], plan_sha256: str, branch: str
    ) -> None: ...
    async def publish(self, outcome: NativeCodingOutcome) -> None: ...
    async def uncertain(
        self,
        binding: CodingBinding,
        phase: Literal["preflight", "planning", "mutation", "verification", "publication"],
        reason: Literal[
            "cancelled",
            "deadline",
            "stopping",
            "identity_changed",
            "model_unknown",
            "native_unknown",
            "evidence_invalid",
            "hook_unknown",
        ],
    ) -> None: ...


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if result.returncode != 0:
        raise NativeCodingDenied("fixture Git check failed")
    return result.stdout


def _regular_path(path: Path) -> None:
    for parent in (*reversed(path.parents), path):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise NativeCodingDenied("redirected fixture")


_REGISTRY_OWNERS: weakref.WeakKeyDictionary[
    KernelRegistry | RuntimeRegistry, weakref.ReferenceType[NativeCodingExecutor]
] = weakref.WeakKeyDictionary()


class NativeCodingExecutor:
    """One explicitly trusted fixture and dedicated registry pair, on one owner loop.

    Cancellation/deadline fence publication; they do not kill Git or isolate the
    inherited environment. Keep this loop and metering stores alive after stop False.
    """

    def __init__(
        self,
        *,
        profile: NativeLiteralProfile,
        factory: AgentFactory,
        kernel_registry: KernelRegistry,
        runtime_registry: RuntimeRegistry,
    ) -> None:
        if type(profile) is not NativeLiteralProfile:
            raise NativeCodingDenied("fixed native profile required")
        profile.__post_init__()
        for registry in (kernel_registry, runtime_registry):
            owner = _REGISTRY_OWNERS.get(registry)
            if owner is not None and owner() is not None:
                raise NativeCodingDenied("dedicated registries already owned")
        self.profile: NativeLiteralProfile = profile
        self._factory: AgentFactory = factory
        self._kernels: KernelRegistry = kernel_registry
        self._runtimes: RuntimeRegistry = runtime_registry
        self._loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        self._thread: int = threading.get_ident()
        self._pool: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="literal-factory"
        )
        self._identity: Agent | None = kernel_registry.get(profile.runtime_agent_id)
        self._record: AgentRecord | None = runtime_registry.get_agent(profile.runtime_agent_id)
        self._claimed: object | None = None
        self._latched: bool = False
        self._profile_sha: str = profile.sha256
        self._operations: set[asyncio.Future[Any]] = set()
        self._native_futures: list[ConcurrentFuture[Any]] = []
        self._bridge_futures: list[ConcurrentFuture[Any]] = []
        self._stopping: bool = False
        self._job: asyncio.Future[NativeCodingOutcome] | None = None
        self._binding: CodingBinding | None = None
        self._phase: _Phase = "preflight"
        self._uncertain_task: asyncio.Future[Any] | None = None
        self.uncertainty_acknowledged: bool | None = None
        self.native_report: CodingAgentReport | None = None
        self._signal: asyncio.Future[Any] = self._loop.create_future()
        self._request: NativeCodingRequest
        self._grant: OwnerExecutionGrant
        self._model: MeteredModel
        self._hooks: NativeCodingHooks
        self._deadline: float
        if (
            factory._kernels is not kernel_registry
            or factory._runtimes is not runtime_registry
            or profile.exclusive_registries is not True
        ):
            raise NativeCodingDenied("dedicated registries required")
        for registry in (kernel_registry, runtime_registry):
            _REGISTRY_OWNERS[registry] = weakref.ref(self)

    def _entry(
        self,
        request: NativeCodingRequest,
        grant: OwnerExecutionGrant,
        model: MeteredModel,
    ) -> NativeCodingRequest:
        try:
            if (
                type(request) is not NativeCodingRequest
                or type(grant) is not OwnerExecutionGrant
                or type(model) is not MeteredModel
            ):
                raise ValueError
            r = FactoryCodingRequest.model_validate(request.request)
            b = CodingBinding.model_validate(request.binding)
            grant.__post_init__()
            grant.lease.__post_init__()
            p = self.profile
            if (
                p.sha256 != self._profile_sha
                or r.request_id != b.request_id
                or r.thread_id != b.thread_id
                or r.profile_id != b.profile_id
                or b.profile_sha256 != p.sha256
                or b.owner_id != grant.lease.principal.owner_id
                or b.request_id != grant.lease.request_id
                or b.task_id != grant.lease.task_id
                or grant.max_model_calls != 1
                or grant.allowed_tools
                or any(
                    getattr(b, key) != getattr(p, key)
                    for key in (
                        "owner_id",
                        "workspace_id",
                        "profile_id",
                        "blueprint_id",
                        "blueprint_version",
                        "runtime_agent_id",
                        "base_commit",
                        "checker_id",
                        "checker_version",
                        "checker_sha256",
                        "criteria_sha256",
                    )
                )
                or type(model.binding) is not OwnerMeteredBinding
                or model.binding.grant is not grant
                or (model.provider, model.model, model.binding.provider_config_id)
                != (p.provider, p.model, p.provider_config_id)
            ):
                raise ValueError
            snap = model._snapshot
            facts = {
                key: getattr(snap, key)
                for key in ("provider", "model", "base_url", "max_tokens", "temperature", "timeout")
            }
            if facts != json.loads(p.dispatch_json):
                raise ValueError
            return NativeCodingRequest(r, b)
        except Exception:  # noqa: BLE001 -- fail closed at a trusted adapter/hook boundary
            raise NativeCodingDenied("request/profile/grant mismatch") from None

    def _live(self, binding: CodingBinding) -> tuple[Agent, AgentRecord]:
        assert threading.get_ident() == self._thread
        assert asyncio.get_running_loop() is self._loop
        p = self.profile
        if self._latched:
            raise NativeCodingDenied("executor fenced")
        try:
            bp = self._factory.validate(p.blueprint_id, p.blueprint_version)
        except Exception:  # noqa: BLE001 -- fail closed at a trusted adapter/hook boundary
            raise NativeCodingDenied("identity changed") from None
        agent = self._kernels.get(p.runtime_agent_id)
        record = self._runtimes.get_agent(p.runtime_agent_id)
        if agent is None or record is None:
            raise NativeCodingDenied("identity changed")
        try:
            prompt = issued_system_prompt(agent)
            metadata = agent.metadata
            prompt_valid = (
                (
                    bool(prompt)
                    and metadata.get("prompt_refs") == [list(r) for r in bp.prompt_refs]
                    and metadata.get("prompt_composed_sha256")
                    == hashlib.sha256(metadata["system_prompt"].encode("utf-8")).hexdigest()
                )
                if bp.prompt_refs
                else not any(
                    k in metadata
                    for k in ("system_prompt", "prompt_refs", "prompt_composed_sha256")
                )
            )
        except (TypeError, ValueError, KeyError):
            prompt_valid = False
        if (
            not prompt_valid
            or record.agent is not agent
            or metadata.get("factory") != "agent-factory-v1"
            or metadata.get("blueprint_id") != p.blueprint_id
            or metadata.get("blueprint_version") != p.blueprint_version
            or (agent.name, agent.domain, tuple(c.name for c in agent.capabilities))
            != (bp.name, bp.domain, bp.capabilities)
            or bp.to_dict() != json.loads(p.blueprint_json)
            or self._factory.list_events(p.blueprint_id, p.blueprint_version)
            != json.loads(p.events_json)
            or agent is None
            or record is None
            or agent is not self._identity
            or record is not self._record
            or agent.revoked
            or agent.trust_level.value != "unknown"
            or any(c.max_level.value != "L0_OBSERVATION" for c in agent.capabilities)
            or bp.status.value != "ACTIVE"
            or bp.runtime_agent_id != p.runtime_agent_id
            or not bp.approver
            or bp.approver == bp.creator
            or record.status is not (AgentStatus.BUSY if self._claimed else AgentStatus.IDLE)
            or record.current_task_id != (binding.task_id if self._claimed else None)
            or agent.model_dump(mode="json") != json.loads(p.identity_json)
            or record.agent.model_dump(mode="json") != json.loads(p.identity_json)
        ):
            raise NativeCodingDenied("identity changed")
        return agent, record

    def _preflight(self, binding: CodingBinding, *, collision: bool = True) -> None:
        p = self.profile
        _regular_path(p.repo / "m.py")
        _regular_path(p.repo / ".git")
        _regular_path(p.worktree_root.parent)
        if collision:
            if p.worktree_root.exists():
                _regular_path(p.worktree_root)
                if (
                    not p.worktree_root.is_dir()
                    or next(p.worktree_root.iterdir(), None) is not None
                ):
                    raise NativeCodingDenied("preexisting worktree effects")
            registrations = _git(p.repo, "worktree", "list", "--porcelain").decode().split("\n\n")
            paths = [
                line[9:]
                for block in registrations
                for line in block.splitlines()
                if line.startswith("worktree ")
            ]
            if paths != [str(p.repo).replace("\\", "/")]:
                raise NativeCodingDenied("preexisting worktree effects")
        if (
            not (p.repo / ".git").is_dir()
            or {x.name for x in p.repo.iterdir()} != {".git", "m.py"}
            or not stat.S_ISREG((p.repo / "m.py").lstat().st_mode)
            or (p.repo / "m.py").read_bytes() != b"x = 1\n"
            or _git(p.repo, "rev-parse", "--show-toplevel").decode().strip()
            != str(p.repo).replace("\\", "/")
            or _git(p.repo, "rev-parse", "HEAD").decode().strip() != p.base_commit
            or _git(p.repo, "rev-parse", "HEAD^{tree}").decode().strip() != p.base_tree
            or _git(p.repo, "ls-tree", "-r", "--name-only", "HEAD") != b"m.py\n"
            or not _git(p.repo, "ls-tree", "HEAD", "m.py").startswith(b"100644 blob ")
            or _git(p.repo, "status", "--porcelain", "--untracked-files=all")
        ):
            raise NativeCodingDenied("fixture drift")
        if collision and _git(
            p.repo, "for-each-ref", "--format=%(refname)", "refs/heads/" + coding_branch(binding)
        ):
            raise NativeCodingDenied("branch collision")

    def _affinity(self) -> None:
        if threading.get_ident() != self._thread or asyncio.get_running_loop() is not self._loop:
            raise NativeCodingDenied("creating application loop required")

    def _remember(self, future: _FutureT) -> _FutureT:
        self._operations.add(future)

        def settled(done: asyncio.Future[Any]) -> None:
            if not done.cancelled():
                done.exception()
            self._release_if_settled()

        future.add_done_callback(settled)
        return future

    def _drained(self) -> bool:
        futures: tuple[asyncio.Future[Any] | ConcurrentFuture[Any], ...] = (
            *self._operations,
            *self._native_futures,
            *self._bridge_futures,
        )
        return all(f.done() for f in futures)

    def _owns_claim(self) -> bool:
        record = self._runtimes.get_agent(self.profile.runtime_agent_id)
        return (
            self._claimed is not None
            and self._binding is not None
            and record is self._record
            and self._kernels.get(self.profile.runtime_agent_id) is self._identity
            and record is not None
            and record.agent is self._identity
            and not self._identity.revoked
            and record.current_task_id == self._binding.task_id
            and record.status in (AgentStatus.BUSY, AgentStatus.DRAINING)
        )

    def _release_if_settled(self) -> None:
        if not self._drained():
            return
        if self._latched and self.uncertainty_acknowledged is not True:
            return
        if self._owns_claim():
            # Never reactivate a revoked/replaced factory identity.
            try:
                bp = self._factory.get(self.profile.blueprint_id, self.profile.blueprint_version)
                active = (
                    bp.status.value == "ACTIVE"
                    and bp.runtime_agent_id == self.profile.runtime_agent_id
                )
            except Exception:  # noqa: BLE001 -- fail closed at a trusted adapter/hook boundary
                active = False
            if active:
                self._runtimes.update_agent_status(self.profile.runtime_agent_id, AgentStatus.IDLE)
        self._claimed = None

    def _fence(self, reason: _UncertainReason) -> None:
        self._latched = True
        if not self._signal.done():
            self._signal.set_result(reason)
        if self._owns_claim() and self._binding is not None:
            self._runtimes.update_agent_status(
                self.profile.runtime_agent_id,
                AgentStatus.DRAINING,
                current_task_id=self._binding.task_id,
            )
        if self._uncertain_task is None:
            phase = self._phase

            async def acknowledge() -> None:
                self.uncertainty_acknowledged = False
                try:
                    result = cast(
                        "object",
                        await self._hooks.uncertain(
                            cast("CodingBinding", self._binding), phase, reason
                        ),
                    )
                    self.uncertainty_acknowledged = result is None
                except BaseException:  # noqa: BLE001 -- cancellation must retain and fence unknown effects
                    # Local fencing survives failed/lost acknowledgement; never retry.
                    self.uncertainty_acknowledged = False

            self._uncertain_task = self._remember(self._loop.create_task(acknowledge()))

    def _guard(self, *, finalized: bool = False) -> None:
        self._affinity()
        if self._loop.time() >= self._deadline:
            self._fence("deadline")
        if self._latched or self._stopping:
            raise NativeCodingUncertain("publication fenced; retained work may remain")
        if finalized:
            # Acknowledged publication may have finalized quota and revoked the
            # lease. Keep identity/claim/profile fencing, not pre-finalization owns().
            if self.profile.sha256 != self._profile_sha:
                raise NativeCodingDenied("identity changed")
        else:
            self._entry(self._request, self._grant, self._model)
        self._live(cast("CodingBinding", self._binding))

    async def _hook(self, name: str, *args: Any) -> None:
        self._guard()

        async def call() -> None:
            result = await getattr(self._hooks, name)(*args)
            if result is not None:
                raise NativeCodingUncertain("hook acknowledgement invalid")

        try:
            await asyncio.shield(self._remember(self._loop.create_task(call())))
        except Exception:  # noqa: BLE001 -- fail closed at a trusted adapter/hook boundary
            self._fence("hook_unknown")
            raise NativeCodingUncertain("hook acknowledgement unknown") from None
        self._guard(finalized=name == "publish")

    async def _worker(self, fn: Callable[..., _T], *args: Any) -> _T:
        self._guard()
        future = self._pool.submit(fn, *args)
        self._native_futures.append(future)
        wrapped = self._remember(asyncio.wrap_future(future, loop=self._loop))
        return await asyncio.shield(wrapped)

    async def execute(
        self,
        request: NativeCodingRequest,
        *,
        grant: OwnerExecutionGrant,
        model: MeteredModel,
        hooks: NativeCodingHooks,
    ) -> NativeCodingOutcome:
        self._affinity()
        if self._claimed or self._latched or self._stopping:
            raise NativeCodingDenied("executor unavailable")
        request = self._entry(request, grant, model)
        b = request.binding
        self._live(b)
        self._claimed = object()
        self._request, self._binding, self._grant, self._model, self._hooks = (
            request,
            b,
            grant,
            model,
            hooks,
        )
        self._deadline = self._loop.time() + self.profile.job_timeout_s
        self._phase = "preflight"
        self._runtimes.update_agent_status(
            self.profile.runtime_agent_id, AgentStatus.BUSY, current_task_id=b.task_id
        )
        self._job = self._remember(
            self._loop.create_task(self._drive(request, grant, model, hooks))
        )
        try:
            done, _ = await asyncio.wait(
                (self._job, self._signal),
                timeout=max(0, self._deadline - self._loop.time()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                self._fence("deadline")
            if self._latched:
                await asyncio.sleep(
                    0
                )  # Give the retained acknowledgement its first turn, never wait for drain.
                if self._job.done() and not self._job.cancelled():
                    error = self._job.exception()
                    if isinstance(error, (MeteredDenied, MeteredError)):
                        raise error
                raise NativeCodingUncertain("publication fenced; uncertainty may be unacknowledged")
            return self._job.result()
        except asyncio.CancelledError:
            self._fence("cancelled")
            await asyncio.sleep(0)
            raise
        finally:
            self._release_if_settled()

    async def _drive(
        self,
        request: NativeCodingRequest,
        grant: OwnerExecutionGrant,
        model: MeteredModel,
        hooks: NativeCodingHooks,
    ) -> NativeCodingOutcome:
        try:
            return await self._run(request, grant=grant, model=model, hooks=hooks)
        except MeteredError:
            self._fence("model_unknown")
            raise
        except NativeCodingDenied as exc:
            if str(exc) == "identity changed" or self._phase in ("verification", "publication"):
                self._fence("identity_changed")
                raise NativeCodingUncertain("identity changed") from None
            raise
        except MeteredDenied:
            raise
        except BaseException:  # noqa: BLE001 -- cancellation must retain and fence unknown effects
            self._fence("evidence_invalid" if self._phase == "verification" else "native_unknown")
            raise NativeCodingUncertain("native/hook outcome unknown") from None

    async def _run(
        self,
        request: NativeCodingRequest,
        *,
        grant: OwnerExecutionGrant,
        model: MeteredModel,
        hooks: NativeCodingHooks,
    ) -> NativeCodingOutcome:
        p, b = self.profile, request.binding
        agent, _record = self._live(b)
        await self._worker(self._preflight, b)
        self._phase = "planning"
        await self._hook("checkpoint", b, "planning")
        raw: list[str] = []
        errors: list[Exception] = []
        call_count = 0

        async def invoke(prompt: str) -> str:
            nonlocal call_count
            self._remember(cast(asyncio.Future[Any], asyncio.current_task()))
            try:
                self._guard()
                call_count += 1
                if call_count != 1:
                    raise NativeCodingDenied("model call count exceeded")
                prefix = issued_system_prompt(agent)
                text = await model.generate(
                    (prefix + "\n\n" if prefix else "") + prompt,
                    {"owner_grant": grant, "task_id": b.task_id, "strict": True},
                )
            except Exception as exc:
                errors.append(exc)
                raise
            raw.append(text)
            return text

        def bridge(prompt: str, context: dict[str, Any]) -> Any:
            if context:
                errors.append(NativeCodingDenied("planner context override"))
                raise errors[-1]
            future = asyncio.run_coroutine_threadsafe(invoke(prompt), self._loop)
            self._bridge_futures.append(future)
            return future.result(max(0, self._deadline - self._loop.time()))

        proposed = await self._worker(plan_edits, bridge, request.request.task, {"m.py": "x = 1\n"})
        if errors:
            raise errors[0]
        self._guard()
        try:
            if len(raw) != 1 or type(raw[0]) is not str or len(raw[0].encode("utf-8")) > 65536:
                raise ValueError

            def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                result: dict[str, Any] = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError
                    result[key] = value
                return result

            data = json.loads(raw[0], object_pairs_hook=unique)
            if type(data) is not list or len(data) != 1 or data != proposed:
                raise ValueError
            edits = (CodingEdit.model_validate(data[0]),)
            if "x = 1\n".count(edits[0].old) != 1:
                raise ValueError
            text = "x = 1\n".replace(edits[0].old, edits[0].new, 1)
            if len(text.encode("utf-8")) > p.max_bytes or (os.linesep != "\n" and "\n" in text):
                raise ValueError
            patch = literal_patch(text)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise NativeCodingDenied("invalid complete plan") from None
        self._phase = "mutation"
        await self._hook("checkpoint", b, "mutation")
        await self._hook(
            "record_intent",
            b,
            edits,
            canonical_sha256([e.model_dump(mode="json") for e in edits]),
            coding_branch(b),
        )
        self._guard()

        async def dispatch_checkpoint() -> None:
            self._remember(cast(asyncio.Future[Any], asyncio.current_task()))
            await self._hook("checkpoint", b, "mutation")

        def forge() -> CodingAgentReport:
            self._preflight(b)
            # Read-only Git can stall too. Re-enter the owner loop after it
            # settles, so cancellation/claim drift cannot schedule Forge late.
            barrier = asyncio.run_coroutine_threadsafe(dispatch_checkpoint(), self._loop)
            self._bridge_futures.append(barrier)
            barrier.result(max(0, self._deadline - self._loop.time()))
            return AgencyCodingAgent(workspace=p.repo, store_path=None).run_task(
                coding_branch(b),
                [e.model_dump() for e in edits],
                test_paths=None,
                run_tests_flag=False,
                cleanup=False,
                persist=False,
            )

        report = await self._worker(forge)
        self.native_report = report  # Retain late native evidence even when publication is fenced.
        self._phase = "verification"
        self._guard()

        def verify() -> NativeCodingOutcome:
            self._preflight(b, collision=False)
            root = Path(report.worktree_path)
            if (
                root.parent != p.worktree_root
                or report.branch != coding_branch(b)
                or _git(root, "rev-parse", "HEAD").decode().strip() != p.base_commit
                or _git(root, "rev-parse", "--git-common-dir").decode().strip().replace("\\", "/")
                != str(p.repo / ".git").replace("\\", "/")
                or ("worktree " + str(root).replace("\\", "/"))
                not in _git(p.repo, "worktree", "list", "--porcelain").decode()
            ):
                raise NativeCodingUncertain("native binding invalid")
            receipt = verify_literal_artifact(
                binding=b, edits=edits, forge_report=report, artifact_root=root, patch=patch
            )
            text = _read_artifact(root)[0] if receipt.artifact_sha256 else None
            validate_coding_receipt(
                receipt,
                binding=b,
                edits=edits,
                forge_report=report,
                artifact_text=text,
                patch=patch,
            )
            return NativeCodingOutcome(b, edits, report, text, patch, receipt)

        outcome = await self._worker(verify)
        self._phase = "publication"
        await self._hook("checkpoint", b, "publication")
        validate_coding_receipt(
            outcome.receipt,
            binding=b,
            edits=edits,
            forge_report=report,
            artifact_text=outcome.artifact_text,
            patch=patch,
        )
        self._guard()
        await self._hook("publish", outcome)
        return outcome

    async def stop(self, *, drain_timeout_s: float) -> bool:
        self._affinity()
        if (
            type(drain_timeout_s) not in (int, float)
            or not math.isfinite(drain_timeout_s)
            or drain_timeout_s < 0
        ):
            raise ValueError("finite nonnegative drain timeout required")
        self._stopping = True
        if self._claimed and not self._drained():
            self._fence("stopping")
        end = self._loop.time() + drain_timeout_s
        while not self._drained():
            if self._loop.time() >= end:
                return False
            await asyncio.sleep(min(0.01, max(0, end - self._loop.time())))
        self._release_if_settled()
        self._pool.shutdown(wait=False)
        return True
