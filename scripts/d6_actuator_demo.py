"""D6 live multi-peer actuator demonstration (gaps.md #18 route-enforcement tail).

Self-contained loopback demo — no network beyond 127.0.0.1, no LLM keys, no
real side effects. It proves the §12 grant path end-to-end against a real API
server process:

1. create peer identities (Ed25519) through ``agency.peers.identity_store`` —
   the pinned genesis format (owner decision D-01) requires exactly four
   validator members; the demo actuates with the first two (issuer + revoker);
2. derive genesis membership from those identities and start ``uvicorn
   agency.api.server:app`` with file-backed stores (tasks, grant ledger,
   audit, evidence, memory) under a temp dir;
3. pre-seed the durable grant store with a signed revocation (so the server
   loads it as persisted state, not in-process memory);
4. POST /v1/tasks with a bearer token (control), then with a valid signed
   grant and NO bearer (the grant path) — both accepted; the grant-intake
   audit line records ``auth_kind=grant`` and the task is bound server-side
   to the verified issuer peer id;
5. replay the same grant, then present expired / tampered / revoked /
   wrongly-scoped grants — every one rejected fail-closed (401/403).

Evidence JSON is written to ``D:/agency-merge-backups/d6-actuator-evidence.json``.
No secrets are written: bearer token values and private keys stay in the temp
dir (which is removed) and never enter the evidence file.

Run with::

    uv run python scripts/d6_actuator_demo.py
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from agency.consensus.genesis import load_genesis
from agency.consensus.grant_store import DurableGrantStore
from agency.consensus.grants import ExecutionGrant, GrantLedger
from agency.peers.canonical import digest_json
from agency.peers.identity_store import IdentityStore

EVIDENCE_PATH = Path("D:/agency-merge-backups/d6-actuator-evidence.json")
POLICY_VERSION = "peer-policy/1"
GENESIS_MEMBERS = 4  # pinned genesis format requires exactly 4 validators
ISSUER_INDEX = 0
REVOKER_INDEX = 1
BFT_TIME_MS = 1000  # the server's committed-time default (AGENCY_GRANT_BFT_TIME_MS)
OWNER_BEARER_TOKEN = "d6-demo-owner-token-0123456789abcdefghij"  # >= 24 chars, temp only
HEALTH_TIMEOUT_S = 120.0
REQUEST_TIMEOUT_S = 30.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _grant_payload(grant: ExecutionGrant) -> dict[str, Any]:
    return {
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


def _grant_header(grant: ExecutionGrant, *, tamper: bool = False) -> str:
    payload = _grant_payload(grant)
    if tamper:
        signature = payload["signature"]
        payload["signature"] = signature[:-2] + ("00" if signature[-2:] != "00" else "11")
    return base64.b64encode(json.dumps(payload).encode()).decode()


def _request(
    url: str, *, body: dict[str, Any] | None = None, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, Any]]:
    sent_headers = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        sent_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method="POST", headers=sent_headers)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            return int(resp.status), json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode() or "{}"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return int(exc.code), parsed


def _wait_for_health(process: subprocess.Popen[Any], url: str, deadline_s: float) -> bool:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if resp.status == 200:
                    return True
        except urllib.error.URLError:
            time.sleep(0.5)
    return False


def main() -> int:
    steps: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {
        "demo": "d6-actuator-route-swap",
        "gap": "gaps.md #18",
        "generated_at": datetime.now(UTC).isoformat(),
        "worktree": "D:/agency-wave-d6",
        "branch": "gap/d6-route-swap",
        "transport": "loopback HTTP (127.0.0.1)",
        "llm": "echo provider (no API keys)",
        "steps": steps,
    }
    work: list[str] = []

    temp_dir = Path(tempfile.mkdtemp(prefix="d6-actuator-demo-"))
    server: subprocess.Popen[Any] | None = None
    log_handle = None
    try:
        # 1. Peer identities through the protected identity stores.
        identities = [
            IdentityStore(temp_dir / f"peer-{index}").create() for index in range(GENESIS_MEMBERS)
        ]
        issuer = identities[ISSUER_INDEX]
        revoker = identities[REVOKER_INDEX]
        members = [
            {
                "peer_id": identity.peer_id,
                "public_key": identity.fingerprint,
                "roles": ["validator"],
                "joined_epoch": 1,
            }
            for identity in identities
        ]
        genesis_path = temp_dir / "genesis.json"
        genesis_path.write_text(
            json.dumps({"policy_version": POLICY_VERSION, "members": members}), encoding="utf-8"
        )
        tokens_path = temp_dir / "tokens.json"
        tokens_path.write_text(
            json.dumps([{"token": OWNER_BEARER_TOKEN, "caller_id": "danny", "role": "owner"}]),
            encoding="utf-8",
        )
        tasks_db = temp_dir / "tasks.db"
        grants_db = temp_dir / "grants.db"
        audit_db = temp_dir / "audit.db"
        evidence_db = temp_dir / "evidence.db"
        memory_db = temp_dir / "memory.db"
        work += [
            str(temp_dir),
            genesis_path.name,
            grants_db.name,
            tasks_db.name,
            audit_db.name,
        ]

        # 2. Membership from the pinned genesis file (deterministic epoch/root).
        membership = load_genesis(genesis_path)
        evidence["genesis"] = {
            "path": genesis_path.name,
            "policy_version": POLICY_VERSION,
            "epoch": membership.epoch,
            "root": membership.root,
            "members": [member.peer_id for member in membership.members],
            "issuer_peer_id": issuer.peer_id,
            "revoker_peer_id": revoker.peer_id,
        }

        # 3. Pre-seed the durable grant store with a signed revocation; the
        #    server loads this file-backed state on startup (no in-process
        #    revocation hand-off).
        durable = DurableGrantStore(membership=membership, db_path=grants_db)
        grant_revoked = durable.issue(
            issuer,
            proposal_id="prop_" + "b" * 32,
            task_id="POST /v1/tasks",
            issued_at_ms=1000,
            expires_at_ms=61000,
            affected_consumers=[],
        )
        durable.revoke(revoker, grant_revoked, reason_digest=digest_json({"reason": "d6-demo"}))
        durable.close()

        # Remaining grants are issued by the issuer peer; the server verifies
        # them against the same pinned membership.
        ledger = GrantLedger(membership=membership)
        grant_ok = ledger.issue(
            issuer,
            proposal_id="prop_" + "c" * 32,
            task_id="POST /v1/tasks",
            issued_at_ms=1000,
            expires_at_ms=61000,
            affected_consumers=[],
        )
        grant_expired = ledger.issue(
            issuer,
            proposal_id="prop_" + "d" * 32,
            task_id="POST /v1/tasks",
            issued_at_ms=1,
            expires_at_ms=BFT_TIME_MS,  # committed time >= expiry -> expired
            affected_consumers=[],
        )
        grant_wrong_scope = ledger.issue(
            issuer,
            proposal_id="prop_" + "e" * 32,
            task_id="GET /v1/agents",  # not the POST /v1/tasks intake scope
            issued_at_ms=1000,
            expires_at_ms=61000,
            affected_consumers=[],
        )

        # 4. Start the API server process (loopback only).
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = dict(os.environ)
        env.update(
            {
                "AGENCY_GENESIS_PATH": str(genesis_path),
                "AGENCY_API_TOKENS": str(tokens_path),
                "AGENCY_GRANT_STORE_DB": str(grants_db),
                "AGENCY_TASKS_DB": str(tasks_db),
                "AGENCY_AUDIT_DB": str(audit_db),
                "AGENCY_EVIDENCE_DB": str(evidence_db),
                "AGENCY_MEMORY_DB": str(memory_db),
                "AGENCY_LLM_PROVIDER": "echo",
                "AGENCY_LLM_MODEL": "echo",
                "PYTHONPATH": str(REPO_ROOT / "src"),
            }
        )
        server_log = temp_dir / "server.log"
        log_handle = server_log.open("w", encoding="utf-8")
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "agency.api.server:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "info",
            ],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
        if not _wait_for_health(server, f"{base_url}/v1/health", HEALTH_TIMEOUT_S):
            raise RuntimeError("API server did not become healthy in time")
        steps.append(
            {"step": "server_up", "detail": f"uvicorn agency.api.server:app on 127.0.0.1:{port}"}
        )

        def record(name: str, status: int, body: dict[str, Any], expect: int, note: str) -> None:
            ok = status == expect
            steps.append(
                {
                    "step": name,
                    "status": status,
                    "expected": expect,
                    "ok": ok,
                    "detail": note,
                    "response": body,
                }
            )
            if not ok:
                raise AssertionError(f"{name}: expected {expect}, got {status}: {body}")

        harmless = {"title": "d6 demo task", "input": {"description": "harmless noop"}}

        # 5a. Control: bearer-token intake (unchanged path).
        status, body = _request(
            f"{base_url}/v1/tasks",
            body=harmless,
            headers={"Authorization": f"Bearer {OWNER_BEARER_TOKEN}"},
        )
        record("bearer_intake", status, body, 201, "owner bearer token; auth_kind=bearer in log")
        work.append("bearer task_id=" + str(body.get("task_id")))

        # 5b. The grant path: valid signed grant, NO bearer header.
        ok_header = _grant_header(grant_ok)
        status, body = _request(
            f"{base_url}/v1/tasks", body=harmless, headers={"X-Agency-Grant": ok_header}
        )
        record(
            "grant_intake",
            status,
            body,
            201,
            "signed task-scoped grant verified actuator-side; "
            "created_by bound to the verified issuer peer id; auth_kind=grant in log",
        )
        if body.get("created_by") != issuer.peer_id:
            raise AssertionError(f"grant intake not bound to issuer: {body}")
        grant_digest = grant_ok.digest
        work.append("grant task_id=" + str(body.get("task_id")))

        # 5c. Replay of the consumed grant -> 403 (nonce spent at point of use).
        status, body = _request(
            f"{base_url}/v1/tasks", body=harmless, headers={"X-Agency-Grant": ok_header}
        )
        record(
            "grant_replay",
            status,
            body,
            403,
            "same grant re-presented; nonce consumed at point of use -> fail closed",
        )

        # 5d. Expired grant -> 403.
        status, body = _request(
            f"{base_url}/v1/tasks",
            body=harmless,
            headers={"X-Agency-Grant": _grant_header(grant_expired)},
        )
        record("grant_expired", status, body, 403, "expiry beyond committed BFT time -> 403")

        # 5e. Tampered grant -> 401.
        status, body = _request(
            f"{base_url}/v1/tasks",
            body=harmless,
            headers={"X-Agency-Grant": _grant_header(grant_ok, tamper=True)},
        )
        record(
            "grant_tampered", status, body, 401, "flipped signature byte -> invalid_signature 401"
        )

        # 5f. Revoked grant -> 403 (revocation loaded from the durable store).
        status, body = _request(
            f"{base_url}/v1/tasks",
            body=harmless,
            headers={"X-Agency-Grant": _grant_header(grant_revoked)},
        )
        record(
            "grant_revoked",
            status,
            body,
            403,
            "revocation pre-recorded in the file-backed grant store -> revoked 403",
        )

        # 5g. Wrongly-scoped grant -> 403.
        status, body = _request(
            f"{base_url}/v1/tasks",
            body=harmless,
            headers={"X-Agency-Grant": _grant_header(grant_wrong_scope)},
        )
        record("grant_wrong_scope", status, body, 403, "grant scoped to GET /v1/agents -> 403")

        # 6. Audit observation: the server's own log lines carry the auth kind.
        time.sleep(1.0)  # allow the handler log lines to flush
        log_text = server_log.read_text(encoding="utf-8", errors="replace")
        grant_line = "auth_kind=grant" in log_text and "api.task.created" in log_text
        bearer_line = "auth_kind=bearer" in log_text
        if not grant_line or not bearer_line:
            raise AssertionError("auth-kind not found in the server audit log lines")
        steps.append(
            {
                "step": "audit_auth_kind",
                "ok": True,
                "detail": "server log contains api.task.created lines with "
                "auth_kind=grant (with grant digest) and auth_kind=bearer",
            }
        )

        evidence["grant_digest"] = grant_digest
        evidence["verdict"] = "pass"
        evidence["artifacts"] = work
        print("d6 demo PASS")
        for step in steps:
            print(f"  {step['step']}: {step.get('status', 'ok')}")
        return 0
    except Exception as exc:  # noqa: BLE001 - demo records every failure in the evidence file
        evidence["verdict"] = "fail"
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        print(f"d6 demo FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=15)
        if log_handle is not None:
            log_handle.close()
        # Keep the evidence honest: on failure, preserve the server log.
        if evidence.get("verdict") == "fail":
            failed_log = temp_dir / "server.log"
            if failed_log.exists():
                evidence["server_log_tail"] = failed_log.read_text(
                    encoding="utf-8", errors="replace"
                )[-4000:]
        shutil.rmtree(temp_dir, ignore_errors=True)
        EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE_PATH.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(f"evidence written to {EVIDENCE_PATH}")


if __name__ == "__main__":
    sys.exit(main())
