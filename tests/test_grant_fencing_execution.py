"""Grant-admitted tasks fence their downstream execution (gaps.md #18).

Two levels, both hermetic (echo LLM, loopback-only, no keys):

* orchestrator seam — a grant-stamped task executes under a committed fencing
  token and records an idempotent finality receipt; a revoked grant, a broken
  fencing store and a missing fencer all refuse the execution fail-closed with
  an honest FAILED outcome; tasks without a grant digest (bearer path and
  internally created tasks) run byte-for-byte unchanged;
* HTTP intake — ``POST /v1/tasks`` stamps the verified grant digest
  server-side on the grant path only and strips any client-supplied value, and
  a grant-intake task executes under a receipt persisted to a durable fencing
  store that survives the server shutdown.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from agency.agents.executor import ExecutionStatus
from agency.consensus.fencing import FencingStateStore
from agency.consensus.fencing_commit import FencingCommitOutcome, GrantFencingCommit
from agency.consensus.grants import GrantLedger
from agency.kernel.tasks import TaskManager, TaskStatus
from agency.orchestrator import AgencyOrchestrator
from agency.peers.canonical import digest_json
from agency.peers.identities import PeerIdentity
from agency.peers.membership import Membership, derive_genesis_membership

POLICY_VERSION = "peer-policy/1"
TASKS_SCOPE = "POST /v1/tasks"
OWNER_TOKEN = "sk-owner-token-with-at-least-24-chars"
PROPOSAL_A = "prop_" + "a" * 32


# --------------------------------------------------------------------------- #
# Shared fixtures
# --------------------------------------------------------------------------- #


def _genesis() -> tuple[list[PeerIdentity], list[dict[str, Any]], Membership]:
    ids = [PeerIdentity.from_private_key(Ed25519PrivateKey.generate()) for _ in range(4)]
    members = [
        {
            "peer_id": identity.peer_id,
            "public_key": identity.fingerprint,
            "roles": ["validator"],
            "joined_epoch": 1,
        }
        for identity in ids
    ]
    return ids, members, derive_genesis_membership(members, policy_version=POLICY_VERSION)


def _ledger(membership: Membership) -> GrantLedger:
    return GrantLedger(membership=membership)


def _issue_digest(ledger: GrantLedger, issuer: PeerIdentity, proposal: str) -> str:
    """Issue one intake-shaped grant and return its digest (the stamped value)."""

    grant = ledger.issue(
        issuer,
        proposal_id=proposal,
        task_id=TASKS_SCOPE,
        issued_at_ms=1000,
        expires_at_ms=61000,
        affected_consumers=[],
    )
    return str(grant.digest)


def _fencer(tmp_path: Path | None, ledger: GrantLedger | None) -> GrantFencingCommit:
    store = FencingStateStore(db_path=tmp_path / "fencing.db" if tmp_path else ":memory:")
    return GrantFencingCommit(store=store, ledger=ledger)


def _orchestrator(manager: TaskManager, fencer: GrantFencingCommit | None) -> AgencyOrchestrator:
    return AgencyOrchestrator(
        task_manager=manager,
        memory_db_path=":memory:",
        skill_db_path=":memory:",
        grant_fencing=fencer,
    )


async def _grant_task(manager: TaskManager, digest: str) -> Any:
    return await manager.create_task(
        created_by="peer-x",
        input={"description": "harmless noop", "grant_digest": digest},
    )


# --------------------------------------------------------------------------- #
# Orchestrator seam
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_grant_task_executes_under_committed_fencing_token() -> None:
    ids, _members, membership = _genesis()
    ledger = _ledger(membership)
    fencer = _fencer(None, ledger)
    manager = TaskManager()
    orch = _orchestrator(manager, fencer)
    await orch.start()
    try:
        digest = _issue_digest(ledger, ids[0], PROPOSAL_A)
        task = await _grant_task(manager, digest)
        result = await orch.execute_task(task.task_id)
        assert result.status is ExecutionStatus.COMPLETED
        record = await manager.get_task(task.task_id)
        assert record.status is TaskStatus.COMPLETED
        receipts = fencer.receipts
        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt.grant_digest == digest
        assert receipt.resource_id == task.task_id
        assert receipt.fencing_token == 1
        assert receipt.outcome == ExecutionStatus.COMPLETED.value
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_requeued_grant_task_replays_existing_receipt() -> None:
    ids, _members, membership = _genesis()
    ledger = _ledger(membership)
    fencer = _fencer(None, ledger)
    manager = TaskManager()
    orch = _orchestrator(manager, fencer)
    await orch.start()
    try:
        digest = _issue_digest(ledger, ids[0], PROPOSAL_A)
        task = await _grant_task(manager, digest)
        # Simulate the prior attempt: receipt committed, task requeued RUNNING.
        fence = fencer.begin(grant_digest=digest, resource_id=task.task_id)
        assert fence.outcome is FencingCommitOutcome.COMMITTED
        recorded = fencer.finalize(
            grant_digest=digest,
            resource_id=task.task_id,
            token=fence.token,
            outcome=ExecutionStatus.COMPLETED.value,
        )
        await manager.update_status(task.task_id, TaskStatus.RUNNING)
        result = await orch.execute_task(task.task_id)
        assert result.status is ExecutionStatus.COMPLETED
        assert result.attempts == 0  # nothing re-executed
        record = await manager.get_task(task.task_id)
        assert record.status is TaskStatus.COMPLETED
        assert record.output.get("fencing_replayed") is True
        assert record.output.get("fencing_receipt_digest") == recorded.digest()
        assert len(fencer.receipts) == 1  # the receipt is the one from before
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_replayed_failed_receipt_surfaces_failed_outcome() -> None:
    ids, _members, membership = _genesis()
    ledger = _ledger(membership)
    fencer = _fencer(None, ledger)
    manager = TaskManager()
    orch = _orchestrator(manager, fencer)
    await orch.start()
    try:
        digest = _issue_digest(ledger, ids[0], PROPOSAL_A)
        task = await _grant_task(manager, digest)
        fence = fencer.begin(grant_digest=digest, resource_id=task.task_id)
        fencer.finalize(
            grant_digest=digest,
            resource_id=task.task_id,
            token=fence.token,
            outcome=ExecutionStatus.FAILED.value,
        )
        await manager.update_status(task.task_id, TaskStatus.RUNNING)
        result = await orch.execute_task(task.task_id)
        assert result.status is ExecutionStatus.FAILED
        assert result.error == "grant_fencing_replayed"
        record = await manager.get_task(task.task_id)
        assert record.status is TaskStatus.FAILED
        assert "receipt replayed" in (record.error or "")
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_revoked_grant_refuses_execution_fail_closed() -> None:
    ids, _members, membership = _genesis()
    ledger = _ledger(membership)
    fencer = _fencer(None, ledger)
    manager = TaskManager()
    orch = _orchestrator(manager, fencer)
    await orch.start()
    try:
        grant = ledger.issue(
            ids[0],
            proposal_id=PROPOSAL_A,
            task_id=TASKS_SCOPE,
            issued_at_ms=1000,
            expires_at_ms=61000,
            affected_consumers=[],
        )
        # Revoked after intake, before the task reaches the executor.
        ledger.revoke(ids[1], grant, reason_digest=digest_json({"reason": "superseded"}))
        task = await _grant_task(manager, grant.digest)
        result = await orch.execute_task(task.task_id)
        assert result.status is ExecutionStatus.FAILED
        assert result.error == "grant_fencing_refused"
        record = await manager.get_task(task.task_id)
        assert record.status is TaskStatus.FAILED
        assert "revoked" in (record.error or "")
        assert fencer.receipts == ()  # nothing executed, nothing recorded
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_broken_fencing_store_refuses_execution_fail_closed() -> None:
    ids, _members, membership = _genesis()
    ledger = _ledger(membership)
    store = FencingStateStore(db_path=":memory:")
    store.close()
    fencer = GrantFencingCommit(store=store, ledger=ledger)
    manager = TaskManager()
    orch = _orchestrator(manager, fencer)
    await orch.start()
    try:
        digest = _issue_digest(ledger, ids[0], PROPOSAL_A)
        task = await _grant_task(manager, digest)
        result = await orch.execute_task(task.task_id)
        assert result.status is ExecutionStatus.FAILED
        assert result.error == "grant_fencing_refused"
        record = await manager.get_task(task.task_id)
        assert record.status is TaskStatus.FAILED
        assert "store_error" in (record.error or "")
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_grant_task_without_fencer_fails_closed() -> None:
    ids, _members, membership = _genesis()
    ledger = _ledger(membership)
    manager = TaskManager()
    orch = _orchestrator(manager, None)
    await orch.start()
    try:
        digest = _issue_digest(ledger, ids[0], PROPOSAL_A)
        task = await _grant_task(manager, digest)
        result = await orch.execute_task(task.task_id)
        assert result.status is ExecutionStatus.FAILED
        assert result.error == "grant_fencing_refused"
        record = await manager.get_task(task.task_id)
        assert record.status is TaskStatus.FAILED
        assert "grant fencing unavailable" in (record.error or "")
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_bearer_task_executes_without_fencing() -> None:
    _ids, _members, membership = _genesis()
    ledger = _ledger(membership)
    fencer = _fencer(None, ledger)
    manager = TaskManager()
    orch = _orchestrator(manager, fencer)
    await orch.start()
    try:
        task = await manager.create_task(created_by="danny", input={"description": "harmless noop"})
        result = await orch.execute_task(task.task_id)
        assert result.status is ExecutionStatus.COMPLETED
        record = await manager.get_task(task.task_id)
        assert record.status is TaskStatus.COMPLETED
        assert fencer.receipts == ()  # no fencing on the bearer path
    finally:
        await orch.stop()


# --------------------------------------------------------------------------- #
# HTTP intake: digest stamping + durable end-to-end execution
# --------------------------------------------------------------------------- #


def _write_genesis(tmp_path: Path, members: list[dict[str, Any]]) -> str:
    path = tmp_path / "genesis.json"
    path.write_text(
        json.dumps({"policy_version": POLICY_VERSION, "members": members}), encoding="utf-8"
    )
    return str(path)


def _write_tokens(tmp_path: Path) -> str:
    path = tmp_path / "tokens.json"
    path.write_text(
        json.dumps([{"token": OWNER_TOKEN, "caller_id": "danny", "role": "owner"}]),
        encoding="utf-8",
    )
    return str(path)


def _encode_grant(payload: dict[str, Any]) -> str:
    return base64.b64encode(json.dumps(payload).encode()).decode()


def _issue_grant(ledger: GrantLedger, issuer: PeerIdentity, proposal: str) -> Any:
    return ledger.issue(
        issuer,
        proposal_id=proposal,
        task_id=TASKS_SCOPE,
        issued_at_ms=1000,
        expires_at_ms=61000,
        affected_consumers=[],
    )


def _grant_header(grant: Any) -> str:
    return _encode_grant(
        {
            "proposal_id": grant.proposal_id,
            "task_id": grant.task_id,
            "issuer_peer_id": grant.issuer_peer_id,
            "membership_epoch": grant.membership_epoch,
            "membership_root": grant.membership_root,
            "nonce": grant.nonce,
            "issued_at_ms": grant.issued_at_ms,
            "expires_at_ms": grant.expires_at_ms,
            "affected_consumers": list(grant.affected_consumers),
            "attestation": {
                "attestor_peer_id": grant.attestation.attestor_peer_id,
                "signature": grant.attestation.signature.hex(),
            },
            "signature": grant.signature.hex(),
        }
    )


def _api_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    members: list[dict[str, Any]],
    *,
    fencing_db: Path | None = None,
) -> TestClient:
    monkeypatch.setenv("AGENCY_GENESIS_PATH", _write_genesis(tmp_path, members))
    monkeypatch.setenv("AGENCY_TASKS_DB", str(tmp_path / "tasks.db"))
    monkeypatch.setenv(
        "AGENCY_API_TOKENS",
        _write_tokens(tmp_path),
    )
    monkeypatch.setenv("AGENCY_LLM_PROVIDER", "echo")
    monkeypatch.setenv("AGENCY_LLM_MODEL", "echo")
    if fencing_db is not None:
        monkeypatch.setenv("AGENCY_FENCING_DB", str(fencing_db))
    else:
        monkeypatch.delenv("AGENCY_FENCING_DB", raising=False)  # in-memory fencing by default
    from agency.api.server import create_app

    return TestClient(create_app())


def _owner_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {OWNER_TOKEN}"}


def test_grant_intake_stamps_digest_bearer_strips_client_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids, members, mem = _genesis()
    with _api_client(tmp_path, monkeypatch, members) as client:
        grant = _issue_grant(_ledger(mem), ids[0], PROPOSAL_A)
        granted = client.post(
            "/v1/tasks",
            json={
                "title": "grant",
                "input": {"description": "noop", "grant_digest": "f" * 64},
            },
            headers={"X-Agency-Grant": _grant_header(grant)},
        )
        bearer = client.post(
            "/v1/tasks",
            json={
                "title": "bearer",
                "input": {"description": "noop", "grant_digest": "e" * 64},
            },
            headers=_owner_headers(),
        )
        assert granted.status_code == 201, granted.text
        assert bearer.status_code == 201, bearer.text
        granted_task = client.get(
            f"/v1/tasks/{granted.json()['task_id']}", headers=_owner_headers()
        ).json()
        bearer_task = client.get(
            f"/v1/tasks/{bearer.json()['task_id']}", headers=_owner_headers()
        ).json()
    # The grant path stamps the server-verified digest; a client-supplied
    # value can never slip through either path.
    assert granted_task["input"]["grant_digest"] == grant.digest
    assert "grant_digest" not in bearer_task["input"]


def test_grant_task_executes_under_durable_receipt_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids, members, mem = _genesis()
    fencing_db = tmp_path / "fencing.db"
    with _api_client(tmp_path, monkeypatch, members, fencing_db=fencing_db) as client:
        grant = _issue_grant(_ledger(mem), ids[0], PROPOSAL_A)
        response = client.post(
            "/v1/tasks",
            json={"title": "fenced", "input": {"description": "harmless noop"}},
            headers={"X-Agency-Grant": _grant_header(grant)},
        )
        assert response.status_code == 201, response.text
        task_id = response.json()["task_id"]
        digest = grant.digest
        deadline = time.monotonic() + 60.0
        body: dict[str, Any] = {}
        while time.monotonic() < deadline:
            body = client.get(f"/v1/tasks/{task_id}", headers=_owner_headers()).json()
            if body.get("status") in ("completed", "failed", "blocked"):
                break
            time.sleep(0.2)
        assert body.get("status") in ("completed", "failed"), body
        assert body["input"]["grant_digest"] == digest
    # The dispatcher executed the grant task under a committed token; the
    # durable fencing store holds the receipt after the server shut down.
    with FencingStateStore(db_path=fencing_db) as store:
        receipt = store.receipt(digest)
        assert receipt is not None
        assert receipt.resource_id == task_id
        assert receipt.fencing_token == 1
        assert receipt.outcome == body["status"]
