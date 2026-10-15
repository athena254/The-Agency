# Preserved work in progress — metering

> HISTORICAL CHECKPOINT, superseded October 15, 2026. The candidate was merged in `8fd860d` and consolidated onto `main`. The following prose/test counts describe an earlier WIP snapshot, not current status. Read `CURRENT_STATE.md` and the consolidated verification evidence for the qualified implementation and still-disabled beta ingress.

This is a recovery checkpoint of unfinished free-worker output, NOT a verified
feature or a candidate for main. The completed specification has been merged
separately; these source/test changes intentionally remain on this feature branch.

Latest focused offline check: `9 passed, 3 warnings in 1.75s` on
`tests/test_model_metering.py` (all 9 metering tests green; warnings are benign
leaked-aiosqlite-thread `Event loop is closed` notices). `tests/test_tools_driver.py`
was checked read-only: `2 failed, 23 passed` — both failures pre-exist on the base
commit without these edits (bare `FakeLLM` denied before any generate, so the legacy
beta expectations no longer apply) and that file was intentionally left untouched.
Full log is retained locally at `C:/Users/alphi/AppData/Local/Temp/meter-fix2.log`.
Static gates: `ruff check` clean, `ruff format --check` clean,
`mypy src/agency/llm/metered.py src/agency/tools/driver.py` clean.

Timeout deviation from `docs/SPEC_MODEL_METERING_V1.md` §5: a dispatch
`TimeoutError` now leaves the pending intent unresolved (stays blocking via
`has_pending_calls() is True`) instead of writing a durable `unknown` outcome,
because the provider outcome is genuinely unknown (the call may still complete)
and the test contract requires pending to stay blocking. Cancel/exception/empty
responses still write durable redacted `unknown` outcomes.

No deployment, beta enablement, production data changes or live provider calls.
Tests used `AGENCY_LLM_PROVIDER=echo AGENCY_LLM_MODEL=echo PYTHONPATH=src`.

Before integration: inspect failures, finish missing implementation, independent
review, full test/static gates, and verify request/identity/durability boundaries.
A successful Git commit here preserves work; it does not make the feature complete.
