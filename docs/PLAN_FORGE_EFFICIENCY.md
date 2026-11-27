# Plan: faster, more efficient Forge across varied jobs

Eight gaps from the parallel run, each with a concrete implementation:

1. **Route by job class.** `forge/routing.py` maps a job to apprentice/striker, cheap/strong model, tool budget and quorum need. CLI `--class` and Blacksmith work orders carry the route. Biggest time win.
2. **Real reviewer config.** `forge/env_config.py` loads a supplied `.env` into the Forge process before review, so review uses the configured provider instead of echo fallback.
3. **Structured plans.** `forge/planning.py` parses an `Allowed files:` list from `Files:`. Forge fails any change outside that list; unstructured plans stay backward compatible with a caveat.
4. **Project adapters.** `forge/project_adapters.py` centralises dependency reuse (node_modules junction, Python path) for isolated test workdirs.
5. **Managed concurrency.** `forge/concurrency.py` + `forge run` dispatch up to N jobs through one Blacksmith queue with bounded threads; `forge jobs --watch` gives live state.
6. **Runtime verification.** `forge/runtime_verify.py` starts a product and polls real endpoints before evidence is signed.
7. **Same-product integration.** `forge/integration.py` checks changed-path overlap across parallel branches, combines disjoint work, runs the suite and stops short of merge.
8. **Observability.** `forge/observability.py` records phase timings and job traces; reports carry the timings.

Every item has focused tests. Gates: Ruff, Mypy where configured, full pytest, real pipeline on changed paths. Merge via reviewed PR; no deploy.
