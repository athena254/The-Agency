# Known-peer decentralized authority protocol — design specification (not implemented)

> **Owner decision update (2026-09-28):** Byzantine/compromised-peer tolerance is now selected. All affected consumers must give attributable input; missing input blocks execution, but a rejection is **not** an individual veto and may be overridden by the approved peer decision rule. These decisions supersede historical `UNDECIDED` statements about fault *class* and veto semantics below. Fault count, validator count, exact engine/algorithm, application thresholds, membership changes and human-input proof are still unapproved; actuation remains disabled. See [SPEC_FREE_ONLY_DECENTRALIZED_PROGRAM.md](SPEC_FREE_ONLY_DECENTRALIZED_PROGRAM.md). This update is documentation, not an implementation or deployment claim.

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

Every protocol message carries this envelope. The signature does **not** cover a re-serialization of the message: it covers `signed_bytes`, which is a versioned prefix plus `envelope_digest` — and `envelope_digest` is the hash of the canonical envelope with `signature` (and `envelope_digest` itself) removed (§5.2).

| Field | Type | Meaning |
|---|---|---|
| `proto` | `str` | `"agency-known-peer/1"` (reject unknown major). |
| `kind` | `str` | One of `proposal`, `ballot`, `membership`, `decision-certificate`, `grant`, `heartbeat` (liveness only, never authority), `recovery-request`, `recovery-response`. |
| `sender_peer_id` | `str` | Stable peer identity (public-key fingerprint, see §5.4). Must equal the key that signed. |
| `membership_epoch` | `u64` | Membership epoch number the sender operates under (§13). Never accepted alone: it must agree with `membership_root`. |
| `membership_root` | `hex` | SHA-256 of the canonical membership set (§13.1) the sender is operating under. A root the receiver has not pinned, or a second root for an epoch the receiver already pins → reject + audit, never "pick one". |
| `msg_id` | `str` | `"<sender_peer_id>:<monotonic_seq>"`; globally unique per sender. |
| `monotonic_seq` | `u64` | Per-sender strictly increasing counter, persisted before send. Never reused after restart. |
| `issued_at_ms` | `i64` | Unix millis, sender clock. Used for expiry and skew checks only, never for ordering. |
| `expires_at_ms` | `i64` | After which honest peers refuse the message (except for audit replay). |
| `correlation_id` | `str` | Proposal correlation: `proposal_id` for ballots/certificates/grants; own `msg_id` root for new proposals. |
| `payload_digest` | `hex` | `SHA-256(canonical(payload))` — the payload bytes alone (§5.2). Receivers recompute and compare. |
| `envelope_digest` | `hex` | `SHA-256(canonical(header_projection))` (§5.2). The projection excludes `signature`, `envelope_digest` itself, and `payload` bytes; it includes `payload_digest`, which binds the payload. |
| `payload` | `object` | Kind-specific body (§§6–8, 12, 13). |
| `signature` | `object` | `{ "alg": "Ed25519", "key_id": "<fingerprint>", "sig": "<base64>" }` over `signed_bytes` (§5.2), never over a re-encoding of the message. |

Validation order on receipt (fail closed, in this order): transport authentication (§15) → `proto`/`kind` known → `membership_epoch` + `membership_root` resolve to one locally pinned, non-superseded membership version and are mutually consistent (§13; competing roots for the same epoch → reject + audit, no grant on either root) → signature verifies over the versioned signed bytes (§5.2) under the membership-listed key for `sender_peer_id` → `payload_digest` and `envelope_digest` recompute and match → `monotonic_seq` duplicate/replay check with the bounded out-of-order window and gap set (§5.5) → `expires_at_ms` within skew tolerance → schema validation that rejects unknown, duplicate, and noncanonical fields (§5.2) → kind-specific validation.

### 5.2 Canonical serialization (deterministic digest input)

- Canonical JSON: UTF-8, object keys sorted lexicographically (byte order), no whitespace, no duplicate keys, numbers as integers only (reject floats in protocol fields), strings as NFC-normalized UTF-8, `null` only where the schema allows, arrays in schema order. Every message schema is **closed**: unknown fields, duplicate keys, and noncanonical encodings cause rejection, never silent omission or last-write-wins merging.
- `payload_digest` = `SHA-256(canonical(payload))` — hashes the payload alone.
- `envelope_digest` = `SHA-256(canonical({ proto, kind, sender_peer_id, membership_epoch, membership_root, msg_id, monotonic_seq, issued_at_ms, expires_at_ms, correlation_id, payload_digest }))` — every envelope field **except** `signature` and except `envelope_digest` itself (no self-reference). The payload is bound through `payload_digest`, so payload bytes are never re-encoded for the envelope hash.
- Signed bytes (versioned, exact): `signed_bytes = "agency-known-peer/1|sig-v1|" || envelope_digest_bytes` — the ASCII prefix, then the raw 32-byte digest. Ed25519 signs `signed_bytes` exactly: one fixed byte string built from the 32-byte digest, never a re-encoding of the message object. The prefix versions the signed-byte encoding; `proto` is additionally inside `envelope_digest`. Independent implementations must agree byte-for-byte; a future encoding change requires a new `proto`/sig version, not a per-deployment interpretation.
- Receivers recompute `payload_digest`, `envelope_digest`, and the signature over `signed_bytes`; any mismatch, unknown field, duplicate key, or noncanonical form → reject + audit.
- Rationale: avoids the current free-form `dict(payload)` ambiguity in `governance.py:92` and `api.py:455`.

### 5.3 Proposal correlation and deterministic digest

- `proposal_id = "prop_" + hex(SHA-256(canonical_proposal_body_without_proposal_id))[0:32]`. The hash covers **every immutable proposal payload field except the self-referential `proposal_id`**: `proposer_peer_id`, `proposal_version`, `membership_epoch`, `membership_root`, `action` (§12.1), `affected_snapshot` (§11), `policy_version`, `evidence_manifest` (digests only, §9), `idempotency_key`, `fencing_base`, `expires_at_ms`, and `supersedes` (optional prior `proposal_id` for conflict chains). The derived `proposal_id` is then added to the signed payload and verified by recomputation. `proposal_version` and `fencing_base` are included explicitly because omitting them would let a version bump or a fencing change masquerade as the same proposal. Any other immutable field not covered by the ID is a spec bug; unknown/duplicate/noncanonical proposal fields are rejected (§5.2) so they cannot vary after the ID is computed.
- Correlation: every ballot, certificate, and grant carries `correlation_id == proposal_id`. A ballot for an unknown `proposal_id` is buffered (bounded) or rejected per §14 recovery, never applied to another proposal.
- Conflicting proposals against the same resource/action+idempotency scope create a **conflict set**. Deterministic digest makes equivocation detectable: two different bodies with the same `proposal_id` is a collision/impossibility; two different `proposal_id`s for the same scope is a conflict. **How a conflict set is ordered and resolved — which conflicting proposal wins, the round structure, any lock/unlock rule, and what constitutes the final commit certificate — is algorithm-dependent and UNDECIDED (§10).** There is deliberately **no** first-observed/first-valid authority: arrival order never decides a conflict, and no honest peer locks on, or grants authority for, "the first proposal it happened to see". Until a conflict-resolution algorithm is selected and tested, peers may sign at most one proposal per scope, refuse to sign further conflicts for that scope, buffer the rest, and **issue no grant** (§12). Once a decision for a scope is committed under the selected algorithm it is final: later ballots, later-arriving messages, and later conflicting proposals can never change it; a conflicting attempt is recorded as evidence (§14), never resolved by double-execution.

### 5.4 Identities and signatures

- Peer identity = Ed25519 keypair. `peer_id = "peer_" + hex(SHA-256(public_key_bytes))[0:32]`; `key_id`/fingerprint = full hex of the public key. No pet names, no Telegram IDs, no `sender` strings as peer identity.
- Algorithm: Ed25519, signatures 64 bytes, base64 standard encoding. No algorithm agility in v1 (`alg` must equal `"Ed25519"`); rotation happens by membership update introducing the new key, not by negotiating algorithms per message.
- Key rotation/revocation: only via a signed `membership` message under the rules of §13 (UNDECIDED threshold). A revoked key's messages are rejected after the revocation epoch takes effect; messages signed before revocation remain verifiable for audit but confer no new authority.
- Human inputs (§9) are authenticated by the ingress adapter (Telegram allowlist + owner token semantics as hardened by the beta boundary spec) and then **re-signed by the receiving peer as an identity-bound attested human-input record** binding the authenticated ingress identity, admission claim, and content digest. The receiving peer's signature attests *that this human input was ingested*; it never converts the input into a peer ballot. The human does not hold a peer key and never casts a peer ballot (§9).

### 5.5 Replay, duplication, and ordering protection

- `(sender_peer_id, monotonic_seq)` is the replay key. Per sender per epoch, receivers keep three things: the high-water mark (highest contiguous accepted seq), a **bounded out-of-order window** above it, and a **bounded gap set** of missing seqs inside/behind that window. Handling: an already-accepted seq, a `msg_id` collision, or seq regression → drop for state purposes + audit (counts toward equivocation evidence, §14); an out-of-order seq inside the window → accept it, record the resulting gap, and issue `recovery-request` (§14) to fill it; a seq outside the window or a gap set over the bound → refuse for state purposes, retain for audit, fail closed. A high-water mark alone is **not** sufficient: it must never cause a valid older message (or the audit evidence inside it) to be discarded while later seqs are accepted — older evidence is validated on its own merits (signature, `membership_epoch`/`membership_root`, digests, expiry) and retained regardless of window position.
- At-least-once transport: senders retry unacknowledged messages with the **same** `msg_id`/`monotonic_seq`/bytes; retries are idempotent, never new seq numbers for the same content. Gap fill via `recovery-response` re-enters the same validation path; out-of-order delivery is safe because decisions derive from message **sets** (§11), not arrival order.
- Wall-clock is never authoritative: `issued_at_ms`/`expires_at_ms` gate freshness with an explicit skew tolerance (proposed default: reject if `|now - issued_at| > 5 min` or already past `expires_at_ms`; exact value is a deployment parameter, not consensus). Ordering for decisions comes from deterministic reduction over the ballot set (§11), not arrival order. Locally recorded derivation timestamps (`derived_at_ms`, §8) are informational only and are never accepted as proof of freshness, timeliness, or clock agreement.
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
| `membership_epoch` / `membership_root` | Membership epoch **and full membership-root digest** the proposal is issued under (both equal the envelope fields, §13). |
| `policy_version` | Governance policy bundle version string (e.g., `"peer-policy/3"`). Receivers reject unknown/newer-than-local policy. |
| `evidence_manifest` | List of `{ name, sha256, media_type, provenance }` — digests of supporting evidence. Full evidence replicated via `recovery-response` or content-addressed sync; ballots may cite it by digest. **No raw prompts, message content, credentials, or PII in the manifest or payload** (§9). |
| `idempotency_key` | `"<action_type>:<resource>:<uuid>"`, unique per intended side effect. |
| `fencing_base` | Expected current fencing-token value at the resource (see §12.3); `0`/null only for resource-creation actions. |
| `expires_at_ms` | Proposal deadline. Expired proposals resolve to `expired`, never auto-pass. |
| `supersedes` | Optional prior `proposal_id` this version replaces, with reason string. |

Every field above is immutable for the life of the proposal and is hashed into `proposal_id` (§5.3), including `proposal_version` and `fencing_base`. Changing any field means a new `proposal_id` (a new version linked by `supersedes`), never an in-place edit. Unknown, duplicate, or noncanonical fields → reject (§5.2).

Proposer must persist the signed proposal to its local durable log **before** first send.

## 7. Ballot message (`kind: ballot`)

One voter, one proposal version, one signed statement. There is no `user` override, no string caller bypass, no weight-by-default.

| Field | Meaning |
|---|---|
| `proposal_id` / `proposal_version` | Must match a known, unexpired proposal. Ballots for stale versions are rejected (re-vote on the new version instead). |
| `voter_peer_id` | Must equal envelope sender and be in the epoch's eligible voter set. |
| `decision` | `"approve" \| "deny" \| "abstain"`. |
| `affected_role` | `"affected-consumer" \| "peer-voter"` (a peer voter can be both; the flag is what satisfies §11 coverage for an affected member that is a **peer**). It never stands in for an affected member that is a human consumer (§9). |
| `evidence_cited` | List of digests from the proposal's `evidence_manifest` this ballot relies on, plus optional ballot-local finding digests. No raw content (§9). |
| `human_input_ref` | Optional digest reference to an attested human-input record (§9) that informed this ballot. The ballot is still the peer's own signed decision. |
| `ballot_seq` | `u64` per (voter, proposal_version): starts at 1; used **only** for replay/equivocation detection, never as a replacement counter. |
| `expires_at_ms` | Ballot expiry; must be `<=` proposal expiry. |

A peer signs exactly **one** ballot per proposal version. Ballots are **not replaceable**: a second, differing valid ballot for the same `(voter, proposal_version)` — at any seq, in any arrival order — is equivocation evidence (§14), not a re-vote, and does not supersede the first for tally purposes. All ballot bytes are retained in the audit trail, and none of the retained versions is "the effective one by arrival" — there is no first-seen or last-seen authority. Changing a decision requires a new **proposal version** (§6 `supersedes`), never a re-vote on the same version (contrast the current silent replace in `governance.py:132`). Abstentions count for participation accounting but not for approve/deny weight — exact threshold semantics are UNDECIDED (§10).

**Finality:** once a decision for a proposal version is committed under the selected algorithm (§8), no ballot arriving afterwards — higher seq, duplicate, or delayed — can change, re-tally, or reverse it. Ballot arrival order is never observable in the outcome.

## 8. Decision certificate and decision evidence (`kind: decision-certificate`)

A decision is **derived locally and deterministically** by each peer from the same signed inputs (§11); the certificate is the portable proof that derivation succeeded.

| Field | Meaning |
|---|---|
| `proposal_id` / `proposal_version` | What was decided. |
| `membership_epoch` / `membership_root` | Epoch(s) **and full membership-root digests** the deciding input set belongs to; cross-epoch decisions list both epochs, both roots, and both voter sets (§13.3). Competing roots → the certificate is unverifiable. |
| `outcome` | `"passed" \| "denied" \| "expired"` (never auto-pass on timeout; timeout → `expired` or `denied` per policy, UNDECIDED which — fail closed either way). |
| `eligible_set_digest` | Digest of the eligible voter set + affected set actually used. This fixed input-set digest, together with the cited membership roots, is the freshness/finality evidence a verifier re-derives against. |
| `ballot_refs` | List of `{ voter_peer_id, msg_id, signature_digest }` for every peer ballot counted **and** every affected **peer** member's ballot required. Verifiers fetch/verify each ballot. A peer ballot can never stand in for a human consumer's input. |
| `human_input_refs` | List of `{ consumer_id, input_digest, attestation_digest, ingress_identity_binding }` for every affected **human consumer** required (§9). Verifiers fetch/verify each attestation, its identity binding, and its citation by the affected-set derivation. An attestation can never stand in for a peer ballot. |
| `tally` | Deterministic tally breakdown: counts/weights per decision, participation list, per-member coverage list (peer ballot vs human attestation, marked). |
| `derived_at_ms` | Local derivation time. **Informational only** — never proof of freshness, timeliness, network order, or clock agreement (§5.5). |

A certificate is *internally consistent* for a verifier iff every referenced input verifies (§5), every peer voter was eligible under the cited membership root, the affected peer/human coverage is complete for a claimed `passed` outcome (§§9, 11), and deterministic reduction over exactly the cited input set yields the certificate's stated `passed`, `denied`, or `expired` outcome. A `denied` or `expired` certificate is portable refusal evidence, **never an execution grant**. A `passed` certificate also needs the selected protocol's final commit proof and a verifiable freshness rule demonstrating that the proposal and required inputs were still eligible when it committed. Input digests, claimed timestamps, and `derived_at_ms` alone do **not** prove that timing; the exact signed-round/deadline evidence is algorithm-dependent and UNDECIDED (§10). Until that evidence rule is specified and implemented, **no passed certificate is valid for actuation**.

Finality is **not** derived from arrival order. Whether this certificate is the *commit* certificate for its proposal/idempotency scope — how conflicts are ordered, how many rounds/lock steps occurred, what the final commit rule is — is algorithm-dependent and **UNDECIDED (§10)**. Until an algorithm is selected and tested, a verifier treats a certificate as verifiable test output only: it never becomes authority, and **no honest peer treats any certificate as passing for actuation** (§12 stays disabled). After selection, a committed decision is final: later ballots, later-arriving messages, or a second certificate arriving later can never change or supersede it. A conflicting certificate for the same scope is a detected safety failure to be recorded and escalated (§14), never resolved by "whichever arrived first" or "whichever I saw first".

Missing any required input → `outcome != passed` (fail closed). Whether a present-but-negative human input is a veto or counted feedback remains UNDECIDED (§10); the only decided rule is that **missing** input blocks execution.

## 9. Human input, model votes, and evidence privacy

- **Cryptographic authentication ≠ model preference.** A valid Ed25519 signature proves *which peer* attested to *which bytes*. A model completion proves nothing about authority. Peers never accept model text as a ballot, vote, or signature.
- **Human requests are inputs, not ballots.** Telegram/Butler authenticate the human per the beta boundary (allowlist, private chat, budget, audit) and the receiving peer emits a signed `HumanInputRecord { input_digest, admission_claim_digest, ingress_identity_binding, received_at_ms, transport }`. Ballots may cite `human_input_ref` by digest. The human never signs peer messages and the adapter never signs for a peer.
- **Ingress attestation is not independent human proof.** A peer signature on `HumanInputRecord` proves only what that peer attested to; a compromised ingress peer could forge it. Before a human-affecting consequential grant is valid under a Byzantine threat model, the chosen protocol must require independently verifiable human confirmation (for example a human-held signing key) or a reviewed multi-peer verification ceremony tied to the transport identity. The exact mechanism is UNDECIDED; a single gateway/peer's claim never counts as BFT proof of human participation. Until resolved, human-affecting consequential grants remain disabled.
- **No substitution in either direction.** For each affected member the certificate must cite the *matching* input kind: an eligible signed **ballot** from an affected **peer**, and an authenticated, identity-bound **input attestation** from an affected **human consumer** (§8 `ballot_refs` / `human_input_refs`). A peer's ballot can never stand in for a silent human consumer, and a human attestation can never stand in for a peer's ballot; neither may be manufactured from the other by an adapter, proposer, or peer. Supplying one kind never satisfies the other's slot.
- **Affected-consumer human operators** (if the consumer is human-represented) express preference through the same path: an explicit, attributable input (button/confirmation tied to the authenticated ingress identity), recorded with its admission claim, cited by digest, and verified by the certificate before any pass. Natural-language Telegram text is **never** parsed into an auto-generated vote.
- **Mandatory ≠ veto.** Every required input must be present or the proposal cannot execute (decided, §1.2). Whether an input that is *present* acts as a per-member veto or as counted feedback subject to a threshold is **UNDECIDED** (§10) and must not be assumed by any implementation or test as if decided.
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
- [ ] UNDECIDED: exact agreement algorithm (round structure, leader/proposer selection, conflict-set ordering, lock/unlock, timeout schedule, and what constitutes the **final commit certificate**).
- [ ] UNDECIDED: membership-change rule (who can add/remove, under which epoch/threshold, §13).
- [ ] UNDECIDED: timeout/expiry values and clock-skew tolerance.
- [ ] UNDECIDED: policy-version upgrade rule.

## 11. Deterministic decision rule and affected-consumer inclusion proof

### 11.1 Eligible voter set and affected-set snapshot

- Each proposal carries `affected_snapshot = { graph_root_digest, epoch, membership_root, members: [{ peer_id | consumer_id, kind: "peer" | "human-consumer", role, reason }], derivation }`, where `derivation` names the deterministic function + inputs (relationship-graph edges of types `DEPENDS_ON`/`CONSUMES`/`GOVERNS` as materialized in `PeerLattice`, graph root digest, epoch) that produced the member list. `kind` decides which input can satisfy that member: `peer` → eligible signed ballot; `human-consumer` → authenticated identity-bound input attestation (§§8, 9). The two are never interchangeable.
- The proposer must include a `reason` per member (why this consumer is affected by this action/resource). A bare list with no reasons is invalid.
- Every verifier **recomputes** the derivation from its own local graph view at the cited `graph_root_digest` + epoch, and verifies consumer notification/input evidence. Proposer assertion alone is not proof of completeness. Forks (two different member lists for the same epoch+root) or stale views (epoch/root behind local) → reject.
- Membership changes mid-proposal require a new proposal version (new `proposal_id` via `supersedes`); silent voter substitution is forbidden.

### 11.2 Deterministic reduction (same evidence → same outcome)

Given (proposal, epoch voter set + membership root, affected set, valid input set — peer ballots and human-consumer attestations, policy version), each peer computes:

1. Filter peer ballots: valid signatures, eligible voters in the cited epoch **and membership root**, matching `proposal_id`+version, unexpired, exactly **one** valid ballot per voter per proposal version (a differing second ballot from the same voter = equivocation → the voter's ballots are excluded, counted as evidence, and never resolved by "highest seq wins" or "last seen wins"; all versions retained for audit).
2. Check affected coverage **by member kind**: every `affected_snapshot` member of `kind: "peer"` has a valid eligible signed ballot (decision any of approve/deny/abstain — presence is mandatory), and every member of `kind: "human-consumer"` has a verified, authenticated identity-bound input attestation cited and checked (§§8, 9). Neither kind can be supplied by the other; one missing member → coverage fails. Presence/threshold semantics (veto vs counted feedback) remain UNDECIDED per §10.
3. Check participation and approval per the decided threshold (UNDECIDED — implement as pluggable policy, default `deny`).
4. Check expiry, conflict-set status **under the selected algorithm** (§5.3 — algorithm-dependent and UNDECIDED; there is no first-observed lock and no authority derived from arrival order), and epoch + membership-root validity (§13).
5. Output `passed` only if all checks succeed; else `denied` (on decisive deny/quorum-impossible) or `expired` (on deadline). Never default to pass. The output is a function of the fixed input set only — later-arriving ballots can never revise a committed output (§§7, 8).

### 11.3 Worked fail-closed examples (normative behavior)

- One approval among three peers with two affected consumers silent → **no execution** (missing affected input).
- All peers ballot, but one affected **human consumer** never confirms → **no execution**; the peers' ballots cannot fill a human consumer's slot.
- An affected human consumer confirms, but an affected **peer** never ballots → **no execution**; the human attestation cannot fill a peer's slot.
- Single-ballot "100% of cast" → **no execution** (denominator is the eligible + affected set, never cast-only; this reverses the current `governance.py` tally).
- A voter re-signs a different ballot for the same proposal version (any seq) → **equivocation**, not a replacement: neither ballot counts toward a pass; a legitimate revision requires a new proposal version.
- A ballot arriving after a committed decision, offering a different decision → **decision unchanged** (finality; §7).
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
| `voter_certificate` | The §8 inputs: peer `ballot_refs` **and** `human_input_refs` (or embedded verified ballots/attestations for single-round-trip actuation; verifier still checks each signature and identity binding). Peer ballots alone never satisfy affected coverage. |
| `membership_epoch` + `membership_root` | Issuing epoch **and full membership-root digest**; both are bound into the signed grant. Cross-epoch grants list both epochs + both roots and prove intersection (§13.3). A grant whose root the verifier has not pinned, or that cites competing roots, is rejected. |
| `idempotency_key` | From the proposal; actuator keeps a durable `executed_grants` table keyed by it. |
| `expected_current` / `next_token` | Resource-scoped monotonic fencing values: `next_token = expected_current + 1`; the resource compares `expected_current` and atomically advances on a new idempotency key (§12.3). |
| `expires_at_ms` | Short-lived (minutes, not hours); expired grants are rejected even if the certificate was valid. |
| `grant_signers` | The peers that co-signed the grant (threshold UNDECIDED; until decided, no threshold verifies). |

### 12.3 Fencing, idempotency, and the limits of the local ledger (resource-side, mandatory)

Each mutable resource holds a monotonic `fencing_token` (counter persisted **with the resource**). The grant carries `expected_current` and `next_token = expected_current + 1` (or the explicit creation rule for new resources). In the **same atomic operation as its idempotency check and effect**, the resource requires `stored_token == expected_current`, rejects a previously used `idempotency_key`, performs the effect once, and then persists `stored_token = next_token` plus that key. A repeated call with the same key returns its prior recorded outcome without repeating the effect; a different key with an obsolete `expected_current` fails. A local actuator's token check alone is not fencing. Stale, replayed, or cross-partition grants fail closed at the resource.

**The local ledger is not atomic with an external call.** No local SQLite transaction can commit together with an arbitrary bridge/HTTP/side-effecting call, so this spec makes **no exactly-once claim derived from local persistence**. Required rules:

1. **Resource capability gate (eligibility).** A resource is eligible for automatic execution only if it supports fencing/idempotency that is atomic *at the resource* — i.e. the resource itself can compare `expected_current`, advance to `next_token`, and process the `idempotency_key` in the same atomic step as performing (or refusing) the effect. If the target resource cannot do that, the `action_type` is **ineligible for automatic execution**: the actuator refuses it (fail closed) and no grant authorizes it automatically, regardless of how many peers signed.
2. **Intent before call.** The actuator durably persists an intent record — `grant_digest`, `idempotency_key`, `expected_current`, `next_token`, `PENDING` — *before* issuing any side-effecting call, and passes both fencing values and the idempotency key into the call so the resource can enforce them.
3. **Outcome marking.** After the call, persist `EXECUTED` (definite success) or `FAILED` (definite rejection) with the resource's response evidence.
4. **Crash/uncertain outcome.** If the actuator cannot determine the outcome (crash, timeout, transport error after send), the intent is marked **`UNKNOWN_AFTER_CRASH`**. It is never optimistically marked executed and never silently retried.
5. **Reconcile before retry.** Before any retry, the actuator **reconciles at the resource**: read the resource's own fencing/idempotency state (or query its effect) to determine whether the effect already landed. Retry only if the resource states it did not; if it did, adopt the recorded result. A local ledger row is evidence about the attempt, never proof about the remote effect.

Idempotency per proposal alone is explicitly **insufficient**: intersecting epochs, membership-root binding, algorithm-dependent conflict handling (§5.3, UNDECIDED), and resource-side fencing are all required.

### 12.4 Actuator validation order (normative)

1. Grant signature(s) valid under the keys of a locally pinned `membership_epoch` + `membership_root`; grant unexpired.
2. `action` matches proposal exactly; `params_digest` verifies against fetched params.
3. Decision certificate verifies per §8 (all peer ballots, all human-consumer attestations, affected coverage by kind, deterministic rule) **and** satisfies the selected protocol's finality rule for the scope (§8 — algorithm-dependent, UNDECIDED; conflict status is never resolved by arrival order).
4. Membership epoch(s) **and membership roots** valid with intersection across upgrades (§13.3); an unpinned or competing root → refuse.
5. Resource capability gate (§12.3): the target supports atomic resource-side fencing/idempotency, or the action is refused as ineligible for automatic execution.
6. `idempotency_key` not already executed (durable check first — duplicate → return prior result, no re-execution). Any prior `UNKNOWN_AFTER_CRASH` intent for this key → reconcile at the resource first (§12.3), never blind-retry.
7. Fencing token check at the resource (§12.3), passed into the call for the resource to enforce atomically with the effect.
8. Persist grant + intent record (`PENDING`) → execute → persist outcome (`EXECUTED`/`FAILED`/`UNKNOWN_AFTER_CRASH`). The local ledger records the attempt; it does **not** and cannot atomically commit the external call. Audit append failure at any point → abort and report failure (§14 inverts nothing: no success claim without durable evidence **and** resource-side confirmation).

## 13. Membership: versioned set, upgrades, revocation (change rule UNDECIDED)

### 13.1 Membership set and epoch

`Membership(epoch, members: [{ peer_id, public_key, roles, joined_epoch }], removed: [{ peer_id, removed_epoch, reason_digest }], policy_version, prev_epoch_digest)`. `epoch` is a `u64` starting at 1 (genesis). `membership_root = SHA-256(canonical bytes of the full membership set)`; peers pin **both** the epoch number and the root digest they operate under and include **both** (`membership_epoch` + `membership_root`, §5.1) in every signed message, certificate, and grant. An epoch number without its root is not a valid citation.

- Genesis membership is provisioned out-of-band (operator ceremony, keys exchanged over an authenticated channel, each peer configured with the identical genesis digest). There is deliberately **no** in-protocol open enrollment in phase one.
- **Competing roots:** if two different roots claim the same epoch, or a peer presents a root the receiver has not pinned, the receiver rejects the message, records the divergence as evidence, and **suspends grants** on that epoch until the divergence is resolved under §13.2's rule. Peers never choose between competing roots by arrival order or by local preference.
- Phase-one admission/removal is by operator-provisioned update satisfying the UNDECIDED change rule; phase-two federated trust groups are deferred and must not be assumed.

### 13.2 Upgrade mechanics

- A `membership` message proposes epoch `e+1` with `prev_epoch_digest == digest(e)`, a full new member list, and approval evidence under epoch `e`'s rule. Peers validate the chain (`prev` links unbroken), persist both epochs during a transition window, and only retire `e` after the transition rule (UNDECIDED) is met.
- **Reconfiguration is a consequential action like any other, and additionally requires:** (a) a decision certificate that is *final* under the selected protocol's commit rule, and (b) an explicit **quorum-intersection proof** showing that the approving set under `e` and the resulting set under `e+1` intersect to the degree required by the **chosen fault bound** (`f`, quorum fraction, failure class — all UNDECIDED, §10). Until the fault bound and algorithm are decided, (a) and (b) cannot be produced, so **no reconfiguration takes effect and grants are suspended** — peers keep serving the pinned genesis/root and refuse new authority traffic rather than partially adopting an epoch. The threshold for approving a membership change remains UNDECIDED (§13.4); nothing here selects one.
- Proposals/ballots in flight under epoch `e` do **not** auto-carry to `e+1`: they either complete under `e`'s rule before the cutoff or must be re-issued as new proposal versions under `e+1` (with the new `membership_root` bound, §5).
- Revocation takes effect at the epoch boundary; in-flight grants under the old epoch are re-validated under §13.3 or refused.

### 13.3 Cross-epoch execution safety (normative)

A grant spanning epochs `e → e+1` executes only after the reconfiguration certificate is final **and** the chosen algorithm's old/new quorum-intersection proof under its fault bound verifies (§13.2). The action must then be re-decided fully under `e+1` (or satisfy a separately specified joint-epoch commit rule with a proved honest intersection). Independently valid old and new tallies alone do **not** prove their signers intersect; a bare majority in their union, or `e`-only approval after membership changed, is insufficient. Until an intersection rule is selected and tested, all cross-epoch grants are refused.

### 13.4 UNDECIDED membership change rule (explicitly not specified)

Who may propose a membership change, what threshold approves it, whether affected consumers of membership changes get a veto, how key-compromise emergency revocation works, and the transition/retirement timing are all UNDECIDED. No central membership administrator may silently add a voter — that invariant is decided; the positive rule replacing it is not.

## 14. Partition, timeout, fencing, and fail-closed behavior

- **Partition without valid agreement fails closed.** A side that cannot assemble a complete, verifiable certificate (including all affected inputs) under its pinned epoch executes nothing. Two sides can never both hold valid intersecting certificates for conflicting actions in the same epoch if the decided threshold requires intersection — and until that threshold is decided, neither side executes at all.
- **Timeouts:** proposal expiry → `expired`; ballot collection timeout with missing affected input → `denied`/`expired` (policy picks which; both fail closed). Grant expiry is short; expired grants are rejected even with a valid certificate. Timeout values themselves are UNDECIDED (§10).
- **Equivocation handling:** two conflicting signed messages with the same `(sender, seq)`, or two differing valid ballots from one voter for the same proposal version (regardless of `ballot_seq` or arrival order) = attributable misbehavior. Honest peers retain both, refuse to count either toward a pass, flag the peer in audit, and (post-decision) apply the UNDECIDED slashing/removal policy. Never resolve equivocation by "first seen wins" or "last seen wins" for authority; a replacement ballot for the same proposal version does not exist (§7).
- **Conflicting proposals:** conflicting `proposal_id`s in the same scope form a conflict set (§5.3). There is **no** first-observed/first-valid lock and no arrival-order authority: whichever conflicting proposal a peer happened to see first confers nothing. Ordering the conflict set, rounds, lock/unlock, and the final commit certificate are algorithm-dependent and **UNDECIDED** (§10); until an algorithm is selected and tested, peers sign at most one proposal per scope, buffer the rest, and **issue no grant**. Once selected: no double-execution of the same `idempotency_key`, no parallel execution of conflicting scopes, and a committed decision cannot be changed by later messages.
- **Recovery:** `recovery-request { need_digests[] }` / `recovery-response { messages[] }` with per-message verification identical to live traffic. Receivers fill their bounded **gap set** (§5.5) and validate older messages/evidence individually (signature, epoch + membership root, digests, expiry) instead of discarding anything below a high-water mark. Reconnect/replay converges state without duplicate side effects because execution is gated on durable `executed_grants` + resource-side fencing + resource reconciliation (§12.3), not on "have I seen this message." Out-of-order delivery is safe: decisions derive from sets, not sequences.
- **Heartbeat** messages exist for liveness/missing-vote observability only. A heartbeat carries no authority, cites no votes, and can never substitute for a ballot.

## 15. Threat assumptions and trust boundaries (normative)

1. **Network:** asynchronous, adversarial. Adversary can drop, delay, reorder, duplicate, and partition traffic, and can run its own peers with valid keys up to the UNDECIDED fault bound. Adversary cannot break Ed25519, SHA-256, or (assumed) authenticated transport encryption. Transport authentication (mutual TLS with peer-key-bound certificates, or Noise with static peer keys — exact mechanism a deployment decision) is required; signatures alone do not provide confidentiality for evidence payloads.
2. **Process compromise:** any peer process may be fully compromised (key exfiltrated, arbitrary messages signed) up to the fault bound — hence the BFT-leaning posture in §10. A compromised peer must still be unable to forge another peer's signature or execute side effects on another peer's resources (per-peer actuators verify independently). **Rewriting its own history is *not* prevented by the local hash chain alone:** a peer that controls its own store can recompute the entire chain from any edited point. Detecting that requires independently held evidence — signed chain heads anchored/replicated to other peers or a witness, plus cross-checks against other peers' copies of shared messages (§16). Until head anchoring exists, no tamper-evidence claim is made about a single peer's log.
3. **Model/ingress untrusted:** LLM outputs, Telegram text, Butler routing decisions, and human-provided content are untrusted inputs. Prompt injection, confabulated citations, forged `sender` strings, and replayed Telegram `update_id`s are in scope. None can become a ballot or grant except through the signed, digest-cited paths in §§6–9.
4. **Supply/operator:** genesis key ceremony, binary provenance, host hardening, and secret storage are out of protocol scope but preconditions for any security claim. A compromised genesis ceremony voids all guarantees.
5. **What this protocol does NOT defend against:** Sybil enrollment beyond the closed membership list (no Sybil resistance mechanism is claimed — admission is the perimeter); traffic-analysis; compromised majority above the fault bound; operator coercion; side channels in model hosting. Federated membership (later phase) must add explicit anti-Sybil admission before it activates.
6. **Privacy:** peer traffic contains digests and protocol metadata by default; raw content stays out (§9). Evidence payloads distributed for verification are redacted findings, never prompts/credentials/PII. Operators must inventory every sink (peer log, audit DB, Lattice mirror, Butler log, Telegram replies) before handling real user data.

## 16. Audit durability, ordering, and recovery (per-peer, normative)

Each peer maintains a durable, append-only, hash-chained decision log in the **same transactional durability domain** as its idempotency/intent state (for example, separate tables in one SQLite database). A log in a separate database without an atomic cross-store commit is insufficient: a crash between a log append and state update could create conflicting recovery claims. Each peer still has its own independent store, never a shared network database.

- Record order: `proposal → ballots/attestations (each) → derivation outcome → certificate → grant → intent persist (PENDING) → side-effect call → outcome (EXECUTED / FAILED / UNKNOWN_AFTER_CRASH) → resource reconciliation → executed_grants`. No step is claimed before its persist succeeds; append failure fails the local operation and suspends further consequential work until audit health recovers (mirrors the beta audit rule, extended to peers). The local ledger records intent and outcome around the external call — it never claims to have atomically committed that call (§12.3).
- Durability: per-write `fsync` (`synchronous=FULL`-equivalent), `BEGIN IMMEDIATE` transactions, explicit recovery scan on startup (torn-write detection via chain verification, `UNKNOWN_AFTER_CRASH` reconciliation — never silently re-execute billable/side-effecting work; reconcile **at the resource** before any retry, then via `recovery-request` and the durable `executed_grants` table).
- Tamper-evidence: each record includes `prev_record_digest`; genesis record pins the membership root, and each record after it pins `membership_epoch` + `membership_root`. Verification replays the chain and detects gaps, truncation, and edits made by anyone **other** than the store's owner. **The local chain alone cannot detect a wholesale rewrite by a compromised peer** — such a peer recomputes every digest from the edit forward. Before any tamper-evidence claim: each peer periodically signs a chain head `{ peer_id, last_seq, last_record_digest, membership_root, ts }` and **anchors/replicates it independently** to other peers (or a witness) with append-only retention; auditors compare later claims against independently held heads, and against other peers' copies of shared messages. Head anchoring is a precondition for the tamper-evidence claim; without it the honest statement is "integrity vs. non-owners only". The current `synchronous=NORMAL` + no-chain implementation (§3.15) does **not** satisfy any part of this.
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

**Topology is parameterized by the decided failure model and fault bound `f` (§10)** — no fixed peer count is approved here. Required sizing per model under test: **CFT `n ≥ 2f+1`** (smallest: `f=1 → 3` peers) and **BFT `n ≥ 3f+1`** (smallest: `f=1 → 4` peers). Run the full matrix for *both* models until the failure class is decided; a test pass under one model is not a pass under the other.

**A. Hermetic local peer simulation (topology per the matrix above — CFT: ≥3 separate peer processes/stores; BFT f=1: ≥4 — plus ≥2 affected consumers, at least one of them a human consumer; Butler stopped):**

- Propose a provider change; prove all affected peers see the identical immutable proposal (digest equality, including `proposal_version`/`fencing_base` in `proposal_id`), contribute independent attributable inputs (peer ballots **and** human-consumer attestations, never interchangeable), and derive the identical outcome from the same evidence.
- Prove determinism: shuffle delivery order, duplicate every message, deliver out of order and leave seq gaps (recovery fills them), restart one peer mid-vote — same derived outcome, no duplicate side effects, convergence after heal. A later-arriving or higher-`ballot_seq` ballot never changes a committed decision.
- Negative tests (each must fail closed, with no execution and an auditable refusal): one-ballot pseudo-quorum; missing affected consumer; peer ballot offered in place of a human attestation (and vice versa); forged signature; duplicate/replayed ballot (`monotonic_seq` reuse); stale `membership_epoch`/unknown `membership_root`; tampered payload (`payload_digest`/`envelope_digest` mismatch); unknown/duplicate/noncanonical envelope field; ballot for unknown/stale proposal version; equivocating voter (two differing ballots, any seq) counted toward pass; expired proposal/grant; audit-append failure injected at each of proposal/ballot/certificate/grant persist points; conflicting proposals double-executing the same `idempotency_key`; `user`-string/Telegram-text/model-completion offered as ballot; resource without atomic fencing/idempotency accepted for automatic execution.

**B. Fault/partition proof (same topology as A, adversary harness):**

- Partition into sides of sizes drawn from the matrix (e.g. CFT `2+1`, BFT `3+1`) and prove the decided rule holds on **each** side: under **CFT**, a majority side (≥ `f+1` with all affected inputs present) **may commit** under the decided majority rule, while the minority side must **not** commit; under **BFT**, no side below the quorum commits, and **no two sides can both hold final conflicting certificates** (quorum intersection). In every case: no split-brain double execution; heal → converge without duplicate side effects. Until §10 selects a model, treat every side as forbidden to execute (fail closed) and assert exactly that.
- Equivocation and conflicting-certificate tests (mandatory for BFT `f=1`, run for CFT too): a peer signing two differing ballots for one proposal version, and two conflicting certificates claimed for the same idempotency scope → both detected, attributable, retained as evidence, and neither treated as final by arrival order.
- Delay/reorder/replay storms and seq-gap floods; kill -9 one peer mid-grant-persist → restart → `UNKNOWN_AFTER_CRASH` reconciliation with no re-execution of completed side effects and no loss of `executed_grants`; retry only after resource-side reconciliation.
- Revoke a peer mid-proposal (under the decided change rule) → in-flight items complete-or-hold per §13, new epoch required for new authority; cross-epoch stale grant refused by fencing + intersection; membership reconfiguration without a final certificate + quorum-intersection proof → grants suspended.
- Conflicting proposals on the same resource scope → exactly one outcome executes at most once; the other is denied/expired with evidence retained; never "first proposal seen wins".
- Real transport identity review (mutual authentication with peer-bound keys) independent of mock-transport tests; mock tests alone do not satisfy B.

**C. Multi-host deployment proof:**

- Same as A–B across at least the model's minimum host count (CFT: ≥ `2f+1`; BFT f=1: ≥ 4 hosts) with real keys, real transport auth, independent clocks (skew injected), and independent stores; wall-clock skew beyond tolerance fails closed rather than auto-passing; `derived_at_ms` is never accepted as clock evidence.
- Operator ceremony test: genesis digest mismatch on one host → that host refuses all authority traffic until re-provisioned.
- Standard gates on the exact head: full `pytest`, `ruff check`, `ruff format --check`, `mypy`, `git diff --check`, independent security review.

**D. Product wiring (only after A–C):** Telegram/Butler show proposal digest, affected parties with reasons, per-voter status, missing inputs, evidence digests (never raw content), and honest failure text. No synthetic votes; human input only through explicit attributable confirmation. Controlled rollout needs owner approval plus live proof.

## 20. Open questions (UNDECIDED — require owner decision; not answered by this spec)

1. Failure class and tolerated faults (Byzantine vs crash; values of `f`, `n`).
2. Exact agreement algorithm (round structure, proposer selection, conflict-set ordering, lock/unlock, timeout schedule, final commit certificate rule).
3. Decision thresholds: participation floor, approve fraction, and whether affected-consumer input is a per-member veto or counted feedback.
4. Membership-change rule: who proposes/approves add/remove/rotate, emergency revocation path, transition timing.
5. Timeout, expiry, and clock-skew values; policy-version upgrade rule.
6. Genesis ceremony operators and emergency out-of-band coordination channel.
7. Evidence retention windows and pruning policy for the peer log.
8. Transport choice (mutual TLS vs Noise) and certificate/key-storage provisioning.
9. Signed head anchoring scheme for §16 tamper-evidence (replicate to peer subset vs external witness; cadence, retention, and how conflicts between independently held heads are adjudicated).
10. Human-consumer input authentication across peers: user-held signature or independently corroborated ingress ceremony; a single receiving peer's attestation cannot be its own proof under the Byzantine threat model.

---

*Authoring notes: `docs/SPEC_L2_EXECUTION.md` (brief reference) does not exist at base `36f41c8`; `docs/SPEC_L2_TELEGRAM_BETA.md` was used instead and the substitution is recorded here. No deployed features, test results, or live evidence are claimed. All code citations verified against the base tree; no source files were modified by this spec.*
