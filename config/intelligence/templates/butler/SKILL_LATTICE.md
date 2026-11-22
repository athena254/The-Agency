---
type: SKILL_LATTICE
schema_version: 1
---

# Skill — Lattice, peers, grants and consensus

**What exists today (foundation, verified).**

- **Peer identities**: Ed25519 per-runtime identities in protected identity
  stores (`src/agency/peers/identity_store.py`).
- **Signed envelopes**: all eight peer message kinds, bound to receiver,
  membership epoch/root and caller nonce (`src/agency/peers/`).
- **§12 grants**: signed, task-scoped, nonce/expiry-bounded, 3-of-4 pinned
  signer threshold, revocable per epoch; durable SQLite grant store;
  `grant_guard`/`fencing` verify at the point of use with per-resource
  fencing tokens and idempotent finality receipts.
- **Consensus engine**: CometBFT 0.38.26 (pinned), binary provenance
  recorded; a live 4-node agency-net lab passed fault scenarios (node stop,
  restart catch-up, quorum-loss stop, restore, fresh commit).
- **State machine**: deterministic ordered-block application state
  (`src/agency/consensus/app_state.py`) with canonical replay.
- **Assignment journal** (`src/agency/autonomy/assignment_journal.py`):
  atomic state+outbox commits, generation fencing, UNCERTAIN reopen
  transitions (never auto-resumed).

**Owner-approved policy (D-01–D-05)**: 4 validator peers, tolerance of 1
Byzantine peer, 3/4 application quorum, CometBFT 0.38.26 pinned, signed
task-scoped grants with nonce/expiry/revocation, finality proof at
actuators, affected-set declaration with attributable consumer input,
explicit human attestation for overrides, local-only resources, bounded
numeric ceilings, the Inception mercury-3 runtime route, no confidential
data, no paid fallback, 30-day evidence retention.

**What does NOT exist yet — never imply otherwise.**

- Live decentralized peer authority (P2): the missing core milestone is
  human → Butler → independently authorized peers → harmless task → durable
  result. The lab is evidence, not production authority.
- Real ballot/human-attestation verification, affected-set derivation,
  external-resource reconciliation, crash-uncertainty handling.
- The Lattice UI page is a single-process prototype, not consensus.

**Guidance.** When a user asks about multi-agent authority: explain grants
(a signed permission to do exactly one bounded task, checked again at the
actuator), epochs (revocation windows), and fencing (an executed grant
cannot silently run twice). Point operators to `SPEC_KNOWN_PEER_PROTOCOL.md`
§18 and `docs/CURRENT_STATE.md` for the migration path.
