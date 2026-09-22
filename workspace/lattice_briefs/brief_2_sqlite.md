# Lattice Brief 2: SQLite Backend Implementation

You are building the SQLite backend for The Agency's Unified Lattice Service.

## Context
Project: C:\Users\alphi\theagency\
Spec: C:\Users\alphi\theagency\docs\SPEC_LATTICE.md
Models: C:\Users\alphi\theagency\src\agency\lattice\models.py (already exists or being built in parallel)
Backend ABC: C:\Users\alphi\theagency\src\agency\lattice\backends\base.py (already exists or being built in parallel)

## Task
Implement `SQLiteLattice` — the default backend that requires ZERO external dependencies.

## File to Create

### `src/agency/lattice/backends/sqlite.py`
- `SQLiteLattice` class implementing ALL methods from `LatticeBackend` protocol
- Full async implementation using `aiosqlite` (already in pyproject.toml or add it)
- SQLite schema from spec section 5.1 — include ALL tables:
  - `nodes` (id, node_type, properties JSON, created_at, updated_at)
  - `edges` (id, source_id, target_id, edge_type, properties, created_at)
  - `events` (id, event_type, actor, target_id, payload, prior_state, timestamp)
  - `proposals`, `votes`, `reputations`, `vectors`
  - Indexes from spec section 5.1

### Implementation Details

**Event Sourcing (spec section 6):**
- Every create/update/delete creates an IMMUTABLE event in `events` table
- `replay_events(from, to)` returns events in timestamp order
- `get_audit_trail(node_id)` returns all events for a node

**Graph Traversal:**
- `traverse(start_node, edge_type, max_depth)` — BFS using recursive CTEs
- `find_path(source, target)` — shortest path using BFS
- `get_dependencies(task_id)` — find all tasks a task depends on
- `get_dependents(task_id)` — find all tasks depending on this task

**Governance (spec section 7):**
- `submit_proposal` — insert proposal with status "open"
- `cast_vote` — insert vote, then check if quorum reached
- `check_quorum` — compute weighted votes vs quorum threshold
- `update_reputation` — update rolling stats per agent
- `vote_weight` — Butler and User always weight 1.0, agents use reputation score
- `compute_reputation` — 60% task success + 40% peer ratings (spec section 7.2)

**Vector Search (degraded):**
- `upsert_vector` — store in vectors table (id, collection, vector blob, payload)
- `search_vectors` — NOT IMPLEMENTED (no Qdrant), return empty list or raise NotImplementedError
- `search_by_text` — NOT IMPLEMENTED, return empty list
- `delete_vectors` — delete from vectors table

**Configuration:**
- WAL mode for better concurrency
- Foreign keys enabled
- JSON1 extension for JSON operations (built into SQLite)
- Use `aiosqlite` for async operations

**Schema Migration:**
- Include `migrate()` method that creates tables if not exist
- Version table for future migrations

## Rules
- ALL methods from base class must be implemented
- Use `aiosqlite` for async SQLite
- Event sourcing is MANDATORY for every write operation
- Use ULID for IDs (or uuid4 if ULID not available)
- All timestamps as ISO 8601 UTC
- Type hints everywhere
- Error handling: log and re-raise with context
