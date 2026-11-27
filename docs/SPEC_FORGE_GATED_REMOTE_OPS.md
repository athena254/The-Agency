# SPEC: Forge gated remote merge and GitHub release

Status: owner-authorized scope (2026-11-26). This is a design boundary, not a claim of
production hardening. It changes *what Forge may ask for*, never what it may silently do.

## Authority model

Forge remains a **proposer**. A write to a remote or a release happens only when a named
human explicitly approves it and every gate is evidence-backed. The MCP server still
refuses `forge_ship` (the local fast-forward path); the two new tools are explicit:

- `forge_remote_merge` — fast-forward push to an allow-listed remote.
- `forge_github_release` — annotated tag + GitHub release for a verified commit.

Both are **disabled by default**. `AGENCY_FORGE_ENABLE_REMOTE_OPS=1` must be set in `.env`
before the handlers will do anything. With it unset, `forge_capabilities` reports
`merge: not_authorized`, `release: not_authorized`, and the handlers fail closed.

## Hard preconditions (all required)

1. A **fresh real pipeline run** inside the handler: inspection + security + tests + review.
   Caller-supplied gate strings are never trusted. `changed_paths` and `test_paths` are
   mandatory; no test paths means no gate evidence and the call is refused.
2. Pipeline gate `PASS`. Any sub-gate FAIL blocks the write.
3. A **named human approver** in `approved_by`. Generic identities (`agent`, `forge`,
   `auto`, `system`, `cli`, `bot`, `none`) and empty strings are refused.
4. Allow-listed destination: `AGENCY_FORGE_ALLOWED_REMOTES` for pushes,
   `AGENCY_FORGE_ALLOWED_RELEASE_REPOS` (owner/repo) for releases. Empty list = no writes.
5. Clean working tree; no embedded remote credentials in the report.
6. Fast-forward only. No `--force`, no branch deletion, no ref rewrites.
7. `execute=true` on top of the default dry run. A dry run only returns the exact
   command plan and never touches a remote.
8. GitHub release additionally requires semver-ish `vX.Y.Z` tag, an available and
   authenticated `gh`, no pre-existing tag, and `gh release create --verify-tag`.

## Evidence

Every executed operation appends a JSON proof line (report id, approver, SHAs, gates,
destination) to `.forge-remote-merge-proof.jsonl` / `.forge-release-proof.jsonl` under the
repo root. Reports repeat their caveats; a PASS is never authority to merge, deploy, or
release by itself.

## Still unauthorized

- **Deploy** is not implemented. No service, host, or cluster is touched.
- **Reviewer calibration** is now `ai_quorum_calibrated`, not human-calibrated. After the rubric
  fix, 20 real verdicts were judged by two independent Hermes models (upstage/solar-mini4:free and
  tencent/hy4-flash:free), each at 20/20 agreement with 20/20 inter-judge agreement. Evidence:
  `data/forge_calibration_v2_result.json`. The owner waived the human-labeling requirement on
  2026-11-26; `human_calibration_pending` is therefore no longer advertised. Fixture-only verdicts
  never count as calibration.
- Forge never authorizes a release of *this* repository without the owner rerunning the
  gates at the revision being tagged.

## Reversal

Set `AGENCY_FORGE_ENABLE_REMOTE_OPS=0` (or unset it) to return to the previous all-read
surface. Existing allow-list env vars default to empty.
