"""Second live-provider qualification comparison (gaps.md #10 remaining leg).

Gap 10 was qualified live on an OpenAI-compatible provider (CentAI
``hy4-flash:free`` via ``AGENCY_LLM_PROVIDER=openai``). The remaining open item
is "a second provider for comparison". This script runs the same qualification
legs against BOTH configured live providers — the ambient default provider
(the ``AGENCY_LLM_*`` / provider-key auto-detect load path) and the Google
provider (``ProviderKind.GOOGLE``, resolved via ``GOOGLE_API_KEY`` / built-in
``gemini-3.5-flash-lite`` default), already used live for the Forge review gate
but never qualified as the task provider:

Adapter leg — dotenv → ``load_config`` → ``LLMAdapter.generate`` with the
exact-marker prompt pattern of the original qualification; the marker must
round-trip exactly.

Task leg — one exactly-verifiable task (``17+25`` → ``42``, the original
qualification fixture) end-to-end through the dispatcher pipeline on fresh
per-leg file-backed stores (``AGENCY_TASKS_DB`` / ``AGENCY_AUDIT_DB`` /
``AGENCY_EVIDENCE_DB`` / ``AGENCY_MEMORY_DB`` under a temp run dir), then the
terminal status, output match, verifier verdict, audit/evidence/memory counts
and captured provider backoff/error events are recorded.

The JSON comparison is written to the ``--out`` path argument. Honest by
construction: observed absence of errors is recorded as observed absence,
never a forced pass; provider failures (429/503/…) are recorded as the leg
outcome with their error class; the exit code reflects what actually ran.
No API keys are ever printed or stored (secret-value scan before write).

Not part of the test suite — tests stay hermetic (echo/stub only). The
offline refusal path is unit-tested instead (tests/test_second_provider_
comparison.py) via ``--env-file``: hermetic runs never load live keys.

Usage (from a worktree with a live ``.env``)::

    uv run python scripts/second_provider_comparison.py --out <evidence.json>
    uv run python scripts/second_provider_comparison.py --providers google
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_PATH = Path("D:/agency-merge-backups/second-provider-evidence.json")

MARKER = "QX-5281"
MARKER_PROMPT = f"Reply with exactly the marker {MARKER} and nothing else."
EXPECTED_RESULT = "42"
TASK_TITLE = "arith 17+25"
TASK_DESCRIPTION = "Compute 17+25. Reply with the integer result only."

# Generic default-provider variables (see agency.llm.config); they describe the
# ambient default provider and are re-shaped per leg so the executor's legacy
# ``LLMAdapter()`` env path and the planner's ``load_config`` path agree.
_GENERIC_ENV_KEYS = (
    "AGENCY_LLM_PROVIDER",
    "AGENCY_LLM_API_KEY",
    "AGENCY_LLM_MODEL",
    "AGENCY_LLM_BASE_URL",
    "LLM_PROVIDER",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_BASE_URL",
)
_STORE_ENV_KEYS = (
    "AGENCY_TASKS_DB",
    "AGENCY_AUDIT_DB",
    "AGENCY_EVIDENCE_DB",
    "AGENCY_MEMORY_DB",
    "AGENCY_SKILL_DB_PATH",
    "AGENCY_MAX_CONCURRENT_TASKS",
    "AGENCY_TASKS_LEASE_SECONDS",
    "AGENCY_TASKS_RECOVER",
)
GOOGLE_KEY_VARS = (
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "AGENCY_GOOGLE_API_KEY",
    "AGENCY_GEMINI_API_KEY",
)

# Worker log events turned into evidence counts (same events quality_soak.py parses).
BACKOFF_EVENT = "llm_provider_retry"
PROVIDER_ERROR_EVENT = "llm_error"
PLANNER_OK_EVENT = "planner.llm_decomposed"
PLANNER_FALLBACK_EVENT = "planner.llm_decompose_failed"
EXECUTOR_RETRY_EVENTS = ("executor.retry", "executor.timeout")

ADAPTER_ATTEMPTS = 3  # script-level retries; the adapter also retries transient statuses itself
ADAPTER_RETRY_SLEEP_S = 15.0


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _host_of(base_url: str | None) -> str:
    if not base_url:
        return ""
    return base_url.split("//")[-1].split("/")[0]


def output_matches(summary: str, expected: str) -> bool:
    """Exact-token match, digit-guarded so 42 does not match inside 420."""
    if not summary:
        return False
    pattern = rf"(?<![\w]){re.escape(expected)}(?![\w])"
    return re.search(pattern, summary, re.IGNORECASE) is not None


def resolve_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def secrets_scan(evidence_text: str, env_file: Path | None) -> list[str]:
    """Return names of configured secret values that leaked into the evidence text."""
    sources: list[Path] = []
    if env_file is not None and env_file.is_file():
        sources.append(env_file)
    cwd_env = REPO_ROOT / ".env"
    if cwd_env.is_file() and cwd_env not in sources:
        sources.append(cwd_env)
    leaked: list[str] = []
    for source in sources:
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if not value or len(value) < 16:
                continue
            if (
                "KEY" in key or "TOKEN" in key or "SECRET" in key or "PASSWORD" in key
            ) and value in evidence_text:
                leaked.append(key)
    return leaked


# --------------------------------------------------------------------------- #
# Structlog event capture (per-leg window)
# --------------------------------------------------------------------------- #


class EventCapture:
    """Capture structlog event dicts for one leg without losing console output.

    A pass-through recorder is prepended to the saved processor chain: it
    records the event dict and returns it so the rest of the chain (renderer
    included) still runs. ``structlog.testing.LogCapture`` would raise
    ``DropEvent`` and silence console output for the whole window.
    """

    def __init__(self) -> None:
        import structlog

        self._structlog = structlog
        self._saved: dict | None = None
        self._recorder = None
        self.entries: list[dict] = []

    def start(self) -> None:
        def record(_logger: object, _method: str, event_dict: dict) -> dict:
            # Snapshot a copy: the same dict object flows through the rest of
            # the chain and later processors mutate it; the snapshot keeps the
            # ``event`` name as seen at record time.
            self.entries.append(dict(event_dict))
            return event_dict

        self._recorder = record
        self._saved = self._structlog.get_config()
        self._structlog.configure(
            processors=[record, *self._saved.get("processors", [])],
        )

    def stop(self) -> None:
        if self._saved is not None:
            self._structlog.configure(**self._saved)
            self._saved = None

    def count(self, event: str) -> int:
        return sum(1 for entry in self.entries if entry.get("event") == event)

    def values(self, event: str, key: str) -> list[str]:
        return [
            str(entry.get(key))
            for entry in self.entries
            if entry.get("event") == event and entry.get(key)
        ]


# --------------------------------------------------------------------------- #
# Environment shaping (per provider; always restored)
# --------------------------------------------------------------------------- #


class EnvShaper:
    """Snapshot/restore the provider-relevant env around each leg."""

    def __init__(self) -> None:
        self._snapshot = {
            key: os.environ.get(key) for key in (*_GENERIC_ENV_KEYS, *_STORE_ENV_KEYS)
        }

    def restore(self) -> None:
        for key, value in self._snapshot.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def shape_stores(self, run_dir: Path, *, concurrency: int = 1, lease_seconds: int = 45) -> None:
        os.environ.update(
            {
                "AGENCY_TASKS_DB": str(run_dir / "tasks.db"),
                "AGENCY_AUDIT_DB": str(run_dir / "audit.db"),
                "AGENCY_EVIDENCE_DB": str(run_dir / "evidence.db"),
                "AGENCY_MEMORY_DB": str(run_dir / "memory.db"),
                "AGENCY_SKILL_DB_PATH": str(run_dir / "skills.db"),
                "AGENCY_MAX_CONCURRENT_TASKS": str(concurrency),
                "AGENCY_TASKS_LEASE_SECONDS": str(lease_seconds),
            }
        )
        os.environ.pop("AGENCY_TASKS_RECOVER", None)

    def shape_google(self, api_key: str, model: str, base_url: str | None) -> None:
        """Point the ambient default-provider vars at Google.

        The executor's plain ``LLMAdapter()`` reads only ``AGENCY_LLM_*`` (its
        ``_default_model`` has no google entry — observed live as 404s against
        ``openai-fast``), and the planner's ``load_config`` reads the generic
        vars first, so both paths must carry the Google values here.
        """
        os.environ["AGENCY_LLM_PROVIDER"] = "google"
        os.environ["AGENCY_LLM_API_KEY"] = api_key
        os.environ["AGENCY_LLM_MODEL"] = model
        if base_url:
            os.environ["AGENCY_LLM_BASE_URL"] = base_url
        else:
            os.environ.pop("AGENCY_LLM_BASE_URL", None)
        for key in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL"):
            os.environ.pop(key, None)


# --------------------------------------------------------------------------- #
# Legs
# --------------------------------------------------------------------------- #


async def run_adapter_leg() -> dict:
    """dotenv → load_config → LLMAdapter.generate with the exact-marker prompt."""
    from agency.llm.adapter import LLMAdapter
    from agency.llm.config import load_config

    record: dict = {
        "ok": False,
        "marker_expected": MARKER,
        "marker_observed_head": None,
        "duration_s": None,
        "attempts": 0,
        "error_class": None,
        "error_status": None,
    }
    config = load_config(load_dotenv=False)
    record["provider"] = str(config.provider)
    record["model"] = config.model
    record["base_url_host"] = _host_of(config.base_url)
    adapter = LLMAdapter(config=config)
    for attempt in range(1, ADAPTER_ATTEMPTS + 1):
        record["attempts"] = attempt
        start = time.monotonic()
        try:
            text = await adapter.generate(MARKER_PROMPT)
        except Exception as exc:  # noqa: BLE001 — the class/status are the honest record
            record["duration_s"] = round(time.monotonic() - start, 2)
            record["error_class"] = type(exc).__name__
            response = getattr(exc, "response", None)
            record["error_status"] = getattr(response, "status_code", None)
            if attempt < ADAPTER_ATTEMPTS:
                await asyncio.sleep(ADAPTER_RETRY_SLEEP_S)
            continue
        record["duration_s"] = round(time.monotonic() - start, 2)
        record["marker_observed_head"] = text.strip()[:80]
        record["ok"] = text.strip() == MARKER
        break
    return record


async def run_task_leg(run_dir: Path, timeout_s: float) -> dict:
    """One exactly-verifiable task through the dispatcher pipeline (fresh stores)."""
    from agency.evidence.store.store import EvidenceStore, FindingsFilter
    from agency.kernel.audit import AuditFilter, AuditLog
    from agency.kernel.task_store import TaskStore
    from agency.kernel.tasks import TaskManager
    from agency.memory.sms.store import MemoryStore
    from agency.orchestrator import AgencyOrchestrator

    record: dict = {
        "status": None,
        "expected": EXPECTED_RESULT,
        "output_match": None,
        "output_head": None,
        "verifier_verdict": None,
        "risk_status": None,
        "subtasks": None,
        "audit_entries_total": None,
        "audit_subtask_entries": None,
        "evidence_findings": None,
        "memory_write_observed": None,
        "duration_s": None,
        "error": None,
        "run_dir": str(run_dir),
    }
    capture = EventCapture()
    capture.start()
    try:
        store = TaskStore(os.environ["AGENCY_TASKS_DB"])
        await store.initialize()
        manager = TaskManager(store)
        orchestrator = AgencyOrchestrator(
            task_manager=manager,
            audit_db_path=os.environ["AGENCY_AUDIT_DB"],
            evidence_db_path=os.environ["AGENCY_EVIDENCE_DB"],
            memory_db_path=os.environ["AGENCY_MEMORY_DB"],
            skill_db_path=os.environ["AGENCY_SKILL_DB_PATH"],
        )
        await orchestrator.start()
        await orchestrator.start_dispatcher()
        task = await manager.create_task(
            title=TASK_TITLE,
            created_by="second-provider-comparison",
            input={"description": TASK_DESCRIPTION},
        )
        start = time.monotonic()
        deadline = start + timeout_s
        final = None
        while time.monotonic() < deadline:
            current = await manager.get_task(task.task_id)
            if current is not None and current.status.value in ("completed", "failed"):
                final = current
                break
            await asyncio.sleep(2.0)
        record["duration_s"] = round(time.monotonic() - start, 2)

        if final is None:
            current = await manager.get_task(task.task_id)
            record["status"] = current.status.value if current is not None else "missing"
            record["error"] = f"not terminal within {timeout_s:.0f}s"
        else:
            record["status"] = final.status.value
            output = final.output if isinstance(final.output, dict) else {}
            summary = str(output.get("summary", "") or "")
            record["output_head"] = summary[:120]
            record["output_match"] = (
                output_matches(summary, EXPECTED_RESULT)
                if final.status.value == "completed"
                else None
            )
            record["verifier_verdict"] = output.get("verification")
            record["risk_status"] = output.get("risk")
            record["subtasks"] = output.get("subtasks")

        async with AuditLog(os.environ["AGENCY_AUDIT_DB"]) as audit:
            entries = await audit.query(AuditFilter(limit=1000))
        record["audit_entries_total"] = len(entries)
        record["audit_subtask_entries"] = sum(
            1
            for entry in entries
            if isinstance(entry.action, str) and entry.action.startswith("subtask_")
        )

        evidence_store = EvidenceStore(os.environ["AGENCY_EVIDENCE_DB"])
        await evidence_store.initialize()
        try:
            findings = await evidence_store.list_findings(FindingsFilter(limit=1000))
            record["evidence_findings"] = len(findings)
        finally:
            await evidence_store.close()

        memory = MemoryStore(os.environ["AGENCY_MEMORY_DB"])
        await memory.initialize()
        try:
            items = await memory.list_all(limit=100)
            record["memory_write_observed"] = any(TASK_TITLE in str(item.content) for item in items)
        finally:
            await memory.close()

        record["planner_llm_decomposed"] = capture.count(PLANNER_OK_EVENT)
        record["planner_fallbacks"] = capture.count(PLANNER_FALLBACK_EVENT)
        record["executor_retries"] = sum(capture.count(event) for event in EXECUTOR_RETRY_EVENTS)
        record["provider_backoffs"] = capture.count(BACKOFF_EVENT)
        record["backoff_statuses"] = capture.values(BACKOFF_EVENT, "status")
        record["provider_errors"] = capture.count(PROVIDER_ERROR_EVENT)
        record["provider_error_statuses"] = capture.values(PROVIDER_ERROR_EVENT, "status")

        await orchestrator.stop()
        await store.close()
    except Exception as exc:  # noqa: BLE001 — harness faults are evidence too
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        capture.stop()
    return record


# --------------------------------------------------------------------------- #
# Provider selection and harness
# --------------------------------------------------------------------------- #


def discover_providers(env_file: Path | None) -> dict:
    """Resolve both provider configs without mutating the environment.

    The pre-import env view (ambient env overlaid by the ``--env-file`` values,
    existing env winning — the same precedence as ``load_dotenv``) is passed as
    an explicit ``env=`` mapping so discovery never depends on the adapter
    module's import-time ``load_dotenv()`` side effect. The google view drops
    the generic default-provider variables first (``_without_generic``), so a
    mixed setup (CentAI task LLM + Google key) resolves each provider's own
    credentials — the same separation ``load_named_provider`` implements.
    """
    from agency.llm.config import ProviderKind, _without_generic, load_config

    file_env: dict[str, str] = {}
    if env_file is not None and env_file.is_file():
        from dotenv import dotenv_values

        file_env = {key: value for key, value in dotenv_values(env_file).items() if value}
    env_view = {
        **file_env,
        **{key: value for key, value in os.environ.items() if value is not None},
    }

    default_config = load_config(env=env_view, load_dotenv=False)
    default_live = default_config.provider is not ProviderKind.ECHO and bool(default_config.api_key)

    google_config = load_config(
        provider="google", env=_without_generic(env_view), load_dotenv=False
    )
    google_live = google_config.api_key is not None

    return {
        "default": {
            "live": default_live,
            "provider": str(default_config.provider),
            "model": default_config.model,
            "base_url_host": _host_of(default_config.base_url),
        },
        "google": {
            "live": google_live,
            "provider": str(google_config.provider),
            "model": google_config.model,
            "base_url_host": _host_of(google_config.base_url),
            "api_key": google_config.api_key.get_secret_value() if google_config.api_key else "",
            "base_url": google_config.base_url,
        },
    }


def _empty_run_record(reason: str) -> dict:
    return {
        "configured": False,
        "ran": False,
        "verdict": "skipped",
        "reason": reason,
        "adapter": None,
        "task": None,
        "notes": [],
    }


async def run_provider_legs(name: str, discovery: dict, run_root: Path, timeout_s: float) -> dict:
    """Run the adapter + task legs for one provider with its env shaped."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    run_dir = run_root / f"{name}-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    shaper = EnvShaper()
    notes: list[str] = []
    try:
        if name == "google":
            google = discovery["google"]
            shaper.shape_google(google["api_key"], google["model"], google["base_url"])
        else:
            # The default provider already lives in the ambient env (dotenv →
            # load_config); restore the snapshot in case the google leg ran first.
            shaper.restore()
        shaper.shape_stores(run_dir)

        adapter_record = await run_adapter_leg()
        task_record = await run_task_leg(run_dir, timeout_s)

        reasons: list[str] = []
        if not adapter_record["ok"]:
            reasons.append(
                "adapter marker did not round-trip"
                + (
                    f" ({adapter_record['error_class']} status={adapter_record['error_status']})"
                    if adapter_record["error_class"]
                    else ""
                )
            )
        if task_record["status"] != "completed":
            reasons.append(f"task terminal status is {task_record['status']!r}")
        elif task_record["output_match"] is not True:
            reasons.append("completed output lacks the exact expected result")

        return {
            "configured": True,
            "ran": True,
            "verdict": "fail" if reasons else "pass",
            "reason": "; ".join(reasons)
            if reasons
            else "marker round-trip + task completed correctly",
            "adapter": adapter_record,
            "task": task_record,
            "notes": notes,
            "run_dir": str(run_dir),
        }
    finally:
        shaper.restore()


def _write_evidence(evidence: dict, out_path: Path, env_file: Path | None) -> None:
    text = json.dumps(evidence, indent=2, default=str)
    leaked = secrets_scan(text, env_file)
    if leaked:
        raise SystemExit(
            f"evidence would leak secret values for: {sorted(leaked)}; refusing to write"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")


async def async_main(args: argparse.Namespace) -> int:
    env_file = Path(args.env_file) if args.env_file else None
    load_dotenv(env_file)  # existing env wins; missing file is a no-op
    discovery = discover_providers(env_file)

    requested = [name.strip() for name in args.providers.split(",") if name.strip()]
    run_root = Path(tempfile.mkdtemp(prefix="agency-second-provider-"))
    evidence: dict = {
        "generated_at": _now(),
        "commit": resolve_commit(),
        "item": "gaps.md #10 — second live provider for comparison",
        "env_file": str(env_file) if env_file else None,
        "marker": MARKER,
        "task_fixture": {"title": TASK_TITLE, "expected": EXPECTED_RESULT},
        "providers_configured": {
            name: {key: value for key, value in record.items() if key != "api_key"}
            for name, record in discovery.items()
        },
        "runs": {},
        "notes": [
            (
                "Provider errors/backoffs are counted from captured structlog events "
                "(llm_error / llm_provider_retry) within each leg's window; zero is "
                "recorded as observed absence, not a forced pass."
            ),
            (
                "The verifier verdict is recorded exactly as the pipeline emitted it "
                "(post-8a9a48f the classic path fabricates no check-category criteria)."
            ),
            (
                "The executor's legacy LLMAdapter() env path has no google default "
                "model (resolves 'openai-fast'); the leg therefore sets "
                "AGENCY_LLM_MODEL explicitly to the google load path's resolved model."
            ),
        ],
    }

    ran_any = False
    all_ok = True
    for name in requested:
        if name not in discovery:
            evidence["runs"][name] = _empty_run_record(f"unknown provider {name!r}")
            continue
        if not discovery[name]["live"]:
            evidence["runs"][name] = _empty_run_record("provider not configured (no credentials)")
            continue
        if name == "google" and discovery["default"]["provider"] == "google":
            evidence["runs"][name] = _empty_run_record(
                "ambient default provider is already google; nothing separate to compare"
            )
            continue
        print(f"=== provider leg: {name} ({discovery[name]['model']}) ===", flush=True)
        try:
            result = await run_provider_legs(name, discovery, run_root, args.timeout)
        except Exception as exc:  # noqa: BLE001 — harness faults are evidence too
            result = {
                "configured": True,
                "ran": True,
                "verdict": "fail",
                "reason": f"harness exception: {type(exc).__name__}: {exc}",
                "adapter": None,
                "task": None,
                "notes": [],
            }
        evidence["runs"][name] = result
        ran_any = True
        if result["verdict"] != "pass":
            all_ok = False
        print(
            f"=== provider {name}: {result['verdict']} ({result['reason']})",
            flush=True,
        )
        _write_evidence(evidence, Path(args.out), env_file)

    _write_evidence(evidence, Path(args.out), env_file)
    print(f"evidence: {args.out}", flush=True)
    if not ran_any:
        return 2
    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT_PATH),
        help="Where to write the comparison JSON (default: %(default)s)",
    )
    parser.add_argument(
        "--env-file",
        default=str(REPO_ROOT / ".env"),
        help=(
            "Optional .env to load (existing env wins). Pass a missing path in "
            "offline/hermetic contexts so no live keys are loaded."
        ),
    )
    parser.add_argument(
        "--providers",
        default="default,google",
        help="Comma-separated subset of {default,google} to run (default: both)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="Task-leg terminal wait in seconds (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
