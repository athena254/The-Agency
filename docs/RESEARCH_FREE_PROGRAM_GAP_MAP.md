# Research Free-Program Gap Map (recovery, evidence-only)
Base 1e38912. Source: [archived source lookups](research-evidence/free-program-gap-evidence.md); `ev:N` cites a line in that snapshot. No live inspection; uninspected=UNVERIFIED. No missing inferred from silence.

## Status key
IMPLEMENTED=seen in evidence; PARTIAL=seen with gaps; DORMANT=code present but unwired (only if evidence shows); UNVERIFIED=not in evidence.

## What evidence confirms
- Peer envelope primitives IMPLEMENTED: `sign_envelope` / `verify_envelope` in `src/agency/peer_envelope.py` (ev:1077-1078); tested (ev:1084-1099). Transport/membership/replay/grants UNVERIFIED.
- Threads IMPLEMENTED local SQLite: `ThreadStore`, `sqlite3`, tables `workspaces/threads/thread_messages` (ev:1104-1129). HTTP/Telegram guards UNVERIFIED.
- Factory deterministic, no-LLM: `AgentFactory create/validate/approve/activate/revoke`, fail-closed race handling (ev:1135-1166). Restart semantics UNVERIFIED.
- Skills immutable SQLite registry: `register/get/transition/publish` with evidence gate (ev:1172-1203).
- Evidence append-only SQLite with triggers blocking update/delete; `add_finding/get_finding/list_findings/add_evidence` (ev:1215-1256).
- Bridges coordinator with circuit-breaker `allow/record_success/record_failure`, `route_task/route_stream/health_check_all` (ev:1262-1279). Subprocess/shell policy UNVERIFIED.
- Tools beta fail-closed: `ToolDriver` beta_path denial, `beta audit unavailable` handling, LLM `generate` calls (ev:1044-1071, ev:762-764).
- LLM paths IMPLEMENTED: `LLMAdapter.generate` dispatch nous/openai/anthropic/openrouter/pollinations/ollama/echo (ev:714-736); providers `generate` (ev:740-756); Butler `_build_llm_callable` returns `adapter.generate` (ev:775-777); Butler config defaults ollama/llama3.1 (ev:769-773).
- Audit async SQLite via aiosqlite, append-only; beta audit requires disk path, WAL+FULL (ev:1370-1401).
- Lattice governance API surface: `submit_proposal/cast_vote/get_proposal_status/list_open_proposals`, `GovernanceEngine` wrapper (ev:1406-1429). Override/volatile-state semantics UNVERIFIED.
- Inventory: 73 tests (ev:425); file list + line counts confirm modules exist (ev:52-420, ev:431-450). Contents beyond excerpts UNVERIFIED.
- Butler, agents/executor retries, workflows/executor, Forge, memory SMS, risk/security, GUI/prototype, peer authority, recovery: UNVERIFIED in excerpts (only file names seen).

## Checks (CHECK-1..5) — verdict
- CHECK-1 Governance override / volatile vs guards: UNVERIFIED.
- CHECK-2 `reserve_model_call` callers: UNVERIFIED; `LLMAdapter.generate` paths IMPLEMENTED per above.
- CHECK-3 Envelope vs runtime/transport/membership/replay/grants: primitives IMPLEMENTED, rest UNVERIFIED.
- CHECK-4 Workflows/agent retries, persistence, restart: UNVERIFIED.
- CHECK-5 Tests do not prove deployment: respected; no live claim.

## Worker-suggested slices (not approved implementation order)
Reviewer: these suggestions are incomplete. SPEC_FREE_ONLY_DECENTRALIZED_PROGRAM.md is the authoritative dependency/ownership plan. S2 cannot produce BFT by extending the legacy vote counter, and S3 cannot enable beta from tool-level audit alone. No implementation is authorized by this report.

No global orchestrator / shared-SQLite consensus / synthetic votes / invented consensus / paid fallback.
- S1 Owners `peer_envelope.py+tests/test_peer_envelope.py`. Prereq: envelope schema frozen. Layer durable sequence replay tracking and pinned membership in new peers modules; preserve existing envelope signed-byte contract; wire to explicit peer CLI. Fail-inject: replay, wrong key, expired envelope → reject + audit.
- S2 Owners `lattice/api.py+governance.py+backends/sqlite.py`. Prereq: S1. Affected-consumer input mandatory, rejection≠veto (owner rule). Fail-inject: crash peer, duplicate vote → no double-count.
- S3 Owners `tools/driver.py+registry.py+kernel/audit.py`. Prereq: S1. Beta fail-closed already evidenced; wire to peer CLI entrypoint. Fail-inject: LLM down, audit disk full → `Model unavailable/Beta audit unavailable`.
- S4 Owners `factory/service.py+skills/registry.py+evidence/store/store.py`. Prereq: S3 audit. Deterministic publish gates. Fail-inject: revoke-race, empty evidence → fail-closed.
- S5 Owners `butler/server.py+config.py+threads.py+bridges/coordinator.py`. Prereq: S3. Local-first threads/bridges; no shared consensus. Fail-inject: breaker open, thread DB locked → degraded, no silent fallback.

## Owner decisions (fixed)
- Byzantine tolerance (not crash-only). Affected input mandatory but rejection is not a veto. Do not implement global orchestrator authority, shared SQLite consensus, synthetic votes, custom consensus, silent paid fallback.

## Remaining human decisions
- Replay window / key rotation; quorum threshold under Byzantine rule; beta evidence quorum; thread retention; bridge allowlist.

Report: `docs/RESEARCH_FREE_PROGRAM_GAP_MAP.md`
