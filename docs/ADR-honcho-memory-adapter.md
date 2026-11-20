# ADR: Optional Honcho memory mirror

- Status: accepted; merged into `main` at `e44fab2` (2026-11-20)
- Date: 2026-11-20

## Context

The default SMS memory (`src/agency/memory/sms/`) is local, SQLite-backed and
offline-capable. It has no vector search or background reasoning. Honcho
(`plastic-labs/honcho`) provides peer-centric memory, hybrid BM25+vector
retrieval and async derivation, but it is AGPL-3.0, requires PostgreSQL with
pgvector, a deriver worker and an LLM key, and is a separate FastAPI service.

## Decision

Adopt Honcho as an **optional, pinned, external adapter** — never as a fork and
never as an in-process dependency:

1. SMS is always the durable source of truth; every write lands in SQLite.
2. Honcho is a best-effort mirror and preferred read layer when healthy.
3. On Honcho timeout/error, reads fall back to the same principal's SMS rows.
4. One Honcho workspace; one deterministic peer id per principal (the existing
   `PrincipalScope.agent_id` hash).
5. `honcho-ai` is pinned to an exact version (`2.9.0`); the server image must be
   pinned by digest in the deployment example. Upgrades are deliberate,
   evidence-gated PRs — upstream `main` is never tracked.

## Consequences

- The default path gains no new dependency, infrastructure or license change.
- AGPL obligations are isolated to a separately deployed service; see
  `THIRD_PARTY_LICENSES.md`.
- Honcho reads are eventually consistent; tool evidence must not treat them as
  authoritative for authorization.
- Multi-tenant isolation comes from the adapter's peer mapping plus the trusted
  `PrincipalMemoryScope`, not from Honcho's workspace model alone.
