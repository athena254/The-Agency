# Research — Building the Full Lattice, Peer Authorization and the Orchestrator

Status: **research and design report.** No production code, no commits, no deployment.
Date: 2026-11-17. Base branch: `feat/dual-tui-agency-forge`.

This document consolidates five research streams:

- **A** — deterministic application on CometBFT v0.38.x (`scratch/lattice-research/A-consensus.md`)
- **B** — peer identity, transport authentication, networking (`scratch/lattice-research/B-transport.md`)
- **C** — non-authoritative orchestrator (`scratch/lattice-research/C-orchestrator.md`)
- **D** — governance and cryptography (`scratch/lattice-research/D-governance.md`)
- **E** — requirements digest of the repo's own specs and code (this repo, `docs/`)

Where a claim comes from a vendor's own documentation it is marked **[self-reported]**.
Where I could not verify something it is marked **[uncertain]**.

---

## 1. Executive summary

**The policy is settled; the implementation is not.** The owner has already approved D-01…D-05:
4 validator peers, tolerate 1 Byzantine peer, 3/4 application approval quorum, epoch-rooted
membership, signed task-scoped grants with nonce and expiry, and local-only fixture resources
(`docs/OWNER_DECISIONS_D01_D05.md:9-58`). `docs/CURRENT_STATE.md:116-124` says it plainly:
*"The policy is approved; the implementation is missing."*

**What genuinely exists today** (verified, not claimed):

- A **CometBFT 0.38.26 four-process loopback lab** that commits, tolerates one dead peer,
  halts under two, catches up on restart, and cleans up (`docs/COMET_LAB_VERIFIED_STATUS.md`).
- A substantial **pure/type-level peer protocol** in `src/agency/peers/`: canonical JSON,
  closed-schema validation, Ed25519 identities, signed envelopes for eight message kinds
  (including grant with nonce/fencing), versioned membership with epoch + root, and a
  3-of-4 `PeerAuthority` with replay and expiry checks.
- A **durable per-peer hash-chained log** with WAL and `synchronous=FULL` (`peers/log.py`).
- A **single-process Lattice** with a working SQLite backend, hash-chained events and
  process-local leases.
- A 1,188-line **composition root** (`orchestrator.py`) wiring kernel, agents, memory,
  evidence, risk and bridges.

**What is missing** — the actual work:

1. **Transport.** `peers/gossip.py` is explicit: *"Anti-entropy sync is in-process only
   (no network)."* Nothing speaks to another process.
2. **A consensus application.** The lab runs CometBFT's built-in `persistent_kvstore`,
   not an Agency state machine. There is no ABCI app.
3. **Replication of signed peer history** across independent processes.
4. **Affected-consumer and human attestation** — the D-03 gate.
5. **Certificates / commit proofs** derived from engine finality.
6. **Grant enforcement at an actuator** (fencing token, idempotency key, revocation).
7. **The HTTP grant verifier** that would replace `api/authority.py`'s unconditional 503.
8. **A decomposed, non-authoritative orchestrator.**

**The one-sentence architecture:** the engine orders opaque records and proves finality;
the application re-derives policy from signed evidence; the actuator verifies a grant and
performs one atomic conditional write; the orchestrator only launches, leases and
reconciles — it never approves.

---

## 2. Target architecture

```
L7  Ingress          Butler / Telegram / UI        adapters only — no vote, no validator key
L6  Control plane     orchestrator / supervisor     lifecycle: launch, restart, lease, reconcile
L5  Actuator         effect + fence                one atomic conditional write, exactly once
L4  Authorization    grant + revocation           signed, task-scoped, nonce, expiry, fencing
L3  Policy           quorum + affected-set         epoch/root-bound, participation + approval
L2  Application      deterministic state machine   re-validates everything after ordering
L1  Ordering         CometBFT 0.38.26 BFT          >2/3 voting power, instant finality
L0  Transport        authenticated channels        CometBFT P2P (STS) / Noise_XX / mTLS
```

The load-bearing rule, stated in `docs/SPEC_PEER_LATTICE_GOVERNANCE.md:11`:

> "No central authority: no Butler, one orchestrator process, one privileged `system`/`user`
> string, database writer, or hidden super-agent can unilaterally authorize an affected-peer
> decision."

And the orchestrator's rule, from `docs/SPEC_LATTICE.md:534`:

> "`src/agency/orchestrator.py` is current local wiring, not a separate target authority.
> Schedulers are replaceable and cannot grant permissions or manufacture peer approval."

---

## 3. Consensus layer (stream A)

### 3.1 ABCI++ in v0.38

CometBFT is the ABCI **client**, the app is the **server**; CometBFT always initiates.
Messages live in `proto/tendermint/abci/types.proto`.

| Method | Determinism | Notes |
|---|---|---|
| `PrepareProposal` | **MAY be non-deterministic** | proposer only; must not touch committed state |
| `ProcessProposal` | **MUST** | status depends only on request + last committed state |
| `ExtendVote` | **MAY** | app may return an extension |
| `VerifyVoteExtension` | **MUST** | accept/reject |
| `FinalizeBlock` | **MUST** | state + `tx_results` depend only on last committed state + block |
| `Commit` | persists | `retain_height` non-deterministic; **never** call `broadcast_tx_*` here (deadlock) |
| `CheckTx` | weak filter only | runs against `CheckTxState`, not execution state |

Sequence at height *h*: `PrepareProposal` (proposer) → `ProcessProposal` (all) →
`ExtendVote`/`VerifyVoteExtension` → `FinalizeBlock` (all) → `Commit` (all).
A proposer's `PrepareProposal` is always followed by its own `ProcessProposal`.

**Forbidden inside the app:** randomness, wall-clock time, floating point, hash-map
iteration order, library version drift, I/O, filesystem, network. Use the request's
BFT `time`, never `time.Now()`. `UNKNOWN` from `ProcessProposal` or
`VerifyVoteExtension` makes CometBFT crash.

Sources: `spec/abci/abci++_app_requirements.md`, `abci++_methods.md`,
`abci++_comet_expected_behavior.md`.

### 3.2 The state machine

`state(h+1) <- Execute(state(h), App, block(h))`. The app owns its state DB; CometBFT
owns block/state/evidence/indexer stores. `Header.AppHash` is the app's Merkle root and
CometBFT cannot validate it — which is exactly why app-hash mismatches halt the chain.

- `FinalizeBlock` **must not persist**; `Commit` persists before returning.
- On restart, `Info` drives replay; a mismatch panics with
  `block.AppHash does not match AppHash after replay`.
- Validator updates at height H take effect at H+2; consensus-param updates at H+1.
- `ResponseFinalizeBlock.app_hash` (v0.38 moved it here from `ResponseCommit`).
- v0.38.0 is state-machine-breaking; upgrades are coordinated and versioned.

### 3.3 Ordering vs policy — the separation that matters

CometBFT orders transactions identically on all correct nodes. It does **not** validate
policy. A Byzantine proposer can include invalid transactions, so the app must re-check
the exact ordered block in `ProcessProposal` (reject ⇒ prevote nil) and then execute
deterministically in `FinalizeBlock`.

**Consequence for this project:** BFT commit is *not* authorization. Three matching
app-level approvals are not a commit proof, because:

1. `ProcessProposal` ACCEPT is unsigned, local, and never gossiped.
2. It is not voting-power weighted.
3. It is not committed in any header.
4. Non-validators can call it.
5. A Byzantine proposer's own app can accept its own block.

The correct construction: the engine orders *opaque signed records*; each peer
independently re-derives policy from those records; a **commit certificate** (>2/3 voting
power on a specific block) is the finality proof that a grant must cite.

### 3.4 Four peers on one Windows host

`cometbft testnet --v 4` creates `node0..node3` with distinct `node_key.json` (P2P
identity) and `priv_validator_key.json` (consensus signing key), and a shared
`genesis.json` with all voting power 1. Because `testnet` assigns one P2P port to all
nodes, ports must be re-edited per node.

| Node | p2p | rpc | proxy_app |
|---|---|---|---|
| 0 | 26656 | 26657 | 26658 |
| 1 | 26666 | 26667 | 26668 |
| 2 | 26676 | 26677 | 26678 |
| 3 | 26686 | 26687 | 26688 |

Set `persistent_peers` to the other three `ID@127.0.0.1:<p2p-port>`, with
`addr_book_strict=false` and `allow_duplicate_ip=true` (same-IP peers). Bind everything to
`127.0.0.1`. Use `--proxy_app=persistent_kvstore` only for the engine lab; the real
application will be an ABCI app (socket or in-process). Blocks start after the 3rd node.

**[uncertain]** Binding four nodes to distinct `127.0.0.x` addresses on Windows was not
verified; the table above is CometBFT's documented "Local Network" scheme.

### 3.5 Engine comparison

| Engine | Fault model | Tolerates | Leader | App interface |
|---|---|---|---|---|
| CometBFT v0.38 / Tendermint | BFT, partially synchronous | <1/3 Byzantine | round-robin | ABCI++ 2.0 |
| Raft / etcd / Consul | CFT | <1/2 crash, **0 Byzantine** | stable | native K/V |
| HotStuff | BFT, partially synchronous | <1/3 Byzantine | chained/rotating | library |
| Narwhal + Tusk/Bullshark | BFT, async/partially sync | <1/3 Byzantine | DAG rounds | mempool + consensus |

**Recommendation (agrees with `docs/RESEARCH_FREE_PROGRAM_BFT.md:17-20`):** use a
maintained BFT engine as the ordering sidecar. Do **not** write custom Python consensus —
it repeats the failure modes already present in `lattice/governance.py` (single lock,
in-memory state, best-effort mirror).

---

## 4. Transport and identity (stream B)

### 4.1 Correction worth noting

CometBFT does **not** use Noise. Its P2P layer uses a custom **Station-to-Station (STS)**
handshake over TCP: ephemeral X25519, a Merlin (Keccak) transcript, HKDF-SHA256 and
ChaCha20-Poly1305. Versions ≤0.32 were malleable; the Merlin transcript was added to fix
non-malleability. So if we ride on CometBFT's P2P, we inherit STS, not Noise.

### 4.2 Choices

- **Identity:** Ed25519 (RFC 8032), deterministic nonces, 32-byte keys, 64-byte
  signatures. CometBFT adopts **ZIP-215** verification rules — pin one rule set and test
  vectors, because edwards25519 has cofactor 8 and implementations historically diverged.
- **Peer ID:** derive from the key (`SHA256(pubkey)[:20]` for Ed25519), never an arbitrary
  name. A mismatch is detected by recomputing the hash.
- **Key separation:** transport identity key, consensus signing key, application signing
  key. CometBFT already separates `node_key.json` from `priv_validator_key.json`.
- **Custody:** an online software key is a single point of exfiltration. For a system
  meant to survive host compromise, use an HSM/remote signer or threshold signing
  (FROST, RFC 9591, has a `FROST(Ed25519, SHA-512)` ciphersuite).
- **Pinning, not TOFU:** pin fingerprints out-of-band (genesis file, trusted channel).
  Treat any key change as an alarm requiring explicit re-approval.

### 4.3 Transport options

| Option | Auth | Revocation | Cost |
|---|---|---|---|
| CometBFT P2P (STS) | mutual, key-derived | peer eviction at epoch | free if we use the engine |
| Noise_XX (rev 34) | mutual, CA-less | app-level | lean; we build pinning |
| mTLS 1.3 (RFC 8446) + private CA | certificate-based | CRL/expiry | PKI lifecycle |

For the first milestone, **reuse CometBFT's P2P** — it is already there and already
authenticated. Add Noise or mTLS only if the application traffic leaves the engine's
scope.

### 4.4 Envelopes

- Canonical JSON: the repo already has `peers/canonical.py` (strict integer-only,
  `MAX_MEMBERS=4`). Prefer **RFC 8785 (JCS)** semantics; never sign raw protobuf or a
  library's `json.dumps`.
- Every message binds: `version`, `sender`, `chain_id`, `epoch`, `membership_root`,
  `height/round/type`, `timestamp`, monotonic `seq`, `payload`, `signature`.
- **Epoch binding is the replay defence:** a vote valid under two membership roots is a
  replay vulnerability. Bind the canonical membership root into every signature.
- Store the signed vote **before** broadcasting, or a restarted validator can sign a
  second conflicting vote.

---

## 5. Peer authorization design (stream D + repo)

### 5.1 The flow

```
1  proposal      proposer builds body;  proposal_id = H(canonical(body))
2  affected-set  versioned snapshot, one reason per member, derived from the graph
3  ballot        one per (voter, proposal_version); signed envelope; monotonic seq
4  quorum        participation floor AND approval supermajority over eligible+affected
5  certificate   engine commit proof (>2/3 power) + tally
6  grant         signed, task-scoped, nonce, expiry, fencing token, idempotency key
7  actuator      verify grant + finality + revocation, then ONE atomic conditional write
```

### 5.2 Quorum arithmetic

Three **separate, versioned** parameters — not one "quorum" number:

1. **Participation floor** — minimum weight that must engage.
2. **Approval supermajority** — minimum affirmative weight.
3. **Abstain/against counting rule.**

`docs/SPEC_KNOWN_PEER_PROTOCOL.md:253` is explicit:

> "Single-ballot '100% of cast' → **no execution** (denominator is the eligible + affected
> set, never cast-only)."

A cast-only denominator is wrong because it has no anti-apathy floor, it rewards silence,
the denominator can move during the vote, and it creates a minority-veto surface.
Fix the denominator at a **snapshot** and bind the vote to it cryptographically.

Thresholds: CFT needs `2f+1`; BFT needs `3f+1` (Lamport–Shostak–Pease: agreement is
solvable iff more than two-thirds are loyal). D-01 fixes `n=4, f=1, Q=3`.

**Liveness budget:** `N − Q` is the number of peers that may be silent before the system
stalls. For 4 peers at Q=3 that is **one**. Publish it; never lower Q to hide it.

### 5.3 Equivocation

`docs/SPEC_KNOWN_PEER_PROTOCOL.md:161`:

> "A peer signs exactly **one** ballot per proposal version. Ballots are **not replaceable**."

On two differing ballots from one peer for one `(epoch, slot)`:

- **Append both** verbatim to the evidence log — the signature pair *is* the proof.
  Silently replacing A with B destroys the evidence and creates a first-seen race in
  which replicas commit to different "first" ballots depending on arrival order.
- **Mark the peer equivocating**, exclude it from the quorum and from grant eligibility.
- **Void grants** that depended on its vote.
- **Gossip the pair.**

The current code does not do this: `peers/authority.py:185,211` lets a higher `ballot_seq`
overwrite the stored ballot. That is the single most important correctness gap in the
existing peer layer.

### 5.4 Membership, epochs, revocation

- Every ballot and grant commits to `(chain_id, epoch, membership_root, proposal_hash)`.
  Reference patterns: CometBFT `ValidatorHash`/`NextValidatorHash`; Ethereum
  `genesis_validators_root` folded into the signature domain.
- Reconfiguration must satisfy **quorum intersection** (old ∩ new ≥ f+1 honest). Never
  allow disjoint adjacent quorums. `docs/SPEC_KNOWN_PEER_PROTOCOL.md:13.2-13.3`.
- Two distinct revocations: **voter revocation** (a membership reconfiguration) and
  **grant revocation** (checked at the actuator, at dispatch time, fail-closed).

### 5.5 Threshold signatures — do not adopt yet

BLS and FROST aggregation pay off only when aggregating many signatures on one message
(commit certificates). At `n=4` they add a build-time dependency, rogue-key/PoP machinery
and a subtle risk: FROST signatures are **non-deterministic**, unlike Ed25519.
**Recommendation:** keep individual Ed25519 votes at this scale; retain the full ballot set
for the slashing window.

### 5.6 Grants and the actuator

A grant is signed, task-scoped, and carries: `nonce`, `expiry`, `fencing_token`,
`idempotency_key`, `authority_lineage`, `proposal_id`, `action_digest`, `epoch`, `root`.

`docs/SPEC_KNOWN_PEER_PROTOCOL.md:283`:

> "In the **same atomic operation** as its idempotency check and effect, the resource
> requires `stored_token == expected_current` … performs the effect once."

So the actuator performs exactly one atomic conditional write: compare the stored fencing
token, advance it, record the dedupe key, and perform the effect — all in one transaction.
Revocation is checked at that moment, not at issue time.

---

## 6. Orchestrator design (stream C)

### 6.1 The governing invariant

> The orchestrator may **launch, restart, pause, cancel, re-drive and reconcile**
> processes. It must **never** grant permission, widen scope, assign a risk tier, or
> approve an action.

This is standard practice, not a preference: decision, policy and execution are separate
components, and the executor re-checks authorization immediately before a side effect.
Production policy examples state that no automated signal may "grant, widen, or infer a
permission or approval", and that a missing risk tier fails closed to the higher tier.

**Consequence:** because no decision of record depends on a particular orchestrator being
alive, the orchestrator can be replaceable, leaderless and crashable. The Saga literature
warns the opposite design "can become a single point of failure because it controls the
entire workflow" — that is what we design away.

### 6.2 What to borrow and what to avoid

| Engine | Borrow | Avoid |
|---|---|---|
| Temporal / Cadence | event-sourced history + replay; determinism discipline; durable timers; task queues | running a cluster; no I/O in workflow code; replay tests |
| Restate | journal + quorum append defines "the step happened"; epoch fencing | another runtime to operate |
| Inngest | stable step IDs as memoization keys; built-in flow control | dedupe windows expire; step-ID changes alter live runs |
| DBOS | checkpoint written **in the same DB transaction** as app data | scale bounded by Postgres; "exactly-once" stops at DB-local steps |

**Avoid "exactly-once" marketing.** Exactly-once *delivery* over an unreliable network is
impossible. What exists is exactly-once *effect* = at-least-once delivery + idempotent
processing. And never use a workflow ID as the idempotency key for an external effect.

### 6.3 The outbox — why a local SQLite write cannot commit an external effect

SQLite's atomic commit is scoped to its own file. An HTTP call, broker accept or message
send is a separate system with its own commit boundary. Write-then-send loses the event
on crash; send-then-write produces a phantom effect; send-write-crash is the Two Generals
problem.

**Rule:** a local transaction can make a business write and a *record of intent* atomic.
It cannot make a business write and an *external effect* atomic. So:

```
BEGIN IMMEDIATE;
  UPDATE task SET state='DISPATCHED' WHERE id=:id AND state='PENDING';
  INSERT INTO outbox (event_id, topic, payload) VALUES (...);
COMMIT;
-- relay publishes; on ack, DELETE FROM outbox WHERE id=:id;
```

The relay uses `SELECT ... FOR UPDATE SKIP LOCKED` so multiple relays can coexist.

### 6.4 Crash semantics

Intent state machine:

```
PENDING ──dispatch──> DISPATCHED ──known success──> SUCCEEDED
   │                      │
   │                      └── known rejection──> FAILED
   └── timeout / crash / ambiguous ──> UNKNOWN_AFTER_CRASH (stays PENDING)
```

**Never optimistically execute an uncertain effect.** On restart, `UNKNOWN_AFTER_CRASH`
must be **reconciled at the resource** before any retry. This is exactly what the repo
already specifies: *"UNKNOWN_AFTER_CRASH never optimistically executed"*
(`docs/RESEARCH_FREE_PROGRAM_BFT.md:15`).

### 6.5 Leases, fencing, and replaceable supervision

- A supervisor is a **lease holder, not a master**. Any correct process can take over.
- Fencing token: monotonic, checked atomically at the resource so a stale supervisor
  cannot act after a lease transfer. Raft terms are fencing tokens; consensus-backed
  tokens are the stronger version.
- **Leaderless by default.** If leadership is needed, elect it, but make the *state* the
  authority, not the leader.
- To stop two supervisors double-dispatching: the dispatch claim must be an atomic
  compare-and-swap (`state='PENDING' -> 'DISPATCHED'` returns 0 rows ⇒ someone else got it).

### 6.6 Scheduling

Bounded queues, dependency graphs, backpressure, cancellation with drain, and due-work
indexing so the scheduler does not scan everything. Waiting must not hold a slot —
use durable timers.

---

## 7. Build order

Aligned to `docs/SPEC_PEER_LATTICE_GOVERNANCE.md:25-30` slices A–D, which are
**"not yet passed"**.

### Slice A — freeze the protocol contract *(design only, no production code)*

- Canonical schemas for: proposal, affected-snapshot, ballot, membership, certificate,
  grant, intent, outcome, reconciliation, executed-grant, recovery.
- Signature and digest rules; epoch/root binding; closed-world validation.
- Quorum arithmetic as three versioned parameters.
- API surface and error enums.
- Adversarial review of the contract.

**Acceptance:** the contract is written, reviewed, and committed. Nothing runs.

### Slice B — engine application + hermetic peer simulation

- An ABCI++ application over CometBFT's KV store holding the Agency state machine.
- At least **three separate peer runtimes/stores**, at least two affected consumers.
- Verify all affected peers see the same immutable proposal, contribute independent
  ballots, and derive the same decision **with no Butler in the loop**.
- Negative gates: one ballot, forged vote, duplicate replay, missing required consumer,
  or backend audit failure must **not** approve.

**Acceptance:** both positive progress and genuine refusal of invalid input. The repo's
own prior research warns: *"a harness that always refuses is insufficient"*
(`docs/RESEARCH_FREE_PROGRAM_BFT.md:30`).

### Slice C — network and fault proof

- Partition peers; reorder/delay/replay; restart a node; revoke a peer; introduce
  conflicting proposals.
- Verify no split-brain execution, convergence after heal, durable attribution,
  deterministic fail-closed behaviour.
- Verify transport identity and authorization **independently of tests that use mocks.**

### Slice D — product wiring

- UI/Telegram show affected parties, status, evidence and real explicit human input.
- No synthetic user/Butler ballots.
- The HTTP grant verifier replaces `api/authority.py`'s 503.
- Only after A–C. Controlled rollout requires owner approval and live proof.

### Supporting work (can proceed in parallel, isolated)

- Decompose `orchestrator.py` into a lifecycle control plane + a durable intent store.
- Real network transport for `peers/` (currently in-process only).

---

## 8. Decisions I need from you

These are explicitly open in the specs, which say *"No defaults are invented here"*
(`docs/SPEC_LATTICE_FOUNDATION_REPAIRS.md:75`). I will not pick them for you.

| # | Decision | Why it matters |
|---|---|---|
| 1 | Fault bound and validator power | Determines `n` and every threshold |
| 2 | Membership admission / key recovery | How a peer joins or is replaced |
| 3 | Application approval and override rules | What can override a rejection, and who |
| 4 | Affected-graph derivation | Which consumers must be consulted |
| 5 | Independently verifiable human input | The D-03 gate; a gateway signature is explicitly insufficient |
| 6 | Deterministic committed-time / deadline rules | Needed for deterministic replay |
| 7 | Transport limits and operator recovery powers | Blast radius of a lost host |
| 8 | D-05 numeric ceilings | Still unpinned (`OWNER_DECISIONS_D01_D05.md:51`) |
| 9 | Evidence retention and pruning horizon | Dispute window and storage |

Already decided and **not** open: Byzantine tolerance, 4 peers, 1 fault, 3/4 approval,
epoch-rooted membership, signed task-scoped grants, local-only resources.

---

## 9. Risks and rejected alternatives

**Rejected** (`docs/RESEARCH_FREE_PROGRAM_BFT.md:38-42`):

- **Custom Python BFT** — repeats the existing `governance.py` failure modes; no rounds,
  locks or intersection; unauditable.
- **Raft / CFT majority** — crash-only; an equivocating adversary breaks safety.
- **Single-peer / Butler / Telegram / `user`-string authority** — violates
  no-central-authority.
- **Local-ledger exactly-once, first-seen-wins, cast-only quorum** — contradicts the spec.

**Risks to design against:**

- **Correlated failure.** Four peers on one host share an admin, clock and network. `f=1`
  assumes independent faults; the specs are explicit that single-host tests do not prove
  independent-host resilience.
- **Non-determinism in the app.** A single `datetime.now()` or dict-order dependence makes
  all correct peers effectively Byzantine and halts the chain on an app-hash mismatch.
- **Equivocation handling.** The current `PeerAuthority` silently replaces ballots — this
  must be fixed before anything is called decentralized.
- **"Engine works" ≠ "authorization works".** The CometBFT lab proves engine operation
  only, and the repo says so repeatedly.

---

## 10. Reference map

**Repo (authoritative):**
`docs/OWNER_DECISIONS_D01_D05.md`, `docs/SPEC_KNOWN_PEER_PROTOCOL.md`,
`docs/SPEC_PEER_LATTICE_GOVERNANCE.md`, `docs/SPEC_SINGLE_HOST_LATTICE_MILESTONE.md`,
`docs/SPEC_LATTICE.md`, `docs/SPEC_LATTICE_RESOURCE_LEASES.md`,
`docs/SPEC_LATTICE_FOUNDATION_REPAIRS.md`, `docs/SPEC_BOUNDED_AUTONOMY_V1.md`,
`docs/RESEARCH_FREE_PROGRAM_BFT.md`, `docs/RESEARCH_FREE_PROGRAM_GAP_MAP.md`,
`docs/COMET_LAB_VERIFIED_STATUS.md`, `docs/COMET_LAB_COORDINATOR_REVIEW.md`,
`docs/SPEC_COMET_ENGINE_LAB.md`, `docs/CURRENT_STATE.md`, `docs/KNOWN_ISSUES.md`.

**External (verified in streams A–D):** CometBFT v0.38 ABCI++ spec and protobuf,
`spec/core/data_structures.md`, `spec/consensus/{consensus,bft-time,signing,evidence}.md`,
`spec/p2p/legacy-docs/peer.md`; RFC 8032 (EdDSA), RFC 8785 (JCS), RFC 8446 (TLS 1.3),
RFC 9591 (FROST), RFC 9380 (hash-to-curve), RFC 7748 (X25519), RFC 8439 (ChaCha20-Poly1305),
Noise Protocol rev 34; Temporal/Restate/Inngest/DBOS documentation; AWS transactional
outbox prescriptive guidance; Snapshot/Solana/Compound/OpenZeppelin quorum design.

Full external citations are in the four stream reports under
`scratch/lattice-research/`.
