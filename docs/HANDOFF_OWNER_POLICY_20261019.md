# Owner policy handoff — parent completion of timed-out free worker

Base8d0257c; branchfeat/owner-policy-20261019. Free OpenCode/DeepSeek authored the actual policy/context feature after a parent-verified behavioral RED, then its1800secondrunner deadline expired without final qualification/handoff/commit. This is NOT reported as a completed CLI delivery. Parent inspected the full partial policy and tests, corrected two test-only lint issues (unused import/unpacked variable), formatted one owned production conditional, and performed checks below.

Owned production: existing ToolContext gains owner_grant:OwnerExecutionGrant|None=None with TYPE_CHECKING import; new OwnerToolPolicy() exact callable uses real OwnerExecutionGrant/issuing budget, task binding, live/revoked/cancelled/expired/store lifecycle, no metadata authority or mixed beta/privileged handles. Only named READ_ONLY calculator,utc_time,web_search with bounded effective arguments/defaults; no dispatch, grant minting or I/O by policy. Legacy defaults unchanged. Registry/Driver are not wired by this slice.

## Actual parent verification

- Parent repeated original behavioral RED with controlled source-bound/scratch/network-blocked runner:1failed,0collectionerrors at ToolContext unexpected owner_grant. Receipt integration/owner-policy-parent-red/.
- After reading worker output:14ownerpolicytests passed; no production source change needed to make them pass.
- Final after parent style corrections:214focusedtests passed,0failures/errors/skips (ownerpolicy + toolsbase/driver/betaboundary/safelocaltools). Receipt integration/owner-policy-parent-final/result.json, source fingerprints stable.
- Worktree-root Ruff lint/format passed on the three affected Python files.
- Strict mypy win32 and Linux-target checks passed on both affected source files. Native testing was Windows, not Linux.
- Fresh source-bound cold import confirmed actual policy file and ToolContext owner_grant field with dotenv disabled and echo defaults. That direct cold-import command was not an OS sandbox.

The initial free RED wrote synthetic test temporary files under OS Temp instead of requested Hermes scratch. Parent reproduced RED and all acceptance tests using the corrected controlled runner; original receipts retained, not relabeled as sealed. Shared OpenCode orchestration now sets native TMP/TEMP/TMPDIR and offline Lattice path under Hermes scratch, disables dotenv and strips broader credential-like environment names. Those environment controls are not an OS sandbox.

## Remaining

Actual registry direct calls/factory/Driver final-only/forced-final strict owner admission and metered model contexts must be wired and tested by next worker. Final integrated independent review, Python/Node/typing/fullsuite, actual browser/API/provider/restart qualification remain open. No provider calls, beta enablement, push/merge or deployment. Only base.py,policy.py,test_owner_policy.py and this handoff are committed; raw evidence/briefs are not included.
