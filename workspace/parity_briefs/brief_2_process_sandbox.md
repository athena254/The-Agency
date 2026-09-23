# Parity Brief 2: Process Sandbox Backend (Docker-free code execution)

You are building a process-based sandbox backend for The Agency so agents can execute code WITHOUT Docker.

## Context
Project: C:\Users\alphi\theagency\
Spec: C:\Users\alphi\theagency\docs\SPEC_PARITY1.md (section 2.4)
Existing sandbox code (READ ALL FIRST):
- src/agency/security/sandbox/manager.py — SandboxManager, DockerSandboxBackend, Sandbox, ExecutionResult, SandboxError, SandboxStatus
- src/agency/security/sandbox/config.py — SandboxConfig, SandboxBackend enum (DOCKER/FIRECRACKER/CHROOT)
The enum value PROCESS will be added by the integrator BEFORE you finish — write your code assuming `SandboxBackend.PROCESS` exists. If it doesn't exist yet when you run tests, add it yourself to config.py (append `PROCESS = "process"` to the enum) — this is the ONLY existing file you may touch, and only that one line.

## Task
Implement `ProcessSandboxBackend` — same interface as DockerSandboxBackend (create_container, exec_command, inspect_status, remove_container) but running commands as local subprocesses in per-sandbox workspace directories.

## Files to Create

### 1. `src/agency/security/sandbox/process.py`
- `ProcessSandboxBackend` class with the SAME method signatures as DockerSandboxBackend:
  - `__init__(self, command_timeout: int = 120)` 
  - `create_container(self, sandbox_id: str, config: SandboxConfig, workspace: Path) -> str` — returns a "container id" (use f"proc-{sandbox_id}"); registers the workspace dir for that id; creates the dir if missing
  - `exec_command(self, container_id: str, command: Sequence[str], timeout: int) -> ExecutionResult` — runs the command via `subprocess.run`:
    - `cwd` = the sandbox's workspace dir
    - Windows: `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP` (guard with `sys.platform == "win32"` — the constant doesn't exist on POSIX)
    - POSIX: `start_new_session=True`
    - `capture_output=True`, `timeout=timeout`, `text=True`, `encoding="utf-8"`, `errors="replace"`
    - On `subprocess.TimeoutExpired` → return ExecutionResult with `timed_out=True`, exit_code=-1, stderr=f"timed out after {timeout}s", duration_ms measured
    - Truncate stdout and stderr to 64 KB each before building the result
    - Unknown container_id → raise SandboxError
    - Map the ExecutionResult fields exactly as DockerSandboxBackend.exec_command does (read its return statement and copy the field construction)
  - `inspect_status(self, container_id: str) -> str` — return "running" if the id is registered (process sandboxes are ephemeral), else "stopped"
  - `remove_container(self, container_id: str) -> None` — deregister the id; best-effort `shutil.rmtree` of its workspace dir (ignore errors)
- Track workspaces in a `dict[str, Path]` with a `threading.Lock` (manager calls may be concurrent)
- Class docstring MUST document the weaker isolation honestly: process-level separation only; on Windows there are no CPU/memory rlimits (resource.setrlimit is POSIX-only); bounds are the command timeout and output caps. Suitable for trusted code and demos, NOT for hostile code — hostile code needs the Docker backend.
- `__init__.py` of the sandbox package: check if it re-exports things; if it has an explicit `__all__`/imports, ADD ProcessSandboxBackend to the exports (this file may be modified for the export line only).

### 2. `tests/test_sandbox_process.py` (~8 tests)
Use the REAL ProcessSandboxBackend with real subprocesses (they're fast):
- create + exec `python -c "print('hello')"` → exit_code 0, stdout contains "hello" (use `sys.executable` for the python binary in the command)
- exec a failing command → exit_code != 0, stderr populated
- exec `python -c "import time; time.sleep(10)"` with timeout=1 → timed_out=True, duration under ~3s
- large stdout (print 100k chars) → truncated to 64 KB cap, does not blow up
- unknown container_id → SandboxError raised
- remove_container then exec → SandboxError
- create twice with same id → same workspace reused (or second create replaces registration — assert consistent behavior)
- exec runs in the workspace cwd: write a file with `python -c "open('marker.txt','w').write('x')"` then assert the file exists in the workspace dir

## Conventions
- Python 3.11+, `from __future__ import annotations`, structlog
- NO new dependencies — stdlib subprocess/threading/shutil/pathlib only
- Windows host: all tests must pass on Windows. Use `sys.executable` not "python" (Store stub issue).
- Run: `PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_sandbox_process.py -v` — all must pass
- Run: `ruff check src/agency/security/sandbox/` — clean
- Do NOT touch manager.py — the integrator wires backend selection.

## Do NOT modify any existing files except: the PROCESS enum line in config.py (if missing) and the export line in the sandbox package __init__.py.
