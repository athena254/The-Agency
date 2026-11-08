"""SERVICE admission, synthetic HTTP transport, real native planner/Forge/checker."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from agency.admission.budget import OwnerBudgetAdapter
from agency.admission.models import OwnerPrincipal
from agency.admission.service import OwnerPilotService
from agency.admission.store import OwnerPilotStore
from agency.agents.factory_coding import NativeCodingExecutor, NativeLiteralProfile
from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.butler.service import ButlerService
from agency.butler.threads import ThreadStore
from agency.factory.coding_models import FactoryCodingRequest, canonical_sha256
from agency.factory.coding_verification import checker_source_sha256, criteria_document
from agency.factory.service import AgentFactory
from agency.kernel.audit import BetaAuditLog
from agency.kernel.registry import AgentRegistry as KernelRegistry
from agency.llm.adapter import LLMAdapter
from agency.llm.config import LLMConfig, ProviderKind
from agency.orchestrator import AgencyOrchestrator
from agency.telegram.budget_store import BetaBudgetStore

pytestmark = pytest.mark.asyncio
PLAN = [{"path": "m.py", "old": "x = 1\n", "new": "x = 2"}]


def git(repo, *args):
    return (
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
        .stdout.decode()
        .strip()
    )


def make_service(c, *, coding=True):
    store = OwnerPilotStore(str(c.root / "owner.sqlite"))
    budget = OwnerBudgetAdapter(BetaBudgetStore(str(c.root / "quota.sqlite")), store)
    audit = BetaAuditLog(c.root / "audit.sqlite")
    butler = ButlerService(orchestrator=c.orch, thread_store=c.threads)
    return OwnerPilotService(
        butler=butler,
        store=store,
        budget=budget,
        audit=audit,
        provider_config=c.config,
        owner_id=c.owner.owner_id,
        workspace_id=c.workspace,
        coding=c.executor if coding else None,
    )


@pytest.fixture
async def coding_case(tmp_path, monkeypatch, request):
    repo = tmp_path / "trusted"
    repo.mkdir()
    (repo / "m.py").write_bytes(b"x = 1\n")
    git(repo, "init", "-q")
    for key, value in [
        ("user.name", "Synthetic Author"),
        ("user.email", "fixture@example.invalid"),
        ("core.autocrlf", "false"),
        ("core.hooksPath", str(tmp_path / "no-hooks")),
    ]:
        git(repo, "config", key, value)
    git(repo, "add", "m.py")
    git(repo, "commit", "-qm", "synthetic trusted fixture")
    kernels, runtimes = KernelRegistry(), RuntimeRegistry()
    factory = AgentFactory(
        str(tmp_path / "factory.sqlite"),
        kernel_registry=kernels,
        runtime_registry=runtimes,
        authorizer=lambda a, p: a == "synthetic-approver",
    )
    factory.create("fixture-coder", "1.0.0", "Fixture coder", creator="synthetic-author")
    factory.approve(
        "fixture-coder",
        "1.0.0",
        approver="synthetic-approver",
        evidence="synthetic fixture approval, not production",
    )
    agent = factory.activate("fixture-coder", "1.0.0")
    owner = OwnerPrincipal("fixture-owner", "local", "fixture-subject")
    # Provision only an existing owner thread, never a pre-admitted/running request.
    setup = OwnerPilotStore(str(tmp_path / "owner.sqlite"))
    await setup.initialize()
    workspace = await setup.ensure_workspace(owner)
    _, thread = await setup.create_thread(owner, "thread", "Synthetic fixture")
    await setup.close()
    config = LLMConfig(
        provider=ProviderKind.OPENAI,
        model="synthetic-model",
        api_key=SecretStr("SYNTHETIC-NOT-A-SECRET"),
        base_url="https://provider.invalid/v1",
        timeout=10.0,
    )
    dispatch = LLMAdapter(config=config).freeze_dispatch()
    profile = NativeLiteralProfile(
        owner_id=owner.owner_id,
        workspace_id=workspace,
        blueprint_id="fixture-coder",
        blueprint_version="1.0.0",
        runtime_agent_id=agent.id,
        blueprint_json=json.dumps(factory.get("fixture-coder", "1.0.0").to_dict()),
        identity_json=json.dumps(agent.model_dump(mode="json")),
        events_json=json.dumps(factory.list_events("fixture-coder", "1.0.0")),
        repo=repo,
        base_commit=git(repo, "rev-parse", "HEAD"),
        base_tree=git(repo, "rev-parse", "HEAD^{tree}"),
        worktree_root=tmp_path / "trusted-worktrees",
        evidence_root=tmp_path / "evidence",
        checker_sha256=checker_source_sha256(),
        criteria_sha256=canonical_sha256(criteria_document()),
        provider=dispatch.provider,
        model=dispatch.model,
        provider_config_id="immutable-fixture-config",
        dispatch_json=json.dumps(
            {
                k: getattr(dispatch, k)
                for k in ("provider", "model", "base_url", "max_tokens", "temperature", "timeout")
            }
        ),
        exclusive_registries=True,
        job_timeout_s=getattr(request, "param", 30.0),
    )
    executor = NativeCodingExecutor(
        profile=profile, factory=factory, kernel_registry=kernels, runtime_registry=runtimes
    )
    threads = ThreadStore(str(tmp_path / "owner.sqlite"))
    orch = AgencyOrchestrator(
        llm=LLMAdapter(provider="echo", model="echo"), memory_db_path=":memory:"
    )
    c = SimpleNamespace(
        root=tmp_path,
        repo=repo,
        owner=owner,
        workspace=workspace,
        thread=thread,
        config=config,
        executor=executor,
        threads=threads,
        orch=orch,
        factory=factory,
        agent=agent,
        runtimes=runtimes,
        calls=[],
        response=json.dumps(PLAN),
        service=None,
    )
    client = httpx.AsyncClient
    c.http_client = client

    async def handle(request):
        c.calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": c.response}}]})

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(handle), **kw)
    )
    try:
        yield c
    finally:
        if c.service is not None:
            await c.service.stop()
        assert await executor.stop(drain_timeout_s=5.0)
        factory.close()
        threads.close()


def payload(c, request_id="coding", **kw):
    return FactoryCodingRequest(
        request_id=request_id,
        thread_id=c.thread.id,
        profile_id="literal-assignment-v1",
        task="Set x to integer 2",
        **kw,
    )


async def settled(c, view):
    async with asyncio.timeout(15):
        await asyncio.shield(c.service._execution)
    return await c.service._store.get(c.owner, view.task_id)


async def test_service_admits_real_native_success_and_replays_history(coding_case, monkeypatch):
    import sqlite3

    import agency.agents.factory_coding as native

    c = coding_case
    counts = []
    planner, forge = native.plan_edits, native.AgencyCodingAgent.run_task

    def planning(*a, **kw):
        counts.append("planner")
        return planner(*a, **kw)

    def writing(*a, **kw):
        counts.append("forge")
        return forge(*a, **kw)

    monkeypatch.setattr(native, "plan_edits", planning)
    monkeypatch.setattr(native.AgencyCodingAgent, "run_task", writing)
    service = c.service = make_service(c)
    await service.start()
    admitted = await service.submit_coding(c.owner, payload(c))
    row = await settled(c, admitted)
    assert row.state == "COMPLETED"
    assert row.model_attempts == 1 and row.tool_attempts == 0
    saved = await service._store.get_coding_record(c.owner, row.task_id, c.workspace)
    assert saved.evidence_phase == "published"
    assert saved.evidence.receipt.objective_status == "passed"
    assert saved.edits[0].new == "x = 2"
    assert Path(saved.evidence.forge_report.worktree_path, "m.py").read_bytes() == b"x = 2"
    assert (c.repo / "m.py").read_bytes() == b"x = 1\n"
    assert saved.evidence.receipt.tests_run is False
    assert saved.evidence.receipt.release_authorized is False
    receipt = await service.get_coding_receipt(c.owner, row.task_id)
    assert receipt["reason"] == "available"
    assert receipt["receipt"] == saved.evidence.receipt.model_dump(mode="json")
    assert len(c.calls) == 1
    assert "tools" not in c.calls[0]
    messages = await service._store.list_messages(c.owner, c.thread.id)
    assert len(messages) == 2 and messages[0].content == payload(c).task
    assert not await service._audit.has_pending_calls()
    assert counts == ["planner", "forge"]
    with sqlite3.connect(c.root / "quota.sqlite") as conn:
        assert conn.execute("SELECT state FROM requests").fetchall() == [("COMPLETED",)]
    with sqlite3.connect(c.root / "audit.sqlite") as conn:
        assert conn.execute(
            "SELECT result FROM audit_entries WHERE action='owner.gateway_result'"
        ).fetchall() == [("completed",)]
    assert not service._butler._started and not c.orch._started
    print(
        "SERVICE_NATIVE_PROOF "
        + json.dumps(
            {
                "receipt": receipt,
                "report": saved.evidence.forge_report.model_dump(mode="json"),
                "original_hex": (c.repo / "m.py").read_bytes().hex(),
                "artifact_hex": Path(saved.evidence.forge_report.worktree_path, "m.py")
                .read_bytes()
                .hex(),
            }
        )
    )
    await service.stop()
    c.service = service = make_service(c, coding=False)
    await service.start()

    def forbidden(*a, **kw):
        raise AssertionError("historical replay must not allocate/provision/check live files")

    import agency.admission.service as module

    monkeypatch.setattr(module, "uuid4", forbidden)
    monkeypatch.setattr(service._store, "ensure_workspace", forbidden)
    monkeypatch.setattr(c.executor, "execute", forbidden)
    replay = await service.submit_coding(c.owner, payload(c))
    assert replay.replayed and replay.task_id == row.task_id and replay.state == "COMPLETED"
    assert await service.get_coding_receipt(c.owner, row.task_id) == receipt
    assert len(c.calls) == 1
    assert len(await service._store.list_messages(c.owner, c.thread.id)) == 2


@pytest.mark.parametrize("objective", ["failed", "unable_to_verify"])
async def test_known_objective_failure_is_private_and_failed_quota(
    coding_case, monkeypatch, objective
):
    import sqlite3

    import agency.factory.coding_verification as checker

    c = coding_case
    if objective == "failed":
        c.response = json.dumps([{**PLAN[0], "new": "x = 3"}])
    else:
        # Controlled checker read boundary, not a fabricated native outcome/report.
        monkeypatch.setattr(checker, "_read_artifact", lambda root: (None, "artifact_unavailable"))
    c.service = service = make_service(c)
    await service.start()
    admitted = await service.submit_coding(c.owner, payload(c))
    row = await settled(c, admitted)
    assert row.state == "FAILED"
    saved = await service._store.get_coding_record(c.owner, row.task_id, c.workspace)
    assert saved.evidence is None  # Approved read boundary conceals private stage.
    assert saved.evidence_phase == "staged"
    with sqlite3.connect(c.root / "owner.sqlite") as conn:
        evidence = json.loads(
            conn.execute(
                "SELECT evidence_json FROM pilot_coding_bindings WHERE sequence=?", (row.sequence,)
            ).fetchone()[0]
        )
        assert evidence["receipt"]["objective_status"] == objective
        assert (
            conn.execute(
                "SELECT result_json FROM pilot_requests WHERE sequence=?", (row.sequence,)
            ).fetchone()[0]
            is not None
        )
    assert (await service.get_coding_receipt(c.owner, row.task_id))["receipt"] is None
    assert await service._store.list_messages(c.owner, c.thread.id) == []
    with sqlite3.connect(c.root / "quota.sqlite") as conn:
        assert conn.execute("SELECT state FROM requests").fetchall() == [("FAILED",)]
    assert len(c.calls) == 1
    assert c.executor.uncertainty_acknowledged is None


@pytest.mark.parametrize(
    "fact", ["provider", "model", "base_url", "max_tokens", "temperature", "timeout"]
)
async def test_constructor_rejects_each_dispatch_mismatch_without_taking_ownership(
    coding_case, fact
):
    from agency.admission.models import PilotError

    c = coding_case
    values = {
        "provider": ProviderKind.ECHO,
        "model": "different",
        "base_url": "https://other.invalid",
        "max_tokens": 123,
        "temperature": 0.125,
        "timeout": 7.0,
    }
    c.config = c.config.model_copy(update={fact: values[fact]})
    with pytest.raises(PilotError) as error:
        make_service(c)
    assert error.value.code == "invalid_request"
    assert not c.executor._stopping


@pytest.mark.parametrize("drift", ["revoked", "profile", "foreign_claim"])
async def test_fresh_authority_drift_refused_before_admission(coding_case, drift):
    from agency.admission.models import PilotError
    from agency.agents.registry import AgentStatus

    c = coding_case
    c.service = service = make_service(c)
    await service.start()
    if drift == "revoked":
        c.agent.revoked = True
    elif drift == "profile":
        object.__setattr__(c.executor.profile, "provider_config_id", "mutated")
    else:
        c.runtimes.update_agent_status(c.agent.id, AgentStatus.BUSY, current_task_id="foreign")
    with pytest.raises(PilotError):
        await service.submit_coding(c.owner, payload(c))
    assert await service._store.list_tasks(c.owner) == []
    assert c.calls == []
    if drift == "foreign_claim":
        assert c.runtimes.get_agent(c.agent.id).current_task_id == "foreign"


async def test_cross_kind_copied_reference_cannot_replay_as_ordinary(coding_case):
    from agency.admission.models import OwnerTaskRequest, PilotError

    c = coding_case
    c.service = service = make_service(c)
    await service.start()
    view = await service.submit_coding(c.owner, payload(c))
    row = await settled(c, view)
    marker = json.loads(row.input_json)["message"]
    with pytest.raises(PilotError) as error:
        await service.submit(
            c.owner,
            OwnerTaskRequest(request_id=row.request_id, thread_id=row.thread_id, message=marker),
        )
    assert error.value.code == "conflict"
    assert len(c.calls) == 1


@pytest.mark.parametrize("boundary", ["stage", "audit", "quota", "commit"])
async def test_lost_finalization_ack_is_unknown_never_retried_or_downgraded(
    coding_case, monkeypatch, boundary
):
    from agency.admission.models import PilotError

    c = coding_case
    c.service = service = make_service(c)
    await service.start()
    obj, name = {
        "stage": (service._store, "stage_coding_result"),
        "audit": (service._audit, "append_beta"),
        "quota": (service._budget, "finish_request"),
        "commit": (service._store, "commit_coding_turn"),
    }[boundary]
    original = getattr(obj, name)
    calls = []

    async def lost(*a, **kw):
        if boundary == "audit" and a[0].action != "owner.gateway_result":
            return await original(*a, **kw)
        calls.append(name)
        await original(*a, **kw)
        raise PilotError("audit" if boundary == "audit" else "store", uncertain=True)

    monkeypatch.setattr(obj, name, lost)
    view = await service.submit_coding(c.owner, payload(c))
    row = await settled(c, view)
    assert row.state == ("COMPLETED" if boundary == "commit" else "UNCERTAIN")
    assert service._blocked and calls == [name]
    assert len(c.calls) == 1
    replay = await service.submit_coding(c.owner, payload(c))
    assert replay.replayed and replay.state == row.state
    receipt = await service.get_coding_receipt(c.owner, row.task_id)
    assert (receipt["receipt"] is not None) == (boundary == "commit")
    assert len(await service._store.list_messages(c.owner, c.thread.id)) == (
        2 if boundary == "commit" else 0
    )


async def test_ambiguous_admission_ack_latches_without_dispatch_or_retry(coding_case, monkeypatch):
    from agency.admission.models import PilotError

    c = coding_case
    c.service = service = make_service(c)
    await service.start()
    original = service._store.admit_coding
    calls = []

    async def lost(*a, **kw):
        calls.append("admit")
        await original(*a, **kw)
        raise PilotError("store", uncertain=True)

    monkeypatch.setattr(service._store, "admit_coding", lost)
    with pytest.raises(PilotError) as error:
        await service.submit_coding(c.owner, payload(c))
    assert error.value.uncertain and service._blocked
    assert service._execution is None and c.calls == []
    replay = await service.submit_coding(c.owner, payload(c))
    assert replay.replayed and replay.state == "ADMITTED"
    assert calls == ["admit"]
    with pytest.raises(PilotError) as error:
        await service.submit_coding(c.owner, payload(c, "fresh"))
    assert error.value.code == "uncertainty"


async def test_invalid_principal_type_is_invalid_request_not_unauthorized(coding_case):
    from agency.admission.models import PilotError

    c = coding_case
    c.service = service = make_service(c)
    await service.start()
    with pytest.raises(PilotError) as error:
        await service.submit_coding(object(), payload(c))
    assert error.value.code == "invalid_request"
    assert await service._store.list_tasks(c.owner) == []


async def test_disabled_lookup_precedence_and_no_provisioning(coding_case, monkeypatch):
    from agency.admission.models import PilotError

    c = coding_case
    c.service = service = make_service(c, coding=False)
    await service.start()

    def forbidden(*a, **kw):
        raise AssertionError("coding read/admission must never provision")

    monkeypatch.setattr(service._store, "ensure_workspace", forbidden)
    with pytest.raises(PilotError) as error:
        await service.submit_coding(c.owner, payload(c))
    assert error.value.code == "disabled"
    assert await service._store.list_tasks(c.owner) == []
    await service._store.close()
    with pytest.raises(PilotError) as error:
        await service.submit_coding(c.owner, payload(c))
    assert error.value.code == "store"


@pytest.mark.parametrize("change", ["task", "thread_id"])
async def test_historical_complete_body_conflict_with_native_disabled(coding_case, change):
    from agency.admission.models import PilotError

    c = coding_case
    c.service = service = make_service(c)
    await service.start()
    view = await service.submit_coding(c.owner, payload(c))
    await settled(c, view)
    await service.stop()
    c.service = service = make_service(c, coding=False)
    await service.start()
    changed = payload(c).model_copy(update={change: "different"})
    with pytest.raises(PilotError) as error:
        await service.submit_coding(c.owner, changed)
    assert error.value.code == "conflict" and len(c.calls) == 1


async def test_body_and_principal_detached_before_lifecycle_wait(coding_case):
    c = coding_case
    c.service = service = make_service(c)
    await service.start()
    original = payload(c)
    principal = OwnerPrincipal(c.owner.owner_id, c.owner.transport, c.owner.subject_id)
    await service._gate.acquire()
    submission = asyncio.create_task(service.submit_coding(principal, original))
    try:
        await asyncio.sleep(0)
        object.__setattr__(original, "task", "changed after first await")
        object.__setattr__(principal, "owner_id", "intruder")
    finally:
        service._gate.release()
    view = await submission
    row = await settled(c, view)
    assert row.state == "COMPLETED"
    saved = await service._store.get_coding_record(c.owner, row.task_id, c.workspace)
    assert saved.request.task == "Set x to integer 2"
    assert saved.binding.owner_id == c.owner.owner_id


@pytest.mark.parametrize("invalid", ["extra", "task", "profile", "owner"])
async def test_mutated_input_refused_before_any_dispatch(coding_case, invalid):
    from agency.admission.models import PilotError

    c = coding_case
    c.service = service = make_service(c)
    await service.start()
    body = payload(c)
    principal = c.owner
    if invalid == "extra":
        body = {**body.model_dump(), "repo": str(c.repo)}
    elif invalid == "owner":
        principal = OwnerPrincipal("intruder", "local", "intruder")
    else:
        object.__setattr__(body, "task" if invalid == "task" else "profile_id", 1)
    with pytest.raises(PilotError) as error:
        await service.submit_coding(principal, body)
    assert error.value.code == ("unauthorized" if invalid == "owner" else "invalid_request")
    assert await service._store.list_tasks(c.owner) == [] and c.calls == []


async def test_ordinary_marker_never_promotes_and_cross_kind_coding_conflicts(
    coding_case, monkeypatch
):
    from agency.admission.models import OwnerTaskRequest, PilotError

    c = coding_case
    c.service = service = make_service(c)
    await service.start()
    c.response = '{"final":"ordinary only"}'

    async def forbidden(*a, **kw):
        raise AssertionError("text cannot select native")

    monkeypatch.setattr(c.executor, "execute", forbidden)
    view = await service.submit(
        c.owner,
        OwnerTaskRequest(
            request_id="coding", thread_id=c.thread.id, message="factory-coding.v1:" + "a" * 64
        ),
    )
    row = await settled(c, view)
    assert row.state == "COMPLETED"
    assert (await service.get_coding_receipt(c.owner, row.task_id))["reason"] == "not_requested"
    with pytest.raises(PilotError) as error:
        await service.submit_coding(c.owner, payload(c))
    assert error.value.code == "conflict"


async def test_corrupt_completed_evidence_is_sanitized_without_checker_io(coding_case, monkeypatch):
    import sqlite3

    import agency.factory.coding_verification as checker
    from agency.admission.models import PilotError

    c = coding_case
    c.service = service = make_service(c)
    await service.start()
    view = await service.submit_coding(c.owner, payload(c))
    await settled(c, view)

    def forbidden(*a, **kw):
        raise AssertionError("historical evidence never reruns checker")

    monkeypatch.setattr(checker, "checker_source_sha256", forbidden)
    monkeypatch.setattr(checker, "verify_literal_artifact", forbidden)
    with sqlite3.connect(c.root / "owner.sqlite") as conn:
        conn.execute("UPDATE pilot_coding_bindings SET evidence_json=?", (b"not text",))
    with pytest.raises(PilotError) as error:
        await service.get_coding_receipt(c.owner, view.task_id)
    assert error.value.code == "store" and len(c.calls) == 1


async def test_thread_creation_same_id_is_conflict_not_coding(coding_case):
    from agency.admission.models import PilotError

    c = coding_case
    c.service = service = make_service(c)
    await service.start()
    await service.create_thread(c.owner, "coding", "Different owner operation")
    with pytest.raises(PilotError) as error:
        await service.submit_coding(c.owner, payload(c))
    assert error.value.code == "conflict" and c.calls == []


async def test_butler_refuses_different_thread_store_before_native(coding_case, monkeypatch):
    import sqlite3

    c = coding_case
    c.service = service = make_service(c)
    await service.start()
    # Caller-owned but different database: same owner/thread spelling is not authority.
    with (
        sqlite3.connect(c.root / "owner.sqlite") as source,
        sqlite3.connect(c.root / "alien.sqlite") as target,
    ):
        source.backup(target)
    alien = ThreadStore(str(c.root / "alien.sqlite"))
    native_calls = []

    async def never(*a, **kw):
        native_calls.append("execute")
        raise AssertionError("mismatched ThreadStore reached native")

    monkeypatch.setattr(c.executor, "execute", never)
    monkeypatch.setattr(service._butler, "_threads", alien)
    try:
        view = await service.submit_coding(c.owner, payload(c))
        row = await settled(c, view)
        assert native_calls == []
        assert row.state == "FAILED" and row.error_code == "forbidden"
    finally:
        alien.close()
