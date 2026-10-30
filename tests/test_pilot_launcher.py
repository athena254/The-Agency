"""Offline contracts for the supported owner launcher; synthetic secrets only."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ACL_PROVISION = r"""
$ErrorActionPreference = 'Stop'
$p = $env:AGENCY_TEST_PRIVATE_DIR
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
& "$env:SystemRoot\System32\icacls.exe" $p /inheritance:r /grant:r "*$($sid.Value):(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'ACL provisioning failed' }
"""


def protect(path: Path) -> None:
    if sys.platform == "win32":
        env = dict(os.environ, AGENCY_TEST_PRIVATE_DIR=str(path))
        result = subprocess.run(
            [
                str(
                    Path(os.environ["SystemRoot"])
                    / "System32/WindowsPowerShell/v1.0/powershell.exe"
                ),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                _ACL_PROVISION,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, "synthetic private directory provisioning failed"
    else:
        path.chmod(0o700)


@pytest.fixture
def private_dir(tmp_path: Path) -> Path:
    protect(tmp_path)
    return tmp_path


def config_file(directory: Path, **overrides: object) -> Path:
    values = {
        "owner_id": "synthetic-owner",
        "subject_id": "synthetic-browser",
        "provider": "openai",
        "model": "synthetic-model",
        "base_url": "http://127.0.0.1:9999/v1",
        "api_key": "synthetic-key-not-real",
    }
    values.update(overrides)
    path = directory / "owner.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    if sys.platform != "win32":
        path.chmod(0o600)
    return path


def token_file(directory: Path) -> Path:
    path = directory / "owner.token"
    path.write_text("t" * 64, encoding="ascii")
    if sys.platform != "win32":
        path.chmod(0o600)
    return path


def snapshot(directory: Path) -> dict[str, tuple[bytes, int]]:
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()}


def test_prototype_import_is_lazy() -> None:
    """The -m package import must not load app/dotenv before launcher policy."""
    code = (
        "import sys; import agency.prototype; "
        "assert 'agency.prototype.app' not in sys.modules; "
        "assert 'agency.llm.adapter' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr


def test_launcher_module_exists() -> None:
    import importlib.util

    assert importlib.util.find_spec("agency.prototype.pilot_launcher") is not None


def test_check_is_read_only(
    private_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sqlite3

    from agency.prototype import pilot_launcher as launcher

    config = config_file(private_dir)
    token_file(private_dir)
    before = snapshot(private_dir)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("--check opened SQLite"))
    assert launcher.main(["--config", str(config), "--data-dir", str(private_dir), "--check"]) == 0
    assert snapshot(private_dir) == before
    output = capsys.readouterr()
    assert "configuration_valid" in output.out
    assert "synthetic-key-not-real" not in output.out + output.err
    assert "t" * 64 not in output.out + output.err


@pytest.mark.parametrize(
    "changes",
    [
        {"provider": "echo"},
        {"provider": "pollinations"},
        {"model": ""},
        {"timeout": 46},
        {"timeout": float("inf")},
        {"temperature": float("nan")},
        {"max_tokens": True},
        {"owner_id": ""},
        {"subject_id": 1},
        {"base_url": "http://public.example/v1"},
        {"base_url": "https://user:secret@example.test/v1"},
        {"api_key": None},
        {"unknown": "synthetic-key-not-real"},
    ],
)
def test_invalid_configuration_never_mutates(
    private_dir: Path, changes: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    from agency.prototype import pilot_launcher as launcher

    config = config_file(private_dir, **changes)
    token_file(private_dir)
    before = snapshot(private_dir)
    assert launcher.main(["--config", str(config), "--data-dir", str(private_dir), "--check"]) == 2
    assert snapshot(private_dir) == before
    output = capsys.readouterr()
    assert "synthetic-key-not-real" not in output.out + output.err


@pytest.mark.parametrize(
    "case",
    [
        "missing_token",
        "missing_dir",
        "outside_config",
        "canonical",
        "hardlink",
        "unsafe_type",
        "duplicate_json",
        "oversize",
    ],
)
def test_unsafe_paths_and_prerequisites_fail_read_only(private_dir: Path, case: str) -> None:
    from agency.prototype import pilot_launcher as launcher

    config = config_file(private_dir)
    token_file(private_dir)
    data = private_dir
    if case == "missing_token":
        (data / "owner.token").unlink()
    elif case == "missing_dir":
        data = data / "missing"
    elif case == "outside_config":
        data = data / "other"
        data.mkdir()
        protect(data)
    elif case == "canonical":
        data = Path.home() / "theagency" / "data"
    elif case == "hardlink":
        os.link(config, private_dir / "alias")
    elif case == "unsafe_type":
        (data / "pilot.db").mkdir()
    elif case == "duplicate_json":
        config.write_text(
            config.read_text().replace(
                '"provider": "openai"', '"provider": "echo", "provider": "openai"'
            )
        )
    elif case == "oversize":
        config.write_text(config.read_text() + " " * 65536)
    with pytest.raises(launcher.LauncherError):
        launcher.validate(config, data, check=True)
    if case == "missing_dir":
        assert not data.exists()
    assert not (private_dir / "pilot.db.recover.lock").exists()


def test_parent_traversal_of_forbidden_root_is_refused_read_only(
    private_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lexical ``..`` spelling must not bypass the forbidden-home guard."""
    from agency.prototype import pilot_launcher as launcher

    synthetic_home = private_dir / "synthetic-home"
    forbidden = synthetic_home / "theagency" / "private-pilot"
    sibling = synthetic_home / "ordinary-sibling"
    forbidden.mkdir(parents=True)
    sibling.mkdir()
    protect(forbidden)
    config = config_file(forbidden)
    token_file(forbidden)
    before = snapshot(forbidden)
    monkeypatch.setattr(launcher.Path, "home", lambda: synthetic_home)
    traversing_data = sibling / ".." / "theagency" / "private-pilot"
    traversing_config = traversing_data / config.name

    with pytest.raises(launcher.LauncherError, match="isolated_data_dir_required"):
        launcher.validate(traversing_config, traversing_data, check=True)

    assert snapshot(forbidden) == before
    assert not (forbidden / "pilot.db.recover.lock").exists()


def test_public_acl_is_refused_without_repair(private_dir: Path) -> None:
    from agency.prototype import pilot_launcher as launcher

    config = config_file(private_dir)
    token_file(private_dir)
    if sys.platform == "win32":
        command = ["icacls.exe", str(private_dir), "/grant", "*S-1-1-0:(OI)(CI)R"]
        assert subprocess.run(command, capture_output=True, check=False).returncode == 0
    else:
        private_dir.chmod(0o755)
    try:
        with pytest.raises(launcher.LauncherError):
            launcher.validate(config, private_dir, check=True)
    finally:
        if sys.platform == "win32":
            subprocess.run(
                ["icacls.exe", str(private_dir), "/remove:g", "*S-1-1-0"],
                capture_output=True,
                check=True,
            )
        else:
            private_dir.chmod(0o700)


def test_directory_alias_is_refused(private_dir: Path) -> None:
    from agency.prototype import pilot_launcher as launcher

    target = private_dir / "real"
    target.mkdir()
    protect(target)
    config_file(target)
    token_file(target)
    alias = private_dir / "alias"
    if sys.platform == "win32":
        result = subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(alias), str(target)],
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, "synthetic junction creation failed"
    else:
        alias.symlink_to(target, target_is_directory=True)
    try:
        with pytest.raises(launcher.LauncherError):
            launcher.validate(alias / "owner.json", alias, check=True)
    finally:
        if sys.platform == "win32":
            alias.rmdir()
        else:
            alias.unlink()


async def test_real_bound_construction_cleanup_and_fresh_restart(
    private_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3
    import threading

    import uvicorn

    from agency.admission.models import OwnerPrincipal, PilotError
    from agency.admission.store import OwnerPilotStore
    from agency.butler.service import ButlerService
    from agency.orchestrator import AgencyOrchestrator
    from agency.prototype import pilot_launcher as launcher
    from agency.skills.registry import SkillRegistry

    config = config_file(private_dir)
    working_dir = private_dir.parent / "isolated-launcher-cwd"
    working_dir.mkdir()
    monkeypatch.chdir(working_dir)
    settings = launcher.validate(config, private_dir)
    monkeypatch.setenv("BUTLER_HOST", "0.0.0.0")
    monkeypatch.setenv("BUTLER_PORT", "not-a-port")
    monkeypatch.setenv("BUTLER_TIMEOUT", "nan")
    monkeypatch.setenv("AGENCY_LLM_PROVIDER", "invalid-inherited-provider")

    async def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("legacy lifecycle called")

    monkeypatch.setattr(ButlerService, "start", forbidden)
    monkeypatch.setattr(ButlerService, "stop", forbidden)
    monkeypatch.setattr(AgencyOrchestrator, "start", forbidden)
    monkeypatch.setattr(AgencyOrchestrator, "stop", forbidden)
    seen = []
    connections: list[sqlite3.Connection] = []
    skill_registries: list[SkillRegistry] = []
    thread = threading.get_ident()
    loop = asyncio.get_running_loop()

    class Server:
        def __init__(self, config: uvicorn.Config) -> None:
            assert config.host == "127.0.0.1"
            assert config.proxy_headers is False
            assert config.workers == 1 and config.reload is False
            self.app = config.app

        async def serve(self) -> None:
            app = self.app
            pilot = app.state.pilot
            assert threading.get_ident() == thread
            assert asyncio.get_running_loop() is loop
            assert pilot._butler.config.host == "127.0.0.1"
            assert pilot._butler.orchestrator._memory_store.db_path == ":memory:"
            assert pilot._butler.orchestrator._llm_adapter._provider == "echo"
            skill_registry = pilot._butler.orchestrator.skill_registry
            assert skill_registry is not None
            skill_registries.append(skill_registry)
            assert pilot._dispatch.api_key == "synthetic-key-not-real"
            assert pilot._dispatch.model == "synthetic-model"
            async with app.router.lifespan_context(app):
                principal = OwnerPrincipal("synthetic-owner", "local", "synthetic-browser")
                assert (await pilot.capabilities(principal)).ready
                competitor = OwnerPilotStore(str(private_dir / "pilot.db"))
                try:
                    with pytest.raises(PilotError, match="Owner store unavailable"):
                        await competitor.initialize()
                finally:
                    await competitor.close()
                if not seen:
                    await pilot.create_thread(principal, request_id="thread-1", title="Retained")
                else:
                    assert len(await pilot.list_threads(principal)) == 1
                seen.append(pilot)
                connections.append(pilot._butler._threads._conn)

    monkeypatch.setattr(uvicorn, "Server", Server)
    await launcher.serve(settings)
    first_token = (private_dir / "owner.token").read_bytes()
    assert len(first_token) >= 43
    assert launcher.validate(config, private_dir, check=True)
    await launcher.serve(launcher.validate(config, private_dir))

    class StartupFailureServer:
        def __init__(self, config: uvicorn.Config) -> None:
            orchestrator = config.app.state.butler.orchestrator
            skill_registry = orchestrator.skill_registry
            assert skill_registry is not None
            skill_registries.append(skill_registry)
            raise RuntimeError("synthetic server construction failure")

    monkeypatch.setattr(uvicorn, "Server", StartupFailureServer)
    with pytest.raises(launcher.LauncherError, match="startup_or_shutdown_failed"):
        await launcher.serve(launcher.validate(config, private_dir))

    assert len(seen) == 2 and seen[0] is not seen[1]
    assert (private_dir / "owner.token").read_bytes() == first_token
    assert len(skill_registries) == 3
    assert all(registry._db_path == ":memory:" for registry in skill_registries)
    assert all(registry._conn is None for registry in skill_registries)
    assert not (working_dir / "data" / "skills.db").exists()
    for pilot in seen:
        assert pilot._store._closed and pilot._budget._closed
        assert pilot._audit._conn is None
        assert pilot._butler._threads._conn is None
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


async def test_partial_orchestrator_construction_closes_owned_resources(
    private_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sandbox construction failure explicitly drains the initialized registry/stores."""
    from agency.evidence.store.store import EvidenceStore
    from agency.kernel.audit import AuditLog
    from agency.memory.sms.store import MemoryStore
    from agency.prototype import pilot_launcher as launcher
    from agency.security.sandbox.manager import SandboxManager
    from agency.skills.registry import SkillRegistry

    config = config_file(private_dir)
    working_dir = private_dir.parent / "partial-orchestrator-cwd"
    working_dir.mkdir()
    monkeypatch.chdir(working_dir)
    closed: list[str] = []

    original_skill_close = SkillRegistry.close
    original_memory_close = MemoryStore.close
    original_audit_close = AuditLog.close
    original_evidence_close = EvidenceStore.close

    def close_skill(self: SkillRegistry) -> None:
        closed.append("skills")
        original_skill_close(self)

    async def close_memory(self: MemoryStore) -> None:
        closed.append("memory")
        await original_memory_close(self)

    async def close_audit(self: AuditLog) -> None:
        closed.append("audit")
        await original_audit_close(self)

    async def close_evidence(self: EvidenceStore) -> None:
        closed.append("evidence")
        await original_evidence_close(self)

    def fail_sandbox(self: SandboxManager, *args: object, **kwargs: object) -> None:
        raise OSError("synthetic sandbox construction failure")

    monkeypatch.setattr(SkillRegistry, "close", close_skill)
    monkeypatch.setattr(MemoryStore, "close", close_memory)
    monkeypatch.setattr(AuditLog, "close", close_audit)
    monkeypatch.setattr(EvidenceStore, "close", close_evidence)
    monkeypatch.setattr(SandboxManager, "__init__", fail_sandbox)

    with pytest.raises(launcher.LauncherError, match="startup_or_shutdown_failed"):
        await launcher.serve(launcher.validate(config, private_dir))

    assert {"skills", "memory", "audit", "evidence"} <= set(closed)
    assert not (working_dir / "data" / "memory.db").exists()
    assert not (working_dir / "data" / "skills.db").exists()


async def test_real_loopback_http_server_stops_cleanly(
    private_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    import httpx
    import uvicorn

    from agency.prototype import pilot_launcher as launcher

    config = config_file(private_dir)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    parent_server = uvicorn.Server

    class ShutdownServer(parent_server):
        async def serve(self, sockets: object = None) -> None:
            running = asyncio.create_task(super().serve(sockets=sockets))
            try:
                for _ in range(200):
                    if self.started:
                        break
                    await asyncio.sleep(0.01)
                assert self.started, "loopback server did not start"
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"http://127.0.0.1:{port}/pilot")
                assert response.status_code == 200
                assert "Single-owner local pilot" in response.text
            finally:
                self.should_exit = True
                await running

    monkeypatch.setattr(uvicorn, "Server", ShutdownServer)
    await launcher.serve(launcher.validate(config, private_dir, port=port))


def test_check_refuses_corrupt_token_without_replacing(private_dir: Path) -> None:
    from agency.prototype import pilot_launcher as launcher

    config = config_file(private_dir)
    token = token_file(private_dir)
    token.write_text("invalid token\n", encoding="ascii")
    before = snapshot(private_dir)
    with pytest.raises(launcher.LauncherError):
        launcher.validate(config, private_dir, check=True)
    assert snapshot(private_dir) == before


def test_check_cold_import_disables_dotenv(private_dir: Path) -> None:
    config = config_file(private_dir)
    token_file(private_dir)
    before = snapshot(private_dir)
    code = """
import os, runpy, sys
os.environ.pop('PYTHON_DOTENV_DISABLED', None)
def guard(event, args):
    if event == 'open' and isinstance(args[0], str) and args[0].replace('\\\\', '/').rsplit('/', 1)[-1] == '.env':
        raise RuntimeError('dotenv read forbidden')
    if event == 'sqlite3.connect':
        raise RuntimeError('SQLite forbidden in check')
sys.addaudithook(guard)
runpy.run_module('agency.prototype.pilot_launcher', run_name='__main__', alter_sys=True)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            "--config",
            str(config),
            "--data-dir",
            str(private_dir),
            "--check",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "configuration_valid" in result.stdout
    assert "synthetic-key-not-real" not in result.stdout + result.stderr
    assert snapshot(private_dir) == before


@pytest.mark.parametrize(
    "extra",
    [["--host", "0.0.0.0"], ["--port", "0"], ["--port", "65536"], ["--api-key", "never-echo-argv"]],
)
def test_cli_refuses_unapproved_inputs(
    private_dir: Path, extra: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    from agency.prototype import pilot_launcher as launcher

    config = config_file(private_dir)
    token_file(private_dir)
    before = snapshot(private_dir)
    assert (
        launcher.main(["--config", str(config), "--data-dir", str(private_dir), "--check", *extra])
        == 2
    )
    output = capsys.readouterr()
    assert "never-echo-argv" not in output.out + output.err
    assert snapshot(private_dir) == before
