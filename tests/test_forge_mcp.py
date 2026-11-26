"""Tests for the Forge MCP surface (stdio dispatcher + HTTP auth boundary)."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from agency.forge.mcp_server import (
    MAX_TEXT_CHARS,
    PROTOCOL_VERSION,
    SERVER_NAME,
    ForgeMcpServer,
    McpConfig,
    McpError,
    create_http_app,
)


def _git_repo(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README.md").write_text("hi\n")
    for name, content in (files or {}).items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True
    )
    return repo


def _call(server: ForgeMcpServer, name: str, arguments: dict) -> dict:
    async def run() -> dict:
        response = await server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        assert response is not None and "result" in response, response
        return response["result"]

    return asyncio.run(run())


def _structured(result: dict) -> dict:
    return result["structuredContent"]


# ---------------------------------------------------------------- discovery


def test_tools_list_is_bounded_and_wellformed() -> None:
    server = ForgeMcpServer()
    tools = server.tools()
    names = [tool["name"] for tool in tools]
    assert len(names) == len(set(names))
    assert all(name.startswith("forge_") for name in names)
    # Silent ship/deploy is never exposed; the two named gated tools are explicit.
    assert not any("ship" in name or "deploy" in name for name in names)
    assert "forge_remote_merge" in names
    assert "forge_github_release" in names
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"


def test_initialize_reports_protocol_and_server() -> None:
    async def run() -> dict:
        return await ForgeMcpServer().handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )  # type: ignore[return-value]

    response = asyncio.run(run())
    result = response["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == SERVER_NAME
    assert result["capabilities"] == {"tools": {}}


def test_unknown_method_is_an_error() -> None:
    with pytest.raises(McpError) as excinfo:
        asyncio.run(ForgeMcpServer().handle({"jsonrpc": "2.0", "id": 1, "method": "nope"}))
    assert excinfo.value.code == -32601


# ---------------------------------------------------------------- validation


def test_missing_required_argument_fails_closed() -> None:
    with pytest.raises(McpError) as excinfo:
        _call(ForgeMcpServer(), "forge_pipeline", {"root": "D:/x"})
    assert excinfo.value.code == -32602


def test_extra_argument_rejected() -> None:
    with pytest.raises(McpError):
        _call(ForgeMcpServer(), "forge_capabilities", {"unexpected": 1})


def test_oversized_spec_refused_before_any_work() -> None:
    with pytest.raises(McpError) as excinfo:
        _call(ForgeMcpServer(), "forge_build", {"repo": "D:/x", "spec": "x" * (MAX_TEXT_CHARS + 1)})
    assert "exceeds" in excinfo.value.message


def test_relative_root_refused(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    result = _call(
        ForgeMcpServer(),
        "forge_inspect",
        {"root": str(repo.parent / ".." / "escape"), "changed_paths": []},
    )
    # A root outside a real repo simply has nothing to inspect; the handler must
    # not crash and must not claim success on an empty inventory.
    assert result["isError"] is True or result["structuredContent"]["ok"] is False


def test_unknown_tool_is_an_error() -> None:
    with pytest.raises(McpError) as excinfo:
        _call(ForgeMcpServer(), "forge_deploy_prod", {})
    assert excinfo.value.code == -32602


# ---------------------------------------------------------------- read-only tools


def test_inspect_passes_on_a_clean_python_change(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path, {"app.py": "def add(a, b):\n    return a + b\n"})
    (repo / "app.py").write_text("def add(a, b):\n    return a + b + 0\n")
    result = _call(
        ForgeMcpServer(), "forge_inspect", {"root": str(repo), "changed_paths": ["app.py"]}
    )
    assert _structured(result)["ok"] is True
    assert _structured(result)["report"]["gate"] == "PASS"


def test_security_scan_flags_eval(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path, {"evil.py": "print('ok')\n"})
    (repo / "evil.py").write_text("eval('1+1')\n")
    result = _call(
        ForgeMcpServer(), "forge_security_scan", {"root": str(repo), "changed_paths": ["evil.py"]}
    )
    body = _structured(result)
    assert body["ok"] is False
    assert body["report"]["gate"] == "FAIL"
    assert "heuristic_only" in body["caveats"]


def test_pipeline_reports_the_same_gate_as_the_direct_call(tmp_path: Path) -> None:
    from agency.forge.pipeline import run_pipeline

    repo = _git_repo(tmp_path, {"app.py": "x = 1\n"})
    (repo / "app.py").write_text("x = 2\n")
    direct = run_pipeline(repo, ["app.py"], run_tests_flag=False, run_review_flag=False)
    via_mcp = _call(
        ForgeMcpServer(),
        "forge_pipeline",
        {
            "root": str(repo),
            "changed_paths": ["app.py"],
            "run_tests": False,
            "run_review": False,
        },
    )
    body = _structured(via_mcp)
    assert body["report"]["gate"] == direct.gate
    assert body["report"]["inspection"]["gate"] == direct.inspection.gate
    assert "release_not_authorized" in body["caveats"]


def test_capabilities_declares_no_merge_authority() -> None:
    body = _structured(_call(ForgeMcpServer(), "forge_capabilities", {}))
    assert body["authority"]["merge"] == "not_authorized"
    assert body["authority"]["release"] == "not_authorized"
    assert body["authority"]["ship_exposed"] is False
    assert body["limits"]["max_test_timeout_s"] == 300


# ---------------------------------------------------------------- job tools


def test_submit_then_get_job_roundtrip(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    db = str(tmp_path / "forge_jobs.db")
    submitted = _structured(
        _call(
            ForgeMcpServer(),
            "forge_submit_job",
            {
                "db": db,
                "title": "Add healthcheck",
                "task": "add `agency health`",
                "repo": str(repo),
            },
        )
    )
    job_id = submitted["job"]["job_id"]
    assert submitted["job"]["state"] == "QUEUED"
    assert submitted["caveats"] == ["queued_not_dispatched"]

    fetched = _structured(_call(ForgeMcpServer(), "forge_get_job", {"db": db, "job_id": job_id}))
    assert fetched["job"]["job_id"] == job_id


def test_get_unknown_job_is_ok_false_not_an_exception(tmp_path: Path) -> None:
    body = _structured(
        _call(ForgeMcpServer(), "forge_get_job", {"db": ":memory:", "job_id": "nope"})
    )
    assert body["ok"] is False


def test_submit_refuses_relative_repo(tmp_path: Path) -> None:
    result = _call(
        ForgeMcpServer(),
        "forge_submit_job",
        {
            "db": str(tmp_path / "j.db"),
            "title": "t",
            "task": "do it",
            "repo": "relative/repo",
        },
    )
    assert result["isError"] is True
    assert "failed" in result["structuredContent"] or "failed" in result["content"][0]["text"]


def test_status_reads_the_queue(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    db = str(tmp_path / "forge_jobs.db")
    _call(
        ForgeMcpServer(),
        "forge_submit_job",
        {"db": db, "title": "T", "task": "task body", "repo": str(repo)},
    )
    body = _structured(_call(ForgeMcpServer(), "forge_status", {"db": db}))
    assert body["status"]["total"] == 1


# ---------------------------------------------------------------- concurrency


def test_concurrency_semaphore_rejects_the_fifth_call() -> None:
    server = ForgeMcpServer(McpConfig(max_concurrent_calls=1))

    async def run() -> None:
        original = server._run_tool

        release_wrap = threading.Event()

        def slow(name: str, args: dict) -> dict:
            release_wrap.wait(timeout=5)
            return original(name, args)

        server._run_tool = slow  # type: ignore[method-assign]
        first = asyncio.create_task(
            server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "forge_capabilities", "arguments": {}},
                }
            )
        )
        # Give the worker thread a moment to take the semaphore.
        await asyncio.sleep(0.05)
        with pytest.raises(McpError) as excinfo:
            await server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "forge_capabilities", "arguments": {}},
                }
            )
        assert excinfo.value.code == -32000
        release_wrap.set()
        await asyncio.wait_for(first, timeout=5)

    asyncio.run(run())


# ---------------------------------------------------------------- HTTP transport


def test_http_app_refuses_to_build_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENCY_MCP_TOKEN", raising=False)
    monkeypatch.delenv("AGENCY_MCP_TOKEN_FILE", raising=False)
    with pytest.raises(RuntimeError):
        create_http_app()


def test_http_endpoint_rejects_a_wrong_bearer() -> None:
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    client = fastapi_testclient.TestClient(create_http_app(McpConfig(token="tok")))
    assert (
        client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}).status_code
        == 401
    )
    bad = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert bad.status_code == 401


def test_http_endpoint_serves_the_same_verdict_as_the_direct_call(tmp_path: Path) -> None:
    from agency.forge.pipeline import run_pipeline

    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    repo = _git_repo(tmp_path, {"app.py": "x = 1\n"})
    (repo / "app.py").write_text("x = 2\n")
    client = fastapi_testclient.TestClient(create_http_app(McpConfig(token="tok")))
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "forge_pipeline",
                "arguments": {
                    "root": str(repo),
                    "changed_paths": ["app.py"],
                    "run_tests": False,
                    "run_review": False,
                },
            },
        },
        headers={"Authorization": "Bearer tok"},
    )
    assert response.status_code == 200
    body = response.json()["result"]["structuredContent"]
    direct = run_pipeline(repo, ["app.py"], run_tests_flag=False, run_review_flag=False)
    assert body["report"]["gate"] == direct.gate
    # The structured content must be plain-JSON safe (no datetime objects).
    json.dumps(body)


def test_stdio_handshake_over_a_real_subprocess(tmp_path: Path) -> None:
    """Drive `forge mcp --transport stdio` as an external agent would."""
    repo = _git_repo(tmp_path, {"app.py": "x = 1\n"})
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "agency.cli.main",
            "forge",
            "mcp",
            "--transport",
            "stdio",
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert proc.stdin is not None and proc.stdout is not None
    try:
        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
        )
        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "forge_inspect",
                        "arguments": {"root": str(repo), "changed_paths": ["app.py"]},
                    },
                }
            )
            + "\n"
        )
        proc.stdin.flush()
        init_line = proc.stdout.readline()
        call_line = proc.stdout.readline()
        init = json.loads(init_line)
        assert init["result"]["serverInfo"]["name"] == SERVER_NAME
        call = json.loads(call_line)
        assert call["result"]["structuredContent"]["report"]["gate"] == "PASS"
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
