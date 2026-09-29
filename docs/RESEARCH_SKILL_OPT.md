# SkillOpt research — evolving Agency capabilities

Status: **research document, not implementation**. Source: Microsoft SkillOpt paper (arXiv:2605.23904), GitHub repository, and Microsoft Research blog post.

## 1. What SkillOpt is

SkillOpt is a **text-space optimizer for agent skills** developed by Microsoft Research. It treats a skill document (a Markdown file that instructs an LLM agent how to perform a task) as the **trainable external state of a frozen agent** — analogous to how neural network weights are trainable parameters, but without touching the model itself.

The key insight: **agent skills should be trained like neural networks, not hand-written or one-shot generated.**

### Core loop

```
Rollout → Reflect → Aggregate → Select → Update → Gate → (repeat)
```

| Step | What happens | Deep learning analogy |
|---|---|---|
| **Rollout** | Target model executes a batch of tasks with the current skill | Forward pass |
| **Reflect** | Optimizer model analyzes trajectories, proposes add/delete/replace edits | Backward pass / gradient |
| **Aggregate** | Merge, deduplicate, and rank candidate edits | Gradient aggregation |
| **Select** | Clip edits to a textual learning-rate budget | Gradient clipping |
| **Update** | Apply bounded edits to the skill document | SGD step |
| **Gate** | Evaluate candidate on held-out validation split; accept only if strictly better | Validation set |

### Key controls

- **Textual learning rate** — maximum number of edits per step. Prevents destructive rewrites.
- **Rejected-edit buffer** — rejected edits become negative feedback for later optimizer calls.
- **Epoch-wise slow/meta update** — consolidates longer-horizon lessons across epochs.
- **Held-out validation gate** — candidate skills are accepted only if they strictly improve validation performance.

### Results

- Best or tied-best on **all 52 evaluation cells** (6 benchmarks × 7 target models × 3 execution harnesses)
- On GPT-5.5: lifts average no-skill accuracy by **+23.5 points** in direct chat, **+24.8** inside Codex, **+19.1** inside Claude Code
- Optimized skills transfer across model scales, execution harnesses, and nearby benchmarks
- Deployed artifact is a compact `best_skill.md` (typically 300–2,000 tokens)
- **Zero inference-time model calls** — the skill is just a text file the agent reads

## 2. SkillOpt-Sleep — deployment-time companion

SkillOpt-Sleep is a separate tool that applies the same discipline to **your own daily usage**:

- Reviews past coding-agent sessions offline
- Replays recurring tasks on your own API budget
- Consolidates validated long-term memory and skills behind a held-out gate
- Staged for human review before adoption

Available as plugins for Claude Code, Codex, and Copilot.

## 3. How SkillOpt maps to the Agency's capability factory

The Agency's capability factory (see `SPEC_CAPABILITY_CONTRACT_V1.md`) produces versioned, governed capabilities. SkillOpt provides the **optimization engine** that can evolve those capabilities over time.

### 3.1 Skill documents as capability implementations

In the Agency, a `SkillSpec` has an `implementation` field — an opaque logical operation name. SkillOpt operates on the **skill document** (the Markdown text that instructs the agent). This means:

- The `implementation` field could reference a SkillOpt-optimized skill document
- The skill document itself becomes a versioned artifact with its own optimization history
- The `provenance` field in `CapabilityContract` could record SkillOpt evaluation results

### 3.2 Capability evolution loop

SkillOpt's loop maps directly to the Agency's capability lifecycle:

| SkillOpt step | Agency capability lifecycle |
|---|---|
| Rollout | Execute capability on test tasks |
| Reflect | Analyze trajectories, propose edits |
| Aggregate | Merge edit proposals |
| Select | Clip to learning-rate budget |
| Update | Apply edits to capability implementation |
| Gate | Evaluate on held-out validation; accept if improved |
| Epoch | Consolidate long-horizon lessons |

### 3.3 Composed capability optimization

SkillOpt can optimize **composed capabilities** (workflows that reference skills) by:

1. Rolling out the composed capability on a batch of tasks
2. Analyzing which steps succeed and which fail
3. Proposing edits to the workflow specification or to individual skill documents
4. Accepting edits only if the composed capability improves on held-out validation

This is the **specification-driven composition** pattern from AWS, combined with SkillOpt's optimization discipline.

### 3.4 Governance integration

SkillOpt's validation gate aligns with the Agency's governance model:

- **SkillOpt gate** → `TESTING → APPROVED` transition in `CapabilityStatus`
- **Rejected-edit buffer** → negative feedback recorded in `provenance`
- **Epoch-wise slow update** → long-horizon lessons recorded in capability metadata
- **Human review** → `APPROVED → PUBLISHED` transition still requires human approval

## 4. Implementation approach

### 4.1 Skill document as a capability artifact

```python
@dataclass(frozen=True)
class SkillDocument:
    """A versioned skill document that can be optimized by SkillOpt."""

    skill_id: str
    version: str
    content: str                    # The Markdown skill document
    optimization_history: tuple[OptimizationRecord, ...] = ()
    validation_score: float | None = None
```

### 4.2 SkillOpt optimizer integration

```python
class SkillOptOptimizer:
    """Integrates SkillOpt's optimization loop into the Agency's capability lifecycle."""

    def optimize(
        self,
        skill_doc: SkillDocument,
        *,
        test_cases: tuple[TestCase, ...],
        validation_cases: tuple[TestCase, ...],
        learning_rate: int = 4,
        epochs: int = 10,
    ) -> SkillDocument:
        """Run SkillOpt's optimization loop on a skill document.

        1. Rollout: execute the skill on test cases
        2. Reflect: analyze trajectories, propose edits
        3. Aggregate: merge and rank edits
        4. Select: clip to learning-rate budget
        5. Update: apply edits to produce candidate skill
        6. Gate: evaluate on validation cases; accept if improved
        7. Repeat for the specified number of epochs

        Returns the optimized skill document with full optimization history.
        """
```

### 4.3 Capability evolution pipeline

```python
class CapabilityEvolver:
    """Evolves capabilities using SkillOpt's optimization discipline."""

    def evolve(
        self,
        capability: CapabilityContract,
        *,
        test_cases: tuple[TestCase, ...],
        validation_cases: tuple[TestCase, ...],
    ) -> CapabilityContract:
        """Evolve a capability's implementation using SkillOpt.

        1. Extract the implementation (skill document, workflow spec, etc.)
        2. Run SkillOpt's optimization loop
        3. Produce a new capability version with the optimized implementation
        4. Record optimization history in provenance
        5. Return the new capability in DRAFT status (requires approval)
        """
```

## 5. Benefits for the Agency

1. **Continuous improvement.** Capabilities improve over time as they are used, without manual intervention.
2. **Auditable evolution.** Every optimization step is recorded with evidence. You can query "what changed and why?"
3. **Bounded changes.** The textual learning rate prevents destructive rewrites.
4. **Validation-gated.** Changes are accepted only if they improve held-out performance.
5. **Transferable.** Optimized skills transfer across models and execution environments.
6. **Zero deployment cost.** The optimized skill is just a text file — no model retraining.

## 6. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Overfitting to test cases | Use held-out validation split; never optimize on validation data |
| Destructive rewrites | Textual learning rate bounds the number of edits per step |
| Capability drift | Epoch-wise slow update consolidates stable lessons |
| Governance bypass | SkillOpt gate is necessary but not sufficient; human approval still required for PUBLISHED |
| Cost of optimization | SkillOpt runs offline; no inference-time cost |

## 7. Open questions

1. Should SkillOpt optimization be automatic (triggered by usage) or manual (triggered by a human)?
2. How do we handle optimization of composed capabilities vs. atomic capabilities?
3. Should the optimization history be stored in the capability contract or in a separate audit log?
4. How do we handle capabilities that cannot be easily evaluated (e.g., creative tasks)?
5. Should SkillOpt be used for all capability types or only skills?

## 8. References

- SkillOpt paper: https://arxiv.org/abs/2605.23904
- SkillOpt GitHub: https://github.com/microsoft/SkillOpt
- Microsoft Research blog: https://www.microsoft.com/en-us/research/blog/skillopt-agent-skills-as-trainable-parameters/
- SkillOpt documentation: https://microsoft.github.io/SkillOpt/docs
- `SPEC_CAPABILITY_CONTRACT_V1.md` — Agency capability contract specification
