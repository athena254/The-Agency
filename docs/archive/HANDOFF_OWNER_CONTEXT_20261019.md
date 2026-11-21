# Owner-local context handoff — October 19, 2026

## Delivered boundary

Branch `feat/owner-context-20261019`, base `8d0257c26a1b7c1cdea14036e78eb6878e2628de`.
Only production change is the existing `ThreadContextEngine.assemble`:

```python
async def assemble(
    self, owner: str, thread_id: str, message: str, *, local_only: bool = False
) -> ThreadContext:
    ...
```

- `True` bypasses `_topical_recall` completely, rather than querying shared memory and discarding results.
- Ownership authorization, rolling-summary extension/persistence, rowid boundary, and verbatim window use the **same existing code** in both modes.
- Default/`False` retain real legacy thread-scoped recall, including its access-time updates.
- No second engine, private memory store, service/orchestrator change, or automatic turn mirroring.
- Local assembly may persist the existing thread rolling summary; it does not append messages, call `record_turn`, or read/write shared memory.

**Consumer requirement:** the later admitted owner service must explicitly call `assemble(..., local_only=True)` and must not call legacy `record_turn`. This additive flag does not disable that separate legacy public write API or authenticate transport principals.

## Vertical TDD and regression evidence

Evidence directory: `C:/Users/alphi/agency-evidence/limited-autonomy-20261019/owner-context/`.
Runner: `C:/Users/alphi/AppData/Local/hermes/cache/scratch/agency-build-20261019/owner-context/run_scoped.py`.

1. Added only the shared-memory exclusion behavior test. `01-local-red.log`: **1 failed**, missing keyword argument, not collection/setup failure.
2. Added just the keyword signature. `02-local-retrieval-red.log`: **1 failed**, real seeded `postgres` legacy secret actually returned in topical context.
3. Added the single conditional bypass. `03-local-green.log`: **1 passed**; no further production behavior changes.
4. Added each inherited-behavior characterization separately and ran it before the next: authorization (`04`, **7 passed**), default/False recall (`05`, **9 passed**), summary/window (`06`, **14 passed**), isolation (`07`, **17 passed**). These preserved behaviors passed on first execution; they are not represented as additional RED/GREEN implementation cycles.

Tests use concrete temporary file-backed `ThreadStore` and `MemoryStore` and the actual engine/retrieval implementation. Async spies wrap real methods, never return fake recall data. Local-mode checks count **calls**, not merely awaited calls, to catch swallowed best-effort errors. They assert zero topical/semantic/FTS retrieval calls, zero `record_turn`/memory-store/access-time writes, no SQL on the real memory connection, and unchanged stored memory values. Deliberately seeded matching thread-like agent IDs/tags cannot leak into local context. Positive legacy tests execute real FTS and access-time updates, excluding other-thread and sender-global data.

Other coverage: foreign/unknown owner denial before any memory access in all three modes; absent/stale/current/advanced/fully-covered summaries with exact rowid/window splits; no new input publication or message mutation; exact local/legacy context equality without recall; empty/populated/sibling/foreign/branched-thread isolation.

## Final checks on delivered source/tests

All via `C:/Users/alphi/theagency/.venv/Scripts/python.exe` (Python 3.11.16):

| Receipt | Actual result |
| --- | --- |
| `13-ruff-check-final.log` | Worktree-root `ruff check src/ tests/`: passed |
| `14-ruff-format-final.log` | Worktree-root `ruff format --check src/ tests/`: 258 files already formatted |
| `15-cold-import.log` | Fresh interpreter imports this worktree's context module; confirms exact keyword-only default-False signature |
| `16-mypy-win32-final.log` | Strict affected-source mypy, win32 target: no issues in 1 source file |
| `17-mypy-linux-final.log` | Strict affected-source mypy, linux target: no issues in 1 source file |
| `18-focused-final.log` | Owner context + existing Butler context tests, `-k 'not service'`: **41 passed, 4 deselected** |

Mypy uses `--follow-imports=silent`, separate scratch caches, and the worktree root/config; dependencies inform typing but their diagnostics are excluded. Linux is a mypy target on Windows, **not Linux runtime execution**. Four existing service-start tests are intentionally deselected, not failures. No full suite or daemon was run. Parent independent review/integration and later service wiring remain outstanding.

The runner sets `PYTHON_DOTENV_DISABLED=1` before Agency imports, strips credential/provider variables, explicitly selects echo, pins absolute worktree source/tests, and uses scratch CWD/data/temp. Python audit hooks deny external socket connections/DNS, `.env` opens and non-scratch SQLite/database opens; loopback is allowed for asyncio infrastructure. Native Ruff is a static child process with sanitized environment, not covered by the Python socket hook. No provider calls or private/runtime configuration reads are part of qualification.

## Issues and scope

- Initial formatter check found only the new fixture's long signature; formatted only the two owned Python files, then reran all final gates above.
- Initial mypy `runpy` exited before runner footer, and native Ruff bypassed Python output redirection. Retained those initial receipts; corrected the scratch runner to use `mypy.api.run` and captured Ruff subprocess output, then reran both gates with complete final receipts. This was harness behavior, not a product failure.
- Only committed paths: `src/agency/butler/context.py`, `tests/test_owner_context.py`, and this handoff. Evidence/harness remain outside the checkout. No install, provider, private config, runtime DB, push, merge, deployment, or other workers' files touched.
