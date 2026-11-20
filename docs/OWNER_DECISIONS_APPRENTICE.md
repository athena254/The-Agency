# Owner decisions — Apprentice capability (P0)

Date: 2026-11-20. Authority: owner instruction "Use opencode and implement it"
(2026-11-20, Telegram) adopting the proposals in `PLAN_APPRENTICE_CAPABILITY.md` §7.
Implementation proceeds via Forge striker jobs (opencode primary, Hermes one-shot fallback),
gates judging each job, owner approving integration. No beta/peer-authority enablement.

| # | Decision | Adopted value |
|---|---|---|
| 1 | Rung order | A1 → A2 → A3, sequential; each rung its own frozen contract + jobs |
| 2 | Initial tool allowlist | calculator, utc_time, web_search (read-only). sandbox_exec DEFERRED — separate explicit owner decision required |
| 3 | Striker dispatch | Apprentices may NOT dispatch strikers; Blacksmith only |
| 4 | Concurrency | 1 active apprentice step per job; ≤3 roles assigned (researcher/implementer/reviewer) |
| 5 | Trust progression | No automatic trust raise in v1; ceilings ∩ existing trust governs; trust changes stay on the existing human-reviewed path |
| 6 | Grant lifetime | Job-scoped; lapses at job terminal state; hard cap 24h |

Additional frozen invariants for all rungs:
- Promotion = new blueprint version; approver ≠ creator; evidence recorded; superseded
  version revoked. Capability ceilings above L2_CONTROLLED_TESTING are refused in v1.
- Default activation remains L0-only when no ceilings are declared (backward compatible).
- All apprentice file access is worktree-scoped, protected-path-refused, bounded, audited.
- Handoff content is quoted untrusted data; it never carries instructions or authority.
- Human ship approval unchanged; PASS is evidence only.