# Historical worktree material retained during consolidation

These are historical artifacts, not current runtime specifications or fresh test
passes. See `docs/WORKTREE_CONSOLIDATION_20261022.md` for the inventory, backup and
retirement rules.

## Owner-core older test

`owner-core-20261019/test_owner_runtime.py.txt` preserves the previously untracked
test verbatim as inactive text. Current integration's active
`tests/test_owner_runtime.py` already contains all its assertions **plus** the
explicit non-null admitted task-ID assertion. The stronger active test is not
replaced by this older copy. Its original recovery checkpoint is
`f9c4af0ea5fcca03997919a8a8d848d1ae38a402`, verified directly from the repository.
The fresh inventory/bundle also preserves the original bytes and provenance.

Its accompanying tracer brief is retained verbatim at
`workspace/owner_core_tracer_red_20261019.md`.

## Other historical notes and evidence

- `workspace/daily_driver_briefs/owner_policy_green.md`: worker implementation
  brief/history; its filename is not current-source or integration approval.
- `workspace/policy-red-evidence/junit.xml`: historical **RED** result, not a
  passing current run. The ignored `raw.log` is preserved in the private external
  retirement archive and in the verified encrypted machine backup, not exposed by
  this housekeeping commit.
- `workspace/pilot_ui_build_20261019.md` and
  `workspace/pilot_ui_research_20261019.md`: historical build/research briefs,
  not permission to execute their old instructions now.
- `workspace/daily-route-qualification/`: repaired **historical, baseline-bound**
  tooling. Its consolidation note explains why it must not qualify the newer
  containing checkout without a separate source/dependency review.

Neither dirty launcher candidate is merged or modified. Their known divergent
validation behavior and the older daily service's missing publication fence are
recorded in the consolidation decision document. Both remain unqualified.
