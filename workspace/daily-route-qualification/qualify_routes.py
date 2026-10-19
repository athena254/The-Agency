"""Offline, cold-app ASGI surface qualification. Never starts application lifespans."""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import importlib.metadata
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

SCRATCH = Path(
    "C:/Users/alphi/AppData/Local/hermes/cache/scratch/agency-build-20261019/daily-routes"
)
EVIDENCE = Path("C:/Users/alphi/agency-evidence/limited-autonomy-20261019/daily-routes")
FACTORIES = {
    "agency": ("api/server.py", "34a929ea9fb5f600638bb469d139964ad9b8dd69ba65060870bee03e08659921"),
    "butler": (
        "butler/server.py",
        "ea265c969323440adbf86171de62885ee016fda7d4ca55caf81f4e9fda998b8f",
    ),
    "prototype": (
        "prototype/app.py",
        "8db5fc43bccff52856762c18c5cc3ee71132661fe2f5ad1bf4a4be059d098bd1",
    ),
    "telegram": (
        "telegram/server.py",
        "fd95518fe3cbe9478d518b506d7cdefb7b39a30e23edf0765458184d9369151a",
    ),
}
PLANNED_API = [
    ("GET", "/api/pilot/capabilities"),
    ("GET", "/api/pilot/threads"),
    ("POST", "/api/pilot/threads"),
    ("GET", "/api/pilot/threads/{thread_id}/messages"),
    ("POST", "/api/pilot/tasks"),
    ("GET", "/api/pilot/tasks"),
    ("GET", "/api/pilot/tasks/{task_id}"),
    ("POST", "/api/pilot/tasks/{task_id}/cancel"),
]
HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def surface_id(app: str, method: str, path: str) -> str:
    return f"{app} {method} {path}"


def inventory_app(name, app):
    """Union effective FastAPI contexts + generated schema + static file expansion."""
    from fastapi.routing import iter_route_contexts
    from starlette.routing import Mount
    from starlette.staticfiles import StaticFiles

    schema = app.openapi()
    schema_keys = {
        (m.upper(), p)
        for p, item in schema.get("paths", {}).items()
        for m in item
        if m in HTTP_METHODS
    }
    entries = {}
    route_keys = set()
    schema_expected = set()
    mounts = []
    unsupported = []

    def add(method, path, kind, **extra):
        key = (method, path)
        row = entries.setdefault(
            key,
            {
                "id": surface_id(name, method, path),
                "app": name,
                "method": method,
                "path": path,
                "kinds": [],
            },
        )
        if kind not in row["kinds"]:
            row["kinds"].append(kind)
        row.update(extra)
        return row

    for context in iter_route_contexts(app.routes):
        original = context.original_route
        path = context.path
        if path is None:
            unsupported.append({"type": type(original).__name__, "reason": "no HTTP path"})
            continue
        if isinstance(original, Mount):
            mount = add(
                "*",
                path + "/{path:path}",
                "mount",
                name=context.name,
                mounted_type=type(original.app).__name__,
            )
            mounts.append(mount["id"])
            if isinstance(original.app, StaticFiles):
                mount["handler_methods"] = ["GET", "HEAD"]
                mount["other_method_status"] = 405
                mount["html"] = original.app.html
                mount["directories"] = [
                    str(Path(d).resolve()) for d in original.app.all_directories
                ]
                for directory in original.app.all_directories:
                    root = Path(directory).resolve()
                    for file in sorted(root.rglob("*")):
                        if file.is_symlink():
                            raise RuntimeError(
                                "Static symlinks require separate containment review"
                            )
                        if file.is_file():
                            relative = file.relative_to(root).as_posix()
                            for method in ("GET", "HEAD"):
                                add(
                                    method,
                                    path + "/" + relative,
                                    "static-file",
                                    mount_id=mount["id"],
                                    sha256=digest(file.read_bytes()),
                                    size=file.stat().st_size,
                                )
            else:
                unsupported.append(
                    {
                        "type": type(original.app).__name__,
                        "path": path,
                        "reason": "non-static child mount not expanded",
                    }
                )
            continue
        methods = context.methods
        if not methods:
            unsupported.append(
                {
                    "type": type(original).__name__,
                    "path": path,
                    "reason": "not an HTTP method route",
                }
            )
            continue
        for method in sorted(methods):
            key = (method, path)
            route_keys.add(key)
            in_schema = getattr(context, "include_in_schema", False)
            if in_schema:
                schema_expected.add(key)
            kind = "schema-route" if key in schema_keys else "hidden-or-generated"
            add(
                method,
                path,
                kind,
                name=context.name,
                route_type=type(original).__name__,
                include_in_schema=bool(in_schema),
                in_openapi=key in schema_keys,
            )
    for method, path in sorted(schema_keys):
        add(method, path, "openapi", in_openapi=True)
    return {
        "app": name,
        "surfaces": sorted(entries.values(), key=lambda r: (r["path"], r["method"])),
        "generated_openapi": schema,
        "openapi_only": [surface_id(name, *k) for k in sorted(schema_keys - route_keys)],
        "route_only_schema_expected": [
            surface_id(name, *k) for k in sorted(schema_expected - schema_keys)
        ],
        "unsupported": unsupported,
        "mounts": mounts,
        "raw_route_types": dict(Counter(type(r).__name__ for r in app.routes)),
        "effective_context_count": sum(1 for _ in iter_route_contexts(app.routes)),
    }


def source_snapshot(root):
    files = {}
    for path in sorted((root / "src").rglob("*")):
        if path.is_symlink():
            raise RuntimeError("Source symlinks require separate containment review")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            files[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    for relative in ("pyproject.toml", "uv.lock", "docs/SPEC_OWNER_PILOT_M2_M3_20261019.md"):
        path = root / relative
        if path.is_file():
            files[relative] = digest(path.read_bytes())
    return {"sha256": digest(json.dumps(files, sort_keys=True).encode()), "files": files}


def git_metadata(root):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

    return {
        "root": str(root),
        "head": git("rev-parse", "HEAD"),
        "branch": git("branch", "--show-current"),
        "status_porcelain": git("status", "--porcelain=v1"),
    }


def configure_environment(run):
    # Do not inherit arbitrary provider settings, credentials, database paths or proxies.
    keep = {
        k: v
        for k, v in os.environ.items()
        if k.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "SYSTEMDRIVE"}
    }
    os.environ.clear()
    os.environ.update(keep)
    data = run / "data"
    temp = run / "temp"
    data.mkdir(parents=True)
    temp.mkdir()
    os.environ.update(
        {
            "HOME": str(run),
            "USERPROFILE": str(run),
            "APPDATA": str(run),
            "LOCALAPPDATA": str(run),
            "TEMP": str(temp),
            "TMP": str(temp),
            "TMPDIR": str(temp),
            "PYTHON_DOTENV_DISABLED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "AGENCY_LLM_PROVIDER": "echo",
            "AGENCY_LLM_MODEL": "echo",
            "AGENCY_BETA_MODE": "false",
            "BUTLER_LLM_PROVIDER": "disabled",
            "BUTLER_LLM_MODEL": "echo",
            "LATTICE_BACKEND": "inmemory",
            "LATTICE_SQLITE_PATH": str(data / "lattice.db"),
            "AGENCY_EVIDENCE_DB": str(data / "evidence.db"),
            "AGENCY_MEMORY_DB": str(data / "memory.db"),
            "REMEX_PROFILE_DB_PATH": str(data / "profiles.db"),
        }
    )
    tempfile.tempdir = str(temp)
    sys.dont_write_bytecode = True
    os.chdir(run)
    return temp


def install_guard(run, output):
    """Python audit containment, not an OS sandbox; no children are permitted."""
    denied = []
    write_roots = (run.resolve(), output.resolve())

    def inside(path, roots):
        return any(path == root or root in path.parents for root in roots)

    def reject(event, reason):
        denied.append({"event": event, "reason": reason})
        raise PermissionError(f"Qualification guard: {reason}")

    def guard(event, args):
        if event in {
            "socket.connect",
            "socket.bind",
            "socket.getaddrinfo",
            "socket.sendto",
            "subprocess.Popen",
            "os.system",
            "os.exec",
            "os.spawn",
            "os.fork",
        }:
            reject(event, "network/process operation forbidden")
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).resolve()
            if path.name == ".env" or path.name.startswith(".env."):
                reject(event, "dotenv access forbidden")
            if path.suffix.lower() in {
                ".db",
                ".sqlite",
                ".sqlite3",
                ".db-wal",
                ".db-shm",
            } and not inside(path, (run.resolve(),)):
                reject(event, "non-scratch database access forbidden")
            mode, flags = args[1], args[2]
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int)
                and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            )
            if writing and not inside(path, write_roots):
                reject(event, "write outside run/evidence forbidden")
        if (
            event == "sqlite3.connect"
            and args[0] != ":memory:"
            and not inside(Path(args[0]).resolve(), (run.resolve(),))
        ):
            reject(event, "non-scratch sqlite connection forbidden")
        if event in {"os.mkdir", "os.remove", "os.rmdir", "os.rename"}:
            targets = args[:2] if event == "os.rename" else args[:1]
            for value in targets:
                if isinstance(value, (str, bytes, os.PathLike)) and not inside(
                    Path(os.fsdecode(value)).resolve(), write_roots
                ):
                    reject(event, "filesystem mutation outside run/evidence forbidden")

    sys.addaudithook(guard)
    return denied


def reviewed_factory(root, relative, expected):
    tree = ast.parse((root / "src/agency" / relative).read_text(encoding="utf-8"))
    factory = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "create_app"
    )
    actual = digest(ast.dump(factory, include_attributes=False).encode())
    return {
        "expected_ast_sha256": expected,
        "actual_ast_sha256": actual,
        "reviewed_baseline_match": actual == expected,
    }


def planned_coverage(surfaces):
    present = {(r["method"], r["path"]) for r in surfaces if r["app"] == "prototype"}
    return [
        {
            "method": method,
            "path": path,
            "status": "present-unqualified" if (method, path) in present else "MISSING",
            "expected_status": "implemented route required; absence is never a passing 404",
        }
        for method, path in [*PLANNED_API, ("GET", "/pilot")]
    ]


def coverage_report(surfaces, cases):
    ids = {r["id"] for r in surfaces}
    executed = {
        sid for c in cases if c.get("actual_status") is not None for sid in c["surface_ids"]
    }
    passed = {sid for c in cases if c["passed"] for sid in c["surface_ids"]}
    direct = {
        sid
        for c in cases
        if c["passed"] and c["scope"] == "safe-handler"
        for sid in c["surface_ids"]
    }
    return {
        "inventory_count": len(ids),
        "executed_surface_count": len(ids & executed),
        "passed_case_surface_count": len(ids & passed),
        "safe_handler_surface_count": len(ids & direct),
        "unexercised": sorted(ids - executed),
        "not_directly_handler_qualified": sorted(ids - direct),
        "unknown_case_surface_ids": sorted(executed - ids),
        "case_count": len(cases),
        "passed_cases": sum(c["passed"] for c in cases),
        "failed_cases": sum(not c["passed"] for c in cases),
    }


def peer_denial_matches(response):
    # HTTP HEAD deliberately has no response body, including on middleware denials.
    if response.request.method == "HEAD":
        return len(response.content) == 0
    return response.json() == {"detail": "Peer authorization is unavailable."}


async def qualify(root, output, run, metadata, before):
    # The loop is already constructed: on Windows its self-pipe must predate the socket guard.
    denied = install_guard(run, output)
    sys.path.insert(0, str(root / "src"))
    from contextlib import AsyncExitStack

    import fastapi
    import httpx
    from fastapi.routing import iter_route_contexts

    import agency
    from agency.api.server import create_app as agency_app
    from agency.butler.config import ButlerConfig
    from agency.butler.server import create_app as butler_app
    from agency.prototype.app import create_app as prototype_app
    from agency.telegram.config import TelegramConfig
    from agency.telegram.handler import TelegramHandler
    from agency.telegram.profile_store import ProfileStore
    from agency.telegram.server import create_app as telegram_app

    imported_source = Path(agency.__file__).resolve()
    if not imported_source.is_relative_to(root / "src"):
        raise RuntimeError("Imported Agency from the wrong checkout")
    reviews = {name: reviewed_factory(root, *values) for name, values in FACTORIES.items()}
    cases = []
    all_inventory = {}
    skipped = []
    started = metadata["invoked_at"]
    # Only real transport/profile resources; no fake backend service and no product handler patches.
    async with AsyncExitStack() as stack:
        config = TelegramConfig(
            bot_token="synthetic-not-live", webhook_secret="synthetic-not-live", beta_mode=False
        )
        profiles = ProfileStore(":memory:")
        try:
            handler = TelegramHandler(config, profile_store=profiles)
        except BaseException:
            profiles.close()
            raise
        stack.push_async_callback(handler.close)
        apps = {
            "agency": agency_app(),
            "butler": butler_app(
                ButlerConfig(_env_file=None, llm_provider="disabled", llm_model="echo")
            ),
            "prototype": prototype_app(),
            "telegram": telegram_app(config, handler),
        }
        for name, app in apps.items():
            all_inventory[name] = inventory_app(name, app)
        surfaces = [row for inv in all_inventory.values() for row in inv["surfaces"]]
        by_id = {r["id"]: r for r in surfaces}
        assert len(by_id) == len(surfaces), "inventory deduplication failed"
        clients = {
            name: await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(
                        app=app, client=("127.0.0.1", 32100), raise_app_exceptions=False
                    ),
                    base_url="http://127.0.0.1",
                    follow_redirects=False,
                    trust_env=False,
                )
            )
            for name, app in apps.items()
        }

        async def case(
            name,
            method,
            path,
            expected,
            *,
            label,
            scope="boundary-only",
            ids=None,
            check=None,
            **kwargs,
        ):
            row = {
                "case_id": f"{name}:{label}",
                "app": name,
                "method": method,
                "path": path,
                "expected_status": expected,
                "scope": scope,
                "surface_ids": ids if ids is not None else [surface_id(name, method, path)],
                "actual_status": None,
                "passed": False,
            }
            if any(sid not in by_id for sid in row["surface_ids"]):
                raise RuntimeError(
                    "Case refers to an absent surface; do not qualify missing routes"
                )
            response = None
            try:
                response = await clients[name].request(method, path, **kwargs)
                row.update(
                    actual_status=response.status_code,
                    body_sha256=digest(response.content),
                    body_bytes=len(response.content),
                    content_type=response.headers.get("content-type"),
                    location=response.headers.get("location"),
                    allow=response.headers.get("allow"),
                )
                content_ok = True if check is None else bool(check(response))
                row.update(
                    content_check=content_ok, passed=response.status_code == expected and content_ok
                )
            except Exception as exc:  # noqa: BLE001 - record failures without hiding the remaining cases
                row["exception_type"] = type(exc).__name__
            cases.append(row)
            return response

        # Review pins ensure later trees cannot accidentally turn closed probes into privileged operations.
        for name in ("agency", "butler"):
            if not reviews[name]["reviewed_baseline_match"]:
                skipped.append(
                    {
                        "app": name,
                        "reason": "factory changed: re-review before closed-route requests",
                    }
                )
                continue
            for row in all_inventory[name]["surfaces"]:
                method, path = row["method"], row["path"]
                concrete = re.sub(r"\{[^}]+\}", "synthetic-unknown", path)
                is_health = method == "GET" and path == "/v1/health"
                expected = 200 if is_health else 503
                check = (
                    (lambda r: r.json()["status"] in {"degraded", "starting"})
                    if is_health
                    else peer_denial_matches
                )
                await case(
                    name,
                    method,
                    concrete,
                    expected,
                    label=f"declared:{method}:{path}",
                    ids=[row["id"]],
                    scope="safe-handler" if is_health else "middleware-denial",
                    check=check,
                    **(
                        {"content": b"{broken", "headers": {"Content-Type": "application/json"}}
                        if method not in {"GET", "HEAD"}
                        else {}
                    ),
                )
            health_id = surface_id(name, "GET", "/v1/health")
            await case(
                name,
                "POST",
                "/v1/health",
                503,
                label="invalid-method-health",
                ids=[health_id],
                scope="middleware-denial",
            )
            await case(
                name,
                "GET",
                "/v1/health/",
                503,
                label="trailing-slash-no-bypass",
                ids=[health_id],
                scope="middleware-denial",
            )

        if reviews["prototype"]["reviewed_baseline_match"]:
            name = "prototype"
            for path in ("/api/health", "/api/agents", "/api/session", "/"):
                checks = {
                    "/api/health": lambda r: (
                        r.json() == {"status": "starting", "butler": "starting", "agents": 0}
                    ),
                    "/api/agents": lambda r: r.json() == {"agents": []},
                    "/api/session": lambda r: bool(r.json().get("csrf_token")),
                    "/": lambda r: "text/html" in r.headers.get("content-type", ""),
                }
                await case(
                    name,
                    "GET",
                    path,
                    200,
                    label=f"safe-read:{path}",
                    scope="safe-handler",
                    check=checks[path],
                )
            chat_id = surface_id(name, "POST", "/api/chat")
            csrf = apps[name].state.csrf_token
            headers = {"X-Prototype-CSRF": csrf}
            await case(
                name, "POST", "/api/chat", 403, label="missing-csrf", json={"message": "synthetic"}
            )
            await case(
                name,
                "POST",
                "/api/chat",
                403,
                label="foreign-origin",
                json={"message": "synthetic"},
                headers={**headers, "Origin": "https://attacker.invalid"},
            )
            await case(
                name,
                "POST",
                "/api/chat",
                422,
                label="forged-sender",
                json={"message": "synthetic", "sender": "forged-owner"},
                headers=headers,
            )
            await case(
                name,
                "POST",
                "/api/chat",
                422,
                label="empty-message",
                json={"message": ""},
                headers=headers,
            )
            await case(
                name,
                "POST",
                "/api/chat",
                415,
                label="wrong-content-type",
                content="synthetic",
                headers={**headers, "Content-Type": "text/plain"},
            )
            await case(
                name,
                "POST",
                "/api/chat",
                503,
                label="no-running-butler",
                json={"message": "synthetic-not-dispatched"},
                headers=headers,
                check=lambda r: r.json() == {"error": "Butler service is unavailable."},
            )
            for path in ("/api/health", "/"):
                await case(
                    name,
                    "GET",
                    path,
                    403,
                    label=f"foreign-host:{path}",
                    headers={"Host": "attacker.invalid"},
                )
            await case(
                name,
                "GET",
                "/api/chat",
                405,
                label="invalid-method-chat",
                ids=[chat_id],
                check=lambda r: "POST" in r.headers.get("allow", ""),
            )
            health_id = surface_id(name, "GET", "/api/health")
            await case(
                name,
                "GET",
                "/api/health/",
                307,
                label="health-slash-redirect",
                ids=[health_id],
                check=lambda r: r.headers.get("location") == "http://127.0.0.1/api/health",
            )
            for row in all_inventory[name]["surfaces"]:
                if "static-file" in row["kinds"]:

                    def static_check(r, row=row):
                        return (
                            digest(r.content) == row["sha256"]
                            if row["method"] == "GET"
                            else len(r.content) == 0
                        )

                    await case(
                        name,
                        row["method"],
                        row["path"],
                        200,
                        label=f"static:{row['method']}:{row['path']}",
                        ids=[row["id"], row["mount_id"]],
                        scope="safe-handler",
                        check=static_check,
                    )
            for mount_id in all_inventory[name]["mounts"]:
                mount = by_id[mount_id]
                prefix = mount["path"].split("/{path:path}")[0]
                await case(
                    name,
                    "GET",
                    prefix,
                    307,
                    label=f"mount-slash:{prefix}",
                    ids=[mount_id],
                    check=lambda r, prefix=prefix: (
                        r.headers.get("location") == "http://127.0.0.1" + prefix + "/"
                    ),
                )
                await case(
                    name,
                    "GET",
                    prefix + "/synthetic-absent.txt",
                    404,
                    label=f"static-missing:{prefix}",
                    ids=[mount_id],
                )
                await case(
                    name,
                    "POST",
                    prefix + "/app.js",
                    405,
                    label=f"static-invalid-method:{prefix}",
                    ids=[mount_id],
                )
            # Absent docs are configuration probes, not successes for planned routes.
            for path in ("/docs", "/redoc", "/openapi.json"):
                await case(name, "GET", path, 404, label=f"docs-disabled:{path}", ids=[])
        else:
            skipped.append(
                {
                    "app": "prototype",
                    "reason": "factory changed: prototype/API guards need re-review; no dispatch attempted",
                }
            )

        if reviews["telegram"]["reviewed_baseline_match"]:
            name = "telegram"
            for row in all_inventory[name]["surfaces"]:
                if row["method"] not in {"GET", "HEAD"}:
                    continue
                path = row["path"]
                if path == "/openapi.json" and row["method"] == "GET":
                    check = lambda r: r.json() == apps[name].openapi()
                elif path in {"/docs", "/redoc", "/docs/oauth2-redirect"}:
                    check = lambda r: "text/html" in r.headers.get("content-type", "")
                else:
                    check = lambda r: r.json() == {"status": "ok", "service": "telegram-webhook"}
                if row["method"] == "HEAD":
                    check = lambda r: len(r.content) == 0
                await case(
                    name,
                    row["method"],
                    path,
                    200,
                    label=f"safe-read:{row['method']}:{path}",
                    scope="safe-handler",
                    check=check,
                )
            webhook_id = surface_id(name, "POST", "/telegram/webhook")
            await case(
                name,
                "POST",
                "/telegram/webhook",
                403,
                label="auth-before-malformed-json",
                content=b"{broken",
            )
            await case(
                name,
                "POST",
                "/telegram/webhook",
                400,
                label="authenticated-malformed-json",
                content=b"{broken",
                headers={"X-Telegram-Bot-Api-Secret-Token": config.webhook_secret},
            )
            await case(
                name,
                "POST",
                "/telegram/webhook",
                400,
                label="authenticated-nonobject-json",
                json=[],
                headers={"X-Telegram-Bot-Api-Secret-Token": config.webhook_secret},
            )
            config.webhook_secret = None
            await case(
                name,
                "POST",
                "/telegram/webhook",
                503,
                label="disabled-secret-before-parse",
                content=b"{broken",
            )
            await case(
                name,
                "GET",
                "/telegram/webhook",
                405,
                label="invalid-method-webhook",
                ids=[webhook_id],
            )
        else:
            skipped.append(
                {"app": "telegram", "reason": "factory changed: webhook guards need re-review"}
            )

    import sqlite3

    profile_closed = False
    try:
        profiles._conn.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        profile_closed = True
    cleanup_observed = {
        "all_asgi_clients_closed": all(client.is_closed for client in clients.values()),
        "synthetic_profile_closed": profile_closed,
        "telegram_transport_not_open": handler._adapter._client is None,
    }
    after = source_snapshot(root)
    inventory = {
        "source": metadata,
        "source_fingerprint": before,
        "apps": all_inventory,
        "surfaces": surfaces,
        "planned_routes": planned_coverage(surfaces),
        "counts_by_app": dict(Counter(r["app"] for r in surfaces)),
        "factory_safety_review": reviews,
        "router_iteration_implementation": inspect.getsource(iter_route_contexts),
        "fastapi_version": fastapi.__version__,
    }
    coverage = coverage_report(surfaces, cases)
    inconsistent = any(
        i["openapi_only"] or i["route_only_schema_expected"] or i["unsupported"]
        for i in all_inventory.values()
    )
    missing = [p for p in inventory["planned_routes"] if p["status"] == "MISSING"]
    result = {
        "started_at": started,
        "completed_at": datetime.now(UTC).isoformat(),
        "source": metadata,
        "source_sha256_before": before["sha256"],
        "source_sha256_after": after["sha256"],
        "source_unchanged": before == after,
        "harness_sha256": digest(Path(__file__).read_bytes()),
        "interpreter": sys.executable,
        "versions": {
            p: importlib.metadata.version(p) for p in ("fastapi", "starlette", "httpx", "pydantic")
        },
        "execution": {
            "transport": "httpx.ASGITransport; actual ASGI app, synthetic loopback peer",
            "application_lifespans": False,
            "runtime_ready": False,
            "cold_app_fixture": True,
            "synthetic_profile_store": ":memory:",
            "model": "explicit echo configured, no model dispatch intended",
            "cwd": str(run),
            "temp": tempfile.gettempdir(),
            "containment": "Python audit hook; blocks all network/process calls, dotenv/database access and writes outside scratch/evidence; not an OS sandbox",
            "cleanup": "AsyncExitStack closed all HTTP clients and real Telegram handler/profile; no app lifespans started",
        },
        "cleanup_observed": cleanup_observed,
        "guard_denials": denied,
        "cases": cases,
        "coverage": coverage,
        "skipped": skipped,
        "planned_missing_count": len(missing),
        "planned_missing": missing,
        "inventory_inconsistent": inconsistent,
        "bounded_cases_passed": coverage["failed_cases"] == 0
        and not denied
        and before == after
        and not inconsistent
        and all(cleanup_observed.values()),
        "daily_driver_qualified": False,
        "gaps": [
            "No pilot service/API qualification: planned missing/present-unqualified routes are not passing cases.",
            "No real provider, live HTTP server, application lifespan/startup or ready runtime exercised.",
            "Middleware denials exercise the boundary, not underlying business handlers.",
            "No arbitrary resource IDs, authenticated valid webhook updates, owner tasks or model requests executed.",
        ],
    }
    (output / "inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    (output / "receipts.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "evidence": str(output),
                "source_head": metadata["head"],
                "source_sha256": before["sha256"],
                "counts_by_app": inventory["counts_by_app"],
                "coverage": coverage,
                "planned_missing_count": len(missing),
                "bounded_cases_passed": result["bounded_cases_passed"],
                "daily_driver_qualified": False,
            },
            indent=2,
        )
    )
    if not result["bounded_cases_passed"]:
        return 1
    return 2 if missing or coverage["unexercised"] or skipped else 0


def main():
    invoked_at = datetime.now(UTC).isoformat()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--label", default="qualification", help="Non-sensitive evidence prefix")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", args.label):
        parser.error("label must be a short non-sensitive alphanumeric identifier")
    root = args.source_root.resolve(strict=True)
    if not (root / "src/agency").is_dir():
        parser.error("source root must contain src/agency")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run = SCRATCH / stamp
    output = EVIDENCE / (args.label + "-" + stamp)
    run.mkdir(parents=True)
    output.mkdir(parents=True)
    original_cwd = Path.cwd()
    # Read-only git subprocesses finish before the audit guard prohibits child processes.
    metadata = git_metadata(root)
    metadata["invoked_at"] = invoked_at
    before = source_snapshot(root)
    configure_environment(run)
    try:
        return asyncio.run(qualify(root, output, run, metadata, before))
    except Exception as exc:
        (output / "fatal.json").write_text(
            json.dumps(
                {
                    "exception_type": type(exc).__name__,
                    "source": metadata,
                    "source_sha256": before["sha256"],
                    "completed": False,
                }
            ),
            encoding="utf-8",
        )
        raise
    finally:
        os.chdir(original_cwd)


if __name__ == "__main__":
    raise SystemExit(main())
