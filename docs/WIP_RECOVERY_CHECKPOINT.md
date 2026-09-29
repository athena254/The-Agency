# Preserved work in progress — recovery

This is a recovery checkpoint of unfinished free-worker output, NOT a verified
feature or a candidate for main. The completed specification has been merged
separately; these source/test changes intentionally remain on this feature branch.

Latest focused offline check: `37 passed in ~2s` (`tests/test_local_recovery_v1.py`
plus `tests/test_workflow_registry.py` with `AGENCY_LLM_PROVIDER=echo
AGENCY_LLM_MODEL=echo PYTHONPATH=src`). `ruff check`, `ruff format --check`,
and `mypy` are clean on `src/agency/workflows/executor.py`,
`src/agency/workflows/recovery.py`, `src/agency/workflows/registry.py`.

No deployment, beta enablement, production data changes or live provider calls.
Tests used `AGENCY_LLM_PROVIDER=echo AGENCY_LLM_MODEL=echo PYTHONPATH=src`.

Before integration: inspect failures, finish missing implementation, independent
review, full test/static gates, and verify request/identity/durability boundaries.
A successful Git commit here preserves work; it does not make the feature complete.
