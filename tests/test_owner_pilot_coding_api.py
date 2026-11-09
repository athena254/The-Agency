"""Owner pilot coding HTTP surface and launcher wiring."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from agency.admission.auth import OwnerAuthenticator
from agency.prototype.app import create_app
from tests.test_factory_coding_service import coding_case as coding_case  # noqa: PLC0414
from tests.test_factory_coding_service import make_service as make_coding_service
from tests.test_factory_coding_service import payload as coding_payload
from tests.test_owner_service import concrete as concrete_fixture
from tests.test_owner_service import make_service as make_plain_service
from tests.test_pilot_launcher import config_file, protect, token_file

concrete = concrete_fixture

_CODING_BODY = {
    "request_id": "coding-disabled",
    "thread_id": "thread",
    "profile_id": "literal-assignment-v1",
    "task": "Set x to integer 2",
}


@pytest.fixture
async def plain_api(concrete):
    c = concrete
    service = make_plain_service(c)
    auth = OwnerAuthenticator(
        owner_id="owner",
        subject_id="subject",
        bearer_secret=SecretStr("SYNTHETIC_OWNER_TOKEN_32_CHARACTERS"),
    )
    app = create_app(c["butler"], pilot=service, authenticator=auth)
    async with (
        app.router.lifespan_context(app),
        c["client"](transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client,
    ):
        client.headers.update(
            {
                "Authorization": "Bearer SYNTHETIC_OWNER_TOKEN_32_CHARACTERS",
                "X-Prototype-CSRF": app.state.csrf_token,
                "Origin": "http://127.0.0.1",
            }
        )
        yield client, c, app


@pytest.fixture
async def coding_api(coding_case):
    c = coding_case
    service = make_coding_service(c)
    auth = OwnerAuthenticator(
        owner_id=c.owner.owner_id,
        subject_id=c.owner.subject_id,
        bearer_secret=SecretStr("SYNTHETIC_OWNER_TOKEN_32_CHARACTERS"),
    )
    app = create_app(pilot=service, authenticator=auth)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client,
    ):
        client.headers.update(
            {
                "Authorization": "Bearer SYNTHETIC_OWNER_TOKEN_32_CHARACTERS",
                "X-Prototype-CSRF": app.state.csrf_token,
                "Origin": "http://127.0.0.1",
            }
        )
        yield client, c, app


# --------------------------------------------------------------------------- #
# Disabled surface
# --------------------------------------------------------------------------- #


async def test_coding_status_reports_not_configured(plain_api) -> None:
    client, _c, _app = plain_api
    response = await client.get("/api/pilot/coding-status")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "available": False,
        "reason": "not_configured",
        "profile_id": None,
    }


async def test_coding_submit_is_disabled_without_executor(plain_api) -> None:
    client, c, _app = plain_api
    response = await client.post("/api/pilot/coding-tasks", json=_CODING_BODY)
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "disabled"
    assert await c["service"]._store.list_tasks(c["owner"]) == []


async def test_coding_receipt_missing_task_is_not_found(plain_api) -> None:
    client, _c, _app = plain_api
    response = await client.get("/api/pilot/coding-tasks/foreign")
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "not_found"


async def test_coding_write_requires_csrf(plain_api) -> None:
    client, c, _app = plain_api
    client.headers.pop("X-Prototype-CSRF")
    response = await client.post("/api/pilot/coding-tasks", json=_CODING_BODY)
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "forbidden"
    assert await c["service"]._store.list_tasks(c["owner"]) == []


async def test_coding_submit_rejects_invalid_body(plain_api) -> None:
    client, c, _app = plain_api
    response = await client.post(
        "/api/pilot/coding-tasks",
        json={"request_id": "x", "thread_id": "y", "profile_id": "wrong", "task": "z"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "invalid_request"
    assert await c["service"]._store.list_tasks(c["owner"]) == []


async def test_coding_submit_rejects_oversized_body(plain_api) -> None:
    client, c, _app = plain_api
    body = json.dumps({**_CODING_BODY, "task": "x" * 32769})
    response = await client.post(
        "/api/pilot/coding-tasks",
        content=body.encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413, response.text
    assert await c["service"]._store.list_tasks(c["owner"]) == []


async def test_coding_read_requires_bearer(plain_api) -> None:
    client, c, _app = plain_api
    client.headers.pop("Authorization")
    response = await client.get("/api/pilot/coding-status")
    assert response.status_code == 401, response.text
    assert response.json()["code"] == "unauthorized"
    assert await c["service"]._store.list_tasks(c["owner"]) == []


# --------------------------------------------------------------------------- #
# Configured surface
# --------------------------------------------------------------------------- #


async def test_coding_status_reports_configured(coding_api) -> None:
    client, _c, _app = coding_api
    response = await client.get("/api/pilot/coding-status")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "available": True,
        "reason": "configured",
        "profile_id": "literal-assignment-v1",
    }


async def test_coding_submit_publishes_receipt_and_diff(coding_api) -> None:
    client, c, _app = coding_api
    accepted = await client.post(
        "/api/pilot/coding-tasks", json=coding_payload(c).model_dump(mode="json")
    )
    assert accepted.status_code == 202, accepted.text
    task_id = accepted.json()["task_id"]

    async with asyncio.timeout(20):
        while True:
            read = await client.get(f"/api/pilot/tasks/{task_id}")
            assert read.status_code == 200, read.text
            if read.json()["state"] not in {"ADMITTED", "RUNNING"}:
                break
            await asyncio.sleep(0.005)

    result = read.json()
    assert result["state"] == "COMPLETED", result
    assert "Patch follows as DATA" in (result["response"] or "")
    assert "+x = 2" in (result["response"] or "")

    receipt = await client.get(f"/api/pilot/coding-tasks/{task_id}")
    assert receipt.status_code == 200, receipt.text
    body = receipt.json()
    assert body["reason"] == "available"
    assert body["execution_state"] == "COMPLETED"
    assert body["receipt"]["objective_status"] == "passed"
    assert body["receipt"]["reason"] == "matched"
    assert body["receipt"]["release_authorized"] is False
    assert body["receipt"]["tests_run"] is False
    assert body["receipt"]["binding"]["profile_id"] == "literal-assignment-v1"
    assert body["receipt"]["binding"]["task_id"] == task_id

    replay = await client.post(
        "/api/pilot/coding-tasks", json=coding_payload(c).model_dump(mode="json")
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["task_id"] == task_id


async def test_coding_submit_rejects_unknown_thread(coding_api) -> None:
    client, c, _app = coding_api
    body = {**coding_payload(c).model_dump(mode="json"), "thread_id": "foreign"}
    response = await client.post("/api/pilot/coding-tasks", json=body)
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "not_found"


# --------------------------------------------------------------------------- #
# Launcher wiring
# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


def _fixture_repo(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir()
    (repo / "m.py").write_bytes(b"x = 1\n")
    _git(repo, "init", "-q")
    for key, value in [
        ("user.name", "Synthetic Author"),
        ("user.email", "fixture@example.invalid"),
        ("core.autocrlf", "false"),
        ("core.hooksPath", str(root / f"no-hooks-{name}")),
    ]:
        _git(repo, "config", key, value)
    _git(repo, "add", "m.py")
    _git(repo, "commit", "-qm", "synthetic trusted fixture")
    return repo


@pytest.fixture
def private_dir(tmp_path: Path) -> Path:
    protect(tmp_path)
    return tmp_path


def test_validate_accepts_configured_coding_repo(private_dir: Path) -> None:
    from agency.prototype import pilot_launcher as launcher

    repo = _fixture_repo(private_dir.parent, f"trusted-literal-{private_dir.name}")
    config = config_file(private_dir, coding={"repo": str(repo), "job_timeout_s": 30.0})
    token_file(private_dir)
    settings = launcher.validate(config, private_dir, check=True)
    assert settings.config.coding is not None
    assert settings.config.coding.repo == str(repo)
    assert settings.config.coding.job_timeout_s == 30.0


def test_validate_rejects_drifted_coding_repo(private_dir: Path) -> None:
    from agency.prototype import pilot_launcher as launcher

    repo = _fixture_repo(private_dir.parent, f"trusted-literal-{private_dir.name}")
    (repo / "m.py").write_bytes(b"x = 2\n")
    config = config_file(private_dir, coding={"repo": str(repo)})
    token_file(private_dir)
    with pytest.raises(launcher.LauncherError):
        launcher.validate(config, private_dir, check=True)


def test_validate_rejects_invalid_coding_timeout(private_dir: Path) -> None:
    from agency.prototype import pilot_launcher as launcher

    repo = _fixture_repo(private_dir.parent, f"trusted-literal-{private_dir.name}")
    config = config_file(private_dir, coding={"repo": str(repo), "job_timeout_s": 0})
    token_file(private_dir)
    with pytest.raises(launcher.LauncherError):
        launcher.validate(config, private_dir, check=True)


def test_validate_rejects_coding_repo_inside_data_dir(private_dir: Path) -> None:
    from agency.prototype import pilot_launcher as launcher

    config = config_file(private_dir, coding={"repo": str(private_dir / "repo")})
    token_file(private_dir)
    with pytest.raises(launcher.LauncherError):
        launcher.validate(config, private_dir, check=True)


def test_validate_rejects_relative_coding_repo(private_dir: Path) -> None:
    from agency.prototype import pilot_launcher as launcher

    config = config_file(private_dir, coding={"repo": "relative/repo"})
    token_file(private_dir)
    with pytest.raises(launcher.LauncherError):
        launcher.validate(config, private_dir, check=True)


async def test_serve_wires_native_coding_executor(private_dir: Path, monkeypatch) -> None:
    import uvicorn

    from agency.admission.models import OwnerPrincipal
    from agency.prototype import pilot_launcher as launcher

    repo = _fixture_repo(private_dir.parent, f"trusted-literal-{private_dir.name}")
    config = config_file(private_dir, coding={"repo": str(repo)})
    settings = launcher.validate(config, private_dir)
    seen: dict[str, object] = {}

    class Server:
        def __init__(self, config: uvicorn.Config) -> None:
            self.app = config.app

        async def serve(self) -> None:
            app = self.app
            pilot = app.state.pilot
            seen["coding"] = getattr(pilot, "_coding", None)
            seen["workspace"] = pilot._workspace_id
            async with app.router.lifespan_context(app):
                principal = OwnerPrincipal("synthetic-owner", "local", "synthetic-browser")
                assert (await pilot.capabilities(principal)).ready
                coding = pilot._coding
                assert coding is not None
                assert coding.profile.profile_id == "literal-assignment-v1"
                assert coding.profile.workspace_id == pilot._workspace_id
                assert coding.profile.repo == repo

    monkeypatch.setattr(uvicorn, "Server", Server)
    await launcher.serve(settings)
    assert seen["coding"] is not None
    assert seen["workspace"]


async def test_serve_without_coding_config_passes_no_executor(
    private_dir: Path, monkeypatch
) -> None:
    import uvicorn

    from agency.prototype import pilot_launcher as launcher

    config = config_file(private_dir)
    settings = launcher.validate(config, private_dir)
    seen: dict[str, object] = {}

    class Server:
        def __init__(self, config: uvicorn.Config) -> None:
            self.app = config.app

        async def serve(self) -> None:
            seen["coding"] = getattr(self.app.state.pilot, "_coding", None)

    monkeypatch.setattr(uvicorn, "Server", Server)
    await launcher.serve(settings)
    assert seen["coding"] is None


async def test_serve_provisions_missing_coding_repo(private_dir: Path, monkeypatch) -> None:
    import uvicorn

    from agency.prototype import pilot_launcher as launcher

    repo = private_dir.parent / f"provisioned-literal-{private_dir.name}"
    config = config_file(private_dir, coding={"repo": str(repo)})
    settings = launcher.validate(config, private_dir)
    seen: dict[str, object] = {}

    class Server:
        def __init__(self, config: uvicorn.Config) -> None:
            self.app = config.app

        async def serve(self) -> None:
            seen["coding"] = getattr(self.app.state.pilot, "_coding", None)

    monkeypatch.setattr(uvicorn, "Server", Server)
    await launcher.serve(settings)
    assert seen["coding"] is not None
    assert (repo / "m.py").read_bytes() == b"x = 1\n"
    assert (repo / ".git").is_dir()
