# Process-local Lattice ownership repair — implementation contract

Status: reviewed engineering contract for the owner's requested repair batch; not implemented, not peer authority and not deployment approval. Base source `357e4d8`; predecessor investigation used equivalent factory/caller source at `6e14d07`.

## Decision and scope

Choose explicit process-local resource leases to preserve existing shared SQLite-memory/fallback-memory state. Do not silently replace a shared in-memory backend with per-owner disconnected state. Opaque tokens prevent accidental lifetime-accounting errors; they are NOT security capabilities, peer grants, membership or consensus. No resource visibility, retention, human approval or voting policy changes.

Parent source review confirms the factory publishes before initialize and production callers directly close a shared cached instance. The investigation's controlled probes and timings are separate evidence, not a full suite pass. `SPEC_QUALIFICATION_REPAIR_FOLLOWUP.md` records the failed global qualification.

## Frozen public interfaces

```python
async def acquire_lattice(config: LatticeConfig | None = None) -> LatticeLease: ...
class LatticeLease:
    @property
    def lattice(self) -> Lattice: ...
    async def release(self) -> None: ...
async def get_lattice(config: LatticeConfig | None = None) -> Lattice: ...
async def reset_lattice() -> None: ...
```

Only `lattice` and `release()` are required lease-facing interfaces. Constructor/token issuance are manager-internal, not public minting APIs. Existing `get_lattice` identity behavior remains compatible through one explicit legacy pin; managed public close becomes a clear ownership error. Both production consumers must migrate together before the change is qualified.

### Public surface and ownership

- Add `await acquire_lattice(config=None) -> LatticeLease`. A lease contains `.lattice` plus an opaque manager-owned generation/token and has idempotent `await release()`. No `release(lattice)` or caller-supplied decrement count. Copying a lease must not create another ownership claim; duplicate/concurrent release consumes its token at most once.
- Each orchestrator successful start owns exactly one lease; repeated start adds none. Its task/tool/Butler consumers borrow `.lattice`. Stop/failed start releases only its lease and clears the borrowed field. Butler does not acquire a redundant lease.
- Each API application lifespan owns exactly one lease, stores only its borrowed `.lattice` in application state, and releases its own lease on normal, exceptional or cancelled exit. Put acquisition and already-allocated memory/evidence cleanup inside one protected startup/exit scope; today's try/finally begins too late to roll back failed startup.
- Managed factory Lattice objects reject direct public `close()` with an explicit ownership error before touching their backend. Manager-only finalization uses a private authorized close path. Independently constructed `Lattice(config)` retains its current caller-owned initialize/close contract. This protection is needed because a raw public shared handle cannot otherwise prevent a legacy consumer closing another owner's connection. Direct backend manipulation remains outside the supported API, not a security boundary.

### Legacy get compatibility (explicit, not implicit refcounting)

- Keep `get_lattice(config=None)` returning the same ready managed Lattice identity within a generation. Its first use creates ONE process-lifetime legacy pin, not a new owner on every getter. It is a borrowed accessor with no per-call release semantics; public close is rejected as above. Repeated getters must not leak reference counts.
- Migrate BOTH actual production factory callers to leases in the same change. Legacy getters remain a documented compatibility/test seam, not the new production ownership mechanism. An explicit legacy pin prevents last explicit release from closing that generation until reset. This deliberate retention is safer than pretending an untracked getter has released ownership.
- Config is captured as a stable snapshot per generation. A subsequent explicitly supplied different effective config is rejected; never silently reuse a handle for the wrong path. None means the existing generation/default. Do not print config secrets in errors.

### Acquire/startup/cancellation

- Serialize acquisition and generation retirement with an asyncio manager lock owned by the running event loop, created lazily. Construct a private candidate, initialize fully, then publish READY and mint a lease. Never publish an initializing/failed/closed candidate.
- The simplest acceptable implementation holds that lock through initialize/rollback rather than spawning a shared init task. A cancelled waiter that has not acquired a lease contributes no owner; cancellation of the initializer rolls back its private candidate before another waiter may create a generation. Existing waiters can then initialize their own successful generation. Do not accidentally cancel another owner's live connection.
- Catch BaseException around startup so CancelledError is included. Preserve the original exception if cleanup fails; record cleanup exception class separately. Cleanup must actually complete (or leave a retained FAILED/cleanup-needed tombstone that rejects acquisition), not an untracked shielded/fire-and-forget coroutine. A repeated cancellation must not release the serialization guard while owned cleanup is still running.
- SQLite owns its connection immediately when connect returns; every subsequent pragma/migration failure/cancellation closes that connection and resets its fields. Reinitialize either completes usable schema or fails cleanly. No leak on prepublication or partially migrated paths. Awaited connect cancellation also needs explicit testing against the installed aiosqlite behavior; wrapping only schema migration is insufficient.

### Release/close failure/reset/event loops

- Only the last explicit lease with no legacy pin retires a READY generation. Under the manager guard mark it unavailable before await close; new acquire must not reuse it or race retirement. Releasing A while B is live does not close, reset or reopen B's backend. Tokens are generation-specific: old/double releases cannot decrement a newer generation.
- If final close fails or is cancelled, never republish/reuse the object. Retain a cleanup-needed tombstone and surface failure; block new acquisition until the owned cleanup is completed. An explicit quiescent reset can retry that cleanup without decrementing consumed tokens again. Preserve cleanup attempts for independent service-owned stores.
- `reset_lattice()` remains a test-only quiescent operation: reject while ANY explicit lease or initialization is live, rather than force-invalidating consumers. With zero leases, clear the legacy pin, finish close/failed cleanup, then remove the generation. A close failure surfaces and retains cleanup-needed state instead of being swallowed as today's reset does. Repeated clean reset is harmless. Tests using legacy references assert quiescence; an old borrowed reference is invalid after explicit reset and must not magically resurrect.
- A live generation/lock is confined to one event loop. Cross-loop acquire/reset/release fails clearly before touching it. After full release/reset, a fresh generation may be created on a new loop. Never solve loop-bound locks by silently creating one singleton per loop: that splits fallback-memory state and changes current process-sharing semantics. No new cross-thread/process service is proposed.

## Acceptance sketches only (NOT added to tests/)

1. **Actual callers, same local SQLite file:** start two orchestrators with leases; stop A; B register/create/read succeeds; restart A and read the committed row; stop B while A remains live; final release closes once. Also exercise API lifespan + orchestrator in the same loop, both stop orders. No patched fresh-Lattice factory for these tests.
2. **Service restart:** one orchestrator and one Butler start/stop/start with real factory + temporary SQLite; no closed cached handle; exactly one lease per active orchestrator. Butler has no extra lease. Existing borrowed memory semantics unchanged.
3. **Memory sharing:** concurrent leases share actual InMemoryLatticeBackend state; also SQLite `:memory:` state. Stop one, survivor still sees rows. Last-release ephemeral reset is explicitly tested, not presented as file persistence.
4. **Initialization rendezvous:** gate initialize; second acquire cannot return early; release gate and both get one ready instance. Cancel one waiting acquire: survivor succeeds and there is no phantom owner. Cancel initializer: cleanup completes before waiter initializes a replacement. Inject ordinary failure and cancellation after connect, pragma, migrate and caller post-acquire setup; assert no cached failed handle and all created connections close.
5. **Release/reset safety:** concurrent double release closes once; stale-generation release cannot affect new generation; direct managed close rejected; standalone Lattice close still works; reset with active leases rejected without invalidating them; reset racing initializing acquire rejected; final cleanup failure/cancellation blocks reuse and quiescent retry closes; primary startup exception survives cleanup failure.
6. **Legacy compatibility:** repeated get preserves identity and one pin; legacy pin keeps explicit lease release from closing it; reset when quiescent replaces it; mismatched explicit config rejected; no silent changed path. Adapt existing `test_api.py:42-55` and `test_integration.py:46-49`, not delete their compatibility assertions.
7. **Loop isolation:** deliberately contend the SQLite/manager locks to bind them; foreign-loop acquire/release/reset has a deterministic owned-loop error, never cryptic reuse of a bound lock. After same-loop cleanup, a fresh loop succeeds.
8. **Exact warning seam:** synthetic closed standalone SQLite passed to actual submit_task yields captured RuntimeError at lattice.task_create_failed and returns without Rich formatting. This is diagnostic qualification, not the ownership fix. If logging changes are approved separately, test bounded non-locals rendering independently.

Suggested implementation ownership after approval: factory.py, lattice/__init__.py exports, api.py managed-close lifecycle boundary, SQLite backend rollback, orchestrator acquisition/release, API lifespan acquisition/release, and focused ownership tests. Coordinate existing lifecycle-test monkeypatch seams (currently patch orchestrator.get_lattice) and prototype tests that deliberately fail that acquisition. Butler borrowing should need no independent acquisition. Broader logging policy and unexplained earlier suite failures remain separate investigations.


## Implementation ownership and sequencing

Foundation SOLO: `src/agency/lattice/factory.py`, `src/agency/lattice/__init__.py`, managed-close boundary in `src/agency/lattice/api.py`, initialization rollback in `src/agency/lattice/backends/sqlite.py`, new `tests/test_lattice_ownership.py`. No task/governance/query behavior change. Read existing models/config and store internals before choosing serialization or cleanup mechanisms. No new runtime dependency.

After foundation tests and interfaces are independently reviewed, consumer integration: `src/agency/orchestrator.py`, `src/agency/api/server.py`, existing `tests/test_service_lifecycle.py` mock seams, new `tests/test_lattice_consumer_lifecycle.py`; additional existing test changes only with parent review. Butler continues to borrow the orchestrator's handle and needs no extra lease. Preserve all HTTP/beta default-deny gates.

A foundation commit alone is not a qualified runtime repair: the existing consumers would still call the legacy getter/direct close. Keep it isolated until both adapters and actual-consumer tests are complete. Gate integration on real SQLite plus both ephemeral backends, deterministic concurrency/cancellation tests, existing lattice/API/service/prototype tests, Ruff, strict mypy and exact-revision full qualification. Never hide a close failure with a fallback backend or a success status.

## Cancellation and loop precision

Cleanup must own and drain any connection-open/close task before releasing exclusion. Do not treat dropping a reference, cancelling a wrapper, or awaiting an untracked shield as closure. Repeated cancellation must not publish a candidate or allow another initializer to race outstanding cleanup. Keep a retained cleanup-needed state if safe cleanup cannot complete, block reuse, and make quiescent reset the explicit recovery boundary. Enforce one active owning event loop, not silently separate caches per loop. An idle, fully retired/reset generation may be followed by a fresh loop.

## Evidence and rollout

Regression-first RED/GREEN, scoped commits, synthetic scratch-only SQLite, explicit echo and inherited external-network denial. No installs, private runtime/credentials, real providers, daemons, push, merge to main or deployment. Local resource leases do not establish decentralized operation or close P1/P2 production gates.
