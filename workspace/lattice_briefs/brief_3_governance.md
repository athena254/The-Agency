# Lattice Brief 3: Governance Module & Consensus Protocol

You are building the Governance module for The Agency's Unified Lattice Service.

## Context
Project: C:\Users\alphi\theagency\
Spec: C:\Users\alphi\theagency\docs\SPEC_LATTICE.md
SQLite Backend: C:\Users\alphi\theagency\src\agency\lattice\backends\sqlite.py

## Task
Implement the governance layer that handles voting, reputation, and consensus proposals. This is what makes The Agency self-governing.

## Files to Create

### 1. `src/agency/lattice/governance.py`

**Classes & Functions:**

```python
class GovernanceEngine:
    """Consensus protocol for multi-agent governance."""
    
    def __init__(self, lattice: LatticeBackend):
        self.lattice = lattice
    
    async def submit_proposal(
        self,
        proposer_id: str,
        proposal_type: str,
        payload: dict,
        quorum: float = 0.66,
        ttl_seconds: int = 3600
    ) -> str:
        """
        Submit a governance proposal.
        Returns proposal ID.
        Types: 'spawn_agent', 'replace_agent', 'policy_change', 'agent_restart'
        """
    
    async def cast_vote(
        self,
        voter_id: str,
        proposal_id: str,
        decision: str,  # 'approve', 'deny', 'abstain'
        evidence: list[str] | None = None
    ) -> dict:
        """
        Cast a vote on a proposal.
        Returns {'status': 'open'|'passed'|'denied', 'quorum_reached': bool}
        Automatically resolves proposal if quorum reached.
        """
    
    async def resolve_proposal(self, proposal_id: str) -> str:
        """
        Tally votes and resolve proposal.
        Returns: 'passed', 'denied', 'expired'
        Weight by voter reputation.
        """
    
    async def check_quorum(self, proposal_id: str) -> bool:
        """
        Check if quorum is reached for a proposal.
        Quorum = weighted_votes_for / total_possible_weight >= quorum_threshold
        """
    
    async def get_proposal_status(self, proposal_id: str) -> ConsensusProposal:
        """Get full proposal with all votes."""
    
    async def list_open_proposals(self) -> list[ConsensusProposal]:
        """List all proposals with status='open'."""

```

### 2. `src/agency/lattice/reputation.py`

```python
class ReputationEngine:
    """Agent reputation scoring based on task history and peer ratings."""
    
    def __init__(self, lattice: LatticeBackend):
        self.lattice = lattice
    
    async def record_task_completion(
        self,
        agent_id: str,
        success: bool,
        peer_rating: float | None = None
    ) -> Reputation:
        """
        Record task outcome for an agent.
        Update rolling reputation score.
        peer_rating: 0.0-1.0 from another agent (optional)
        """
    
    async def get_reputation(self, agent_id: str) -> Reputation:
        """Get current reputation for an agent."""
    
    async def get_leaderboard(self, limit: int = 20) -> list[Reputation]:
        """Get top agents by reputation score."""
    
    @staticmethod
    def compute_score(rep: Reputation) -> float:
        """
        Compute reputation score:
        60% task success rate + 40% peer rating average
        Returns 0.0-1.0
        """
    
    async def vote_weight(self, voter_id: str) -> float:
        """
        Get vote weight for a voter.
        Butler and User: always 1.0
        Agents: reputation score
        """
    
    async def submit_peer_rating(
        self,
        rater_id: str,
        ratee_id: str,
        rating: float,
        context: str | None = None
    ) -> bool:
        """Allow agents to rate each other's work."""
```

### 3. `tests/test_lattice/test_governance.py`

**Test cases:**
- Proposal submission and retrieval
- Voting with different weights
- Quorum calculation
- Proposal resolution (pass/deny/expire)
- Vote weight: Butler=1.0, User=1.0, Agent=reputation
- Reputation calculation (60% task + 40% peer)
- Rolling window for peer ratings
- Concurrent vote casting
- Expired proposal handling

## Integration with SQLite Backend

The governance module uses the LatticeBackend API for storage:
- `create_node(NodeType.PROPOSAL, ...)` for proposals
- `create_node(NodeType.DECISION, ...)` for resolutions
- `add_edge(voter, proposal, EdgeType.VOTED_ON, ...)` for votes
- `update_node(agent, {reputation updates})` for reputation

All governance operations must be ATOMIC (single transaction per vote/cast).

## Rules
- Async/await throughout
- Type hints everywhere
- Event sourcing: every vote creates an event
- Thread-safe vote counting (use SQLite transactions)
- Human override: user votes always pass immediately
