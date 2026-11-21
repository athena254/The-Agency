# Tier 6 (staged surface opening) implementation status

Recorded 2026-11-21 on branch `feat/consensus-wave1`.

## Track A — landed

- GUI Skills/Evidence/Memories now render read-only `/api/pilot/observe/*` data via a shared `ObservePanel`; honest chips retain the full-text-recall and runtime-binding caveats. Forge stays status-only, Lattice stays locked. No `/v1` gate change was required for this track.
- `agency forge status` and `agency forge ship` added; ship requires explicit `--approved` or an audited `-yolo/AGENCY_YOLO` bypass (`approval_source`/`yolo_audited` recorded in the receipt), requires both gates PASS, never pushes/deploys, exits non-zero on FAIL. Both CLI entry points (`agency`, `forge`) wired.
- Offline `scripts/agency_core_smoke.py` added to CI and CD gate jobs. Live/credentialed scripts remain manual.

## Track B — landed, staged by explicit owner config

- `require_peer_authority` is now a Request-aware, fail-closed verifier: signed grant from `X-Agency-Grant`, canonical reconstruction via `grant_from_payload`, exact `METHOD path` scope binding, pinned membership epoch/root, BFT-time expiry/replay/revocation through `GrantLedger`.
- API lifespan seeds `PeerRegistry` from the pinned genesis public keys (previously an empty registry made real ballots fail "unknown peer").
- B1 read scopes are reachable with a valid scoped grant; without a grant the routes still return 503. Bare-router 503 regression tests retained.
- Memory-search owner scope (`agent_id`) must appear in the grant's `affected_consumers`.
- B3/B4 write scopes require **both** `AGENCY_V1_OPEN_WRITES=1` and `AGENCY_FINALITY_POLICY` plus a valid `X-Agency-Finality` proof. Default remains closed. This preserves `SPEC_KNOWN_PEER_PROTOCOL.md:180,261`: no grant authorizes actuation until §10 finality is decided. The finality check is a policy placeholder (height + app_hash presence), not a cryptographic finality protocol.
- `POST /v1/governance/vote` remains a legacy 400 stub and is not allowlisted; the signed ballot route is the authority source.

## Boundaries

No push, deploy, remote merge, beta enablement, GUI release, or independent-host claim. Resource-side fencing at the bridge actuator is not implemented; bridge execute remains behind the owner config gates and must not be treated as production-ready.

## Landing adaptation (2026-11-21, land/lattice-wave1)

Cherry-picked B0/B1 (`136c615`→`8604f8a`) and B2–B4 (`bc0cd87`→`11854fa`) onto the
wave1+main merge. Adaptations for the merged tree (which carries main's bearer-token
intake that the source branch predated):

- `/v1/tasks` keeps its bearer-token route auth; the grant+finality write path is
  exercised on the grant-gated `POST /v1/governance/propose` instead (tests repointed).
- Middleware: health/ballot/scoped-read/scoped-write admission (Tier6) layered ahead
  of main's `ENABLED_AUTHED_PREFIXES` bearer branch; default stays fail-closed 503.
- Peer-authority genesis wiring moved into the merged lifespan startup (it was
  absent in both parents — `state.peer_authority` was never set before this).
- Tier6 track A (`2dc83cb`, GUI observe panels + forge ship CLI) intentionally NOT
  ported: GUI superseded by feat/mission-control-upgrade's observe pages; CLI is
  tangential to Slice C/D and can be cherry-picked later if wanted.
