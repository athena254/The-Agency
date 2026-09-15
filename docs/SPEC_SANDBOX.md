# Nexus Sandbox Addon — Detailed Specification

## 1. Overview

The Sandbox addon provides **isolated execution environments** where Nexus agents can safely run potentially breaking code. Multiple agents can use sandboxes concurrently, each getting their own isolated workspace with enforced resource limits.

**Two Modes:**
- **Mode 1: Clean Room** — Empty Python environment for testing algorithms, math, data processing
- **Mode 2: Nexus Mirror (Gemini)** — Full Nexus codebase inside sandbox for testing system changes before mainstream rollout

---

## 2. Backend Options

### 2.1 Docker Backend (Primary)

**Why Docker:**
- Strong isolation via Linux namespaces (PID, network, mount, IPC, UTS) + cgroups
- Filesystem isolation: read-only root, writable tmpfs, volume mounts
- Network isolation: `--network none` by default
- Security: seccomp filters, AppArmor profiles, `--cap-drop ALL`, `--security-opt no-new-privileges`
- Snapshotting: `docker commit` for instant state restore
- Available: Docker daemon running on system

**Container Creation:**
```bash
docker run \
  --cpu-quota 50000 \
  --memory 512m \
  --pids-limit 64 \
  --network none \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --security-opt seccomp=builtin \
  --security-opt apparmor=docker-default \
  -v /tmp/nexus-sandbox-workspaces/<id>:/workspace \
  nexus-sandbox-base:latest \
  sleep infinity
```

**Execution:**
```bash
docker exec <container> python -u sandbox_wrapper.py
```

**Metrics:**
```bash
docker stats --format "{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}"
```

### 2.2 Process Backend (Fallback)

**When Docker unavailable:**
- `subprocess.Popen` with `start_new_session=True` (new process group)
- `resource.setrlimit()` for CPU, memory, file descriptors, processes
- `os.nice()` for CPU priority
- Process group kill for clean termination
- Weaker but still bounded — suitable for trusted code

**Resource Limits Applied:**
```python
resource.setrlimit(resource.RLIMIT_CPU, (runtime_seconds, runtime_seconds))
resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024 * 1024, -1))
resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 1024))
resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
```

### 2.3 RestrictedPython Backend (Optional)

**Weakest isolation — only for trusted code:**
- In-process execution with RestrictedPython compilation
- Minimal isolation: just compiler-level restrictions
- No filesystem or process isolation
- Requires `pip install RestrictedPython`

---

## 3. Mode 1: Clean Room

### 3.1 What's Inside
- Python 3.11 runtime
- Minimal OS (Debian slim via Docker, or host OS via subprocess)
- Pre-installed packages from base image
- Workspace directory (`/workspace`) — where code runs
- Agent's code — copied in at runtime

### 3.2 What's NOT Inside
- ❌ Nexus core system (no access to Lattice, agents, governance)
- ❌ Other agents' data (isolated per sandbox)
- ❌ Host filesystem (except limited `/tmp` access)
- ❌ Database connections (unless explicitly provided)
- ❌ API keys/secrets (must be injected via config)

### 3.3 Use Cases
- Mathematical computations
- Data transformations
- Third-party library testing
- Safe code that doesn't need Nexus

---

## 4. Mode 2: Nexus Mirror (Sandbox Gemini)

### 4.1 Purpose
Full Nexus codebase inside sandbox for **testing system changes before mainstream rollout**. Used by self-rectification nodes to validate upgrades.

### 4.2 What Happens During Setup
1. Clone Nexus repository into workspace
2. Checkout specified branch/commit
3. Install dependencies: `pip install -e /workspace/nexus[test]`
4. Optionally spin up isolated Lattice DB instance
5. Optionally seed test data
6. Optionally run test suite automatically

### 4.3 Rollout Strategy (Canary Pattern)
After sandbox validates the change:

```
Phase 1: Canary (1 node)
    ↓
Phase 2: Staging (10% of nodes)
    ↓
Phase 3: Gradual (33% → 66% → 100%)
    ↓
Phase 4: Mainstream (merge to main)
```

### 4.4 Use Cases
- Testing proposed changes to Nexus core
- Self-rectification nodes validating their own upgrades
- Integration testing before mainstream merge
- Dry-running governance proposals

---

## 5. The Agent Wrapper Pattern

### 5.1 What It Is
A Python script (`sandbox_wrapper.py`) injected into each workspace that provides:
1. **Resource limit enforcement** — Sets `resource.setrlimit()` before running code
2. **Import filtering** — Installs import hook that blocks unauthorized modules
3. **Safe execution** — Executes `agent_code.py` in controlled globals dict
4. **Result capture** — Captures result via `__AGENT_RESULT__` marker
5. **Error handling** — Handles exceptions via `__AGENT_ERROR__` marker

### 5.2 Communication Protocol
```
On success:
  stdout: __AGENT_RESULT__\n<repr(result)>\n

On error:
  stdout: __AGENT_ERROR__\n<error_message>\n<traceback>\n
  exit_code: 1
```

### 5.3 Wrapper Template Structure
```python
#!/usr/bin/env python3
"""Sandbox wrapper — DO NOT EDIT"""

import sys
import resource
import builtins
import importlib
import traceback
from pathlib import Path

# 1. Set resource limits
resource.setrlimit(resource.RLIMIT_CPU, (300, 300))
resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, -1))
resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 1024))
resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))

# 2. Install import hook
ALLOWED_IMPORTS = ['math', 'json', 'os', 'sys', 'datetime', ...]
_original_import = builtins.__import__
def safe_import(name, *args, **kwargs):
    if name.split('.')[0] not in ALLOWED_IMPORTS:
        raise ImportError(f"Import of '{name}' is not allowed")
    return _original_import(name, *args, **kwargs)
builtins.__import__ = safe_import

# 3. Execute user code
code_path = Path(__file__).parent / "agent_code.py"
agent_globals = {"__builtins__": builtins.__dict__}
try:
    exec(compile(code_path.read_text(), str(code_path), 'exec'), agent_globals)
    result = agent_globals.get("result")
    print(f"__AGENT_RESULT__\n{repr(result)}")
except Exception as e:
    print(f"__AGENT_ERROR__{e}\n{traceback.format_exc()}")
    sys.exit(1)
```

---

## 6. Public API

### 6.1 SandboxAPI Class

```python
class SandboxAPI:
    async def start(self) -> None
    async def stop(self) -> None
    
    async def create(
        self,
        subject: str,
        backend: str = "docker",
        memory_mb: int = 512,
        runtime_seconds: int = 300,
        network_access: str = "none",
        allowed_imports: Optional[list[str]] = None,
        env_vars: Optional[dict[str, str]] = None,
        athena_mirror: Optional[AthenaMirrorConfig] = None,
        tags: Optional[dict[str, str]] = None,
    ) -> str  # Returns sandbox_id
    
    async def run_code(self, sandbox_id: str, code: str, timeout: Optional[int] = None) -> SandboxResult
    async def run_tests(self, sandbox_id: str, test_path: str = "tests/", timeout: int = 120) -> SandboxResult
    async def install_packages(self, sandbox_id: str, packages: list[str]) -> SandboxResult
    async def copy_to_sandbox(self, sandbox_id: str, host_path: Union[str, Path], sandbox_path: str) -> None
    async def copy_from_sandbox(self, sandbox_id: str, sandbox_path: str, host_path: Union[str, Path]) -> None
    async def snapshot(self, sandbox_id: str, label: str = "") -> str
    async def restore(self, sandbox_id: str, snapshot_id: str) -> None
    async def destroy(self, sandbox_id: str) -> bool
    async def get_metrics(self, sandbox_id: str) -> SandboxStats
    async def list_sandboxes(self, subject: Optional[str] = None) -> list[dict]
    async def get_stats(self) -> dict
```

### 6.2 Data Models

```python
@dataclass
class ResourceLimits:
    cpu_percent: float = 50.0
    memory_mb: int = 512
    disk_mb: int = 1024
    max_pids: int = 64
    max_open_files: int = 1024
    runtime_seconds: int = 300

@dataclass
class AthenaMirrorConfig:
    include: bool = False
    source: Literal["git", "local", "image"] = "local"
    repo_path: Optional[Path] = None
    branch: Optional[str] = None
    commit: Optional[str] = None
    install_editable: bool = True
    extra_dependencies: list[str] = field(default_factory=list)
    mount_lattice: bool = False
    lattice_backend: str = "sqlite"
    lattice_db_path: str = "/workspace/lattice-test.db"
    auto_test: bool = False
    test_command: str = "pytest tests/ -x --tb=short"
    test_timeout: int = 600
    persistent_after_test: bool = False
    ttl_hours: int = 24

@dataclass
class SandboxResult:
    success: bool
    result: Any = None
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    duration_ms: float = 0.0
    memory_peak_mb: float = 0.0
    error_message: Optional[str] = None

@dataclass
class SandboxStats:
    cpu_usage_percent: float = 0.0
    memory_rss_mb: float = 0.0
    thread_count: int = 0
    open_files: int = 0
    disk_used_mb: float = 0.0
    uptime_seconds: float = 0.0
```

---

## 7. SandboxManager — Concurrency & Lifecycle

### 7.1 Concurrency Model
```python
class SandboxManager:
    def __init__(self, max_per_subject: int = 5):
        self._subject_semaphores: dict[str, asyncio.Semaphore] = {}
        self._pending_queues: dict[str, deque[Future]] = {}
        self._registry: dict[str, tuple[Any, SandboxConfig]] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
```

### 7.2 Per-Subject Semaphore
Each subject (agent) gets their own semaphore. When limit reached, requests queue:
```python
async def _acquire(self, subject: str):
    sem = self._subject_semaphores.setdefault(
        subject, asyncio.Semaphore(self._max_per_subject)
    )
    await sem.acquire()
    return sem
```

### 7.3 Lifecycle States
```
CREATING → READY → RUNNING → (COMPLETED | ERROR) → DESTROYED
                     ↓
                (PAUSED / SNAPSHOT)
```

### 7.4 Background Cleanup
Removes aged sandboxes (default >1h). Configurable TTL per sandbox.

---

## 8. Docker Infrastructure

### 8.1 Base Image Dockerfile
```dockerfile
FROM python:3.11-slim
RUN useradd -m -u 1000 sandbox
RUN pip install --no-cache-dir pytest numpy pandas
USER sandbox
WORKDIR /workspace
CMD ["sleep", "infinity"]
```

### 8.2 Build Script
```python
# scripts/build_image.py
import subprocess
subprocess.run([
    "docker", "build",
    "-f", "Dockerfile.base",
    "-t", "nexus-sandbox-base:latest",
    "."
], check=True)
```

---

## 9. Standalone HTTP Service

### 9.1 API Endpoints
```
POST   /sandbox/create           → {"sandbox_id": "..."}
POST   /sandbox/{id}/run         → SandboxResult
POST   /sandbox/{id}/tests       → SandboxResult
POST   /sandbox/{id}/packages    → SandboxResult
POST   /sandbox/{id}/snapshot    → {"snapshot_id": "..."}
POST   /sandbox/{id}/restore     → {"success": true}
GET    /sandbox/{id}/metrics     → SandboxStats
GET    /sandboxes?subject=...    → [{"sandbox_id": "...", ...}]
DELETE /sandbox/{id}             → {"success": true}
GET    /health                   → {"status": "ok"}
```

### 9.2 Quick Start
```bash
cd nexus
docker-compose -f docker-compose.sandbox.yml up -d
curl -X POST http://localhost:8000/sandbox/create \
  -H "Content-Type: application/json" \
  -d '{"subject":"demo","backend":"docker"}'
```

---

## 10. Security Boundaries

| Threat | Docker | Process | RestrictedPython |
|--------|--------|---------|-----------------|
| Filesystem access | ✅ Isolated | ⚠️ Workspace only | ❌ Full access |
| Network access | ✅ Blocked | ❌ Unrestricted | ❌ Unrestricted |
| CPU exhaustion | ✅ RLIMIT+cgroups | ✅ RLIMIT | ❌ None |
| Memory exhaustion | ✅ RLIMIT+cgroups | ✅ RLIMIT | ❌ None |
| Fork bombs | ✅ RLIMIT_NPROC | ✅ RLIMIT_NPROC | ❌ None |
| Import malicious module | ✅ Hook blocks | ⚠️ Can import all | ⚠️ Can import all |
| Escape to host | ✅ Very hard | ⚠️ Possible | ❌ Trivial |

---

## 11. Performance Characteristics

| Backend | Startup | Overhead | Isolation | Use Case |
|---------|---------|----------|-----------|----------|
| Docker | ~50-200ms | ~5-10MB RAM | Strong | Production, untrusted |
| Process | ~5-20ms | ~2-5MB RAM | Medium | Trusted, fast iteration |
| RestrictedPython | ~1ms | ~1MB RAM | Weak | Development only |

---

## 12. Error Handling

### 12.1 Wrapper Errors
- Import blocked → `ImportError` caught, reported via `__AGENT_ERROR__`
- Resource exceeded → Killed by Docker/cgroups
- Timeout → Container/process killed after `runtime_seconds`

### 12.2 Manager Errors
- Concurrency limit reached → Queued in FIFO wait queue
- Backend unavailable → Fallback to next backend
- Setup failure → Cleanup container/process, raise exception
