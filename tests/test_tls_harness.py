"""Hermetic TLS-harness contracts for gap 18's local test-CA leg.

Pins three things: the launcher's fail-closed TLS termination seam (additive
uvicorn ssl_certfile/ssl_keyfile, never half-TLS, plain loopback HTTP
byte-for-byte when unconfigured), the local test-CA generator's chain/SAN/
overwrite contracts (verified with cryptography directly, never verify=False),
and one real HTTPS round trip terminated by the launcher's uvicorn server and
verified against the generated test CA. All certificate material is generated
fresh into temporary directories; nothing here requires or touches production
PKI. The launcher fixture helpers are mirrored from test_pilot_launcher.py on
purpose so this harness stays self-contained and hermetic.
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

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
    values: dict[str, object] = {
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


def snapshot(directory: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in directory.iterdir()}


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "gen_test_ca.py"


def _load_script() -> Any:
    spec = importlib.util.spec_from_file_location("gen_test_ca", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(name="gen")
def gen_script() -> Any:
    return _load_script()


# ------------------------------------------------------------------ #
# Launcher TLS termination seam (fail-closed, additive only)
# ------------------------------------------------------------------ #


def test_tls_pair_flows_from_settings_into_uvicorn_config(
    private_dir: Path, tmp_path: Path
) -> None:
    from agency.prototype import pilot_launcher as launcher

    config = config_file(private_dir)
    token_file(private_dir)
    cert = tmp_path / "server.cert.pem"
    key = tmp_path / "server.key.pem"
    settings = launcher.validate(config, private_dir, tls_certfile=cert, tls_keyfile=key)
    assert settings.tls_certfile == cert and settings.tls_keyfile == key
    assert launcher.validate(config, private_dir, tls_certfile=cert, tls_keyfile=key) == settings
    built = launcher.build_server_config(object(), settings)
    assert built.ssl_certfile == str(cert)
    assert built.ssl_keyfile == str(key)
    # TLS is additive only: the hardened loopback posture is unchanged.
    assert built.host == "127.0.0.1"
    assert built.port == settings.port
    assert built.proxy_headers is False
    assert built.workers == 1 and built.reload is False


def test_half_tls_pair_fails_closed_at_startup(private_dir: Path) -> None:
    from agency.prototype import pilot_launcher as launcher

    config = config_file(private_dir)
    token_file(private_dir)
    lone = private_dir.parent / "lone-cert.pem"
    with pytest.raises(launcher.LauncherError, match="tls_cert_and_key_required"):
        launcher.validate(config, private_dir, tls_certfile=lone)
    with pytest.raises(launcher.LauncherError, match="tls_cert_and_key_required"):
        launcher.validate(config, private_dir, tls_keyfile=lone)


def test_cli_half_tls_refuses_at_startup(
    private_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from agency.prototype import pilot_launcher as launcher

    config = config_file(private_dir)
    token_file(private_dir)
    before = snapshot(private_dir)
    argv = [
        "--config",
        str(config),
        "--data-dir",
        str(private_dir),
        "--check",
        "--tls-certfile",
        str(private_dir.parent / "lone-cert.pem"),
    ]
    assert launcher.main(argv) == 2
    output = capsys.readouterr()
    assert "tls_cert_and_key_required" in output.err
    assert snapshot(private_dir) == before


def test_config_builder_itself_refuses_half_pair(private_dir: Path) -> None:
    """Defense in depth: even a programmatically half-built Settings is refused."""
    from agency.prototype import pilot_launcher as launcher

    config = config_file(private_dir)
    token_file(private_dir)
    settings = launcher.validate(
        config,
        private_dir,
        tls_certfile=private_dir.parent / "server.cert.pem",
        tls_keyfile=private_dir.parent / "server.key.pem",
    )
    half = dataclasses.replace(settings, tls_keyfile=None)
    with pytest.raises(launcher.LauncherError, match="tls_cert_and_key_required"):
        launcher.build_server_config(object(), half)


def test_no_tls_keeps_plain_loopback_http_config(private_dir: Path) -> None:
    from agency.prototype import pilot_launcher as launcher

    config = config_file(private_dir)
    token_file(private_dir)
    settings = launcher.validate(config, private_dir)
    assert settings.tls_certfile is None and settings.tls_keyfile is None
    built = launcher.build_server_config(object(), settings)
    assert built.ssl_certfile is None and built.ssl_keyfile is None
    assert built.host == "127.0.0.1"


# ------------------------------------------------------------------ #
# Local test-CA generator (LOCAL TESTS ONLY, never production PKI)
# ------------------------------------------------------------------ #


def test_generator_writes_verifiable_chain_with_expected_sans(tmp_path: Path, gen: Any) -> None:
    out = tmp_path / "tls"
    assert gen.main([str(out), "--server-dns", "pilot.local"]) == 0
    for name in ("ca.cert.pem", "ca.key.pem", "server.cert.pem", "server.key.pem"):
        assert (out / name).is_file()

    ca = x509.load_pem_x509_certificate((out / "ca.cert.pem").read_bytes())
    server = x509.load_pem_x509_certificate((out / "server.cert.pem").read_bytes())
    server.verify_directly_issued_by(ca)
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is True
    assert (ca.not_valid_after_utc - ca.not_valid_before_utc).days >= 3600

    sans = server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert sans.get_values_for_type(x509.DNSName) == ["localhost", "pilot.local"]
    assert sans.get_values_for_type(x509.IPAddress) == [ipaddress.IPv4Address("127.0.0.1")]
    assert (
        x509.oid.ExtendedKeyUsageOID.SERVER_AUTH
        in server.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    )
    key = serialization.load_pem_private_key((out / "server.key.pem").read_bytes(), None)
    assert isinstance(key, ed25519.Ed25519PrivateKey)
    assert key.public_key() == server.public_key()


def test_generator_fresh_keys_each_run(tmp_path: Path, gen: Any) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    assert gen.main([str(first)]) == 0
    assert gen.main([str(second)]) == 0
    first_ca = x509.load_pem_x509_certificate((first / "ca.cert.pem").read_bytes())
    second_ca = x509.load_pem_x509_certificate((second / "ca.cert.pem").read_bytes())
    assert first_ca != second_ca


def test_generator_refuses_overwrite_without_force(tmp_path: Path, gen: Any) -> None:
    out = tmp_path / "tls"
    assert gen.main([str(out)]) == 0
    before = snapshot(out)
    with pytest.raises(SystemExit) as refused:
        gen.main([str(out)])
    assert refused.value.code == 2
    assert snapshot(out) == before


def test_generator_force_overwrites_with_fresh_keys(tmp_path: Path, gen: Any) -> None:
    out = tmp_path / "tls"
    assert gen.main([str(out)]) == 0
    first = (out / "ca.cert.pem").read_bytes()
    assert gen.main([str(out), "--force"]) == 0
    assert (out / "ca.cert.pem").read_bytes() != first


def test_generator_rsa2048_provides_compatible_keys(tmp_path: Path, gen: Any) -> None:
    out = tmp_path / "tls"
    assert gen.main([str(out), "--key-type", "rsa2048", "--server-dns", "meter.local"]) == 0
    ca = x509.load_pem_x509_certificate((out / "ca.cert.pem").read_bytes())
    server = x509.load_pem_x509_certificate((out / "server.cert.pem").read_bytes())
    server.verify_directly_issued_by(ca)
    key = serialization.load_pem_private_key((out / "server.key.pem").read_bytes(), None)
    assert isinstance(key, rsa.RSAPrivateKey)
    assert key.key_size == 2048
    usage = server.extensions.get_extension_for_class(x509.KeyUsage).value
    assert usage.key_encipherment is True
    sans = server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert sans.get_values_for_type(x509.DNSName) == ["localhost", "meter.local"]


# ------------------------------------------------------------------ #
# Real HTTPS round trip terminated by the launcher's uvicorn server
# ------------------------------------------------------------------ #


async def test_https_round_trip_verified_against_generated_test_ca(
    private_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx
    import uvicorn

    from agency.prototype import pilot_launcher as launcher

    # Certificate material must live OUTSIDE the isolated launcher data dir
    # (its policy forbids unexpected children), as a sibling like the other
    # launcher tests use for their scratch working directories.
    out = private_dir.parent / "tls-harness-certs"
    assert _load_script().main([str(out), "--server-dns", "pilot.local"]) == 0
    config = config_file(private_dir)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    class ShutdownServer(uvicorn.Server):
        async def serve(self, sockets: list[socket.socket] | None = None) -> None:
            running = asyncio.create_task(super().serve(sockets=sockets))
            try:
                for _ in range(300):
                    if self.started:
                        break
                    await asyncio.sleep(0.01)
                assert self.started, "TLS loopback server did not start"
                context = ssl.create_default_context(cafile=str(out / "ca.cert.pem"))
                async with httpx.AsyncClient(verify=context) as client:
                    response = await client.get(f"https://127.0.0.1:{port}/pilot")
                assert response.status_code == 200
                assert "Single-owner local pilot" in response.text
            finally:
                self.should_exit = True
                await running

    monkeypatch.setattr(uvicorn, "Server", ShutdownServer)
    await launcher.serve(
        launcher.validate(
            config,
            private_dir,
            port=port,
            tls_certfile=out / "server.cert.pem",
            tls_keyfile=out / "server.key.pem",
        )
    )
