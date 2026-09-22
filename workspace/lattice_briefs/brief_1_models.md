# Lattice Brief 1: Data Models & SQLite Schema

You are building the foundation of The Agency's Unified Lattice Service.

## Context
Project: C:\Users\alphi\theagency\
Spec: C:\Users\alphi\theagency\docs\SPEC_LATTICE.md
Existing code style: C:\Users\alphi\theagency\src\agency\

## Task
Create the data models and SQLite schema implementation.

## Files to Create

### 1. `src/agency/lattice/models.py`
- `NodeType` enum: AGENT, TASK, DELIVERABLE, EVIDENCE, DECISION, POLICY, WING, EVENT
- `EdgeType` enum: DEPENDS_ON, PRODUCED_BY, EVIDENCE_FOR, VOTED_ON, GOVERNS, MEMBER_OF, TRUSTS, SUPERSEDES
- `LatticeEvent` dataclass (frozen): event_id, timestamp, event_type, actor, target_id, payload, prior_state
- `Vote` dataclass: voter_id, proposal_id, decision, evidence, weight, timestamp
- `Reputation` dataclass: agent_id, score, tasks_completed, tasks_failed, peer_ratings, last_updated
- `ConsensusProposal` dataclass: proposal_id, proposer_id, proposal_type, quorum_required, votes, status, expires_at
- `LatticeConfig` dataclass: backend, sqlite_path, neo4j_uri, neo4j_user, neo4j_password, neo4j_database, qdrant_url, qdrant_api_key, vector_model, vector_dimension, event_retention_days, prune_enabled, default_quorum, proposal_ttl_seconds, reputation_window

### 2. `src/agency/lattice/config.py`
- Load config from `config/lattice.yaml` or env vars
- `LatticeConfig` with validation
- Default to SQLite backend if not specified
- `get_config()` singleton function

### 3. `src/agency/lattice/__init__.py`
- Export main API class
- Export types

### 4. `src/agency/lattice/backends/__init__.py`
- Empty init

### 5. `src/agency/lattice/backends/base.py`
- `LatticeBackend` Protocol/ABC with ALL API methods from spec section 4:
  - create_node, get_node, update_node, delete_node, find_nodes
  - add_edge, get_edges, remove_edge
  - traverse, find_path, get_dependencies, get_dependents
  - upsert_vector, search_vectors, search_by_text, delete_vectors
  - submit_proposal, cast_vote, get_proposal_status, update_reputation, get_reputation, check_quorum
  - get_events, replay_events, get_audit_trail
  - get_status, get_agent_count, get_active_tasks

### 6. `tests/test_lattice/conftest.py`
- Pytest fixtures for in-memory SQLite database
- Clean state per test

### 7. `tests/test_lattice/test_models.py`
- Test all dataclass creation and serialization
- Test enum values

## Rules
- Use Python 3.11+ features (match X | None instead of Optional)
- dataclasses where appropriate
- Use __all__ in __init__.py
- Type hints everywhere
- Follow existing project style (read existing files for reference)
- Use async/await for all I/O
