"""Synthetic principals/provider, real planner -> AgencyCodingAgent -> Forge/Git."""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from agency.admission.budget import OwnerBudgetAdapter
from agency.admission.models import OwnerExecutionGrant, OwnerMeteredBinding, OwnerPrincipal
from agency.admission.store import OwnerPilotStore
from agency.agents.factory_coding import (
    NativeCodingDenied,
    NativeCodingExecutor,
    NativeCodingRequest,
    NativeLiteralProfile,
)
from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.agents.registry import AgentStatus
from agency.factory.coding_models import (
    CodingBinding,
    FactoryCodingRequest,
    canonical_sha256,
    coding_branch,
)
from agency.factory.coding_verification import (
    checker_source_sha256,
    criteria_document,
    validate_coding_receipt,
)
from agency.factory.service import AgentFactory
from agency.kernel.audit import BetaAuditLog
from agency.kernel.registry import AgentRegistry as KernelRegistry
from agency.llm.adapter import LLMAdapter
from agency.llm.metered import MeteredModel
from agency.telegram.budget_store import BetaBudgetStore

pytestmark = pytest.mark.asyncio
BASELINE = b"x = 1\n"
PLAN = [{"path": "m.py", "old": "x = 1\n", "new": "x = 2"}]


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)
    return result.stdout.decode().strip()


def dispatch_facts(model):
    s = model._snapshot
    return {
        key: getattr(s, key)
        for key in ("provider", "model", "base_url", "max_tokens", "temperature", "timeout")
    }


class Recorder:
    """Test acknowledgements only, NOT owner API/durable publication."""

    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.thread = threading.get_ident()
        self.events = []
        self.outcomes = []
        self.unknown = []

    def affinity(self):
        assert asyncio.get_running_loop() is self.loop
        assert threading.get_ident() == self.thread

    async def checkpoint(self, binding, phase):
        self.affinity()
        self.events.append(("checkpoint", phase))

    async def record_intent(self, binding, edits, plan_sha256, branch):
        self.affinity()
        assert plan_sha256 == canonical_sha256([e.model_dump(mode="json") for e in edits])
        assert branch == coding_branch(binding)
        self.events.append(("intent", branch))

    async def publish(self, outcome):
        self.affinity()
        self.events.append(("publish", outcome.receipt.objective_status))
        self.outcomes.append(outcome)

    async def uncertain(self, binding, phase, reason):
        self.affinity()
        self.unknown.append((phase, reason))


@pytest.fixture
async def native(tmp_path, monkeypatch, request):
    options = getattr(request, "param", 30.0)
    job_timeout = options.get("timeout", 30.0) if isinstance(options, dict) else options
    with_prompt = isinstance(options, dict) and options.get("prompt", False)
    repo = tmp_path / "trusted"
    repo.mkdir()
    (repo / "m.py").write_bytes(BASELINE)
    git(repo, "init", "-q")
    # Fixture-local prerequisites, never global Git configuration.
    for key, value in [
        ("user.name", "Synthetic Fixture"),
        ("user.email", "fixture@example.invalid"),
        ("core.autocrlf", "false"),
        ("core.hooksPath", str(tmp_path / "no-hooks")),
    ]:
        git(repo, "config", key, value)
    git(repo, "add", "m.py")
    git(repo, "commit", "-qm", "trusted baseline")
    kernels, runtimes = KernelRegistry(), RuntimeRegistry()
    from types import SimpleNamespace

    resolver = SimpleNamespace(get=lambda *a: SimpleNamespace(body="  Fixture issued guard.  "))
    factory = AgentFactory(
        str(tmp_path / "factory.sqlite"),
        kernel_registry=kernels,
        runtime_registry=runtimes,
        authorizer=lambda a, p: a == "fixture-reviewer",
        is_prompt_published=lambda *a: True,
        prompt_resolver=resolver,
    )
    factory.create(
        "fixture-coder",
        "1.0.0",
        "Fixture coder",
        creator="fixture-author",
        prompt_refs=[("fixture-prompt", "1.0.0")] if with_prompt else [],
    )
    factory.approve(
        "fixture-coder", "1.0.0", approver="fixture-reviewer", evidence="synthetic approval"
    )
    agent = factory.activate("fixture-coder", "1.0.0")
    if isinstance(options, dict) and options.get("corrupt_prompt"):
        if options["corrupt_prompt"] == "digest":
            agent.metadata["prompt_composed_sha256"] = "0" * 64
        else:
            agent.metadata.pop("system_prompt", None)
    owner = OwnerPrincipal("fixture-owner", "local", "fixture-subject")
    store = OwnerPilotStore(str(tmp_path / "pilot.sqlite"))
    backend = BetaBudgetStore(str(tmp_path / "quota.sqlite"))
    budget = OwnerBudgetAdapter(backend, store)
    audit = BetaAuditLog(str(tmp_path / "audit.sqlite"))
    executor = None
    try:
        await store.initialize()
        await backend.initialize()
        await audit.initialize()
        _, thread = await store.create_thread(owner, "fixture-thread", "Synthetic")
        admitted = await store.admit(
            owner,
            "fixture-request",
            operation="turn",
            payload={"thread_id": thread.id, "message": "Set x to integer 2"},
        )
        lease = await budget.reserve_request(owner, admitted.request_id, admitted.task_id)
        await budget.mark_running(lease)
        grant = OwnerExecutionGrant(lease, budget, allowed_tools=frozenset(), max_model_calls=1)
        inner = LLMAdapter(
            provider="openai",
            model="synthetic-model",
            api_key="SYNTHETIC-NOT-A-SECRET",
            base_url="http://127.0.0.1:1/v1",
            timeout=10.0,
        )
        model = MeteredModel(
            inner,
            OwnerMeteredBinding(grant, "openai", "synthetic-model", "fixture-config"),
            budget,
            audit,
        )
        hooks = Recorder()
        calls = []
        response = {"raw": json.dumps(PLAN)}
        client = httpx.AsyncClient

        async def handle(request):
            hooks.affinity()
            calls.append(json.loads(request.content))
            hooks.events.append(("model", "original-loop"))
            return httpx.Response(
                200, json={"choices": [{"message": {"content": response["raw"]}}]}
            )

        monkeypatch.setattr(
            httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(handle), **kw)
        )
        profile = NativeLiteralProfile(
            owner_id=owner.owner_id,
            workspace_id="fixture-workspace",
            blueprint_id="fixture-coder",
            blueprint_version="1.0.0",
            runtime_agent_id=agent.id,
            blueprint_json=json.dumps(factory.get("fixture-coder", "1.0.0").to_dict()),
            identity_json=json.dumps(agent.model_dump(mode="json")),
            events_json=json.dumps(factory.list_events("fixture-coder", "1.0.0")),
            repo=repo,
            base_commit=git(repo, "rev-parse", "HEAD"),
            base_tree=git(repo, "rev-parse", "HEAD^{tree}"),
            worktree_root=repo.parent / (repo.name + "-worktrees"),
            evidence_root=tmp_path / "evidence",
            checker_sha256=checker_source_sha256(),
            criteria_sha256=canonical_sha256(criteria_document()),
            provider=model.provider,
            model=model.model,
            provider_config_id="fixture-config",
            dispatch_json=json.dumps(dispatch_facts(model)),
            job_timeout_s=job_timeout,
            exclusive_registries=True,
        )
        binding = CodingBinding(
            owner_id=owner.owner_id,
            workspace_id=profile.workspace_id,
            request_id=lease.request_id,
            task_id=lease.task_id,
            thread_id=thread.id,
            profile_id=profile.profile_id,
            profile_sha256=profile.sha256,
            blueprint_id=profile.blueprint_id,
            blueprint_version=profile.blueprint_version,
            runtime_agent_id=agent.id,
            base_commit=profile.base_commit,
            checker_sha256=profile.checker_sha256,
            criteria_sha256=profile.criteria_sha256,
        )
        request = NativeCodingRequest(
            FactoryCodingRequest(
                request_id=lease.request_id,
                thread_id=thread.id,
                profile_id=profile.profile_id,
                task="Set x to integer 2",
            ),
            binding,
        )
        executor = NativeCodingExecutor(
            profile=profile, factory=factory, kernel_registry=kernels, runtime_registry=runtimes
        )
        yield locals()
    finally:
        if executor is not None:
            assert await executor.stop(drain_timeout_s=5.0), (
                "test left retained work; stores must stay alive"
            )
        factory.close()
        await audit.close()
        await budget.close()
        await store.close()


async def test_real_native_journey(native, monkeypatch):
    import agency.agents.factory_coding as bridge

    n = native
    planner, forge, metered = (
        bridge.plan_edits,
        bridge.AgencyCodingAgent.run_task,
        n["model"].generate,
    )
    invocations = []

    def planning(*args):
        invocations.append("planner")
        assert args[1:] == ("Set x to integer 2", {"m.py": "x = 1\n"})
        return planner(*args)

    def coding(self, branch, edits, **kw):
        invocations.append("forge")
        assert any(e[0] == "intent" for e in n["hooks"].events)
        assert branch == coding_branch(n["request"].binding)
        assert edits == PLAN
        return forge(self, branch, edits, **kw)

    async def generating(prompt, context):
        n["hooks"].affinity()
        assert context == {
            "owner_grant": n["grant"],
            "task_id": n["request"].binding.task_id,
            "strict": True,
        }
        assert context["owner_grant"] is n["grant"]
        return await metered(prompt, context)

    monkeypatch.setattr(bridge, "plan_edits", planning)
    monkeypatch.setattr(bridge.AgencyCodingAgent, "run_task", coding)
    monkeypatch.setattr(n["model"], "generate", generating)
    outcome = await n["executor"].execute(
        n["request"], grant=n["grant"], model=n["model"], hooks=n["hooks"]
    )
    assert invocations == ["planner", "forge"]
    assert len(n["calls"]) == 1
    payload = n["calls"][0]
    assert payload["messages"][0]["role"] == "user"
    assert len(payload["messages"]) == 1
    assert "Set x to integer 2" in payload["messages"][0]["content"]
    assert "--- m.py ---\nx = 1\n" in payload["messages"][0]["content"]
    assert outcome.artifact_text == "x = 2"
    assert outcome.receipt.objective_status == "passed"
    assert outcome.receipt.reason == "matched"
    assert outcome.receipt.release_authorized is False
    assert outcome.receipt.tests_run is False
    assert outcome.forge_report.tests is None
    assert "tests_not_run" in outcome.forge_report.caveats
    assert Path(outcome.forge_report.worktree_path).parent == n["profile"].worktree_root
    assert (
        git(Path(outcome.forge_report.worktree_path), "rev-parse", "HEAD")
        == n["profile"].base_commit
    )
    assert (n["repo"] / "m.py").read_bytes() == BASELINE
    assert git(n["repo"], "status", "--porcelain") == ""
    events = [e[0] for e in n["hooks"].events]
    assert events.index("model") < events.index("intent") < events.index("publish")
    assert n["hooks"].outcomes == [outcome]
    assert n["runtimes"].get_required(n["agent"].id).status is AgentStatus.IDLE
    assert not await n["audit"].has_pending_calls()
    assert validate_coding_receipt(
        outcome.receipt,
        binding=outcome.binding,
        edits=outcome.edits,
        forge_report=outcome.forge_report,
        artifact_text=outcome.artifact_text,
        patch=outcome.patch,
    ) == outcome.receipt.model_dump(mode="json")
    print(
        "NATIVE_PROOF",
        json.dumps(
            {
                "branch": outcome.forge_report.branch,
                "worktree": outcome.forge_report.worktree_path,
                "base": n["profile"].base_commit,
                "profile": n["profile"].snapshot(),
                "edits": [edit.model_dump(mode="json") for edit in outcome.edits],
                "artifact_text": outcome.artifact_text,
                "patch": outcome.patch,
                "report": outcome.forge_report.model_dump(mode="json"),
                "receipt": outcome.receipt.model_dump(mode="json"),
            }
        ),
    )


@pytest.mark.parametrize(
    "drift",
    [
        "missing",
        "revoked",
        "replaced",
        "busy",
        "foreign_task",
        "provenance",
        "binding_owner",
        "binding_workspace",
        "binding_thread",
        "binding_profile",
        "binding_checker",
        "forged_request",
        "forged_binding",
        "grant",
        "provider",
        "config",
        "dirty",
        "head",
        "collision",
    ],
)
async def test_refuse_authority_before_mutation(native, monkeypatch, drift):
    from agency.agents.coding import AgencyCodingAgent

    n = native
    calls = []

    def forbidden(*a, **kw):
        calls.append(1)
        raise AssertionError("Forge dispatched on invalid authority")

    monkeypatch.setattr(AgencyCodingAgent, "run_task", forbidden)
    request, grant = n["request"], n["grant"]
    record = n["runtimes"].get_required(n["agent"].id)
    if drift == "missing":
        n["runtimes"].deregister(n["agent"].id)
    elif drift == "revoked":
        n["kernels"].revoke(n["agent"].id)
    elif drift == "replaced":
        n["runtimes"].deregister(n["agent"].id)
        n["runtimes"].register(n["agent"].model_copy(deep=True))
    elif drift == "busy":
        record.status = AgentStatus.BUSY
    elif drift == "foreign_task":
        record.current_task_id = "foreign-task"
    elif drift == "provenance":
        n["factory"]._conn.execute("UPDATE blueprints SET evidence='drift'")
        n["factory"]._conn.commit()
    elif drift.startswith("binding_"):
        field = {
            "owner": "owner_id",
            "workspace": "workspace_id",
            "thread": "thread_id",
            "profile": "profile_sha256",
            "checker": "checker_sha256",
        }[drift[8:]]
        value = "0" * 64 if "sha256" in field else "foreign"
        request = replace(request, binding=request.binding.model_copy(update={field: value}))
    elif drift == "forged_request":
        request = replace(request, request=request.request.model_copy(update={"task": 12}))
    elif drift == "forged_binding":
        request = replace(request, binding=request.binding.model_copy(update={"base_commit": True}))
    elif drift == "grant":
        grant = replace(grant, max_model_calls=2)
    elif drift == "provider":
        object.__setattr__(n["model"].binding, "model", "different")
    elif drift == "config":
        object.__setattr__(n["model"].binding, "provider_config_id", "different")
    elif drift == "dirty":
        (n["repo"] / "m.py").write_bytes(b"x = 3")
    elif drift == "head":
        git(n["repo"], "commit", "--allow-empty", "-qm", "drift")
    elif drift == "collision":
        git(n["repo"], "branch", coding_branch(request.binding))
    with pytest.raises(NativeCodingDenied):
        await n["executor"].execute(request, grant=grant, model=n["model"], hooks=n["hooks"])
    assert calls == []
    assert n["calls"] == []
    assert n["hooks"].outcomes == []
    if drift == "foreign_task":
        assert record.current_task_id == "foreign-task"
    if drift == "busy":
        assert record.status is AgentStatus.BUSY


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps(PLAN + [{"path": "m.py", "old": "", "new": "bad"}]),
        json.dumps(PLAN * 2),
        "```json\n" + json.dumps(PLAN) + "\n```",
        "prose " + json.dumps(PLAN),
        '[{"path":"m.py","old":"x = 1\\n","new":"x = 9","new":"x = 2"}]',
        json.dumps([{"path": ".env", "old": "x", "new": "2"}]),
        json.dumps([{"path": "m.py", "old": "absent", "new": "x = 2"}]),
        json.dumps([{"path": "m.py", "old": "x = 1\n", "new": "x = 2\n"}]),
        "[]",
    ],
)
async def test_entire_raw_plan_refused_before_intent(native, monkeypatch, raw):
    from agency.agents.coding import AgencyCodingAgent

    n = native
    n["response"]["raw"] = raw
    calls = []

    def forbidden(*a, **kw):
        calls.append(1)
        raise AssertionError("invalid plan reached Forge")

    monkeypatch.setattr(AgencyCodingAgent, "run_task", forbidden)
    with pytest.raises(NativeCodingDenied):
        await n["executor"].execute(
            n["request"], grant=n["grant"], model=n["model"], hooks=n["hooks"]
        )
    assert len(n["calls"]) == 1
    assert calls == []
    assert not any(e[0] == "intent" for e in n["hooks"].events)
    assert n["hooks"].outcomes == []
    assert (n["repo"] / "m.py").read_bytes() == BASELINE


@pytest.mark.parametrize("kind", ["denied", "error"])
async def test_typed_metering_failure_survives_planner(native, monkeypatch, kind):
    from agency.llm.metered import MeteredDenied, MeteredError

    n = native
    if kind == "denied":

        async def refuse(*a, **kw):
            return False

        monkeypatch.setattr(n["budget"], "reserve_model_call", refuse)
        expected = MeteredDenied
    else:
        n["response"]["raw"] = ""
        expected = MeteredError
    with pytest.raises(expected):
        await n["executor"].execute(
            n["request"], grant=n["grant"], model=n["model"], hooks=n["hooks"]
        )
    assert n["hooks"].outcomes == []
    assert not any(e[0] == "intent" for e in n["hooks"].events)
    assert len(n["calls"]) == (0 if kind == "denied" else 1)
    if kind == "error":
        assert n["hooks"].unknown == [("planning", "model_unknown")]


async def test_profile_snapshot_is_nonsecret_complete_binding(native):
    p = native["profile"]
    snapshot = p.snapshot()
    assert canonical_sha256(snapshot) == native["request"].binding.profile_sha256
    assert json.loads(json.dumps(snapshot)) == snapshot
    assert snapshot["repo"] == str(p.repo)
    assert snapshot["max_model_calls"] == 1
    assert "SYNTHETIC-NOT-A-SECRET" not in json.dumps(snapshot)
    snapshot["repo"] = "altered-copy"
    assert p.snapshot()["repo"] == str(p.repo)


@pytest.mark.parametrize(
    "field,value",
    [
        ("job_timeout_s", float("inf")),
        ("job_timeout_s", float("nan")),
        ("job_timeout_s", True),
        ("job_timeout_s", 0.0),
        ("job_timeout_s", 121.0),
        ("max_edits", 2),
        ("max_bytes", 9000),
        ("max_model_calls", True),
        ("allowed_path", "other.py"),
        ("exclusive_registries", False),
        ("repo", Path("relative")),
        ("worktree_root", Path("relative")),
        ("dispatch_json", '{"api_key":"not-allowed"}'),
        ("checker_sha256", "0" * 64),
        ("criteria_sha256", "0" * 64),
    ],
)
async def test_profile_configuration_fail_closed(native, field, value):
    with pytest.raises((ValueError, NativeCodingDenied)):
        replace(native["profile"], **{field: value})


async def test_second_executor_cannot_borrow_registries(native):
    n = native
    with pytest.raises(NativeCodingDenied):
        NativeCodingExecutor(
            profile=n["profile"],
            factory=n["factory"],
            kernel_registry=n["kernels"],
            runtime_registry=n["runtimes"],
        )


@pytest.mark.parametrize("preexisting", ["root-file", "worktree"])
async def test_preexisting_effects_refused_before_model(native, preexisting):
    n = native
    if preexisting == "root-file":
        n["profile"].worktree_root.mkdir()
        (n["profile"].worktree_root / "retained").write_text("preserve")
    else:
        git(n["repo"], "worktree", "add", "--detach", str(n["repo"].parent / "other"), "HEAD")
    with pytest.raises(NativeCodingDenied):
        await n["executor"].execute(
            n["request"], grant=n["grant"], model=n["model"], hooks=n["hooks"]
        )
    assert n["calls"] == []
    assert n["hooks"].events == []


@pytest.mark.parametrize("native", [{"prompt": True}], indirect=True)
async def test_issued_prompt_is_user_message_prefix(native):
    n = native
    outcome = await n["executor"].execute(
        n["request"], grant=n["grant"], model=n["model"], hooks=n["hooks"]
    )
    assert outcome.receipt.objective_status == "passed"
    assert n["calls"][0]["messages"] == [
        {"role": "user", "content": n["calls"][0]["messages"][0]["content"]}
    ]
    assert n["calls"][0]["messages"][0]["content"].startswith(
        "Fixture issued guard.\n\nYou are a coding assistant."
    )


@pytest.mark.parametrize(
    "native",
    [{"prompt": True, "corrupt_prompt": "digest"}, {"prompt": True, "corrupt_prompt": "missing"}],
    indirect=True,
)
async def test_invalid_issued_prompt_pin_refused(native):
    n = native
    with pytest.raises(NativeCodingDenied):
        await n["executor"].execute(
            n["request"], grant=n["grant"], model=n["model"], hooks=n["hooks"]
        )
    assert n["calls"] == []
    assert n["hooks"].events == []


@pytest.mark.parametrize(
    "artifact,verdict,reason",
    [
        ("mismatch", "failed", "criterion_mismatch"),
        ("missing", "unable_to_verify", "artifact_unavailable"),
        ("extra", "failed", "inventory_mismatch"),
    ],
)
async def test_real_report_objective_outcomes_are_not_verified_success(
    native, monkeypatch, artifact, verdict, reason
):
    from agency.agents.coding import AgencyCodingAgent

    n = native
    original = AgencyCodingAgent.run_task
    calls = []

    def actual(self, *a, **kw):
        calls.append(kw)
        report = original(self, *a, **kw)
        root = Path(report.worktree_path)
        if artifact == "missing":
            (root / "m.py").unlink()
        elif artifact == "extra":
            (root / "extra.txt").write_bytes(b"extra")
        return report

    if artifact == "mismatch":
        n["response"]["raw"] = json.dumps([{"path": "m.py", "old": "x = 1\n", "new": "x = 3"}])
    monkeypatch.setattr(AgencyCodingAgent, "run_task", actual)
    outcome = await n["executor"].execute(
        n["request"], grant=n["grant"], model=n["model"], hooks=n["hooks"]
    )
    assert outcome.receipt.objective_status == verdict
    assert outcome.receipt.reason == reason
    assert outcome.artifact_text == ("x = 3" if artifact == "mismatch" else None)
    assert outcome.receipt.release_authorized is False
    assert n["hooks"].outcomes == [outcome]
    assert calls == [
        {"test_paths": None, "run_tests_flag": False, "cleanup": False, "persist": False}
    ]


@pytest.mark.parametrize("lease_loss", ["revoke", "expiry"])
@pytest.mark.parametrize("ack", ["acknowledged", "lost", "invalid"])
async def test_real_report_lease_loss_is_uncertain_and_latched(
    native, monkeypatch, lease_loss, ack
):
    """R1: hold a real Forge report, not a fabricated native result."""
    from types import SimpleNamespace

    import agency.admission.budget as budget_module
    from agency.agents.coding import AgencyCodingAgent
    from agency.agents.factory_coding import NativeCodingUncertain

    n = native
    executor = n["executor"]
    loop = asyncio.get_running_loop()
    report_ready, ack_entered, ack_release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    report_release = threading.Event()
    original = AgencyCodingAgent.run_task
    reports, dispatches, attempts = [], [], []
    lease = n["grant"].lease

    def actual(self, *args, **kwargs):
        dispatches.append(kwargs)
        report = original(self, *args, **kwargs)
        reports.append(report)
        loop.call_soon_threadsafe(report_ready.set)
        assert report_release.wait(8), "real report was not released by harness"
        return report

    # Keep the recorder available without recursively calling this held ACK.
    original_uncertain = n["hooks"].uncertain

    async def held_ack(binding, phase, reason):
        n["hooks"].affinity()
        assert binding == n["request"].binding
        attempts.append((phase, reason))
        await original_uncertain(binding, phase, reason)
        ack_entered.set()
        await ack_release.wait()
        if ack == "lost":
            raise RuntimeError("synthetic lost uncertainty acknowledgement")
        if ack == "invalid":
            return True
        return None

    monkeypatch.setattr(AgencyCodingAgent, "run_task", actual)
    monkeypatch.setattr(n["hooks"], "uncertain", held_ack)
    task = asyncio.create_task(
        executor.execute(n["request"], grant=n["grant"], model=n["model"], hooks=n["hooks"])
    )
    try:
        await asyncio.wait_for(report_ready.wait(), 10)
        report = reports[0]
        root = Path(report.worktree_path)
        report_json = report.model_dump(mode="json")
        artifact = (root / "m.py").read_bytes()
        assert artifact == b"x = 2"
        assert report.tests is None
        assert "tests_not_run" in report.caveats
        assert report.branch == coding_branch(n["request"].binding)
        assert root.parent == n["profile"].worktree_root
        assert git(root, "rev-parse", "HEAD") == n["profile"].base_commit
        assert (n["repo"] / "m.py").read_bytes() == BASELINE
        assert executor.native_report is None
        assert not executor._native_futures[-1].done()
        record = n["runtimes"].get_required(n["agent"].id)
        assert record.status is AgentStatus.BUSY
        assert n["budget"].owns(lease)
        n["hooks"].affinity()
        if lease_loss == "revoke":
            n["budget"].revoke(lease)
        else:
            # Exercise real owns() expiry without sleeping or moving the job clock.
            clock = SimpleNamespace(time=lambda: lease.deadline_at - 0.001)
            monkeypatch.setattr(budget_module, "time", clock)
            assert n["budget"].owns(lease)
            clock.time = lambda: lease.deadline_at + 0.001
        assert not n["budget"].owns(lease)
        assert loop.time() < executor._deadline
        report_release.set()
        result = (await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 4))[0]
        print(
            "R1_BOUNDARY_PROOF",
            json.dumps(
                {
                    "lease_loss": lease_loss,
                    "ack": ack,
                    "result_type": type(result).__name__,
                    "report": report_json,
                    "report_sha256": canonical_sha256(report_json),
                    "artifact_hex": artifact.hex(),
                    "fixture": str(n["repo"]),
                    "base": n["profile"].base_commit,
                    "preserved_report": executor.native_report is report,
                    "dispatches": len(dispatches),
                    "publish_calls": len(n["hooks"].outcomes),
                }
            ),
        )
        assert type(result) is NativeCodingUncertain, (
            f"post-effect lease loss must be uncertain, got {type(result).__name__}: {result}"
        )
        await asyncio.wait_for(ack_entered.wait(), 4)
        job_result = (await asyncio.gather(executor._job, return_exceptions=True))[0]
        assert type(job_result) is NativeCodingUncertain
        assert attempts == [("verification", "identity_changed")]
        assert n["hooks"].unknown == attempts
        assert executor.native_report is report
        assert report.model_dump(mode="json") == report_json
        assert (root / "m.py").read_bytes() == artifact
        assert (n["repo"] / "m.py").read_bytes() == BASELINE
        assert all(f.done() for f in executor._native_futures)
        assert executor._job.done()
        assert not executor._uncertain_task.done()
        assert executor._drained() is False
        assert executor.uncertainty_acknowledged is False
        assert record.status is AgentStatus.DRAINING
        assert record.current_task_id == n["request"].binding.task_id
        assert n["hooks"].outcomes == []
    finally:
        report_release.set()
        ack_release.set()
        await asyncio.gather(task, return_exceptions=True)
        if executor._uncertain_task is not None:
            await asyncio.shield(executor._uncertain_task)
    await asyncio.sleep(0)  # Let the actual settlement callbacks attempt claim release.
    assert executor._drained() is True
    assert executor._stopping is False
    assert executor._latched is True
    assert executor.uncertainty_acknowledged is (ack == "acknowledged")
    assert record.status is (AgentStatus.IDLE if ack == "acknowledged" else AgentStatus.DRAINING)
    assert record.current_task_id == (
        None if ack == "acknowledged" else n["request"].binding.task_id
    )
    with pytest.raises(NativeCodingDenied, match="executor unavailable"):
        await executor.execute(n["request"], grant=n["grant"], model=n["model"], hooks=n["hooks"])
    assert await executor.stop(drain_timeout_s=0.01) is True
    assert attempts == [("verification", "identity_changed")]
    assert dispatches == [
        {"test_paths": None, "run_tests_flag": False, "cleanup": False, "persist": False}
    ]
    assert len(n["calls"]) == 1
    assert len([event for event in n["hooks"].events if event[0] == "intent"]) == 1
    assert n["hooks"].outcomes == []
    assert executor.native_report is report
    assert report.model_dump(mode="json") == report_json
    assert (root / "m.py").read_bytes() == artifact
    assert (n["repo"] / "m.py").read_bytes() == BASELINE
    print(
        "R1_SETTLEMENT_PROOF",
        json.dumps(
            {
                "lease_loss": lease_loss,
                "ack": ack,
                "report_sha256": canonical_sha256(report_json),
                "uncertainty_attempts": attempts,
                "acknowledged": executor.uncertainty_acknowledged,
                "drained": executor._drained(),
                "latched": executor._latched,
                "status": record.status.value,
                "subsequent_execute_rejected_before_stop": True,
            }
        ),
    )
