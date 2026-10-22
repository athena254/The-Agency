# Historical harness — consolidation note (2026-10-22)

This directory preserves the repaired route-qualification tooling from
`bf214e8fdb673aed21e863e7c1b6d0a917ef1072` and its original ancestor
`97c72f29f3fa7d69421e9d0ab72ccf820f3b3ba3`.

It is **not qualification of the consolidated integration checkout**. Its embedded
165-file source/dependency manifest predates integration baseline `760b9a99`.
Current runtime/service/API source was deliberately not rolled back to make the
historical pins pass. A current-tree invocation must fail closed before Agency
imports; existing baseline-dependent self-check fixtures must not be treated as
proof about the containing, newer checkout.

The original `README.md` is retained as historical provenance. Its old
`daily-harness-20261020` command paths were retired during consolidation and are
**not current execution instructions**. Do not blindly substitute this new
checkout into those commands or regenerate trusted pins during a qualification
run.

## Recover the original context on demand

The branch/ref/history remains preserved even though its permanent worktree is
removed. When separately reviewing this historical harness, create a temporary
checkout at the original reviewed tooling revision:

```bash
git -C C:/Users/alphi/theagency worktree add --detach C:/Users/alphi/agency-worktrees/historical-harness-check bf214e8fdb673aed21e863e7c1b6d0a917ef1072
```

Use that checkout's harness and source paths together, and the reviewed dependency
versions/Windows CRLF assumptions described in the historical README. A later or
mismatched environment may still be refused. This command recovers source; it is
not permission to launch a bot, call a provider or claim current qualification.
Inspect any output/private state before retiring that temporary checkout again;
Git can delete ignored files even without `--force`.

Requalifying current integration requires a separate review of the complete
source/dependency closure, deliberate pin/fixture updates and actual bounded
qualification. This housekeeping task does none of those things.
