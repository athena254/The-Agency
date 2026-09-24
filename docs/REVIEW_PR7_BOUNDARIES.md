# PR #7 — independent boundary review (bounded, not an audit)

Source tree at `c940869f96367120109785020dcf67959ba6f189`. A Muse Spark 1.3 read-only review inspected the public Butler HTTP identity path, Forge's read-only filesystem inspection, and legacy repository absorption. The review worktree was verified clean afterward. The reviewer reported no reproducible blocking flaw in the tested paths; this is **not** a security certification.

## Tested behavior

- Spoofed `sender` with `thread_id` on public `POST /v1/message` returned 403 and did not append to the owner's thread; owner-scoped store/service probes rejected cross-owner get/list/append and thread handling. The HTTP handler mints a one-turn anonymous identity and disables memory persistence.
- Forge accepted a bounded regular source file, rejected traversal/absolute/symlink/missing/nonregular/oversized/duplicate paths, and reported syntax-only results. Inspection PASS does not authorize code execution or release.
- Legacy absorber did not commit by default; explicit opt-in is required. Tested symlink rewrite left an external file unchanged and path validation rejected absolute and parent-traversal commit targets.

These probes are evidence for the named cases only. Existing tests are in `tests/test_butler_thread_integration.py`, `tests/test_forge_inspection.py`, and `tests/test_absorption_boundary.py`.

## Limitations and follow-up

- On Windows, read-only inspection and legacy rewriting cannot atomically pin every parent directory while another process replaces it. The code documents this as best effort, **not** a sandbox against a hostile concurrent writer. No merge/deploy/security assurance may depend on this gate alone.
- A service-level probe accidentally reached the configured Pollinations fallback and got HTTP 429; the review was not fully offline. The other bounded filesystem probes did not use network access. No live secret was used in the report.
- The review did not run the full test suite and did not cover the wider Agency API, Telegram trust boundary, deployment configuration, or secrets lifecycle.
- `MessageRequest.sender` is still accepted but ignored on the unauthenticated HTTP path; the field could mislead clients. The inspector's default SQLite report path is CWD-relative, with no production caller found.
- Tests/probes may mutate `data/lattice.db`. The reviewer restored that file in its detached worktree; an independent Git status check confirmed it was clean. This did not touch the owner's `main` worktree.

Conclusion: bounded negative tests did not reproduce the prior identity/path concerns, but remaining Windows race and wider security review must be handled before production exposure. `docs/SPEC_PR7_READINESS.md` remains the acceptance gate; do not mark the draft PR merge-ready based only on this report.
