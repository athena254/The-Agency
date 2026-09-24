# Muse Spark 1.3 task — Agent Factory v1

Read `docs/SPEC_AGENT_FACTORY_V1.md`, the preserved source brief, `src/agency/kernel/identity.py`, both kernel and runtime registries, `src/agency/skills/registry.py`, and `src/agency/workflows/registry.py` before editing. Build exactly the bounded factory specified in the spec.

Own only `src/agency/factory/__init__.py`, `src/agency/factory/service.py`, and `tests/test_agent_factory.py`. Do not modify existing files or commit. Use SQLite with explicit version and lifecycle; no code execution or LLM calls. Inject independent trusted `authorizer(actor, permission)`, published skill/workflow lookup callbacks, kernel and runtime registries. Fail closed on missing callbacks for requested references. No capabilities above L0 and no permission grants. Validate that approved identity creator/approver differ; test restart, denial, duplicate version, pins, revocation. Catch race/restart ambiguity before activation, or document a fail-closed limitation.

Run focused tests and Ruff, report actual output and limitations. Do not claim this is a full autonomous factory.