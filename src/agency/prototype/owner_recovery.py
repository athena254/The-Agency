"""Offline operator-only uncertainty acknowledgement. No server or model dispatch."""

from __future__ import annotations

# Set policy before transitive Agency imports, just like the supported launcher.
import os

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

import argparse
import asyncio
import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import aiosqlite

from agency.admission.budget import OwnerBudgetAdapter
from agency.admission.models import OwnerPrincipal, PilotError
from agency.admission.owner_recovery import OwnerRecovery
from agency.admission.store import OwnerPilotStore
from agency.kernel.audit import BetaAuditLog
from agency.prototype.pilot_launcher import LauncherError, Settings, _read_token, validate
from agency.telegram.budget_store import BetaBudgetStore


async def operate(settings: Settings, args: argparse.Namespace) -> dict[str, Any]:
    """Open only existing files: never schema-provision, recover, or admit a task."""
    if settings.config.spending is not None:
        raise LauncherError("recovery_spending_unsupported")
    for name in ("pilot.db", "quota.db", "audit.db"):
        if not (settings.data_dir / name).is_file():
            raise LauncherError("existing_stores_required")
    async with AsyncExitStack() as stack:
        store = OwnerPilotStore(str(settings.data_dir / "pilot.db"))
        stack.push_async_callback(store.close)
        if not store._lock.acquire():
            raise LauncherError("server_must_be_stopped")
        # The operator intentionally skips initialize(): that would provision schema.
        # This ready flag means only that the lifetime OS lock is held. Coordinator
        # validates existing schema/mapping/identity under BEGIN IMMEDIATE.
        store._ready = True
        backend = BetaBudgetStore(str(settings.data_dir / "quota.db"))
        budget = OwnerBudgetAdapter(backend, store)
        stack.push_async_callback(budget.close)
        audit = BetaAuditLog(settings.data_dir / "audit.db")
        stack.push_async_callback(audit.close)
        audit._conn = await aiosqlite.connect(audit._db_path.as_uri() + "?mode=rw", uri=True)
        await audit._conn.execute("PRAGMA synchronous=FULL")
        principal = OwnerPrincipal(settings.config.owner_id, "local", settings.config.subject_id)
        recovery = OwnerRecovery(
            store=store,
            budget=budget,
            audit=audit,
            principal=principal,
            key=_read_token(settings.data_dir / "owner.token"),
            operator=True,
        )
        if args.prepare:
            return await recovery.prepare()
        if args.preview:
            snapshot = await recovery.preview()
            return {"snapshot_digest": snapshot.pop("digest"), "snapshot": snapshot}
        receipt = await recovery.accept(
            args.accept,
            args.request_id,
            accept_residual_uncertainty=args.accept_residual_uncertainty,
        )
        return {"receipt": receipt}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline local-owner residual uncertainty acknowledgement; stop the server first."
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--preview", action="store_true")
    mode.add_argument("--accept", metavar="SNAPSHOT_DIGEST")
    parser.add_argument("--request-id")
    parser.add_argument("--accept-residual-uncertainty", action="store_true")
    args = parser.parse_args(argv)
    if args.accept is not None:
        if not args.request_id or not args.accept_residual_uncertainty:
            parser.error("--accept requires --request-id and --accept-residual-uncertainty")
    elif args.request_id is not None or args.accept_residual_uncertainty:
        parser.error("acceptance flags require --accept")
    try:
        settings = validate(args.config, args.data_dir, check=True)
        result = asyncio.run(operate(settings, args))
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except (LauncherError, PilotError) as exc:
        code = exc.code if isinstance(exc, PilotError) else str(exc)
        print(json.dumps({"error": code}), file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 -- fail closed with a fixed public code
        # No paths, raw storage errors, config, bearer or provider credentials.
        print(json.dumps({"error": "recovery_unavailable"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
