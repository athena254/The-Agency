# Owner authority decisions D-01 – D-05 (approved)

Status: **APPROVED by the owner** (2026-11-08). These are the policy inputs for the
P1/P2 contracts. They do not by themselves enable beta, peer actuation, or release;
they unblock the contracts that the remaining gates are built against.

## D-01 — Validators and consensus

- **Validator set:** 4 validator peers.
- **Fault tolerance:** 1 Byzantine (compromised) peer.
- **Application approval rule:** 3/4 quorum (supermajority of the validator set).
- **Consensus engine:** adopt the already lab-exercised **CometBFT 0.38.26** KV
  application, version-pinned. The four-process loopback lab is evidence the engine
  runs, not approval of any other configuration.
- **Determinism:** model/tool execution stays outside consensus transitions; only
  ordered commands and validated receipts enter the state machine.

## D-02 — Membership

- **Genesis:** the 4 validators above.
- **Admission / removal / rotation / revocation:** by signed membership proposal
  approved at the D-01 quorum, with an **epoch bump** on every membership change.
- **Cross-epoch conflicts:** rejected; a peer must catch up to the current epoch
  before it can participate.
- **Coordination:** replaceable; no single coordinator can act unilaterally.
- **No central membership administrator.** A peer cannot grant itself voting power.

## D-03 — Affected consumers and human consent

- **Affected set:** each task declares the consumers it affects.
- **Attributable input:** every affected consumer contributes signed, attributable
  input. Missing input blocks the task.
- **Override rule:** no gateway/orchestrator/"user" override. Override requires an
  explicit human attestation.
- **Human consent:** a signed attestation bound to the exact request/action
  (Telegram confirm or UI action). Gateway signatures alone are insufficient.

## D-04 — Grants and actuators

- **Grant format:** signed, task-scoped, with a nonce and an expiry.
- **Finality proof:** actuators verify the grant **and** a finality proof before any
  protected effect.
- **Revocation:** epoch-based; a revoked grant cannot authorize new effects.
- **Target-side idempotency / fencing:** task-scoped fences prevent replay and
  stale-worker publication.

## D-05 — Resources and provider

- **Fixture/output resources:** local-only; no private directories, shell, money
  movement, account changes, self-modification, or general execution authority.
- **Limits:** bounded numeric ceilings (to be pinned in the P1 contract).
- **Runtime model/provider:** Inception `mercury-3` via the OpenAI-compatible
  endpoint already configured on the box.
- **Data policy:** no confidential data; no paid fallback; no account/region/quota
  bypass.
- **Evidence retention:** 30 days.
- **Build vs runtime:** free CLI workers (OpenCode) are used to **build** the
  runtime; they are not the runtime model.

## What these decisions unblock

- P1 contract: identity, budget, audit chain (D-05).
- P2 contract: peer application authority (D-01 – D-04).
- The bounded-autonomy acceptance scenarios (AC-01 – AC-24) can now be designed
  against a fixed policy.

## Still required before live actuation

Independent review, exact-revision CI, isolation evidence (P3), and explicit
rollout authorization. Approval of these decisions is not release authorization.
