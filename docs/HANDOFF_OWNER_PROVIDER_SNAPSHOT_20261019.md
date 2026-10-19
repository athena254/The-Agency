# Owner provider snapshot handoff — October 19, 2026

Base: `02599f71fde501330b5d7cbd3f50792662a29fd9`; branch `feat/owner-provider-snapshot-20261019`.
Independent adapter slice only: no admission imports/stubs or runtime/metering wiring.

## Delivered API

```python
@dataclass(frozen=True)
class LLMDispatchSnapshot:
    provider: str
    model: str
    base_url: str
    api_key: str = field(repr=False)
    max_tokens: int
    temperature: float
    timeout: float

LLMAdapter.freeze_dispatch(self) -> LLMDispatchSnapshot
async LLMAdapter.generate_bound(self, prompt: str, snapshot: LLMDispatchSnapshot) -> str
```

Import directly from `agency.llm.adapter`. No other file needs to export it.

- Construct an explicit concrete `LLMConfig` or pass explicit adapter constructor arguments. Owner mode accepts only `openai` (OpenAI-compatible JSON endpoint) and offline `echo`. It does not reinterpret OpenRouter/Nous/other legacy provider names. Use provider `openai` with an explicitly selected compatible endpoint instead.
- An owner adapter must set `timeout` explicitly to a finite **float** greater than zero and at most `45.0`; the legacy default `60.0` intentionally cannot freeze. `temperature` must be an exact finite float in `[0.0, 2.0]`; `max_tokens` an exact positive int, never bool. Text fields require exact strings. Model IDs are preserved, not stripped/coerced; empty/whitespace-containing/control-containing IDs are rejected.
- OpenAI dispatch requires an explicit nonempty printable ASCII credential, without whitespace/header injection, and an explicit HTTPS URL. HTTP is accepted only for literal loopback IPs or exact `localhost`, for synthetic tests. Userinfo, queries (including empty `?`), fragments (including empty `#`), whitespace, backslashes, malformed host/ports and external HTTP are rejected. Echo requires empty endpoint/key strings.
- For direct constructor arguments, owner provenance records whether provider/model/key/endpoint were explicitly supplied, separately from unchanged legacy environment/default resolution. Missing/empty source arguments cannot become owner-ready via environment fallback. Typed config values are treated as explicit caller configuration; `freeze_dispatch()` itself never loads configuration or reads environment. A config already resolved by its caller remains that caller's responsibility.
- Freeze copies current effective fields and validates them. Dispatch accepts only the exact snapshot class, revalidates a private copy, and captures provider/model/key/endpoint/limits plus request headers/payload before `AsyncClient.__aenter__`. Later adapter/environment changes cannot change the actual transport request. `trust_env=False`, `follow_redirects=False`; redirects and HTTP errors propagate, without a retry or fallback.
- Prompt and decoded text output are capped at **65,536 UTF-8 bytes**, including echo output. Nothing is truncated. The existing OpenAI `choices[0].message.content` text parser/JSON tool protocol is preserved. The cap is on returned text, not the entire HTTP JSON envelope; the existing nonstreaming HTTPX response protocol remains in use.
- Echo retains the synchronous `_echo_generate(prompt)` patch convention used by existing tests. Ordinary `generate`, `stream`, provider methods and context overrides are unchanged. Snapshot repr hides the key; do not serialize `asdict(snapshot)` or log exceptions/provider response bodies. This slice is configuration binding, **not** admission authorization or host-administrator tamper resistance.

## Verification (actual execution)

Raw logs: `C:/Users/alphi/agency-evidence/limited-autonomy-20261019/provider-snapshot/`.
Isolated runner: `C:/Users/alphi/AppData/Local/hermes/cache/scratch/agency-build-20261019/provider-snapshot/run_checks.py`.
All checks use `C:/Users/alphi/theagency/.venv/Scripts/python.exe`, scratch CWD/data/temp/cache, source-only worktree `PYTHONPATH`, explicit echo environment, credentials removed, `PYTHON_DOTENV_DISABLED=1`, explicit scratch `LATTICE_SQLITE_PATH`. Pytest denies external socket connects; only the exact stdlib Windows socketpair bootstrap caller is permitted for asyncio's wakeup pipe. HTTP tests construct real HTTPX clients/requests with `MockTransport` and a controlled context-entry await; no provider/network call was made.

Vertical TDD slices (retained raw logs, not reconstructed):

| Slice | RED | GREEN |
| --- | --- | --- |
| Immutable independent snapshot / secret repr | `red-01`: 1 failed (missing freeze) | `green-01`: 1 passed |
| Exact source/value/endpoint/provider validation | `red-02`: 81 failed, 8 passed | `green-02`: 89 passed |
| Bound HTTP dispatch across suspended client entry / echo convention | `red-03-feature`: 3 failed, 89 passed (missing bound method) | `green-03`: 92 passed |
| Bounded input/output and exact snapshot dispatch | `red-04-feature`: 12 failed, 121 passed | `green-04`: 133 passed |

Final `focused-final.log`: **231 passed** across `test_owner_provider_binding.py` (133 cases), existing adapter/wiring/router tests, model metering, metering uncertainty, and pilot metering leaf tests. Covers saved bound-call dispatch, adapter and environment mutation before/during entry, exact HTTP URL/auth/model/payload/timeout, malformed bodies/nontext, 64 KiB ASCII/multibyte boundaries, redirects/status/transport failure, cancellation, no retry/fallback, legacy provider leaves and legacy echo context/stream behavior.

- `ruff-check-final.log`: all checks passed (both changed Python files).
- `ruff-format-check-final.log`: both files already formatted.
- `mypy-win-final.log` and `mypy-linux-final.log`: strict success, 3 files each (adapter, config, new tests), no new ignores or typing suppression. Linux is a mypy platform target on this Windows host, not a native Linux execution claim.
- `cold-import.log`: fresh subprocess imports the exact worktree adapter and confirms the three signatures above.

Initial harness failures remain preserved: `red-03.log` blocked Windows asyncio socketpair (corrected runner guard); `red-04.log` had oversized automatic pytest IDs exceeding Windows environment limits (explicit short IDs fixed); `focused-01.log` had 229 passes plus one failure/one setup error because legacy wiring expects a scratch `data/` directory (created there, no repository/runtime data touched). Initial lint and test-only mypy findings were fixed, with originals retained in their logs.

## Remaining gates / integration notes

Parent runtime worker must freeze once at owner binding creation, use the immutable identity fields for its binding/audit, and call `generate_bound` only after existing concrete owner admission/quota/audit checks. Never route owner calls through legacy `generate` or context overrides. This worker did not wire MeteredModel/admission/driver/service/API and did not create private admission stubs.

No full suite (parent baseline already supplied), independent fresh-context review, native Linux tests, live-provider qualification, installation, push, merge or deployment was performed. Parent owns these later integrated gates. Only approved repository files changed: `src/agency/llm/adapter.py`, new `tests/test_owner_provider_binding.py`, and this handoff.
