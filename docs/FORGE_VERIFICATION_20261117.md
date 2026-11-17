# Forge hardening — live verification (2026-11-17)

Worktree: `C:\Users\PC\The-Agency-worktrees\forge-hardening` (branch `forge-hardening`).
All Python commands use `uv run` against the synced `.venv`. Scratch repos/data live only
under `C:\Users\PC\AppData\Local\Temp\opencode\forge-verify` and are not part of the repo.

Secret handling: the four `AGENCY_LLM_*` variables (`PROVIDER`, `MODEL`, `API_KEY`, `BASE_URL`)
were parsed from the main checkout `.env` into the shell process environment for the
`forge code` command only. No value was printed or written to a tracked file; the Forge logs
mask the key as `api_key=***`.

## Result summary

| Surface | Result |
|---------|--------|
| `agency forge code` in worktree + gates (live Gemini review) | PASS |
| TUI import smoke | PASS |
| `agency tui --help` | PASS |
| Forge focused tests (`test_forge_*.py`, `test_llm_google.py`) | PASS (307 passed, 1 skipped) |
| holdout / evidence / smoke / merge_queue / scheduler / cost / generate / backlog / tool_catalog real invocations | PASS |
| Pipeline fails closed on invalid Python syntax | PASS (gate FAIL as required) |
| Pipeline fails closed on secret-like string | PASS (gate FAIL as required) |
| Ship human-approval regression tests | PASS (8 passed) |
| Ruff check / format / mypy | PASS |

## 1. Coding agent in worktree + gates (live)

Scratch git repos were created with `git init -b main`, a base commit, and a local
`user.email`/`user.name`. The `AGENCY_LLM_*` vars were loaded into the shell first, then:

```text
uv run agency forge code "Add a file hello.txt containing hello." --repo <scratch repo2> --cleanup
exit=0
```

Observed log lines (abridged, paths shortened):

```text
forge.worktree_created  branch=forge-external-agent ...
forge.inspected         files=1 gate=PASS rejected=0
forge.scanned           findings=0 gate=PASS scanned=1
llm_config_loaded       api_key=*** base_url=https://generativelanguage.googleapis.com/v1beta model=gemini-3.5-flash-lite provider=google
llm_adapter_initialized has_api_key=True model=gemini-3.5-flash-lite provider=google
llm_generate            model=gemini-3.5-flash-lite provider=google
forge.review            gate=PASS rounds=1 score=5
forge.external_agent_done changed=1 exit_code=0 gate=PASS
forge.worktree_removed  path=...
Forge gate: PASS
Worktree: ...
Changed: hello.txt
Inspection: PASS
Security: PASS
```

This proves: isolated worktree, the `opencode` coding agent making a real edit, inspection +
security gates, **and a live Gemini 3.x LLM review** (provider `google`,
`gemini-3.5-flash-lite`, endpoint `generativelanguage.googleapis.com`, key masked).

A first attempt used `--json` with stdout/stderr redirected to a file:

```text
uv run agency forge code "Create a file greeting.txt containing the single word hello." --repo <scratch repo1> --json   (redirected to file)
```

The report itself showed `gate=PASS`, `exit_code=0`, `changed_paths=["greeting.txt"]`,
`inspection=PASS`, `security=PASS`, but the process exited 1 because Rich could not render
JSON to a redirected file handle on legacy Windows (`legacy_windows_render` /
`SetConsoleTextAttribute` traceback in `cli/main.py:_print_json`). Re-running without
`--json` and without redirection (above) exited 0. This is a Windows console-rendering
issue in the CLI output path, not a Forge gate failure; it is recorded as a caveat.

## 2. TUI import / help smoke

```text
uv run python -c "from agency.tui.forge_app import run_forge_tui; print('ok')"   -> ok
uv run agency tui --help                                                         -> usage/help text, exit 0
```

`agency tui --help` printed the Typer help for the Agency TUI (options `--yolo`, `--model`,
`--server-url`, `--help`). The interactive event loop was **not** launched (headless shell).

## 3. Forge modules — focused tests and real invocations

Focused tests (PowerShell does not expand `*` for native commands, so the glob was expanded
into the 22 `tests/test_forge_*.py` paths before invoking pytest):

```text
uv run pytest <22 expanded test_forge_*.py files> tests/test_llm_google.py -q -p no:cacheprovider
307 passed, 1 skipped in 189.29s (0:03:09)
```

One real API invocation per requested module, driven by a scratch script
(`<scratch>\verify_modules.py`) run with `uv run python`:

```text
uv run python <scratch>\verify_modules.py <scratch>\scratch
holdout:       OK -> gate=PASS          (run_holdout, 1 scenario)
evidence:      OK -> gate=PASS          (capture_evidence over a.txt)
smoke:         OK -> gate=PASS          (smoke_app, status 200)
merge_queue:   OK -> merge_allowed=True (evaluate_merge, all gates PASS, no protected path touched)
scheduler:     OK -> cron=daily echo hi (generate_timer)
cost:          OK -> entries=1          (record_cost to SQLite store, model=gemini)
generate:      OK -> gate=PASS          (generate_app, 1 file, tests off)
backlog:       OK -> gate=PASS          (slice_prd, 1 item)
tool_catalog:  OK -> count=18           (list_tools)
```

`run_review` / `run_pipeline` PASS was additionally exercised by the real `forge code` run
in section 1 (with the Gemini provider) and by `generate_app` (offline echo review).

## 4. Pipeline fails closed

Both cases were run through the real `run_pipeline` (from `src/agency/forge/pipeline.py:102`)
against scratch directories, with `run_review_flag=False` to isolate the deterministic gates:

```text
bad_syntax.py  = "def broken(:\n    pass\n"
run_pipeline(<scratch>\pipeline_bad, ["bad_syntax.py"], run_review_flag=False)
-> pipeline_bad_syntax: gate=FAIL inspection=FAIL security=PASS

config.txt     = 'api_key = "abcdefghijklmnop"'
run_pipeline(<scratch>\pipeline_leak, ["config.txt"], run_review_flag=False)
-> pipeline_secret: gate=FAIL inspection=PASS security=FAIL
```

Invalid Python syntax fails the inspection gate; a secret-like string fails the security
scan (`SEC100 possible hardcoded secret`). In both cases the composed pipeline gate is FAIL.

## 5. Ship default (Task 2 regression)

```text
uv run pytest tests/test_forge_smoke_ship.py -q -p no:cacheprovider
8 passed in 15.55s
```

Covered: default refuses to merge without approval; explicit `allow_auto_merge=True` opt-in
still merges; an approved human path (`approved=True`) still merges; a FAIL gate never merges
even with approval; FAIL gate reasons remain `pipeline_gate_not_pass` / `smoke_gate_not_pass`.

## 6. Required gates

```text
uv run pytest <22 expanded test_forge_*.py files> tests/test_llm_google.py -q -p no:cacheprovider
307 passed, 1 skipped in 189.29s (0:03:09)

uv run ruff check src/ tests/
All checks passed!

uv run ruff format --check src/ tests/
508 files already formatted

uv run mypy src/
Success: no issues found in 259 source files
```

Note: `uv run pytest tests/test_forge_*.py ... -q -p no:cacheprovider` as written does not
work verbatim under PowerShell 5.1 because PowerShell passes the `*` literally and pytest
does not expand it here; the exact expanded-path command above was used instead.

## Honest limitations / not verified

- The full suite (~13 min) was intentionally not run; only the focused Forge + Google tests.
- The interactive TUI event loop was not launched; only import and `--help` were verified.
- `agency forge code --json` under PowerShell file redirection crashes in Rich on this
  Windows build (exit 1) despite a PASS report; the non-redirected invocation is clean.
- No shared-branch merge or release was performed; ship behavior was verified only against
  temporary local git repos. Release remains unauthorized (`release_not_authorized` caveat).
- The Gemini provider was observed live only through Forge's LLM review gate (PASS, score 5)
  via the configured endpoint. No provider SLA, quota, pricing or cost-accuracy claim is made.
- No production-readiness claim is implied by any result above; all checks are local scratch
  evidence for this branch.
