# Slice D — GUI ballot surface: real human ballots, end to end

Recorded 2026-11-22 on branch `feat/slice-d-ballots` (Phase 4, Slice D product wiring).

Research contract (`docs/RESEARCH_LATTICE_FULL_BUILD.md` §7, Slice D):

> UI/Telegram show affected parties, status, evidence and real explicit human
> input. **No synthetic user/Butler ballots.**

This document records the delivered surface, the exact owner flow, and the
boundaries that keep every ballot a real human attestation.

## What was built

- **Backend** (`src/agency/api/routers/governance.py`): `GET /v1/governance/proposals`
  now returns, per open proposal, the affected-party set (`eligible_voters`),
  and every recorded lattice vote (`voter_id`, `decision`, `evidence`, `weight`,
  `timestamp`). The route remains behind the router-level `require_peer_authority`
  grant gate — fail-closed, unchanged.
- **CLI** (`src/agency/cli/main.py`): new dev helper
  `consensus-sign-grant` builds and signs a task-scoped grant payload for the
  `X-Agency-Grant` header via `GrantLedger.issue` (round-trip verified against
  `grant_from_payload` + `GrantLedger.verify`).
- **GUI** (`gui/src/routes/Lattice.tsx`, `gui/src/api/governance.ts`,
  `gui/src/stores/auth.ts`): the Lattice page replaces its locked placeholder
  with a governance panel that (1) reads proposals through a pasted, owner-minted
  grant and shows affected parties, status, quorum/vote counts, expiry and
  per-vote cited evidence; (2) drives the real-ballot flow below.

### No synthetic ballots — hard statement

The GUI never signs, never fabricates, never defaults a decision, and never
holds key material. There is no demo mode, no "vote as user", no server-side
signing with peer keys on the GUI path, and no Telegram synthetic vote path was
touched. A ballot exists only when a human ran `consensus-sign-ballot` with
their own Ed25519 key outside the browser (decision **D-06**: signature from the
owner's device, bound to the proposal identity) and pasted the result in. The
backend's `PeerAuthority` is the only verifier; the panel merely transports the
pasted bytes and renders the verdict verbatim.

## Auth posture (unchanged gates)

- `GET /v1/governance/proposals` — grant-gated read (`READ_SCOPES` in
  `src/agency/api/authority.py`): requires a valid, unexpired, unreplayed
  `X-Agency-Grant` bound to exactly `GET /v1/governance/proposals` (or the
  broader `READ` scope), pinned to the server's membership epoch/root. Without
  a grant: 503. The panel attaches the header the owner pasted; it does not
  mint, extend, or weaken grants.
- `POST /v1/governance/ballot` — no grant, no bearer: the Ed25519 envelope
  signature *is* the authority. It is verified against the pinned genesis
  membership (epoch/root binding, duplicate-seq and equivocation rejection).
  Missing authority still fails closed (503).
- `POST /v1/governance/propose` — stays write-closed by default (needs
  `AGENCY_V1_OPEN_WRITES=1` + `AGENCY_FINALITY_POLICY` + a write-scope grant +
  `X-Agency-Finality` proof).

## Owner flow, end to end

All commands run from the repo root of the worktree. The CLI invocation used
everywhere is the console-script entry path (note: `python -m agency.cli.main`
cannot see the `consensus-*` commands because the `__main__` guard sits before
they are registered — a pre-existing layout quirk):

```bash
export AGENCY_CLI='PYTHONPATH=src .venv/Scripts/python.exe -c "from agency.cli import main; main()"'
```

### 0. Environment

```bash
uv sync --extra dev --extra llm        # if .venv is missing (revert uv.lock churn: git checkout -- uv.lock)
```

### 1. Pin a local genesis (once) and keep the owner key offline-safe

`data/consensus/genesis.json` is the pinned membership the server loads
(`AGENCY_GENESIS_PATH`, default `data/consensus/genesis.json`). If you do not
have one, generate a 4-member genesis; member 0 is the owner in this example:

```bash
PYTHONPATH=src .venv/Scripts/python.exe - <<'PY'
import base64, json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from agency.peers.identities import PeerIdentity
from agency.peers.membership import derive_genesis_membership

keys = [Ed25519PrivateKey.generate() for _ in range(4)]
ids = [PeerIdentity.from_private_key(k) for k in keys]
members = [
    {"peer_id": i.peer_id, "public_key": i.fingerprint,
     "roles": ["validator"], "joined_epoch": 1}
    for i in ids
]
with open("data/consensus/genesis.json", "w", encoding="utf-8") as f:
    json.dump({"policy_version": "peer-policy/1", "members": members}, f, indent=2)
print("owner key (member 0, base64 Ed25519 private bytes):")
print(base64.b64encode(keys[0].private_bytes_raw()).decode())
PY
```

Copy the printed key into your shell only (`export OWNER_KEY=...`). It never
goes into the browser.

### 2. Start the API server

```bash
$AGENCY_CLI start                     # uvicorn agency.api.server:app on 127.0.0.1:8000
```

On startup the lifespan wires `PeerAuthority`, the `GrantLedger`, and pins
`grant_bft_time_ms` from `AGENCY_GRANT_BFT_TIME_MS` (default `1000`). Grants
minted with the helper's default `--issued-at-ms 1000 --expires-at-ms 61000`
verify against that pin; if you change the env pin, pass matching times.

### 3. Start the GUI

```bash
cd gui && npm ci && npm run dev       # vite on http://localhost:5173/mission-control
```

The dev server proxies `/v1` to `http://127.0.0.1:8000` (`gui/vite.config.ts`).
For the served build instead: `npm run build` (output is the tracked
`src/agency/prototype/static/mission-control` bundle served by the prototype app).

### 4. (Optional) create a proposal to vote on

Proposal creation is an owner write — keep the write gates explicit:

```bash
export GRANT=$( $AGENCY_CLI consensus-sign-grant \
  --genesis data/consensus/genesis.json --key "$OWNER_KEY" \
  --task-id "POST /v1/governance/propose" --proposal-id prop_00000000000000000000000000000000 \
  --issued-at-ms 1000 --expires-at-ms 61000 --json | .venv/Scripts/python.exe -c "import json,sys;print(json.load(sys.stdin)['grant_header'])" )

AGENCY_V1_OPEN_WRITES=1 AGENCY_FINALITY_POLICY=owner-local \
$AGENCY_CLI start &                   # server restart required to pick up the env pins

curl -s http://127.0.0.1:8000/v1/governance/propose \
  -H "X-Agency-Grant: $GRANT" \
  -H 'X-Agency-Finality: {"height": 1, "app_hash": "local"}' \
  -H 'Content-Type: application/json' \
  -d '{"name": "archivist", "domain": "memory", "capabilities": ["recall"], "quorum": 0.66, "ttl": 3600}'
```

The response `proposal_id` (`prop_` + 32 hex) is what you vote on.

### 5. Mint the read grant and open the panel

```bash
$AGENCY_CLI consensus-sign-grant \
  --genesis data/consensus/genesis.json --key "$OWNER_KEY" \
  --task-id "GET /v1/governance/proposals" \
  --proposal-id prop_00000000000000000000000000000000 --json
```

Copy the printed `grant_header` (base64) into the Lattice page's "Owner read
grant" box and press **Use grant**. The panel then shows every open proposal:
id, type, proposer, status, quorum required, votes recorded, expiry, eligible
voters, and each recorded vote with its cited evidence. A rejected/expired
grant surfaces the backend's exact 503 detail — re-mint and re-paste.

### 6. Cast a real ballot

In the proposal card, open "Cast a ballot on this proposal", pick the decision
(`approve|deny|abstain`) and seq (start at 1; the panel prints the exact
command, e.g.):

```bash
$AGENCY_CLI consensus-sign-ballot \
  --genesis data/consensus/genesis.json --key "$OWNER_KEY" \
  --proposal-id prop_<from-the-list> \
  --decision approve --seq 1 --json
```

Copy the `wire_hex` from the output, paste it into the panel's envelope box,
and press **Submit ballot for verification**. The panel POSTs the pasted bytes
unchanged to `POST /v1/governance/ballot` and renders the backend verdict:
signed-by peer, decision, seq, tally (approve/deny/abstain), quorum
(3-of-4 pinned validators), and outcome (`open` → `passed`). Any rejection
(bad signature, wrong epoch, duplicate seq, equivocation) is shown verbatim
from the backend's 400 detail. A second ballot from the same voter with a
different decision is rejected as equivocation.

Note on stores: ballots are tallied by `PeerAuthority` (the consensus record);
the lattice listing shows lattice-level votes. The panel shows both honestly —
verdict inline, listing on refresh.

## Tests

- `tests/test_governance_proposals_listing.py` — the listing renders
  `eligible_voters` + votes (with evidence/weight/timestamp) from real
  `ConsensusProposal`/`Vote` model data; 503 without a grant; 503 for a
  wrong-scope grant. (2 tests)
- Existing governance/auth posture groups re-run green with the changes:
  `test_http_grant_gate.py`, `test_http_ballot.py`,
  `test_governance_http_boundary.py`, `test_api_auth.py` (22 tests).
- The grant and ballot CLI helpers were verified live against the server-side
  verifiers (`grant_from_payload` + `GrantLedger.verify`, and
  `PeerAuthority.submit_ballot`).
- GUI: `tsc -b` clean, `oxlint` clean, `vite build` bundles. The gui has **no
  component-test infrastructure** (no vitest/playwright in `gui/package.json`);
  adding a runner would mean a new dependency, deliberately not done here.

## Boundaries / known limits

- `grant_bft_time_ms` is a pinned env value (no wall clock in the verifier);
  grants live at most 1 hour of that pin (`MAX_GRANT_LIFETIME_MS`).
- The panel cannot create proposals; that stays an owner CLI/write-gate path.
- Single-process runtime: `PeerAuthority` tally is in-memory (existing design);
  a server restart clears tallies (durable ballot log remains a follow-up).
