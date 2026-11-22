"""Actual local operator entrypoint; synthetic private stores and credentials only."""

import asyncio
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing

from agency.admission.budget import OwnerBudgetAdapter
from agency.admission.models import OwnerPrincipal
from agency.admission.store import OwnerPilotStore
from agency.kernel.audit import AuditEntry, BetaAuditLog
from agency.telegram.budget_store import BetaBudgetStore
from tests.test_pilot_launcher import config_file, protect, token_file


async def installation(path):
    path.mkdir()
    protect(path)
    config = config_file(path)
    token_file(path)
    owner = OwnerPrincipal("synthetic-owner", "local", "synthetic-browser")
    store = OwnerPilotStore(str(path / "pilot.db"))
    backend = BetaBudgetStore(str(path / "quota.db"))
    budget = OwnerBudgetAdapter(backend, store)
    audit = BetaAuditLog(path / "audit.db")
    try:
        await store.initialize()
        await budget.initialize()
        await audit.initialize()
        _, thread = await store.create_thread(owner, "thread", "Synthetic")
        for index in range(2):
            row = await store.admit(
                owner,
                f"unknown-{index}",
                operation="turn",
                payload={"thread_id": thread.id, "message": "unknown"},
            )
            await store.transition(
                row.sequence, expected="ADMITTED", state="UNCERTAIN", phase="uncertain"
            )
        await audit.claim_intent(
            AuditEntry(
                agent="metered-model",
                task="owner-pilot",
                action="model.generate",
                result="pending",
                evidence={"phase": "intent", "call_id": "a" * 32},
            )
        )
    finally:
        await audit.close()
        await budget.close()
        await store.close()
    # POSIX launcher requires 0o700/0o600 (Windows uses ACLs): the real launcher
    # creates runtime files under umask 0o077, so mirror that for every child.
    import sys as _sys

    if _sys.platform != "win32":
        for child in path.iterdir():
            if child.is_file():
                child.chmod(0o600)
    return config


def invoke(path, *args):
    code = """import socket,sys,runpy,ipaddress
original=socket.socket.connect
original_dns=socket.getaddrinfo
def local(host):
 try:return ipaddress.ip_address(host).is_loopback
 except ValueError:return host=='localhost'
def connect(self,address):
 if isinstance(address,tuple) and not local(address[0]):raise RuntimeError('Network forbidden')
 return original(self,address)
def dns(host,*args,**kwargs):
 if host is not None and not local(host):raise RuntimeError('DNS forbidden')
 return original_dns(host,*args,**kwargs)
socket.socket.connect=connect
socket.getaddrinfo=dns
sys.argv=['agency.prototype.owner_recovery',*sys.argv[1:]]
runpy.run_module('agency.prototype.owner_recovery',run_name='__main__')"""
    return subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            "--config",
            str(path / "owner.json"),
            "--data-dir",
            str(path),
            *args,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
        env=dict(os.environ, PYTHON_DOTENV_DISABLED="1"),
    )


def child_diag(result):
    """KI-04: a failing child keeps its rc and captured streams in the assert."""
    return f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}"


async def test_cli_prepare_preview_accept_reopen_and_lock(tmp_path):
    path = tmp_path / "private"
    await installation(path)
    assert importlib.util.find_spec("agency.prototype.owner_recovery") is not None, (
        "Offline operation unavailable"
    )
    unprepared = invoke(path, "--preview")
    assert unprepared.returncode != 0, child_diag(unprepared)
    first = invoke(path, "--prepare")
    assert first.returncode == 0, child_diag(first)
    assert json.loads(first.stdout) == {"prepared": True}, child_diag(first)
    again = invoke(path, "--prepare")
    assert json.loads(again.stdout) == {"prepared": True}, child_diag(again)
    preview = invoke(path, "--preview")
    assert preview.returncode == 0, child_diag(preview)
    data = json.loads(preview.stdout)
    assert len(data["snapshot"]["uncertain_turns"]) == 2
    assert len(data["snapshot"]["pending_intents"]) == 1
    assert len(data["snapshot_digest"]) == 64
    original = {}
    for name in ("pilot.db", "quota.db", "audit.db"):
        with closing(sqlite3.connect(path / name)) as db:
            original[name] = tuple(db.iterdump())
    denied = invoke(path, "--accept", data["snapshot_digest"], "--request-id", "receipt")
    assert denied.returncode != 0
    accepted = invoke(
        path,
        "--accept",
        data["snapshot_digest"],
        "--request-id",
        "receipt",
        "--accept-residual-uncertainty",
    )
    assert accepted.returncode == 0, child_diag(accepted)
    receipt = json.loads(accepted.stdout)["receipt"]
    assert receipt["receipt_id"]
    assert "t" * 64 not in accepted.stdout
    repeat = invoke(
        path,
        "--accept",
        data["snapshot_digest"],
        "--request-id",
        "receipt",
        "--accept-residual-uncertainty",
    )
    assert json.loads(repeat.stdout)["receipt"] == receipt, child_diag(repeat)
    for name in ("pilot.db", "quota.db"):
        with closing(sqlite3.connect(path / name)) as db:
            assert tuple(db.iterdump()) == original[name]
    with closing(sqlite3.connect(path / "audit.db")) as db:
        rows = db.execute("SELECT action FROM audit_entries").fetchall()
        assert (
            len(rows) == 2 and sum(row[0] == "owner.acknowledge_uncertainty" for row in rows) == 1
        )
    store = OwnerPilotStore(str(path / "pilot.db"))
    await store.initialize()
    try:
        locked = invoke(path, "--preview")
        assert locked.returncode != 0, child_diag(locked)
        assert locked.stdout == "", child_diag(locked)
    finally:
        await store.close()


def test_cli_unexpected_failure_keeps_root_cause_on_stderr(tmp_path, monkeypatch, capsys):
    """KI-04: a failing recovery child retains its root cause on stderr.

    The fixed public code stays on the machine-readable channel, but the
    unexpected-exception path used to DISCARD the traceback, so a failed
    child left no evidence beyond ``recovery_unavailable``.
    """
    path = tmp_path / "private"
    asyncio.run(installation(path))
    from agency.prototype import owner_recovery as cli

    async def synthetic_failure(settings, args):
        raise RuntimeError("synthetic unexpected failure")

    monkeypatch.setattr(cli, "operate", synthetic_failure)
    rc = cli.main(["--config", str(path / "owner.json"), "--data-dir", str(path), "--prepare"])
    assert rc == 1
    err = capsys.readouterr().err
    lines = err.strip().splitlines()
    assert lines, err
    assert json.loads(lines[-1]) == {"error": "recovery_unavailable"}, err
    assert "RuntimeError: synthetic unexpected failure" in err, err
