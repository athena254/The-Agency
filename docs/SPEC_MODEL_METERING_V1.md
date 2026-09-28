# Model metering V1 — frozen spec (CORRECTED, supersedes draft)

Status: FROZEN. Base `18b9717`. Program `docs/SPEC_FREE_ONLY_DECENTRALIZED_PROGRAM.md`. License MIT (`pyproject.toml:11`).
Scope: request-bound metered adapter on the existing ToolDriver beta path ONLY. No beta ingress enablement, no Butler/orchestrator/Telegram-guard change, no `adapter.py` change. Top-level beta admission stays disabled. Ordinary non-beta behavior byte-identical.
P-IDs: P-1 echo-only tests (echo + fake transport, never live); P-2 worktree is not a sandbox (never `.env*`/`data/`/creds/other worktrees); P-3 tests are not deployment proof (exact outputs + limits reported).

## 1. Owned files (exhaustive — no wider edits)
`docs/SPEC_MODEL_METERING_V1.md` (this spec), NEW `src/agency/llm/metered.py`, `src/agency/tools/driver.py` (strict/beta wiring only), NEW `tests/test_model_metering.py`, `tests/test_tools_driver.py` ONLY where a beta test's trusted setup must construct real metering (safety assertions never deleted). `adapter.py` changes forbidden without coordinator review.

## 2. Verified interfaces (exact)
- Map (verified this run): `tools/base.py:53-72` frozen `BetaPrincipal(telegram_user_id: positive int excl. bool, private_chat: exact bool)`; `telegram/budget_store.py:38,533-599` `reserve_request` (opaque `token_urlsafe(32)`, `repr=False`), `mark_running`/`reserve_model_call->bool`/`finish_request(success: bool)` all exact-capability `_owns`, `close()` revokes, `BudgetUnavailable` fail-closed; `kernel/audit.py:328-437` `claim_intent->bool` (single-flight, pending gate, WAL+FULL verify, write+readback ack, cancel re-raises so pending stays blocking), `append_beta` acknowledged once, outcome matches intent via `evidence.phase='outcome'` + same `call_id`; `llm/adapter.py:113-139` `generate(prompt, context)` is the single seam BUT context CAN override provider/model (`:117-118`, metered path must reject), `stream()` delegates to `generate()`; `tools/driver.py:97,231-234,305-306` loop + forced-final both call `self._llm.generate(prompt, {"task_id","strict": True})`; `tools/registry.py:114-180,258-349` beta intent/outcome audit semantics; `pyproject.toml:11` MIT.
- Draft contradictions fixed: (a) seam is a WRAPPER (`MeteredModel` in `llm/metered.py`), never inside `adapter.py`; (b) principal fields inspected (not unverified); (c) echo/suffix/provider names prove NOTHING about free pricing; (d) no free-only fallback switching — fixed provider/model, zero automatic fallback.

## 3. Frozen binding + MeteredModel (wrapper seam, NOT inside adapter)
- `MeteredBinding`: frozen dataclass `(bot_id, update_id, user_id, capability: opaque str, principal: BetaPrincipal, provider: str, model: str)` built once per admitted `RESERVED->RUNNING` request by trusted integration code from `reserve_request` IDs — never from `ToolContext` dicts, webhook JSON, tool args, or caller provider/model. `repr` MUST NEVER include `capability`.

- `MeteredModel` (`llm/metered.py`): wraps a trusted CONCRETE `LLMAdapter`; requires CONCRETE `BetaBudgetStore` + CONCRETE `BetaAuditLog` (no duck-type durability acks); fixed `provider`/`model` snapshotted at bind; finite positive `timeout_s`. Zero automatic fallback, no echo-success fallback, no live calls in tests (fake transport = monkeypatch the adapter provider method). Provider names / `:free` suffix prove NOTHING about current free pricing; real provider enablement stays separately gated.

## 4. Per-generate rule (one generate = reservation -> pending intent -> bounded dispatch -> durable redacted outcome; at most one provider call)
1. Validate prompt: valid nonempty `str`, else deny before any store call. 2. Reject closed/foreign capability, principal mismatch (`binding.principal != ctx principal`), `private_chat is not True`, any context-supplied binding/provider/model override — all BEFORE dispatch, zero provider calls. 3. `ok = await store.reserve_model_call(bot_id, update_id, user_id, capability=cap)`; `False`/`BudgetUnavailable` -> deny, zero provider calls.

## 5. Failure / retry / audit semantics
- Pending intent (`claim_intent`, redacted metadata only — call_id, provider/model labels, principal user id; NEVER prompt, capability, secrets) precedes dispatch; `False`/raise incl. cancel -> deny, zero provider calls, pending stays blocking (no blind retry/refund). Provider unknown outcome (timeout/cancel/exception/ambiguous/empty shape) -> durable `outcome(result='unknown', redacted error_class)` then `llm_error`; never success-shaped text (B-13 fail-open closed).
- Audit/completion persistence failure → no success claim; suspend further beta work until audit health recovers.
- Bounded dispatch uses `asyncio.wait_for(inner.generate(prompt, {}), timeout_s)` with EMPTY context (kills the `adapter.py:117-118` override hole). Retries BOUNDED: `max_tool_iterations=6` + adapter timeouts only. Missing/stale binding can never degrade to non-beta.

Strict/beta path requires `llm` to be a `MeteredModel` bound to the EXACT same `BetaPrincipal` (equality) AND the SAME concrete audit object as `ctx.audit`; a bare `LLMAdapter`/scripted double is denied before any generate. All loop + forced-final calls flow through `MeteredModel.generate` automatically. Non-beta path (`beta_principal is None`, legacy-open registry) untouched. No global mutable current-user slot. Existing beta denial assertions preserved; only trusted setup may adopt real metering.

## 7. Tests (`tests/test_model_metering.py`; RED then GREEN, real temp quota+audit DBs)
Positive ToolDriver internal path (loop + forced-final reservation count == attempts); quota-denial zero provider calls; missing/wrong capability, principal mismatch, non-private, context override; durability failure BEFORE call (reserve/claim raises) and AFTER call (outcome write raises -> no success); concurrent second generate while pending is blocked; closed store denies; timeout/cancel -> pending outcome + `llm_error`; audit/errors/repr contain no secret/prompt/capability.

## 8. Acceptance
`AGENCY_LLM_PROVIDER=echo AGENCY_LLM_MODEL=echo PYTHONPATH=src`; focused `pytest tests/test_model_metering.py tests/test_tools_driver.py` + `ruff check/format` + `mypy` green; commit owned paths explicitly only after evidence. Reports distinguish internally usable metering from still-disabled ingress.

