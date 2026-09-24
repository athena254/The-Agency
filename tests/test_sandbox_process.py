from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

import pytest

from agency.security.sandbox.config import SandboxConfig
from agency.security.sandbox.manager import SandboxError
from agency.security.sandbox.process import ProcessSandboxBackend


def _make_backend(tmp_path: Path) -> tuple[ProcessSandboxBackend, str, str, Path]:
    backend = ProcessSandboxBackend()
    sandbox_id = f"test-{uuid.uuid4().hex[:8]}"
    workspace = tmp_path / sandbox_id
    container_id = backend.create_container(sandbox_id, SandboxConfig(), workspace)
    return backend, sandbox_id, container_id, workspace


def test_create_and_exec_hello(tmp_path: Path) -> None:
    backend, _sid, container_id, _ws = _make_backend(tmp_path)
    try:
        result = backend.exec_command(
            container_id, [sys.executable, "-c", "print('hello')"], timeout=30
        )
        assert result.exit_code == 0
        assert "hello" in result.stdout
    finally:
        backend.remove_container(container_id)


def test_exec_failing_command(tmp_path: Path) -> None:
    backend, _sid, container_id, _ws = _make_backend(tmp_path)
    try:
        result = backend.exec_command(
            container_id,
            [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
            timeout=30,
        )
        assert result.exit_code != 0
        assert result.stderr
    finally:
        backend.remove_container(container_id)


def test_exec_timeout(tmp_path: Path) -> None:
    backend, _sid, container_id, _ws = _make_backend(tmp_path)
    try:
        start = time.perf_counter()
        result = backend.exec_command(
            container_id,
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout=1,
        )
        elapsed = time.perf_counter() - start
        assert result.timed_out is True
        assert elapsed < 3 + 5  # generous upper bound for loaded CI hosts
        assert elapsed < 8
    finally:
        backend.remove_container(container_id)


def test_large_stdout_truncated(tmp_path: Path) -> None:
    backend, _sid, container_id, _ws = _make_backend(tmp_path)
    try:
        result = backend.exec_command(
            container_id,
            [sys.executable, "-c", "print('x' * 100000)"],
            timeout=30,
        )
        assert len(result.stdout) <= 64 * 1024
        assert len(result.stdout) == 64 * 1024
    finally:
        backend.remove_container(container_id)


def test_unknown_container_raises(tmp_path: Path) -> None:
    backend = ProcessSandboxBackend()
    with pytest.raises(SandboxError):
        backend.exec_command(
            "proc-does-not-exist",
            [sys.executable, "-c", "print('hi')"],
            timeout=10,
        )


def test_remove_then_exec_raises(tmp_path: Path) -> None:
    backend, _sid, container_id, _ws = _make_backend(tmp_path)
    backend.remove_container(container_id)
    with pytest.raises(SandboxError):
        backend.exec_command(container_id, [sys.executable, "-c", "print('hi')"], timeout=10)


def test_create_twice_same_id(tmp_path: Path) -> None:
    backend = ProcessSandboxBackend()
    sandbox_id = f"test-{uuid.uuid4().hex[:8]}"
    workspace = tmp_path / sandbox_id
    first = backend.create_container(sandbox_id, SandboxConfig(), workspace)
    second = backend.create_container(sandbox_id, SandboxConfig(), workspace)
    try:
        assert first == second == f"proc-{sandbox_id}"
        result = backend.exec_command(second, [sys.executable, "-c", "print('hello')"], timeout=30)
        assert result.exit_code == 0
        assert "hello" in result.stdout
    finally:
        backend.remove_container(second)


def test_exec_runs_in_workspace_cwd(tmp_path: Path) -> None:
    backend, _sid, container_id, workspace = _make_backend(tmp_path)
    try:
        result = backend.exec_command(
            container_id,
            [sys.executable, "-c", "open('marker.txt','w').write('x')"],
            timeout=30,
        )
        assert result.exit_code == 0
        assert (workspace / "marker.txt").exists()
    finally:
        backend.remove_container(container_id)
