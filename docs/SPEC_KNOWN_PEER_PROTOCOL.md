# Known-peer decentralized authority protocol — design specification (not implemented)

Status: **design specification for review; not implemented, not deployed, not approved for consequential execution.**
Base revision: `36f41c8`. Scope: this document only (`docs/SPEC_KNOWN_PEER_PROTOCOL.md`).
No code, database, credential, bot, or deployment change is authorized by this document.

Companion documents (read first):

- `docs/SPEC_BETA_BOUNDARY_AND_PEER_EXECUTION.md` — beta safety boundary and Stage 2 checkpoint.
- `docs/SPEC_PEER_LATTICE_GOVERNANCE.md` — peer-lattice governance target contract (supersedes central-governance claims in `SPEC_LATTICE.md`).
- `docs/SPEC_L2_TELEGRAM_BETA.md` — Telegram-first bounded beta contract (note: `docs/SPEC_L2_EXECUTION.md` named in the work brief does not exist in this tree; the L2 contract lives in `SPEC_L2_TELEGRAM_BETA.md`).

## 1. Owner decisions recorded (binding constraints)

1. **Topology first:** separately running, authenticated, known peers, each with its own independent process and durable store, under a versioned membership set. Federated peer-selected membership (local trust groups) is a **later phase** and is not specified here beyond a deferral interface.
2. **Affected-consumer gate:** every affected consumer must contribute attributable input; if any required input is missing, the proposal **cannot execute**. Whether that input is a veto or recorded feedback subject to a threshold is **undecided** (see §10); the fail-closed part (no execution while missing) is decided.
3. **Beta tool surface:** bounded `web_search` only, after all identity, audit, privacy, and budget gates pass. `web_fetch`, memory tools, sandbox execution, agent creation, governance voting, bridges, and L2 capabilities remain off in beta.
4. **Consequential actions off** until this agreement protocol (or a reviewed successor) is approved **and** its acceptance tests pass. This explicitly includes agent creation/revocation, policy changes, decisive votes, side-effecting bridges, and execution authorization.
5. **No global single orchestrator.** Each peer runs a local scheduler that is **non-authoritative** for the network: it may schedule its own read-only work but can never invent ballots, grant mutating capabilities, or decide peer consensus.
6. **Telegram/Butler are ingress adapters only.** They deliver human requests and display status; neither votes for peers, manufactures consensus, nor confers execution authority.
7. **Failure model NOT decided.** The owner did not answer the Byzantine-vs-crash question. This document presents both options with safety/liveness tradeoffs, recommends a security-first posture, but **does not select** a fault number, quorum fraction, or threshold as approved. Until those are decided, §10 items remain `UNDECIDED` and §12 (actuation) stays disabled.

## 2. Plain English

Each peer is an independent computer program with its own identity keys and its own database. When something consequential is proposed (create an agent, change a policy, spend a budget, run an action with side effects), the proposing peer writes down exactly what it wants, who it affects, and what evidence supports it, signs that statement, and sends the identical signed statement to every other peer. Each peer checks the signature, checks that the affected parties were really notified and really replied with their own signed statements, and then independently computes the same yes/no answer from the same evidence using the same deterministic rule. Only when a peer has independently verified a complete, signed proof of agreement — against its own local state — may it perform its local share of the action, exactly once, and write down what it did. If peers cannot communicate, disagree, see different membership, run out of time, or lose their audit store, **nothing consequential happens**: the proposal fails closed. A chatbot frontend and a local task scheduler help humans submit and track requests but can never approve anything by themselves.

## 3. Current gaps this protocol must close (evidence, base `36f41c8`)

These are source observations, not deployment claims. Line references are to the base tree.

1. `src/agency/lattice/governance.py:46-58` — `GovernanceEngine` keeps `_proposals`/`_payloads` in process memory behind a process-local `asyncio.Lock`. There is no cross-process replication, no durable proposal log, no peer identity, no signature.
2. `src/agency/lattice/governance.py:96-155` — `cast_vote` resolves on `approve / (approve + deny)` weight (`_approve_ratio_locked`, `_meets_quorum_locked`). Abstentions are excluded, the denominator is **cast votes only**, and there is no eligible-voter set, no affected-consumer set, and no membership epoch. A single approving ballot therefore reads as 100% and can pass. Re-votes silently replace the earlier ballot (`votes = [v ... if v.voter_id != voter_id]`).
3. `src/agency/lattice/governance.py:144-149` — any ballot with `voter_id.strip().lower() == "user"` and decision approve/deny resolves immediately (`user` override). No authentication, no peer attribution.
4. `src/agency/lattice/governance.py:263-332` — backend mirroring (`_mirror_proposal/_mirror_vote/_mirror_resolution`) is explicitly best-effort (`except Exception ... log.debug(..._skipped)`). A failed mirror still returns success to the caller.
5. `src/agency/lattice/models.py:107-128,203-228` — `Vote` and `ConsensusProposal` have no signer identity binding, no signature field, no membership/policy version, no affected-set snapshot, no monotonic sequence, no idempotency key, no expiry binding. `weight` defaults to `1.0` with no Sybil resistance.
6. `src/agency/lattice/models.py:58-104` — `LatticeEvent` has `event_id/actor/target_id/payload` with no signature, no hash chain, no ordering authority. `actor` is a free-form string.
7. `src/agency/lattice/api.py:430-493` (in-memory `Lattice` shim) — `submit_proposal`/`cast_vote` append to a local dict/list with no identity check; `cast_vote` appends duplicate votes without dedup (contrast the `GovernanceEngine` replace semantic — the two paths disagree).
8. `src/agency/orchestrator.py:71-82` — `AgencyOrchestrator` is a central wiring/authority class: it registers agents, issues permissions, spawns from proposals, and executes tasks in one process.
9. `src/agency/orchestrator.py:117` — `AuditLog(":memory:")` default: restart loses the audit trail.
10. `src/agency/orchestrator.py:173-195,496-501` — tool registry is built without a policy/principal; the tool execution path does not carry a peer grant.
11. `src/agency/orchestrator.py:251-285` — `resolve_agent_proposal` auto-spawns on `status == "passed"` with a once-per-process guard (`_spawned_proposals`), reading payload from best-effort events. No grant validation, no idempotency across restarts, no affected-consumer check.
12. `src/agency/orchestrator.py:594-603` — memory write uses shared `task.created_by` agent scope, not an authenticated owner scope; success can be recorded without peer agreement.
13. `src/agency/api/routers/governance.py:18-30,103-113` — HTTP bearer (`X-Agency-Governance-Token`) maps to fixed `voter_id="user"`, which then triggers the immediate override in (3). Caller-supplied `quorum`/`ttl` are accepted per proposal with no policy floor.
14. `src/agency/api/routers/agents.py:89-138` — `POST /v1/agents`, `DELETE`, and `PATCH .../capabilities` mutate identities with no peer grant and no dependency on the governance router's owner token.
15. `src/agency/kernel/audit.py:131-163,183-228` — SQLite WAL with `synchronous=NORMAL`, plain `INSERT` + `commit`, no `FULL` fsync requirement, no hash chain, no tamper-evidence beyond primary-key collision on `entry_id`. Durability and tamper-evidence are therefore **not** established by the current code.
16. Beta boundary gaps (admission, tool grant, search bound, budget, audit/result) are tracked in `SPEC_BETA_BOUNDARY_AND_PEER_EXECUTION.md:13-23` and are **preconditions** for beta search; they do not authorize peer agreement and are not re-solved here.

No part of this section claims any peer protocol, signature, membership, quorum-intersection, fencing, or multi-peer test exists today. It does not.

## 4. Architecture: what is and is not authoritative

- `PeerRuntime(peer_id, signing_key, local_store, policy_version)` — the **only** entity that can authorize its own local side effects, and only on presentation of a valid `PeerExecutionGrant` (§12) verified against its own local state. One OS process (later: one host) per peer; no shared database file; no shared in-memory dict.
- `PeerLattice` — the replicated **authenticated event history** plus the relationship graph from which affected sets are derived. Local SQLite is each peer's durable cache/view, never the network-wide truth.
- `LocalTaskScheduler` — optional per-peer dispatch for that peer's own read-only work (beta search task loop, retries, timeouts). Non-authoritative: it cannot mint ballots, cannot sign for another peer, cannot resolve proposals, cannot issue grants.
- `TelegramGateway / ButlerService` — transport + human-facing interface. They submit `HumanRequest` inputs (authenticated human preference, §9) and render proposal status, missing inputs, evidence digests, and honest failures. They hold no peer signing keys and cast no peer ballots.
- `Actuator` — the code path that performs a consequential write (agent registry mutation, policy write, bridge call, spawn). It executes **only** on a fully validated grant (§12), persists the grant + idempotency record **before** the side effect, and enforces the fencing token at the resource.

```
HumanRequest (Telegram/Butler, authenticated human)
  -> Proposal (signed, §6) -> replicate -> Ballots (signed, §7)
  -> deterministic local decision (§11) -> DecisionCertificate (§8)
  -> PeerExecutionGrant (§12) -> per-peer Actuator (idempotent, fenced)
```

LLM/model outputs are **evidence or proposals only**. A model completion is never a ballot, never a signature, and never authority. Cryptographic authentication (who signed what bytes) and model-expressed preference (what some model text says) are different layers; the protocol verifies the former and records the latter as evidence subject to §9 redaction.

## 5. Wire protocol: messages, canonical bytes, signatures, replay protection

### 5.1 Envelope (all signed messages)

Every protocol message carries this envelope; the signature covers the canonical serialization of everything except `signature` itself.

| Field | Type | Meaning |
|---|---|---|
| `proto` | `str` | `"agency-known-peer/1"` (reject unknown major). |
| `kind` | `str` | One of `proposal`, `ballot`, `membership`, `decision-certificate`, `grant`, `heartbeat` (liveness only, never authority), `recovery-request`, `recovery-response`. |
| `sender_peer_id` | `str` | Stable peer identity (public-key fingerprint, see §5.4). Must equal the key that signed. |
| `membership_epoch` | `u64` | Versioned membership root this message is issued under (§13). |
| `msg_id` | `str` | `"<sender_peer_id>:<monotonic_seq>"`; globally unique per sender. |
| `monotonic_seq` | `u64` | Per-sender strictly increasing counter, persisted before send. Never reused after restart. |
| `issued_at_ms` | `i64` | Unix millis, sender clock. Used for expiry and skew checks only, never for ordering. |
| `expires_at_ms` | `i64` | After which honest peers refuse the message (except for audit replay). |
| `correlation_id` | `str` | Proposal correlation: `proposal_id` for ballots/certificates/grants; own `msg_id` root for new proposals. |
| `payload_digest` | `hex` | SHA-256 of the canonical payload bytes (§5.3). Receivers recompute and compare. |
| `payload` | `object` | Kind-specific body (§§6–8, 12, 13). |
| `signature` | `object` | `{ "alg": "Ed25519", "key_id": "<fingerprint>", "sig": "<base64>" }` over canonical bytes. |

Validation order on receipt (fail closed, in this order): transport authentication (§15) → `proto`/`kind` known → `membership_epoch` known and not superseded (§13) → signature verifies under the membership-listed key for `sender_peer_id` → `payload_digest` matches recomputed digest → `monotonic_seq` greater than last-seen per sender (else drop as duplicate/replay, still retain for audit) → `expires_at_ms` in the future within skew tolerance → kind-specific validation.

### 5.2 Canonical serialization (deterministic digest input)

- Canonical JSON: UTF-8, object keys sorted lexicographically (byte order), no whitespace, no duplicate keys, numbers as integers only (reject floats in protocol fields), strings as NFC-normalized UTF-8, `null` only where the schema allows, arrays in schema order.
- Digest input = canonical bytes of `{ proto, kind, sender_peer_id, membership_epoch, msg_id, monotonic_seq, issued_at_ms, expires_at_ms, correlation_id, payload }` (i.e., envelope minus `signature`, plus canonical payload). Digest = `SHA-256` hex, lowercase.
- Signature input = the same canonical digest bytes (sign the 32-byte digest, not ambiguous re-encodings). State this explicitly so independent implementations agree.
- Rationale: avoids the current free-form `dict(payload)` ambiguity in `governance.py:92` and `api.py:455`.

### 5.3 Proposal correlation and deterministic digest

- `proposal_id = "prop_" + hex(SHA-256(canonical_proposal_body))[0:32]`, where the body includes `proposer_peer_id`, `membership_epoch`, `action` (§12.1), `affected_snapshot` (§11), `policy_version`, `evidence_manifest` (digests only, §9), `idempotency_key`, `expires_at_ms`, and `supersedes` (optional prior `proposal_id` for conflict chains).
- Correlation: every ballot, certificate, and grant carries `correlation_id == proposal_id`. A ballot for an unknown `proposal_id` is buffered (bounded) or rejected per §14 recovery, never applied to another proposal.
- Conflicting proposals against the same resource/action+idempotency scope create a **conflict set**: honest peers lock on first-valid-proposal per scope per epoch and refuse to sign a conflicting one until the first expires or is denied (see §14). Deterministic digest makes equivocation detectable: two different bodies with the same `proposal_id` is a collision/impossibility; two different `proposal_id`s for the same scope is a conflict to resolve by expiry/deny, never by double-execution.

### 5.4 Identities and signatures

- Peer identity = Ed25519 keypair. `peer_id = "peer_" + hex(SHA-256(public_key_bytes))[0:32]`; `key_id`/fingerprint = full hex of the public key. No pet names, no Telegram IDs, no `sender` strings as peer identity.
- Algorithm: Ed25519, signatures 64 bytes, base64 standard encoding. No algorithm agility in v1 (`alg` must equal `"Ed25519"`); rotation happens by membership update introducing the new key, not by negotiating algorithms per message.
- Key rotation/revocation: only via a signed `membership` message under the rules of §13 (UNDECIDED threshold). A revoked key's messages are rejected after the revocation epoch takes effect; messages signed before revocation remain verifiable for audit but confer no new authority.
- Human inputs (§9) are authenticated by the ingress adapter (Telegram allowlist + owner token semantics as hardened by the beta boundary spec) and then **re-signed by the receiving peer as an attested human-input record** — the human does not hold a peer key and never casts a peer ballot.

### 5.5 Replay, duplication, and ordering protection

- `(sender_peer_id, monotonic_seq)` is the replay key; receivers keep a high-water mark plus a bounded out-of-order window per sender per epoch. `msg_id` collisions, seq reuse, or seq regression → drop + audit (counts toward equivocation evidence, §14).
- At-least-once transport: senders retry unacknowledged messages with the **same** `msg_id`/`monotonic_seq`/bytes; retries are idempotent, never new seq numbers for the same content.
- Wall-clock is never authoritative: `issued_at_ms`/`expires_at_ms` gate freshness with an explicit skew tolerance (proposed default: reject if `|now - issued_at| > 5 min` or already past `expires_at_ms`; exact value is a deployment parameter, not consensus). Ordering for decisions comes from deterministic reduction over the ballot set (§11), not arrival order.
- Cross-epoch replay: a message valid in epoch `e` is invalid in epoch `e' > e` unless explicitly re-issued; certificates/grants bind the epoch (§§8, 12) and verifiers require quorum intersection across epochs for execution (§13.3).

## 6. Proposal message (`kind: proposal`)

Payload fields:

| Field | Meaning |
|---|---|
| `proposal_id` | Deterministic digest per §5.3. |
| `proposer_peer_id` | Must equal envelope sender. |
| `proposal_version` | `u64`, starts at 1; any membership/affected-set change requires a **new version**, i.e., a new `proposal_id` linked via `supersedes`. No silent voter substitution. |
| `action` | Machine-readable consequential action descriptor (see §12.1): `action_type`, `resource`, `params_digest` (params by digest; full params distributed out-of-band and hash-verified, never trusted by reference). |
| `affected_snapshot` | Versioned affected-consumer snapshot + derivation proof (§11). |
| `membership_epoch` | Epoch the proposal is issued under (equals envelope epoch). |
| `policy_version` | Governance policy bundle version string (e.g., `"peer-policy/3"`). Receivers reject unknown/newer-than-local policy. |
| `evidence_manifest` | List of `{ name, sha256, media_type, provenance }` — digests of supporting evidence. Full evidence replicated via `recovery-response` or content-addressed sync; ballots may cite it by digest. **No raw prompts, message content, credentials, or PII in the manifest or payload** (§9). |
| `idempotency_key` | `"<action_type>:<resource>:<uuid>"`, unique per intended side effect. |
| `fencing_base` | Expected current fencing-token value at the resource (see §12.3); `0`/null only for resource-creation actions. |
| `expires_at_ms` | Proposal deadline. Expired proposals resolve to `expired`, never auto-pass. |
| `supersedes` | Optional prior `proposal_id` this version replaces, with reason string. |

Proposer must persist the signed proposal to its local durable log **before** first send.

## 7. Ballot message (`kind: ballot`)

One voter, one proposal version, one signed statement. There is no `user` override, no string caller bypass, no weight-by-default.

| Field | Meaning |
|---|---|
| `proposal_id` / `proposal_version` | Must match a known, unexpired proposal. Ballots for stale versions are rejected (re-vote on the new version instead). |
| `voter_peer_id` | Must equal envelope sender and be in the epoch's eligible voter set. |
| `decision` | `"approve" \| "deny" \| "abstain"`. |
| `affected_role` | `"affected-consumer" \| "peer-voter"` (a voter can be both; the affected flag is what satisfies §11). |
| `evidence_cited` | List of digests from the proposal's `evidence_manifest` this ballot relies on, plus optional ballot-local finding digests. No raw content (§9). |
| `human_input_ref` | Optional digest reference to an attested human-input record (§9) that informed this ballot. The ballot is still the peer's own signed decision. |
| `ballot_seq` | `u64` per (voter, proposal): starts at 1; a higher seq **replaces** the earlier ballot for tally purposes, but **all versions are retained** in the audit trail (contrast the current silent replace in `governance.py:132`). |
| `expires_at_ms` | Ballot expiry; must be `<=` proposal expiry. |

A peer signs at most one *effective* ballot per proposal version (equivocation = two different effective ballots with the same seq, or conflicting seqs without monotonic increase → evidence of misbehavior, §14). Abstentions count for participation accounting but not for approve/deny weight — exact threshold semantics are UNDECIDED (§10).

## 8. Decision certificate and decision evidence (`kind: decision-certificate`)

A decision is **derived locally and deterministically** by each peer from the same signed inputs (§11); the certificate is the portable proof that derivation succeeded.

| Field | Meaning |
|---|---|
| `proposal_id` / `proposal_version` | What was decided. |
| `membership_epoch` | Epoch(s) the deciding ballot set belongs to; cross-epoch decisions list both epochs and both voter sets (§13.3). |
| `outcome` | `"passed" \| "denied" \| "expired"` (never auto-pass on timeout; timeout → `expired` or `denied` per policy, UNDECIDED which — fail closed either way). |
| `eligible_set_digest` | Digest of the eligible voter set + affected set actually used. |
| `ballot_refs` | List of `{ voter_peer_id, msg_id, signature_digest }` for every ballot counted **and** every affected-consumer ballot required. Verifiers fetch/verify each ballot. |
| `tally` | Deterministic tally breakdown: counts/weights per decision, participation list, affected-consumer coverage list. |
| `derived_at_ms` | Local derivation time (informational). |

A certificate is valid for a verifier iff: every referenced ballot verifies (§5), every voter was eligible in the cited epoch, every affected consumer has a valid attributable ballot (§11), the deterministic rule (§11) applied to exactly that set yields `outcome == passed`, the proposal and all ballots were unexpired at decision time, and no conflicting certificate for the same idempotency scope exists locally. Until the failure model and thresholds are decided (§10), **no honest peer treats any certificate as passing for actuation** — certificates can be built and verified in tests, but §12 stays disabled.

## 9. Human input, model votes, and evidence privacy

- **Cryptographic authentication ≠ model preference.** A valid Ed25519 signature proves *which peer* attested to *which bytes*. A model completion proves nothing about authority. Peers never accept model text as a ballot, vote, or signature.
- **Human requests are inputs, not ballots.** Telegram/Butler authenticate the human per the beta boundary (allowlist, private chat, budget, audit) and the receiving peer emits a signed `HumanInputRecord { input_digest, admission_claim_digest, received_at_ms, transport }`. Ballots may cite `human_input_ref` by digest. The human never signs peer messages and the adapter never signs for a peer.
- **Affected-consumer human operators** (if the consumer is human-represented) express preference through the same path: an explicit, attributable input (button/confirmation tied to the authenticated ingress identity), recorded with its admission claim, cited by digest. Natural-language Telegram text is **never** parsed into an auto-generated vote.
- **Evidence retention without disclosure:** retain ballot/proposal/certificate bytes, signatures, digests, admission-claim digests, and outcome records. Do **not** persist raw prompts, user message content, search terms, raw model outputs, credentials, or PII in the peer protocol log, audit trail, or evidence manifest. Where a ballot relies on model-derived analysis, cite the digest of a redacted finding, not the prompt/completion. This matches the beta audit privacy rule (`SPEC_BETA_BOUNDARY_AND_PEER_EXECUTION.md:41`) and extends it to peer traffic.
- **Reputation/provider signals** (if any) are evidence inputs to ballots under a reviewed policy version, never silent vote weights. The current `ReputationEngine.vote_weight` implicit weighting must not carry over without an explicit approved policy.

## 10. Failure model: UNDECIDED — both options with tradeoffs (do not implement thresholds yet)

> **Status: UNDECIDED.** The owner did not select Byzantine vs crash tolerance. Nothing in this section approves a peer count, fault number, or quorum fraction. Consequential execution stays off until these are decided, specified, and tested.

### Option A — Crash-fault tolerance (CFT; e.g., Raft-style)

- Assumes peers fail only by crashing, omitting, or delaying — never by lying, forging, or equivocating (forgery is still prevented by signatures, but the agreement rule does not defend against a legitimate key holder acting maliciously).
- Typical shape: `2f+1` peers tolerate `f` crashes; majority agreement; strong leader or leader-election for liveness. Simpler to build, test, and reason about; good fit if all peers run under one operator with hardware/credential isolation and mutual trust.
- Safety/liveness: safe under crash + partition if the protocol refuses minority-side execution (fail closed); liveness requires a timely majority. A single compromised key holder can violate safety (undetected equivocation if the rule doesn't check for it) — hence unsuitable if peers span trust domains or any peer process is exposed to prompt-injection/model-manipulation risk.
- Reference: [Raft paper (Ongaro & Ousterhout)](https://raft.github.io/raft.pdf).

### Option B — Byzantine-fault tolerance (BFT; e.g., Tendermint-style)

- Assumes up to `f` peers may behave arbitrarily (equivocate, withhold, lie about state) while still holding valid keys — the right assumption if peers span hosts/operators, ingest model-derived content, or face a compromised-process threat.
- Typical shape: `3f+1` validators tolerate `f` Byzantine faults with `+2/3` quorum intersection; multi-round propose/prevote/precommit with lock semantics; equivocation is detectable and attributable (signatures + justification sets). More messages, more complexity, stricter timing/evidence demands.
- Safety/liveness: safe under ≤f Byzantine + partitions if quorum intersection is enforced (no two conflicting decisions can both gather `+2/3` in the same epoch); liveness needs `2f+1` honest and timely peers and careful timeout/round management. Partition without quorum fails closed.
- References: [Tendermint consensus spec](https://docs.tendermint.com/master/spec/consensus/consensus.html), [Tendermint consensus paper (Buchman et al.)](https://arxiv.org/abs/1807.04938).

### Recommendation (posture, not a selection)

Adopt a **security-first posture**: design message formats, audit, fencing, and tests so that either model can be layered on without rework (this spec does that), but **default to the BFT assumption for threat modeling** because peers ingest untrusted model content and run on separate hosts — a compromised peer key/process is in scope per §15. Do **not** treat this recommendation as approval of `3f+1`, `+2/3`, or any peer count; those numbers, the exact algorithm (Tendermint-style rounds vs simplified BFT vs Raft), timeout values, and membership-change rules in §13 remain UNDECIDED and require an explicit owner decision plus the tests in §16.

### What must be decided before actuation (checklist)

- [ ] UNDECIDED: failure class (crash vs Byzantine) and tolerated faults `f`.
- [ ] UNDECIDED: minimum peer count `n` and decision threshold (including affected-consumer semantics: veto vs counted feedback).
- [ ] UNDECIDED: exact agreement algorithm (round structure, leader/proposer selection, lock/unlock, timeout schedule).
- [ ] UNDECIDED: membership-change rule (who can add/remove, under which epoch/threshold, §13).
- [ ] UNDECIDED: timeout/expiry values and clock-skew tolerance.
- [ ] UNDECIDED: policy-version upgrade rule.

## 11. Deterministic decision rule and affected-consumer inclusion proof

### 11.1 Eligible voter set and affected-set snapshot

- Each proposal carries `affected_snapshot = { graph_root_digest, epoch, members: [{ peer_id | consumer_id, role, reason }], derivation }`, where `derivation` names the deterministic function + inputs (relationship-graph edges of types `DEPENDS_ON`/`CONSUMES`/`GOVERNS` as materialized in `PeerLattice`, graph root digest, epoch) that produced the member list.
- The proposer must include a `reason` per member (why this consumer is affected by this action/resource). A bare list with no reasons is invalid.
- Every verifier **recomputes** the derivation from its own local graph view at the cited `graph_root_digest` + epoch, and verifies consumer notification/input evidence. Proposer assertion alone is not proof of completeness. Forks (two different member lists for the same epoch+root) or stale views (epoch/root behind local) → reject.
- Membership changes mid-proposal require a new proposal version (new `proposal_id` via `supersedes`); silent voter substitution is forbidden.

### 11.2 Deterministic reduction (same evidence → same outcome)

Given (proposal, epoch voter set, affected set, valid ballot set, policy version), each peer computes:

1. Filter ballots: valid signatures, eligible voters, matching `proposal_id`+version, unexpired, highest `ballot_seq` per voter effective (all retained for audit).
2. Check affected coverage: every `affected_snapshot` member has at least one valid attributable ballot (decision any of approve/deny/abstain — presence is mandatory; weight/threshold semantics UNDECIDED per §10).
3. Check participation and approval per the decided threshold (UNDECIDED — implement as pluggable policy, default `deny`).
4. Check expiry, conflict-set locks (§5.3), and epoch validity (§13).
5. Output `passed` only if all checks succeed; else `denied` (on decisive deny/quorum-impossible) or `expired` (on deadline). Never default to pass.

### 11.3 Worked fail-closed examples (normative behavior)

- One approval among three peers with two affected consumers silent → **no execution** (missing affected input).
- Single-ballot "100% of cast" → **no execution** (denominator is the eligible + affected set, never cast-only; this reverses the current `governance.py` tally).
- `user`-string approval, Telegram message, Butler assertion, or model completion offered as a ballot → **rejected** (not a peer signature).
- Valid certificate but local graph epoch newer than the certificate epoch with an intersecting membership change → **hold for re-confirmation** under §13.3, no execution meanwhile.

## 12. What authority means for write actuation (execution grant)

A consequential write happens **only** through this path. Until §10 is decided, this entire section is **specified but disabled**: actuators must refuse all grants.

### 12.1 Action descriptor

`action = { action_type, resource, params_digest, params_schema_version }` where `action_type ∈ { spawn_agent, revoke_agent, policy_change, grant_capability, bridge_call, execution_authorize, ... }` (closed set per policy version; unknown types rejected). `resource` names the exact target (agent ID, policy key, bridge endpoint + scope). Full params are content-addressed by `params_digest`; the actuator fetches and hash-verifies them before use.

### 12.2 `PeerExecutionGrant` (`kind: grant`)

| Field | Meaning |
|---|---|
| `proposal_id` / `decision_certificate_digest` | Binds the grant to one decided proposal. |
| `action` | The §12.1 descriptor (must match the proposal exactly). |
| `affected_set_digest` + `policy_version` | Must match the decided snapshot/policy. |
| `voter_certificate` | The §8 ballot refs (or embedded verified ballots for single-round-trip actuation; verifier still checks each signature). |
| `epoch` | Issuing epoch; cross-epoch grants list both and prove intersection (§13.3). |
| `idempotency_key` | From the proposal; actuator keeps a durable `executed_grants` table keyed by it. |
| `fencing_token` | Resource-scoped monotonic token (§12.3). |
| `expires_at_ms` | Short-lived (minutes, not hours); expired grants are rejected even if the certificate was valid. |
| `grant_signers` | The peers that co-signed the grant (threshold UNDECIDED; until decided, no threshold verifies). |

### 12.3 Fencing (resource-side, mandatory)

Each mutable resource holds a monotonic `fencing_token` (counter persisted with the resource). The grant carries `fencing_token = expected_current + 1` (or the creation token for new resources). The actuator, in the **same atomic step** as the side effect, checks `grant.token == stored.token + 1` (or the creation rule), then persists `grant_digest + idempotency_key + resulting token` **before** performing the external call, and bumps the stored token. Stale, replayed, or cross-partition grants fail the token check → rejected. Idempotency per proposal alone is explicitly **insufficient**; intersecting epochs + conflict locks + fencing are all required.

### 12.4 Actuator validation order (normative)

1. Grant signature(s) valid under current/recent epoch keys; grant unexpired.
2. `action` matches proposal exactly; `params_digest` verifies against fetched params.
3. Decision certificate verifies per §8 (all ballots, affected coverage, deterministic rule, no conflicts).
4. Membership epoch(s) valid with intersection across upgrades (§13.3).
5. `idempotency_key` not already executed (durable check first — duplicate → return prior result, no re-execution).
6. Fencing token check at the resource (§12.3).
7. Persist grant + idempotency record, then execute, then persist outcome. Audit append failure at any point → abort and report failure (§14 inverts nothing: no success claim without durable evidence).

## 13. Membership: versioned set, upgrades, revocation (change rule UNDECIDED)

### 13.1 Membership set and epoch

`Membership(epoch, members: [{ peer_id, public_key, roles, joined_epoch }], removed: [{ peer_id, removed_epoch, reason_digest }], policy_version, prev_epoch_digest)`. `epoch` is a `u64` starting at 1 (genesis). Digest = SHA-256 of canonical bytes; peers pin the digest they operate under and include it (`membership_epoch`) in every message.

- Genesis membership is provisioned out-of-band (operator ceremony, keys exchanged over an authenticated channel, each peer configured with the identical genesis digest). There is deliberately **no** in-protocol open enrollment in phase one.
- Phase-one admission/removal is by operator-provisioned update satisfying the UNDECIDED change rule; phase-two federated trust groups are deferred and must not be assumed.

### 13.2 Upgrade mechanics

- A `membership` message proposes epoch `e+1` with `prev_epoch_digest == digest(e)`, a full new member list, and (UNDECIDED) approval evidence under epoch `e`'s rule. Peers validate the chain (`prev` links unbroken), persist both epochs during a transition window, and only retire `e` after the transition rule (UNDECIDED) is met.
- Proposals/ballots in flight under epoch `e` do **not** auto-carry to `e+1`: they either complete under `e`'s rule before the cutoff or must be re-issued as new proposal versions under `e+1`.
- Revocation takes effect at the epoch boundary; in-flight grants under the old epoch are re-validated under §13.3 or refused.

### 13.3 Cross-epoch execution safety (normative)

A grant spanning epochs `e → e+1` executes only if the deciding ballot sets in **both** epochs independently satisfy the decision rule (intersecting quorums) **or** the grant is re-decided fully under `e+1`. A bare majority in the union, or `e`-only approval after the member set changed, is insufficient. Rationale: prevents a removed member's stale approval, or an added member's instant fake majority, from authorizing execution.

### 13.4 UNDECIDED membership change rule (explicitly not specified)

Who may propose a membership change, what threshold approves it, whether affected consumers of membership changes get a veto, how key-compromise emergency revocation works, and the transition/retirement timing are all UNDECIDED. No central membership administrator may silently add a voter — that invariant is decided; the positive rule replacing it is not.

## 14. Partition, timeout, fencing, and fail-closed behavior

- **Partition without valid agreement fails closed.** A side that cannot assemble a complete, verifiable certificate (including all affected inputs) under its pinned epoch executes nothing. Two sides can never both hold valid intersecting certificates for conflicting actions in the same epoch if the decided threshold requires intersection — and until that threshold is decided, neither side executes at all.
- **Timeouts:** proposal expiry → `expired`; ballot collection timeout with missing affected input → `denied`/`expired` (policy picks which; both fail closed). Grant expiry is short; expired grants are rejected even with a valid certificate. Timeout values themselves are UNDECIDED (§10).
- **Equivocation handling:** two conflicting signed messages with the same `(sender, seq)` or two effective ballots for the same proposal version from one voter = attributable misbehavior. Honest peers retain both, refuse to count either toward a pass, flag the peer in audit, and (post-decision) apply the UNDECIDED slashing/removal policy. Never resolve equivocation by "first seen wins" for authority.
- **Conflicting proposals:** per-scope lock on first-valid-proposal (§5.3); later conflicts are denied/buffered until the lock clears by decision/expiry. No double-execution of the same `idempotency_key`; no parallel execution of conflicting scopes.
- **Recovery:** `recovery-request { need_digests[] }` / `recovery-response { messages[] }` with per-message verification identical to live traffic. Reconnect/replay converges state without duplicate side effects because execution is gated on durable `executed_grants` + fencing, not on "have I seen this message." Out-of-order delivery is safe: decisions derive from sets, not sequences.
- **Heartbeat** messages exist for liveness/missing-vote observability only. A heartbeat carries no authority, cites no votes, and can never substitute for a ballot.

## 15. Threat assumptions and trust boundaries (normative)

1. **Network:** asynchronous, adversarial. Adversary can drop, delay, reorder, duplicate, and partition traffic, and can run its own peers with valid keys up to the UNDECIDED fault bound. Adversary cannot break Ed25519, SHA-256, or (assumed) authenticated transport encryption. Transport authentication (mutual TLS with peer-key-bound certificates, or Noise with static peer keys — exact mechanism a deployment decision) is required; signatures alone do not provide confidentiality for evidence payloads.
2. **Process compromise:** any peer process may be fully compromised (key exfiltrated, arbitrary messages signed) up to the fault bound — hence the BFT-leaning posture in §10. A compromised peer must still be unable to forge another peer's signature, rewrite history undetectably (hash-chained audit, §16-adjacent), or execute side effects on another peer's resources (per-peer actuators verify independently).
3. **Model/ingress untrusted:** LLM outputs, Telegram text, Butler routing decisions, and human-provided content are untrusted inputs. Prompt injection, confabulated citations, forged `sender` strings, and replayed Telegram `update_id`s are in scope. None can become a ballot or grant except through the signed, digest-cited paths in §§6–9.
4. **Supply/operator:** genesis key ceremony, binary provenance, host hardening, and secret storage are out of protocol scope but preconditions for any security claim. A compromised genesis ceremony voids all guarantees.
5. **What this protocol does NOT defend against:** Sybil enrollment beyond the closed membership list (no Sybil resistance mechanism is claimed — admission is the perimeter); traffic-analysis; compromised majority above the fault bound; operator coercion; side channels in model hosting. Federated membership (later phase) must add explicit anti-Sybil admission before it activates.
6. **Privacy:** peer traffic contains digests and protocol metadata by default; raw content stays out (§9). Evidence payloads distributed for verification are redacted findings, never prompts/credentials/PII. Operators must inventory every sink (peer log, audit DB, Lattice mirror, Butler log, Telegram replies) before handling real user data.

## 16. Audit durability, ordering, and recovery (per-peer, normative)

Each peer maintains a durable, append-only, hash-chained decision log separate from its state DB:

- Record order: `proposal → ballots (each) → derivation outcome → certificate → grant → idempotency persist → side-effect outcome`. No step is claimed before its persist succeeds; append failure fails the local operation and suspends further consequential work until audit health recovers (mirrors the beta audit rule, extended to peers).
- Durability: per-write `fsync` (`synchronous=FULL`-equivalent), `BEGIN IMMEDIATE` transactions, explicit recovery scan on startup (torn-write detection via chain verification, `UNKNOWN_AFTER_CRASH` reconciliation — never silently re-execute billable/side-effecting work; reconcile via `recovery-request` and the durable `executed_grants` table).
- Tamper-evidence: each record includes `prev_record_digest`; genesis record pins the membership digest. Verification replays the chain; any gap/rewrite is detectable. The current `synchronous=NORMAL` + no-chain implementation (§3.15) does **not** satisfy this.
- Ordering: local log order is per-peer; network-wide order is unnecessary because decisions derive from ballot sets (§11), not arrival order. Certificates cite exact ballot sets so any auditor can re-derive.
- Retention: bounded only by an explicit pruning policy that never removes undecided proposals, unexpired grants, `executed_grants` keys, membership history, or equivocation evidence. Dedup/replay windows sized to exceed maximum grant/proposal lifetimes.

## 17. Design alternatives considered (with primary references)

- **Fixed-membership BFT validator set (Tendermint-style rounds).** Explicit quorum-intersection safety, equivocation accountability via justification sets, mature spec. Cost: multi-round messaging, timeout/round complexity, `3f+1` sizing. Best fit if the Byzantine assumption is chosen. Refs: [consensus spec](https://docs.tendermint.com/master/spec/consensus/consensus.html), [Buchman et al. 2018](https://arxiv.org/abs/1807.04938).
- **Crash-fault consensus with leader election (Raft-style).** Simple majority, strong-leader log replication, well-understood liveness. Cost: leader is a liveness/availability bottleneck and a confused-deputy risk if confused with network authority; no Byzantine safety. Only viable under Option A with all peers under one trust domain. Ref: [Raft paper](https://raft.github.io/raft.pdf).
- **Federated quorums (Stellar-style).** Each peer chooses its trust slices; agreement via quorum intersection over federated slices. Permits local trust choice (matches the deferred federated phase) but risks a small de-facto top tier and needs careful intersection analysis per configuration. Deferred to phase two. Ref: [federated quorum analysis (García-Pérez & Gotsman)](https://link.springer.com/article/10.1007/s00446-022-00430-0).
- **Eventual replication / CRDTs alone.** Good for convergent state (relationship graph cache, status display) but cannot authorize conflicting consequential mutations — concurrent approvals can diverge with no principled deny. Rejected as the authority mechanism; acceptable as a dissemination layer beneath the agreement rule.
- **MLS (RFC 9420) for group keying.** Useful for authenticated group membership/key evolution and transport-adjacent concerns, but MLS is **not** a consensus or governance algorithm and does not replace §§8–13. Ref: [RFC 9420](https://datatracker.ietf.org/doc/html/rfc9420). Possible future complement, not a substitute.

## 18. Phased migration from `GovernanceEngine` and API routes (no parallel-file workers)

No source implementation is authorized by this spec. When (and only when) the §10 decisions are made, migrate in this order with one owner per slice:

1. **Types/contracts first:** introduce `peer_id`, envelope, canonical serialization, digest, and membership-epoch types plus redaction helpers in new modules; leave `GovernanceEngine`, `Vote`, `ConsensusProposal`, and all routers untouched and still authoritative-off.
2. **Per-peer log + verification:** add the durable hash-chained log (§16) and pure verify/derive functions (§§5, 8, 11) with unit tests; no wiring to executors or routes.
3. **Adapter shims (read-only):** mirror proposals/ballots to the new log for observation; `GovernanceEngine` remains the (disabled-for-consequential) path. Prove byte-compatibility of digests across two implementations.
4. **Actuator gating:** put grant validation + fencing + idempotency in front of each consequential write (`agents` mutations, governance resolution, bridge calls); default-deny with the feature flag off. Legacy routes (`/v1/governance` user-vote, `/v1/agents` open mutations, orchestrator auto-spawn) are inventoried and disabled-or-gated — Telegram admission alone never protects them.
5. **Ingress separation:** Telegram/Butler emit `HumanRequest`/`HumanInputRecord` only; remove every path where a `sender` string, `user` voter ID, or model completion can reach a ballot or grant. Delete the `user` override; do not leave it behind a flag.
6. **Cutover:** only after §19 A–C pass — retire `GovernanceEngine` tally/override for consequential actions behind the new decision path; keep it (if at all) for explicitly non-consequential local polls with a different endpoint name so the two are never confused.

Interface changes sequence types → consumers → integration tests; no parallel workers editing the same files.

## 19. Acceptance: staged hermetic and multi-host tests + negative tests

Consequential execution stays off until **all** of A–C pass after the §10 decisions, plus D before any product wiring. All tests hermetic where stated (no live network/DNS/model; injected transports/resolvers/keys; `AGENCY_LLM_PROVIDER=echo` where a model stub is needed — model output is never authority).

**A. Hermetic local peer simulation (≥3 separate peer processes/stores, ≥2 affected consumers; Butler stopped):**

- Propose a provider change; prove all affected peers see the identical immutable proposal (digest equality), contribute independent attributable ballots/evidence, and derive the identical outcome from the same evidence.
- Prove determinism: shuffle delivery order, duplicate every message, restart one peer mid-vote — same derived outcome, no duplicate side effects, convergence after heal.
- Negative tests (each must fail closed, with no execution and an auditable refusal): one-ballot pseudo-quorum; missing affected consumer; forged signature; duplicate/replayed ballot (`monotonic_seq` reuse); stale `membership_epoch`/`policy_version`; tampered payload (`payload_digest` mismatch); ballot for unknown/stale proposal version; expired proposal/grant; equivocating voter counted toward pass; `user`-string/Telegram-text/model-completion offered as ballot; audit-append failure injected at each of proposal/ballot/certificate/grant persist points; conflicting proposals double-executing the same `idempotency_key`.

**B. Fault/partition proof (same topology, adversary harness):**

- Partition into 2+1 with no valid agreement on either side → prove **no** split-brain execution on either side; heal → converge without duplicate side effects.
- Delay/reorder/replay storms; kill -9 one peer mid-grant-persist → restart → `UNKNOWN_AFTER_CRASH` reconciliation with no re-execution of completed side effects and no loss of `executed_grants`.
- Revoke a peer mid-proposal (under the decided change rule) → in-flight items complete-or-hold per §13, new epoch required for new authority; cross-epoch stale grant refused by fencing + intersection.
- Conflicting proposals on the same resource scope → exactly one outcome executes at most once; the other is denied/expired with evidence retained.
- Real transport identity review (mutual authentication with peer-bound keys) independent of mock-transport tests; mock tests alone do not satisfy B.

**C. Multi-host deployment proof:**

- Same as A–B across ≥3 hosts with real keys, real transport auth, independent clocks (skew injected), and independent stores; wall-clock skew beyond tolerance fails closed rather than auto-passing.
- Operator ceremony test: genesis digest mismatch on one host → that host refuses all authority traffic until re-provisioned.
- Standard gates on the exact head: full `pytest`, `ruff check`, `ruff format --check`, `mypy`, `git diff --check`, independent security review.

**D. Product wiring (only after A–C):** Telegram/Butler show proposal digest, affected parties with reasons, per-voter status, missing inputs, evidence digests (never raw content), and honest failure text. No synthetic votes; human input only through explicit attributable confirmation. Controlled rollout needs owner approval plus live proof.

## 20. Open questions (UNDECIDED — require owner decision; not answered by this spec)

1. Failure class and tolerated faults (Byzantine vs crash; values of `f`, `n`).
2. Exact agreement algorithm (round structure, proposer selection, lock/unlock, timeout schedule).
3. Decision thresholds: participation floor, approve fraction, and whether affected-consumer input is a per-member veto or counted feedback.
4. Membership-change rule: who proposes/approves add/remove/rotate, emergency revocation path, transition timing.
5. Timeout, expiry, and clock-skew values; policy-version upgrade rule.
6. Genesis ceremony operators and emergency out-of-band coordination channel.
7. Evidence retention windows and pruning policy for the peer log.
8. Transport choice (mutual TLS vs Noise) and certificate/key-storage provisioning.

---

*Authoring notes: `docs/SPEC_L2_EXECUTION.md` (brief reference) does not exist at base `36f41c8`; `docs/SPEC_L2_TELEGRAM_BETA.md` was used instead and the substitution is recorded here. No deployed features, test results, or live evidence are claimed. All code citations verified against the base tree; no source files were modified by this spec.*
