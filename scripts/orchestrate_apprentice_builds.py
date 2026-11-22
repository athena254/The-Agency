"""Orchestrate apprentice URL-shortener builds across four languages.

The Blacksmith queues one job per language; the apprentice (the Agency's own
task LLM via the gated coding agent) builds each service — no external CLI
strikers. Jobs dispatch-to-drain so failed jobs retry before new work starts.
Every job passes the full gate stack (inspection, security scan, native
tests, LLM review) before AWAITING_HUMAN. Independent HTTP acceptance then
runs as a holdout scenario against the built worktree, and smoke probes the
health endpoint.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from agency.forge.apprentice import run_apprentice
from agency.forge.external_agent import run_external_agent
from agency.forge.fleet import FleetRunner
from agency.forge.cost import CostStore
from agency.forge.evidence import EvidenceStore
from agency.forge.holdout import run_holdout
from agency.forge.orchestration import BlacksmithOps, JobState
from agency.forge.smoke import smoke_app

LANGS = ("rust", "go", "python", "typescript")

ACCEPTANCE_SCRIPT = Path("D:/agency-builds/acceptance.py")

_SPEC = """Build a URL shortener HTTP service in {language}.

REQUIREMENTS (all must hold):
- Standard library only; NO external dependencies; NO network installs.
- Read HOST (default 127.0.0.1) and PORT (default 8080) environment variables.
- GET / returns HTTP 200 with a small JSON status body.
- POST /shorten with JSON body {{"url": "<absolute http(s) url>"}} returns 200
  with JSON {{"short": "<id>", "url": "<original>"}}; a missing or invalid url
  returns 400.
- GET /<id> returns 302 with a Location header pointing at the stored url;
  an unknown id returns 404.
- Storage: an in-memory map is sufficient.
- {notes}
- Include the language's native tests covering: shorten happy path, redirect
  resolution, unknown id 404, invalid input 400.
"""

_LANGUAGE_HEADERS = {
    "rust": "Rust (single crate, edition 2021)",
    "go": "Go (single module)",
    "python": "Python (stdlib http.server)",
    "typescript": "TypeScript on Node >= 26 (native type stripping)",
}

_SPEC_NOTES = {
    "rust": (
        "Rust 2021 edition, std-lib ONLY (no crates). src/main.rs is the binary:"
        " reads HOST/PORT from the environment and serves over TcpListener. Keep"
        " the logic in src/lib.rs (Shortener with new/shorten/resolve) with inline"
        " #[cfg(test)] tests. Test gate: cargo test."
    ),
    "go": (
        "Go std-lib ONLY (net/http, encoding/json). package main; func main() reads"
        " HOST/PORT and serves. Keep the handlers reachable to tests (for example"
        " func NewMux() *http.ServeMux). Test gate: go test ./... (net/http/httptest)."
    ),
    "python": (
        "Python 3.12+ std-lib ONLY (http.server). A runnable entry reads HOST/PORT"
        " and serves; keep the request-handling logic importable (functions or a"
        " handler factory) so pytest can test it without real sockets."
        " Test gate: pytest."
    ),
    "typescript": (
        "TypeScript on Node >= 26 (native type stripping: erasable TS only — no"
        " enums/namespaces). node:http + node:crypto ONLY — no npm installs. An"
        " entry file reads HOST/PORT and serves; export shorten/resolve/createHandler"
        " so node:test can import them. package.json scripts.test must invoke"
        " node --test. Test gate: npm test."
    ),
}

_ACCEPTANCE = (
    "GET / returns 200",
    "POST /shorten with a valid url returns 200 and a short id",
    "GET /<id> for a shortened url returns 302 to the original url",
    "GET /<id> for an unknown id returns 404",
    "POST /shorten with an invalid body returns 400",
    "The language's standard test command passes",
)


def _init_repo(path: Path) -> Path:
    if not path.exists():
        path.mkdir(parents=True)
    if not (path / ".git").exists():
        subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "forge@agency.local"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Forge Builds"],
        check=True,
        capture_output=True,
    )
    readme = path / "README.md"
    if not readme.exists():
        readme.write_text(f"# {path.name}\n\nApprentice-built URL shortener.\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    done = subprocess.run(
        ["git", "-C", str(path), "diff", "--cached", "--quiet"],
        check=False,
        capture_output=True,
    )
    if done.returncode != 0:
        subprocess.run(
            ["git", "-C", str(path), "commit", "-m", "init"], check=True, capture_output=True
        )
    return path


def _find_worktree(root: Path, job_id: str) -> Path | None:
    """Worktrees are named after the dispatch branch: blacksmith-<job8>-a<n>."""
    worktrees = root / "worktrees"
    if not worktrees.is_dir():
        return None
    candidates = sorted(
        (p for p in worktrees.iterdir() if p.is_dir() and job_id[:8] in p.name),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="D:/agency-builds")
    parser.add_argument("--langs", default=",".join(LANGS))
    parser.add_argument("--db", default="D:/agency-builds/forge_apprentice_jobs.db")
    args = parser.parse_args()
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    langs = [lang.strip() for lang in args.langs.split(",") if lang.strip()]

    # The fleet: in-process apprentice first (cheap, per-file protocol), then
    # the strikers — opencode and hermes CLIs — when the apprentice's provider
    # is down. Failover happens on infrastructure failures only.
    # Hermes first among strikers: its model family is independent of the
    # apprentice's CentAI backend; opencode's hy4-flash-free rides the same
    # provider, so it is the last resort.
    fleet = FleetRunner(
        [
            ("apprentice", run_apprentice),
            (
                "hermes-striker",
                lambda repo, command, task, **kw: run_external_agent(
                    repo, ["hermes", "--yolo", "-z"], task, timeout_s=1800, **kw
                ),
            ),
            (
                "opencode-striker",
                lambda repo, command, task, **kw: run_external_agent(
                    repo,
                    ["opencode", "run", "--model", "opencode/hy4-flash-free", "--auto"],
                    task,
                    timeout_s=1800,
                    **kw,
                ),
            ),
        ]
    )
    ops = BlacksmithOps(
        args.db,
        runner=fleet,
        worktree_root=root / "worktrees",
        model="centai/hy4-flash:free",
        evidence=EvidenceStore(root / "evidence.db"),
        costs=CostStore(root / "cost.db"),
        max_retries=2,
    )
    results: dict[str, dict[str, object]] = {}
    try:
        jobs = {}
        for lang in langs:
            repo = _init_repo(root / f"{lang}-shortener")
            jobs[lang] = ops.submit(
                f"URL shortener in {lang}",
                _SPEC.format(language=_LANGUAGE_HEADERS[lang], notes=_SPEC_NOTES[lang]),
                str(repo),
                acceptance=_ACCEPTANCE,
                test_paths=["."],
                language=lang,
            )
            print(f"=== queued {lang}: job {job_id8(jobs[lang])} ===", flush=True)

        def drain() -> None:
            # Dispatch until nothing is dispatchable. The free provider needs
            # breathing room between long generations, so failed attempts pace
            # the next dispatch instead of hammering back-to-back.
            while True:
                done = ops.dispatch_next()
                if done is None:
                    return
                print(f"=== job {done.job_id[:8]} -> {done.state.value} ===", flush=True)
                if done.state is JobState.FAILED:
                    time.sleep(45.0)

        drain()
        # Self-reliance: give every BLOCKED job one fresh attempt budget
        # (audited) before the run gives up on it.
        revived = ops.resurrect_blocked()
        if revived:
            print(f"=== resurrected {len(revived)} blocked job(s); second pass ===", flush=True)
            drain()

        for lang, job in jobs.items():
            final = ops.get(job.job_id)
            entry: dict[str, object] = {
                "job_id": job.job_id,
                "state": final.state.value,
                "gate": final.gate,
                "changed_paths": list(final.changed_paths),
            }
            if final.state is JobState.AWAITING_HUMAN:
                wt = _find_worktree(root, job.job_id)
                if wt is not None:
                    smoke = smoke_app(wt, timeout_s=120.0)
                    entry["smoke"] = {"gate": smoke.gate, "status": smoke.status_code}
                    holdout = run_holdout(
                        wt,
                        [
                            {
                                "name": f"acceptance-{lang}",
                                "command": [sys.executable, str(ACCEPTANCE_SCRIPT), "--lang", lang],
                            }
                        ],
                        timeout_s=300,
                    )
                    scenario = holdout.scenarios[0] if holdout.scenarios else None
                    entry["holdout"] = {
                        "gate": scenario.gate if scenario else holdout.gate,
                        "tail": (
                            (scenario.stderr or scenario.stdout or "")[-500:] if scenario else ""
                        ),
                    }
                else:
                    entry["verification"] = "worktree not found"
            else:
                entry["failure_detail"] = final.failure_detail
            results[lang] = entry
            print(f"=== {lang}: {final.state.value} ===", flush=True)
    finally:
        ops.close()

    out = root / "build_results.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps(results, indent=2))
    return 0


def job_id8(job: object) -> str:
    return str(job.job_id)[:8]


if __name__ == "__main__":
    sys.exit(main())
