# SPEC_LATTICE.md — Unified Lattice Service

> **Status:** TARGET BOUNDARY RECONCILED; LOCAL PROTOTYPE IS NOT PEER AUTHORITY
> **Version:** 1.1
> **Date:** 2026-09-22
> **Author:** Hermes Agent + Danny Dis

> **Architecture correction:** This earlier SQLite-first, *central* single-source-of-truth design describes an implementation phase, not the owner's target. The Agency must be fully decentralized: agents collectively govern through peer proposals, affected-consumer input, attributable votes and accountable outcomes; Butler only connects users to that system. See [SPEC_PEER_LATTICE_GOVERNANCE.md](SPEC_PEER_LATTICE_GOVERNANCE.md). Do not call the current single-process governance engine decentralized consensus.

---

## 1. Purpose & Scope

The **Lattice** is the shared hive-memory, messaging, coordination and orchestration layer for The Agency. It connects independently identified agents and supports collective knowledge about:

- **What agents exist** and their current state
- **What tasks are running** and their dependencies
- **What has been delivered** and its evidence
- **What decisions were made** and by whom
- **Who trusts whom** (peer scoring, governance)

Cross-agent coordination uses the Lattice through reviewed interfaces. Shared memory is permission-scoped; it does not require replicating private content to all peers or one authoritative shared database. Butler is the human gateway, not an agent or validator. Orchestration is a Lattice function, not a separate decision-making boss.

### 1.1 What the Lattice IS
- **Hive memory:** permission-scoped knowledge, context, evidence, provenance and work history.
- **Messaging:** authenticated exchange of requests, findings, proposals and results; durable delivery/replay remains an implementation gap.
- **Coordination:** dependencies, assignments, peer inputs and independent checks.
- **Orchestration:** collective task progression under approved rules, using replaceable scheduling roles.
- Graph/vector/relational APIs and local event logs support these functions; they are not themselves peer consensus.

### 1.2 What the Lattice is NOT
- It is not **merely** a message bus: messaging is one of its responsibilities. No particular broker is selected or required by this document.
- It is NOT a file store (use filesystem/S3 for artifacts)
- It is NOT an LLM (it provides data TO agents, not reasoning)

---

## 2. Design Principles

| Principle | Meaning |
|-----------|---------|
| **SQLite-first** | Core operation requires ZERO external dependencies |
| **Backend-agnostic API** | Agents never know or care which DB is running |
| **Event-sourced** | Every state change is an immutable event |
| **Governance-native** | Voting, reputation, and consensus are first-class concepts |
| **Graceful degradation** | Works on SQLite alone; scales with Neo4j + Qdrant |

---

## Implementation status of the remaining local design

Sections 3–6, configuration and the historical file/test/migration sketches preserve local prototype design material, not an approved peer protocol or a complete implemented-API inventory. Optional indexes do not confer authority. The SQLite backend persists proposal state, the separate GovernanceEngine maintains process-local proposal state with best-effort mirroring, and the in-memory backend has different duplicate-ballot behavior; none is independently replicated peer governance. A local SQL transaction or reputation score cannot grant action authority. See `CURRENT_STATE.md` for implemented scope and `SPEC_LATTICE_FOUNDATION_REPAIRS.md` for the repair boundary.

## 3. Data Model

### 3.1 Node Types

```python
class NodeType(str, Enum):
    AGENT = "agent"           # An agent instance
    TASK = "task"             # A unit of work
    DELIVERABLE = "deliverable"  # Output of a task
    EVIDENCE = "evidence"     # Proof attached to a claim
    DECISION = "decision"     # A governance decision
    POLICY = "policy"         # An active policy rule
    WING = "wing"             # Per-agent storage namespace
    EVENT = "event"           # Audit log entry (special)
```

### 3.2 Edge Types

```python
class EdgeType(str, Enum):
    DEPENDS_ON = "depends_on"     # Task A needs Task B
    PRODUCED_BY = "produced_by"   # Deliverable created by Task
    EVIDENCE_FOR = "evidence_for" # Evidence supports Deliverable/Claim
    VOTED_ON = "voted_on"         # Agent voted on Decision
    GOVERNS = "governs"           # Policy controls Agent/Task
    MEMBER_OF = "member_of"       # Agent belongs to Wight/Wing
    TRUSTS = "trusts"             # Agent reputation link
    SUPERSEDES = "supersedes"     # Decision replaces prior decision
```

### 3.3 Event Model (Immutable)

```python
@dataclass(frozen=True)
class LatticeEvent:
    event_id: str           # ULID
    timestamp: datetime     # UTC
    event_type: str         # "node_created", "edge_added", "node_updated"
    actor: str              # Agent/system that caused the event
    target_id: str          # Node or edge affected
    payload: dict           # Event-specific data
    prior_state: dict | None  # For updates: what it was before
```

### 3.4 Governance Primitives

```python
@dataclass
class Vote:
    voter_id: str           # Agent that voted
    proposal_id: str        # What is being voted on
    decision: str           # "approve", "deny", "abstain"
    evidence: list[str]     # Evidence IDs supporting the vote
    weight: float           # Reputation-weighted vote strength
    timestamp: datetime

@dataclass
class Reputation:
    agent_id: str
    score: float            # 0.0 - 1.0
    tasks_completed: int
    tasks_failed: int
    peer_ratings: list[float]  # Rolling window
    last_updated: datetime

@dataclass
class ConsensusProposal:
    proposal_id: str
    proposer_id: str
    proposal_type: str      # "spawn_agent", "replace_agent", "policy_change"
    quorum_required: float  # 0.0 - 1.0
    votes: list[Vote]
    status: str             # "open", "passed", "denied", "expired"
    expires_at: datetime
```

---

## 4. API Surface

### 4.1 Node Operations

```python
async def create_node(
    node_type: NodeType,
    properties: dict,
    actor: str = "system"
) -> str: """Returns node ID, logs event"""

async def get_node(node_id: str) -> dict | None: ...

async def update_node(
    node_id: str,
    properties: dict,
    actor: str = "system"
) -> bool: """Merge update, log event with prior_state"""

async def delete_node(node_id: str, actor: str = "system") -> bool: ...

async def find_nodes(
    node_type: NodeType | None = None,
    filters: dict | None = None,
    limit: int = 100,
    offset: int = 0
) -> list[dict]: """Type + property filter"""
```

### 4.2 Edge Operations

```python
async def add_edge(
    source_id: str,
    target_id: str,
    edge_type: EdgeType,
    properties: dict | None = None,
    actor: str = "system"
) -> str: """Returns edge ID"""

async def get_edges(
    node_id: str,
    direction: str = "both",  # "in", "out", "both"
    edge_type: EdgeType | None = None
) -> list[dict]: ...

async def remove_edge(edge_id: str, actor: str = "system") -> bool: ...
```

### 4.3 Graph Traversal

```python
async def traverse(
    start_node: str,
    edge_type: EdgeType | None = None,
    max_depth: int = 3,
    direction: str = "out"
) -> list[str]: """Return node IDs reachable within depth"""

async def find_path(
    source: str,
    target: str,
    max_depth: int = 5
) -> list[str] | None: """Shortest path between nodes"""

async def get_dependencies(task_id: str) -> list[str]: """All tasks this task depends on"""

async def get_dependents(task_id: str) -> list[str]: """All tasks depending on this task"""
```

### 4.4 Vector Search (when Qdrant is available)

```python
async def upsert_vector(
    collection: str,
    vectors: list[tuple[str, list[float], dict]],  # (id, vector, payload)
) -> bool: ...

async def search_vectors(
    collection: str,
    query_vector: list[float],
    limit: int = 10,
    filters: dict | None = None
) -> list[dict]: """Returns {id, score, payload}"""

async def search_by_text(
    collection: str,
    text: str,
    embedder: callable,
    limit: int = 10
) -> list[dict]: """Embed text then search"""

async def delete_vectors(collection: str, ids: list[str]) -> bool: ...
```

### 4.5 Governance Operations

```python
async def submit_proposal(
    proposer_id: str,
    proposal_type: str,
    payload: dict,
    quorum: float = 0.5,
    ttl_seconds: int = 3600
) -> str: """Returns proposal ID"""

async def cast_vote(
    voter_id: str,
    proposal_id: str,
    decision: str,
    evidence: list[str] | None = None
) -> bool: ...

async def get_proposal_status(proposal_id: str) -> ConsensusProposal: ...

async def update_reputation(
    agent_id: str,
    success: bool,
    peer_rating: float | None = None
) -> Reputation: ...

async def get_reputation(agent_id: str) -> Reputation: ...

async def check_quorum(proposal_id: str) -> bool: ...
```

### 4.6 Event Log

```python
async def get_events(
    target_id: str | None = None,
    event_type: str | None = None,
    actor: str | None = None,
    limit: int = 100
) -> list[LatticeEvent]: ...

async def replay_events(
    from_timestamp: datetime,
    to_timestamp: datetime | None = None
) -> list[LatticeEvent]: ...

async def get_audit_trail(node_id: str) -> list[LatticeEvent]: """All events for a node"""
```

### 4.7 Health & Status

```python
async def get_status() -> dict:
    return {
        "backend": "sqlite" | "neo4j+qdrant",
        "node_count": int,
        "edge_count": int,
        "event_count": int,
        "agent_count": int,
        "task_count": int,
        "pending_proposals": int,
        "last_event_at": str | None,
        "storage_size_mb": float,
    }

async def get_agent_count() -> int: ...
async def get_active_tasks() -> list[dict]: ...
```

---

## 5. Backend Adapters

### 5.1 SQLite (Default, No External Dependencies)

**Schema:**
```sql
-- Core tables
CREATE TABLE nodes (
    id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL,
    properties JSON NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    properties JSON,
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES nodes(id),
    FOREIGN KEY (target_id) REFERENCES nodes(id)
);

CREATE TABLE events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    target_id TEXT NOT NULL,
    payload JSON NOT NULL,
    prior_state JSON,
    timestamp TEXT NOT NULL
);

-- Governance tables
CREATE TABLE proposals (
    id TEXT PRIMARY KEY,
    proposer_id TEXT NOT NULL,
    proposal_type TEXT NOT NULL,
    payload JSON NOT NULL,
    quorum REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE votes (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    voter_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    evidence JSON,
    weight REAL NOT NULL,
    timestamp TEXT NOT NULL,
    FOREIGN KEY (proposal_id) REFERENCES proposals(id)
);

CREATE TABLE reputations (
    agent_id TEXT PRIMARY KEY,
    score REAL NOT NULL DEFAULT 0.5,
    tasks_completed INTEGER NOT NULL DEFAULT 0,
    tasks_failed INTEGER NOT NULL DEFAULT 0,
    peer_ratings JSON NOT NULL DEFAULT '[]',
    last_updated TEXT NOT NULL
);

-- Vector fallback (SQLite-only, no semantic search)
CREATE TABLE vectors (
    id TEXT PRIMARY KEY,
    collection TEXT NOT NULL,
    vector BLOB NOT NULL,
    payload JSON NOT NULL
);

-- Indexes
CREATE INDEX idx_nodes_type ON nodes(node_type);
CREATE INDEX idx_edges_source ON edges(source_id);
CREATE INDEX idx_edges_target ON edges(target_id);
CREATE INDEX idx_events_target ON events(target_id);
CREATE INDEX idx_events_timestamp ON events(timestamp);
CREATE INDEX idx_vectors_collection ON vectors(collection);
```

**Traversal implementation:** Recursive CTEs or in-memory BFS (for small graphs).

### 5.2 Neo4j (Optional, Production Scale)

- Same logical schema, mapped to Neo4j nodes/relationships
- Neo4j handles native Cypher traversals
- Use `neo4j-graphrag[qdrant]` for native Qdrant integration

### 5.3 Qdrant (Optional, Vector Search)

- Used only for vector storage and semantic search
- SQLite may back a peer's local metadata/index; it is not network-wide authority or a substitute for committed peer state
- If Qdrant unavailable, vector operations degrade to "not supported" (not error)

### 5.4 Backend Selection Logic

```python
class LatticeBackend(Protocol):
    async def initialize(self) -> None: ...
    async def create_node(self, ...) -> str: ...
    # ... all API methods

class SQLiteLattice(LatticeBackend):
    """Full implementation, no external deps"""

class Neo4jLattice(LatticeBackend):
    """Neo4j + optional Qdrant for production"""

def create_lattice(config: LatticeConfig) -> LatticeBackend:
    if config.backend == "sqlite":
        return SQLiteLattice(config)
    elif config.backend == "neo4j":
        return Neo4jLattice(config)
    else:
        raise ValueError(f"Unknown backend: {config.backend}")
```

---

## 6. Event Sourcing

### 6.1 Event Types

| Event | Meaning |
|-------|---------|
| `node_created` | New node added to graph |
| `node_updated` | Node properties changed |
| `node_deleted` | Node removed |
| `edge_added` | New relationship created |
| `edge_removed` | Relationship deleted |
| `proposal_submitted` | Governance proposal opened |
| `vote_cast` | Agent voted on proposal |
| `proposal_resolved` | Proposal passed/denied/expired |
| `reputation_updated` | Agent reputation changed |

### 6.2 Event Storage

- SQLite: `events` table (append-only)
- Neo4j: Special `:Event` nodes connected to targets
- Retention: Configurable (default: forever; prune after N days if needed)

### 6.3 Replay & Audit

- `replay_events(from, to)` — reconstruct state at any point in time
- `get_audit_trail(node_id)` — full history of a node
- Critical for security reviews and debugging

---

## 7. Governance Integration

### 7.1 Peer authority target (specified, not enabled)

1. Butler carries an authenticated human request; it has no validator key, ballot or unilateral permission.
2. An identified peer proposes the exact action, membership/policy versions and independently recomputable affected set.
3. Every affected consumer supplies attributable input. Missing input blocks. A present rejection is not an individual veto; override conditions require approved policy.
4. Each replica validates the application transition and committed input set. Engine ordering/finality is distinct from application approval and evidence correctness.
5. A final grant binds the action, resource scope and assignment. The worker/resource independently verifies it before acting; schedulers cannot mint authority.
6. Result/evidence and recovery records are durably acknowledged before success is claimed.

Fault bounds, validator membership/power, application thresholds and human-input proof remain owner decisions. Local API default arguments are not approved peer policy.

### 7.2 Reputation Formula

```python
def compute_reputation(rep: Reputation) -> float:
    total = rep.tasks_completed + rep.tasks_failed
    if total == 0:
        return 0.5  # Neutral baseline
    success_rate = rep.tasks_completed / total
    peer_avg = sum(rep.peer_ratings[-20:]) / max(len(rep.peer_ratings[-20:]), 1)
    # 60% task success, 40% peer ratings
    return 0.6 * success_rate + 0.4 * peer_avg
```

### 7.3 Participation and weighting

Butler never ballots, and a `user` string grants no override. Human input is not a validator ballot. The formula in §7.2 is a legacy local reputation heuristic, not approved voting power. Consensus voting power, application approval weights and voter eligibility require separate explicit contracts.

---

## 8. Configuration

```yaml
# config/lattice.yaml
lattice:
  backend: "sqlite"  # "sqlite" | "neo4j"

  sqlite:
    path: "./data/lattice.db"
    wal_mode: true

  neo4j:
    uri: "bolt://localhost:7687"
    user: "neo4j"
    password: "${NEO4J_PASSWORD}"
    database: "theagency"

  qdrant:
    url: "http://localhost:6333"
    api_key: "${QDRANT_API_KEY}"
    default_collection: "agency_vectors"

  # Vector embedding settings
  vectors:
    model: "sentence-transformers/all-MiniLM-L6-v2"
    dimension: 384
    fallback_to_metadata_only: true  # If Qdrant unavailable

  # Event log
  events:
    retention_days: 365
    prune_enabled: false

  # Legacy local-prototype polling only; NOT approved peer policy
  governance:
    default_quorum: 0.66
    proposal_ttl_seconds: 3600
    reputation_window: 20  # Rolling window for peer ratings
```

---

## 9. Integration Points

### 9.1 Butler (Gateway)

Butler carries human inputs and presents committed status/results. The existing local status/count surfaces are not proof of peer messaging. Previously advertised `send_to_agent` and `send_and_wait` are unimplemented by the inspected Lattice/backend API. Authenticated transport, durable inbox/outbox, deduplication, recovery and scope contracts are prerequisites to adding those capabilities.

### 9.2 Orchestration within the Lattice

Task dependencies, assignments, deliverables, capability discovery and peer proposals are collective Lattice functions. `src/agency/orchestrator.py` is current local wiring, not a separate target authority. Schedulers are replaceable and cannot grant permissions or manufacture peer approval.

### 9.3 Agents (Domain Agents)
```python
# Agents use Lattice for:
- Read/write their own wing (storage namespace)
- Record task progress and deliverables
- Submit evidence
- Vote on governance proposals
- Query peer reputation
```

### 9.4 Evidence Store
```python
# Evidence Store uses Lattice for:
- Linking evidence to deliverables
- Querying evidence chains (multi-hop)
- Audit trail for verification
```

### 9.5 Memory System (SMS)
```python
# SMS nodes use Lattice as their backing store:
- Retrieval: vector + graph search
- Librarian: normalization/dedup
- Dream: merge insights into graph
- CPR: compress before storing
```

---

## 10. Testing Strategy

### 10.1 Unit Tests
- `test_lattice_crud.py` — Node/edge CRUD operations
- `test_lattice_traversal.py` — Graph traversal, pathfinding
- `test_lattice_events.py` — Event sourcing, replay
- `test_lattice_governance.py` — Voting, quorum, reputation

### 10.2 Integration Tests
- `test_lattice_butler.py` — Butler integration
- `test_lattice_orchestrator.py` — Orchestrator integration
- `test_lattice_agents.py` — Agent wing operations

### 10.3 Backend Parity Tests
- `test_lattice_parity.py` — Same tests against SQLite and Neo4j backends

### 10.4 Performance Tests
- `test_lattice_perf.py` — 10k nodes, traversal latency, concurrent writes

---

## 11. Historical local-backend migration sketch

The week labels below preserve historical phases, not dates or delivery promises. Current delivery order and release gates are in `PRODUCTION_READINESS_PLAN.md` and `SPEC_SINGLE_HOST_LATTICE_MILESTONE.md`.

### Phase 1: SQLite Core (Week 1)
- Implement `SQLiteLattice` backend
- All API methods functional
- Event sourcing
- Governance primitives
- Unit tests passing

### Phase 2: Integration (Week 2)
- Wire into Butler (replace mock calls)
- Wire into Orchestrator
- Integration tests

### Phase 3: Neo4j Upgrade Path (Future)
- Implement `Neo4jLattice` backend
- Migration script: SQLite → Neo4j
- Qdrant vector integration
- Performance tests

---

## 12. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| SQLite performance degrades at scale | Monitor node count; plan Neo4j migration at 100k+ nodes |
| Vector search unavailable without Qdrant | Graceful degradation to metadata-only search |
| Event log grows unbounded | Configurable retention + pruning |
| Race conditions in local polling | Atomic local transactions; these are not Byzantine peer agreement |
| Schema migrations | Versioned schema with automatic migration |

---

## 13. File Structure

```
src/agency/lattice/
├── __init__.py
├── api.py              # Unified API surface (what agents call)
├── config.py           # LatticeConfig dataclass
├── models.py           # Node/Edge/Event/Vote/Reputation types
├── backends/
│   ├── __init__.py
│   ├── base.py         # LatticeBackend protocol
│   ├── sqlite.py       # SQLiteLattice (default)
│   └── neo4j.py        # Neo4jLattice (optional)
├── governance.py       # Consensus, voting, reputation
├── events.py           # Event sourcing helpers
├── vectors.py          # Vector search abstraction
└── migrations/
    ├── __init__.py
    ├── v001_initial.py
    └── runner.py

tests/test_lattice/
├── conftest.py
├── test_crud.py
├── test_traversal.py
├── test_events.py
├── test_governance.py
├── test_vectors.py
├── test_parity.py
└── test_integration.py
```

---

## 14. Open Questions

1. **SQLite graph traversal performance** — For >10k nodes, recursive CTEs may be slow. Mitigation: maintain a materialized path table or switch to Neo4j.
2. **Vector embedding model** — Default to local `sentence-transformers/all-MiniLM-L6-v2` (384-dim) to avoid API costs. Allow override.
3. **Cross-wing queries** — Should agents see other agents' wings? Default: no, unless explicitly shared. Governance proposals are global.
4. **Event log pruning** — Archive to compressed files before deleting? Or keep forever on disk?

---

*End of SPEC_LATTICE.md*
