"""Supported isolated single-owner pilot launcher (no legacy lifecycle)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
import subprocess
import sys

# Must precede EVERY transitive Agency import, including the LLM adapter.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

import re
import secrets
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, SecretStr

from agency.admission.models import OwnerPrincipal
from agency.llm.adapter import LLMDispatchSnapshot
from agency.llm.config import LLMConfig, ProviderKind


class LauncherError(Exception):
    """Fixed public failure code; never carries configuration/exception text."""


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    owner_id: str
    subject_id: str
    provider: Literal["openai"]
    model: str
    base_url: str
    api_key: SecretStr | None = None
    max_tokens: int = 1024
    temperature: float = 0.2
    timeout: float = 45.0


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    config_path: Path
    config: _Config
    provider_config: LLMConfig
    port: int


_RUNTIME_NAMES = frozenset({"owner.token", "pilot.db.recover.lock"}) | frozenset(
    name + suffix
    for name in ("pilot.db", "quota.db", "audit.db")
    for suffix in ("", "-wal", "-shm", "-journal")
)
_ACL_READ = r"""
$ErrorActionPreference = 'Stop'
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$records = @(foreach ($p in (ConvertFrom-Json $env:AGENCY_PILOT_ACL_PATHS)) {
  $a = Get-Acl -LiteralPath $p
  $rules = @($a.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]) | ForEach-Object {
    @{sid=$_.IdentityReference.Value; allow=($_.AccessControlType -eq 'Allow'); rights=[int]$_.FileSystemRights; inheritance=[int]$_.InheritanceFlags; propagation=[int]$_.PropagationFlags}
  })
  @{owner=$a.GetOwner([System.Security.Principal.SecurityIdentifier]).Value; protected=$a.AreAccessRulesProtected; rules=$rules}
})
@{user=$sid; records=$records} | ConvertTo-Json -Depth 6 -Compress
"""


class _Rule(BaseModel):
    sid: str
    allow: bool
    rights: int
    inheritance: int
    propagation: int


class _Acl(BaseModel):
    owner: str
    protected: bool
    rules: list[_Rule]


class _AclReadback(BaseModel):
    user: str
    records: list[_Acl]


def _private(paths: list[Path]) -> None:
    """Read actual access controls. Never repair an existing unsafe object."""
    if sys.platform == "win32":
        try:
            executable = (
                Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            )
            result = subprocess.run(
                [str(executable), "-NoProfile", "-NonInteractive", "-Command", _ACL_READ],
                env=dict(os.environ, AGENCY_PILOT_ACL_PATHS=json.dumps([str(p) for p in paths])),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode != 0:
                raise LauncherError("private_acl_unverifiable")
            readback = _AclReadback.model_validate_json(result.stdout)
            allowed = {readback.user, "S-1-5-18", "S-1-5-32-544"}
            if len(readback.records) != len(paths):
                raise LauncherError("private_acl_unverifiable")
            for path, acl in zip(paths, readback.records, strict=True):
                # OWNER RIGHTS means only the object's verified owner, not Everyone.
                if acl.owner not in allowed or not acl.rules:
                    raise LauncherError("private_acl_required")
                if path.is_dir() and not acl.protected:
                    raise LauncherError("private_acl_required")
                if any(not r.allow or r.sid not in allowed | {"S-1-3-4"} for r in acl.rules):
                    raise LauncherError("private_acl_required")
                if path.is_dir() and not any(
                    (r.sid == readback.user or (r.sid == "S-1-3-4" and acl.owner == readback.user))
                    and r.inheritance == 3
                    and r.propagation == 0
                    and r.rights & 0x1F01FF == 0x1F01FF
                    for r in acl.rules
                ):
                    raise LauncherError("private_inheritance_required")
        except LauncherError:
            raise
        except (OSError, KeyError, ValueError, subprocess.SubprocessError):
            raise LauncherError("private_acl_unverifiable") from None
    else:
        for path in paths:
            info = path.lstat()
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise LauncherError("private_mode_required")


def _plain(path: Path, *, directory: bool = False) -> None:
    for ancestor in (path, *path.parents):
        info = ancestor.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise LauncherError("links_forbidden")
    info = path.lstat()
    if directory:
        valid = stat.S_ISDIR(info.st_mode)
    else:
        valid = stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    if not valid:
        raise LauncherError("unsafe_file_type")


def _paths(config_path: Path, data_dir: Path, *, check: bool) -> None:
    if config_path.parent != data_dir or config_path.name in _RUNTIME_NAMES:
        raise LauncherError("config_must_be_inside_data_dir")
    forbidden = (Path(__file__).resolve().parents[3], Path.home() / "theagency")
    if any(data_dir.is_relative_to(root) for root in forbidden) or data_dir.drive.startswith(
        "\\\\"
    ):
        raise LauncherError("isolated_data_dir_required")
    if any((p / ".git").exists() for p in (data_dir, *data_dir.parents)):
        raise LauncherError("isolated_data_dir_required")
    try:
        _plain(data_dir, directory=True)
        paths = [data_dir]
        for child in data_dir.iterdir():
            if child.name not in _RUNTIME_NAMES | {config_path.name}:
                raise LauncherError("unexpected_data_file")
            _plain(child)
            paths.append(child)
        _plain(config_path)
        _private(paths)
        if check and not (data_dir / "owner.token").exists():
            raise LauncherError("token_missing_start_once_without_check")
    except OSError:
        raise LauncherError("missing_or_unsafe_paths") from None


def _read(path: Path, maximum: int) -> bytes:
    with path.open("rb") as handle:
        data = handle.read(maximum + 1)
    if len(data) > maximum:
        raise LauncherError("file_too_large")
    return data


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    data: dict[str, object] = {}
    for key, value in pairs:
        if key in data:
            raise LauncherError("duplicate_json_field")
        data[key] = value
    return data


def _read_token(path: Path) -> SecretStr:
    """Validate an existing secret without creating or replacing any file."""
    try:
        token = _read(path, 128).decode("ascii")
        if re.fullmatch(r"[A-Za-z0-9_-]{43,128}", token) is None:
            raise ValueError
    except (ValueError, OSError):
        raise LauncherError("invalid_existing_token") from None
    return SecretStr(token)


def validate(
    config_path: Path, data_dir: Path, *, port: int = 8766, check: bool = False
) -> Settings:
    """Validate explicit inputs without provisioning or opening any store."""
    if not config_path.is_absolute() or not data_dir.is_absolute() or not 1 <= port <= 65535:
        raise LauncherError("invalid_paths_or_port")
    _paths(config_path, data_dir, check=check)
    if (data_dir / "owner.token").exists():
        _read_token(data_dir / "owner.token")
    try:
        raw = _read(config_path, 65536)
        json.loads(raw, object_pairs_hook=_unique)
        config = _Config.model_validate_json(raw)
        OwnerPrincipal(config.owner_id, "local", config.subject_id)
        LLMDispatchSnapshot(
            provider=config.provider,
            model=config.model,
            base_url=config.base_url,
            api_key=config.api_key.get_secret_value() if config.api_key else "",
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            timeout=config.timeout,
        )
        provider = LLMConfig(
            provider=ProviderKind.OPENAI,
            model=config.model,
            base_url=config.base_url,
            api_key=config.api_key,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            timeout=config.timeout,
        )
    except (ValueError, TypeError, OSError, LauncherError):
        raise LauncherError("invalid_configuration") from None
    return Settings(data_dir, config_path, config, provider, port)


def _token(path: Path) -> SecretStr:
    if not path.exists():
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass  # A concurrent launcher won. Read, validate, never replace.
        else:
            with os.fdopen(descriptor, "wb") as handle:
                _plain(path)
                _private([path.parent, path])
                handle.write(secrets.token_urlsafe(48).encode("ascii"))
                handle.flush()
                os.fsync(handle.fileno())
    _plain(path)
    _private([path.parent, path])
    return _read_token(path)


async def _cleanup(stack: AsyncExitStack) -> None:
    """Drain actual cleanup even when a caller repeatedly cancels its waiter."""
    task = asyncio.create_task(stack.aclose())
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:  # noqa: BLE001 -- drain arbitrary owned cleanup failures
            break
    task.result()
    if cancelled:
        raise asyncio.CancelledError


async def serve(settings: Settings) -> None:
    """Own fresh concrete resources and the Uvicorn app in this loop/thread."""
    import uvicorn

    from agency.admission.auth import OwnerAuthenticator
    from agency.admission.budget import OwnerBudgetAdapter
    from agency.admission.service import OwnerPilotService
    from agency.admission.store import OwnerPilotStore
    from agency.butler.config import ButlerConfig
    from agency.butler.service import ButlerService
    from agency.butler.threads import ThreadStore
    from agency.kernel.audit import BetaAuditLog
    from agency.llm.adapter import LLMAdapter
    from agency.orchestrator import AgencyOrchestrator
    from agency.prototype.app import create_app
    from agency.telegram.budget_store import BetaBudgetStore

    # Recheck disk prerequisites rather than trusting an earlier --check or Settings.
    current = validate(settings.config_path, settings.data_dir, port=settings.port)
    if current != settings:
        raise LauncherError("configuration_changed")
    stack = AsyncExitStack()
    old_umask = os.umask(0o077)
    try:
        store = OwnerPilotStore(str(settings.data_dir / "pilot.db"))
        stack.push_async_callback(store.close)
        await store.initialize()  # Concrete OS owner lock BEFORE caller ThreadStore writes.
        token = _token(settings.data_dir / "owner.token")
        threads = ThreadStore.__new__(ThreadStore)
        try:
            ThreadStore.__init__(threads, str(settings.data_dir / "pilot.db"))
        except BaseException:
            if getattr(threads, "_conn", None) is not None:
                threads.close()
            raise
        stack.callback(threads.close)
        backend = BetaBudgetStore(str(settings.data_dir / "quota.db"))
        stack.push_async_callback(backend.close)
        budget = OwnerBudgetAdapter(backend, store)
        stack.push_async_callback(budget.close)
        audit = BetaAuditLog(settings.data_dir / "audit.db")
        stack.push_async_callback(audit.close)
        inert = LLMAdapter(
            config=LLMConfig(
                provider=ProviderKind.ECHO,
                model="echo",
                api_key=None,
                base_url=None,
                max_tokens=1024,
                temperature=0.2,
                timeout=45.0,
            )
        )
        orchestrator = AgencyOrchestrator(llm=inert, memory_db_path=":memory:")
        # None were initialized; register explicit close without legacy stop/start.
        stack.push_async_callback(orchestrator._memory_store.close)
        stack.push_async_callback(orchestrator._audit_log.close)
        stack.push_async_callback(orchestrator._evidence_store.close)
        butler = ButlerService(
            config=ButlerConfig(
                _env_file=None,
                host="127.0.0.1",
                port=settings.port,
                llm_provider="none",
                llm_model="",
                api_key_env_var="",
                max_message_length=8000,
                timeout=45.0,
            ),
            orchestrator=orchestrator,
            thread_store=threads,
        )
        stack.push_async_callback(butler._audit.close)
        pilot = OwnerPilotService(
            butler=butler,
            store=store,
            budget=budget,
            audit=audit,
            provider_config=settings.provider_config,
            owner_id=settings.config.owner_id,
        )
        stack.push_async_callback(pilot.stop)
        authenticator = OwnerAuthenticator(
            owner_id=settings.config.owner_id,
            subject_id=settings.config.subject_id,
            bearer_secret=token,
        )
        app = create_app(butler=butler, pilot=pilot, authenticator=authenticator)
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=settings.port,
                workers=1,
                reload=False,
                proxy_headers=False,
                forwarded_allow_ips="",
                access_log=False,
                log_level="warning",
            )
        )
        print(f"Owner token file: {settings.data_dir / 'owner.token'}")
        print(f"Owner pilot: http://127.0.0.1:{settings.port}/pilot")
        await server.serve()
    except Exception:  # noqa: BLE001 -- sanitize all startup/provider/resource errors
        raise LauncherError("startup_or_shutdown_failed") from None
    finally:
        try:
            await _cleanup(stack)
        except Exception:  # noqa: BLE001 -- never expose private cleanup exceptions
            raise LauncherError("cleanup_failed") from None
        finally:
            os.umask(old_umask)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise LauncherError("invalid_cli")


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--check", action="store_true")
    try:
        args = parser.parse_args(argv)
        settings = validate(args.config, args.data_dir, port=args.port, check=args.check)
        if args.check:
            print("configuration_valid (read-only; live provider not verified)")
            return 0
        asyncio.run(serve(settings))
        return 0
    except KeyboardInterrupt:
        return 130
    except LauncherError as exc:
        print(f"owner_pilot: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
