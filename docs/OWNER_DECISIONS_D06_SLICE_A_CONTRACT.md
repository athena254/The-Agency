# Owner decision D-06 — Slice A protocol contract (approved)

Status: **APPROVED by the owner (2026-11-17).** All nine decisions below are adopted, and the
numeric-ceiling table in D-06.8 is pinned as written. They are the policy inputs for the Slice A
contract freeze. They do not by themselves enable beta, peer actuation, or release; approval of
D-06 is not release authorization.

## D-06.1 — Fault bound and validator power

- **Decision (proposed):** Confirm D-01 as final and closed: n=4 validators, equal voting
  power 1 each, f=1 Byzantine, application approval quorum Q=3. Not reopened; restated so
  the contract has one arithmetic table.
- **Rationale:**
  - `OWNER_DECISIONS_D01_D05.md` D-01 already fixes 4 validators, 1 Byzantine fault and a
    3/4 approval quorum; `RESEARCH_LATTICE_FULL_BUILD.md` §8 lists these as already decided
    and not open.
  - `3f+1 = 4` with `f=1` satisfies the BFT safety sizing in
    `SPEC_KNOWN_PEER_PROTOCOL.md` §10 (Option B).
  - The liveness budget `N − Q = 1` is published, never hidden by lowering Q
    (`RESEARCH_LATTICE_FULL_BUILD.md` §5.2).
- **Rejected alternative:**
  - Reopening the fault bound or validator count — D-01 is closed; §8 reserves only the nine
    items below as open.

## D-06.2 — Membership admission and key recovery

- **Decision (proposed):** Membership changes are signed membership proposals approved at
  the D-01 quorum with an epoch bump (extends D-02). Reconfiguration must preserve quorum
  intersection: eligible(old) ∩ eligible(new) ≥ 2. Key recovery = admitting a member under
  a new key via that same proposal path; the old key's pin becomes an alarm state, never
  silently re-trusted. No unilateral self-admission.
- **Rationale:**
  - D-02 in `OWNER_DECISIONS_D01_D05.md` already requires signed membership proposals at the
    D-01 quorum with an epoch bump on every change.
  - `SPEC_KNOWN_PEER_PROTOCOL.md` §13.2-13.3 requires a quorum-intersection proof so adjacent
    epochs cannot hold disjoint quorums; `eligible(old) ∩ eligible(new) ≥ 2` is the concrete
    form of that proof at this fault bound.
  - Pinning, not TOFU, and treating any key change as an alarm are the custody rules in
    `RESEARCH_LATTICE_FULL_BUILD.md` §4.2; voter revocation is a membership reconfiguration,
    distinct from grant revocation (`SPEC_KNOWN_PEER_PROTOCOL.md` §5.4).
  - No central membership administrator: a peer cannot grant itself voting power
    (`SPEC_PEER_LATTICE_GOVERNANCE.md` invariant 1; `OWNER_DECISIONS_D01_D05.md` D-02).
- **Rejected alternative:**
  - Unilateral self-admission or operator-assigned voting power — violates no-central-authority
    (`SPEC_PEER_LATTICE_GOVERNANCE.md:11`).
  - Silent re-trust of the old key after recovery — destroys attribution and the epoch chain.

## D-06.3 — Application approval and override rules

- **Decision (proposed):** A "deny" outcome is final for that proposal version; there is no
  override path for any role, orchestrator, gateway or "user" string. Human attestation
  (D-03) can only enable an approval that is otherwise within policy, never overturn a
  rejection. A missing risk tier fails closed to the higher tier. Revision of a proposal
  requires a new proposal version (supersedes), never a re-vote on the same version.
- **Rationale:**
  - `SPEC_KNOWN_PEER_PROTOCOL.md` §7 makes ballots non-replaceable and §11.3 rejects a re-vote;
    finality in §7-§8 forbids reversing a committed deny with later ballots.
  - D-03 in `OWNER_DECISIONS_D01_D05.md` forbids gateway/orchestrator/"user" override; a
    present rejection is mandatory input but not an individual veto
    (`SPEC_KNOWN_PEER_PROTOCOL.md` §9).
  - Human attestation enabling only within-policy approval follows the "mandatory ≠ veto"
    rule (`SPEC_KNOWN_PEER_PROTOCOL.md` §9) and the no-substitution rule in §11.2.
  - Missing risk tier failing closed to the higher tier is the orchestrator rule in
    `RESEARCH_LATTICE_FULL_BUILD.md` §6.1.
- **Rejected alternative:**
  - A `user`-string, orchestrator or gateway override lifting a deny — rejected in
    `SPEC_KNOWN_PEER_PROTOCOL.md` §11.3 and `SPEC_PEER_LATTICE_GOVERNANCE.md` invariant 1.
  - Re-vote on the same version — contradicts non-replaceable ballots (§7).

## D-06.4 — Affected-graph derivation

- **Decision (proposed):** The affected set is declared statically in the proposal body and
  bound into the proposal hash at creation time; the snapshot records one reason per member.
  Runtime discovery of affected consumers is rejected: it is non-deterministic and cannot be
  replayed identically by all peers.
- **Rationale:**
  - `SPEC_KNOWN_PEER_PROTOCOL.md` §11.1 requires a versioned `affected_snapshot` with one
    `reason` per member; `SPEC_PEER_LATTICE_GOVERNANCE.md` invariant 2 requires a reason for
    each member and a new proposal version on membership change.
  - `proposal_id` hashes `affected_snapshot` at creation time (`SPEC_KNOWN_PEER_PROTOCOL.md`
    §5.3), so the set is immutable for the proposal's life (§6).
  - `RESEARCH_LATTICE_FULL_BUILD.md` §5.1 step 2 fixes the snapshot at proposal build time;
    §3.1 forbids non-determinism inside the application.
- **Rejected alternative:**
  - Runtime discovery of affected consumers — non-deterministic and not replayable
    identically by peers (`RESEARCH_LATTICE_FULL_BUILD.md` §3.1, §5.1).

## D-06.5 — Independently verifiable human input (the D-03 gate)

- **Decision (proposed):** A human attestation is an Ed25519 signature from a human-held key
  on a separate device, challenge-bound to (proposal_id, proposal_version, membership_epoch,
  membership_root). Telegram/UI gateway signatures are transport evidence only and are
  explicitly insufficient, per D-03.
- **Rationale:**
  - D-03 in `OWNER_DECISIONS_D01_D05.md` requires a signed attestation bound to the exact
    request and states gateway signatures alone are insufficient.
  - `SPEC_KNOWN_PEER_PROTOCOL.md` §9 requires independently verifiable human confirmation
    under the Byzantine model; one gateway/peer's claim is not BFT proof of human participation.
  - `RESEARCH_LATTICE_FULL_BUILD.md` §8 item 5 names this as the open D-03 gate; a separate
    human-held device avoids the correlated host-compromise risk in §9.
  - Challenge binding to the four fields fixes the attestation to one proposal under one
    membership, preventing cross-proposal and cross-epoch replay (§4.4, §5.4).
- **Rejected alternative:**
  - A Telegram/UI gateway signature as sufficient human proof — explicitly insufficient per
    D-03 and `SPEC_KNOWN_PEER_PROTOCOL.md` §9.

## D-06.6 — Deterministic committed-time and deadline rules

- **Decision (proposed):** All deadlines inside the application are derived exclusively from
  BFT time carried in committed headers, expressed as deterministic height/millisecond bounds.
  Wall-clock reads, floats, and hash-map iteration order are forbidden in application code
  (per the ABCI++ determinism requirements in RESEARCH_LATTICE_FULL_BUILD.md §3.1).
- **Rationale:**
  - `RESEARCH_LATTICE_FULL_BUILD.md` §3.1 forbids randomness, wall-clock time, floating point
    and hash-map iteration order inside the app; §3.2 requires the app hash to match on replay.
  - `SPEC_KNOWN_PEER_PROTOCOL.md` §5.5 states wall-clock is never authoritative; §16 requires
    application transitions and input cutoffs to use engine ordering/finality, not local time.
  - A deterministic committed-time profile is a named open decision
    (`RESEARCH_LATTICE_FULL_BUILD.md` §8 item 6; `SPEC_KNOWN_PEER_PROTOCOL.md` §10).
- **Rejected alternative:**
  - Deriving deadlines from replica-local clocks or wall-clock reads — makes correct peers
    effectively Byzantine and halts the chain on an app-hash mismatch (§9 risks).

## D-06.7 — Transport limits and operator recovery powers

- **Decision (proposed):** Milestone 1 rides CometBFT's authenticated P2P (STS handshake); no
  second transport is built until traffic leaves the engine's scope. Message ceiling 64 KiB
  (matches the existing MAX_WIRE_BYTES). Operators may restart, evict and re-pin peers
  out-of-band; they can never approve, widen scope or mint grants. Any key change is an alarm
  requiring explicit re-approval.
- **Rationale:**
  - `RESEARCH_LATTICE_FULL_BUILD.md` §4.1-4.3: CometBFT P2P uses an STS handshake and is
    authenticated, and §4.3 recommends reusing it for the first milestone; add Noise/mTLS only
    if traffic leaves the engine's scope.
  - §8 item 7 names transport limits and operator recovery powers as open; the 64 KiB ceiling
    matches the existing `MAX_WIRE_BYTES` (`RESEARCH_LATTICE_FULL_BUILD.md` §5.1 envelope).
  - Operators never approving or minting grants follows no-central-authority
    (`SPEC_PEER_LATTICE_GOVERNANCE.md` invariant 1; `RESEARCH_LATTICE_FULL_BUILD.md` §6.1).
  - Key change as an alarm requiring re-approval is the pinning rule
    (`RESEARCH_LATTICE_FULL_BUILD.md` §4.2).
- **Rejected alternative:**
  - Building a second transport (Noise_XX / mTLS) now — deferred until traffic leaves engine
    scope (`RESEARCH_LATTICE_FULL_BUILD.md` §4.3).
  - Operators approving, widening scope or minting grants — violates no-central-authority.

## D-06.8 — D-05 numeric ceilings (left unpinned at D-05)

- **Decision (adopted):** The pinned ceilings: MAX_MEMBERS=4 (already pinned in
  peers/canonical.py); max envelope 64 KiB; max ballots per (voter, proposal version) = 1 (a
  second differing one is evidence, never input); ballot expiry window ≤ 24 h; grant expiry
  ≤ 1 h; grant nonce 128-bit minimum; monotonic seq ceiling 2^53-1; proposals per epoch
  ≤ 256; evidence retention 30 days (already D-05).
- **Rationale:**
  - D-05 in `OWNER_DECISIONS_D01_D05.md` left limits "to be pinned in the P1 contract"
    (line 51); `RESEARCH_LATTICE_FULL_BUILD.md` §8 item 8 names numeric ceilings as open.
  - One ballot per (voter, proposal version) implements the non-replaceable rule and the
    evidence-not-input treatment of a second differing ballot
    (`SPEC_KNOWN_PEER_PROTOCOL.md` §7, §14).
  - A bounded grant expiry (proposed ≤ 1 h) and a 128-bit nonce bound replay and meet the
    signed-grant-with-nonce-and-expiry contract (`SPEC_KNOWN_PEER_PROTOCOL.md` §12.2;
    `OWNER_DECISIONS_D01_D05.md` D-04).
  - These are proposals, not defaults: "No defaults are invented here"
    (`SPEC_LATTICE_FOUNDATION_REPAIRS.md:75`).
- **Rejected alternative:**
  - Shipping "reasonable defaults" without owner pinning — `SPEC_LATTICE_FOUNDATION_REPAIRS.md:75`
    forbids inventing defaults; D-05 explicitly left the ceilings unpinned.

## D-06.9 — Evidence retention and pruning horizon

- **Decision (proposed):** 30 days (D-05), with these mechanics: the full ballot set for a
  proposal — including every equivocation pair — is retained for the dispute window; pruning
  never removes undecided proposals, unexpired grants, executed-grant keys, membership
  history, or equivocation evidence (SPEC_KNOWN_PEER_PROTOCOL.md §17); the hash-chain head is
  retained permanently.
- **Rationale:**
  - D-05 fixes evidence retention at 30 days (`OWNER_DECISIONS_D01_D05.md:56`).
  - `SPEC_KNOWN_PEER_PROTOCOL.md` §16 requires pruning never to remove undecided proposals,
    unexpired grants, `executed_grants` keys, membership history or equivocation evidence.
  - Equivocation handling retains both conflicting ballots verbatim as the proof
    (`SPEC_KNOWN_PEER_PROTOCOL.md` §14; `RESEARCH_LATTICE_FULL_BUILD.md` §5.3); silent
    replacement destroys the evidence.
  - The hash-chain head is retained and independently anchored as a precondition for any
    tamper-evidence claim (`SPEC_KNOWN_PEER_PROTOCOL.md` §16).
- **Rejected alternative:**
  - Pruning by wall-clock age only or dropping equivocation pairs — destroys dispute evidence
    and the first-seen race it is meant to prevent (`SPEC_KNOWN_PEER_PROTOCOL.md` §14).

## Contract skeleton this decision freezes (Slice A deliverables)

Condensed from `RESEARCH_LATTICE_FULL_BUILD.md` §7, Slice A:

- Canonical schemas for: proposal, affected-snapshot, ballot, membership, certificate, grant,
  intent, outcome, reconciliation, executed-grant, recovery.
- Signature and digest rules: epoch/root binding, closed-world validation.
- Quorum arithmetic as three versioned parameters: participation floor, approval
  supermajority, abstain/against counting rule.
- API surface and error enums.
- Adversarial review of the contract.
- Acceptance for Slice A is "written, reviewed, committed — nothing runs."
  (`RESEARCH_LATTICE_FULL_BUILD.md:427`)

## What these decisions unblock

- The P1/P2 contract freeze named in `docs/CURRENT_STATE.md` ("freeze the P1/P2 contracts
  against the approved D-01–D-05 decisions") and the start of Slice B.

## Still required before live actuation

- Independent adversarial review of the contract; Slices B and C evidence. Approval of D-06
  is not release authorization.
