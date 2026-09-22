# Lattice Brief 4: Main API & Integration

You are building the main API entry point and wiring the Lattice into The Agency's existing components.

## Context
Project: C:\Users\alphi\theagency\
Spec: C:\Users\alphi\theagency\docs\SPEC_LATTICE.md
Existing code: C:\Users\alphi\theagency\src\agency\ (Butler, Orchestrator, Agents, Evidence)

## Task
Implement the unified Lattice API class and wire it into the existing system.

## Files to Create

### 1. `src/agency/lattice/api.py`

```python
class Lattice:
    """
    Unified Lattice API — the single entry point for all components.
    Agents never touch backends directly; they call this.
    """
    
    def __init__(self, config: LatticeConfig | None = None):
        self.config = config or get_config()
        self.backend = self._create_backend()
        self.governance = GovernanceEngine(self.backend)
        self.reputation = ReputationEngine(self.backend)
    
    async def initialize(self) -> None:
        """Initialize backend (create tables, connect)."""
    
    # === Node Operations (delegate to backend) ===
    async def create_node(node_type, properties, actor="system") -> str: ...
    async def get_node(node_id) -> dict | None: ...
    async def update_node(node_id, properties, actor="system") -> bool: ...
    async def delete_node(node_id, actor="system") -> bool: ...
    async def find_nodes(node_type, filters, limit, offset) -> list[dict]: ...
    
    # === Edge Operations ===
    async def add_edge(source_id, target_id, edge_type, properties, actor) -> str: ...
    async def get_edges(node_id, direction, edge_type) -> list[dict]: ...
    async def remove_edge(edge_id, actor) -> bool: ...
    
    # === Graph Traversal ===
    async def traverse(start_node, edge_type, max_depth, direction) -> list[str]: ...
    async def find_path(source, target, max_depth) -> list[str] | None: ...
    async def get_dependencies(task_id) -> list[str]: ...
    async def get_dependents(task_id) -> list[str]: ...
    
    # === Vector Search ===
    async def upsert_vector(collection, vectors) -> bool: ...
    async def search_vectors(collection, query_vector, limit, filters) -> list[dict]: ...
    async def search_by_text(collection, text, limit) -> list[dict]: ...
    async def delete_vectors(collection, ids) -> bool: ...
    
    # === Governance (delegated) ===
    async def submit_proposal(proposer_id, proposal_type, payload, quorum, ttl) -> str: ...
    async def cast_vote(voter_id, proposal_id, decision, evidence) -> dict: ...
    async def get_proposal_status(proposal_id) -> ConsensusProposal: ...
    async def update_reputation(agent_id, success, peer_rating) -> Reputation: ...
    async def get_reputation(agent_id) -> Reputation: ...
    async def check_quorum(proposal_id) -> bool: ...
    
    # === Event Log ===
    async def get_events(target_id, event_type, actor, limit) -> list[LatticeEvent]: ...
    async def replay_events(from_ts, to_ts) -> list[LatticeEvent]: ...
    async def get_audit_trail(node_id) -> list[LatticeEvent]: ...
    
    # === Health & Status ===
    async def get_status() -> dict: ...
    async def get_agent_count() -> int: ...
    async def get_active_tasks() -> list[dict]: ...
    
    # === Convenience Methods ===
    async def register_agent(agent_id, agent_type, capabilities) -> str: ...
    async def create_task(agent_id, task_type, payload) -> str: ...
    async def complete_task(task_id, deliverable) -> bool: ...
    async def link_evidence(evidence_id, target_id) -> str: ...
```

### 2. `src/agency/lattice/factory.py`

```python
_lattice_instance: Lattice | None = None

async def get_lattice() -> Lattice:
    """Get or create the singleton Lattice instance."""
    global _lattice_instance
    if _lattice_instance is None:
        _lattice_instance = Lattice()
        await _lattice_instance.initialize()
    return _lattice_instance

async def reset_lattice() -> None:
    """Reset singleton (for testing)."""
    global _lattice_instance
    _lattice_instance = None
```

### 3. `config/lattice.yaml`
```yaml
lattice:
  backend: "sqlite"
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
  vectors:
    model: "sentence-transformers/all-MiniLM-L6-v2"
    dimension: 384
    fallback_to_metadata_only: true
  events:
    retention_days: 365
    prune_enabled: false
  governance:
    default_quorum: 0.66
    proposal_ttl_seconds: 3600
    reputation_window: 20
```

### 4. `tests/test_lattice/test_api.py`

**Test cases:**
- Singleton factory (get_lattice returns same instance)
- Node CRUD lifecycle
- Edge operations (add, query, remove)
- Graph traversal (3-hop, pathfinding)
- Event sourcing (verify events created on every write)
- Agent registration convenience method
- Task lifecycle (create → complete → deliverable)
- Evidence linking
- Health status returns correct counts

### 5. `tests/test_lattice/test_integration.py`

**Test cases:**
- Butler integration (get_agent_count, get_status)
- Orchestrator integration (task registration, dependencies)
- Agent wing operations (namespaced storage)
- Governance flow (submit proposal → vote → resolve)
- Reputation flow (record task → update score)
- Concurrent operations (multiple agents reading/writing)

## Integration with Existing Code

Read these files and understand how they work:
- `src/agency/butler/server.py` — Butler uses Lattice for routing
- `src/agency/agents/registry.py` — Agent registry
- `src/agency/agents/executor.py` — Task execution

The Lattice should:
- Replace any direct SQLite calls in Butler/Orchestrator
- Become the backing store for agent wings
- Provide the governance substrate for consensus decisions

## Rules
- Singleton pattern via factory function
- Async/await throughout
- Type hints everywhere
- Graceful degradation: if Qdrant unavailable, vector search returns empty
- Log all initialization steps
