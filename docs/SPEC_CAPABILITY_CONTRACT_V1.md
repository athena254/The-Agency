# SPEC_CAPABILITY_CONTRACT_V1.md

Status: **proposed specification, not implemented**. Source: `SOURCE_CONSOLIDATED_BRIEF.md` section 7.3, capability factory research (AWS specification-driven composition, Traverse capability registry, cap-protocol, Invokta capability packages), and Microsoft SkillOpt paper (arXiv:2605.23904).

## 1. Purpose and scope

The Agency currently has three separate registries — `AgentFactory`, `SkillRegistry`, and `WorkflowRegistry` — each with its own schema, lifecycle, and approval mechanism. The consolidated brief (section 7.3) calls for a general reusable capability factory that can produce agents, skills, workflows, and potentially tool adapters and evaluation packages.

This specification defines **Phase 1: a unified capability contract** that all registries project into. It does not replace the existing registries. It provides a shared schema, a common governance model, and a composition mechanism that lets capabilities reference and build on each other.

### In scope

- `CapabilityContract` dataclass — the shared schema for all capability types
- `CapabilityType` enum — agent, skill, workflow, tool_adapter
- `CapabilityRef` — a pinned `(capability_id, version)` reference
- `CapabilityComposer` — validates and assembles composed capabilities
- `CapabilityIndex` — discovery by metadata, type, and interface
- Governance integration — how the contract interacts with existing authorizers
- Migration path — how existing registries adopt the contract

### Out of scope (later phases)

- Evaluation harness (Phase 4)
- Tool adapter production (Phase 5)
- Automated skill optimization via SkillOpt (separate research track)
- Runtime process isolation for capabilities
- Cross-host capability distribution

## 2. Design principles

1. **Contract as source of truth.** The `CapabilityContract` is the authoritative record. Registries may store additional fields, but the contract is what composers, indexers, and governance layers consume.
2. **Immutable publication.** Once a capability version is published, it cannot change. New versions must follow semver rules.
3. **Atomic and composed are first-class.** A composed capability (e.g., a workflow that references skills) is itself a versioned, governed capability — not an undocumented workflow-only artifact.
4. **Deterministic validation.** Contract validation is deterministic. No LLM is required to validate structure, references, or interface compatibility.
5. **Fail closed.** Missing references, incompatible interfaces, and unverifiable claims all fail closed.
6. **Zero inference-time cost.** The contract is metadata. It adds no model calls at deployment time.

## 3. Data model

### 3.1 CapabilityType

```python
class CapabilityType(str, Enum):
    AGENT = "agent"
    SKILL = "skill"
    WORKFLOW = "workflow"
    TOOL_ADAPTER = "tool_adapter"
```

### 3.2 CapabilityContract

```python
@dataclass(frozen=True)
class CapabilityContract:
    """Unified schema for all capability types."""

    # Identity
    capability_id: str                    # e.g., "research.skill", "finance.agent"
    version: str                         # semver "X.Y.Z"
    name: str                            # human-readable name
    description: str                     # what this capability does
    capability_type: CapabilityType

    # Interface
    inputs: dict[str, Any]               # JSON schema for inputs
    outputs: dict[str, Any]              # JSON schema for outputs
    permissions: tuple[str, ...]         # what this capability needs (e.g., "web_search", "file_read")

    # Implementation
    implementation_ref: str              # opaque reference to the actual implementation
    implementation_kind: str             # "executable" | "workflow" | "agent" | "skill"

    # Governance
    creator: str                         # who created this version
    provenance: str                      # evidence reference (evaluation, review, etc.)
    status: CapabilityStatus             # lifecycle state

    # Composability
    composability: ComposabilityMetadata # how this capability can participate in compositions

    # Metadata
    tags: tuple[str, ...] = ()           # searchable tags
    created_at: str = ""
    updated_at: str = ""
```

### 3.3 ComposabilityMetadata

```python
@dataclass(frozen=True)
class ComposabilityMetadata:
    """Machine-readable metadata for safe composition."""

    # What roles can this capability play in a composition?
    roles: tuple[str, ...] = ()          # e.g., ("enrichment", "validation", "transformation")

    # What input types can it accept? (for interface matching)
    input_schema: dict[str, Any] = field(default_factory=dict)

    # What output types does it produce?
    output_schema: dict[str, Any] = field(default_factory=dict)

    # Can this capability be composed into others?
    composable: bool = True

    # What other capability types can it depend on?
    allowed_dependencies: tuple[CapabilityType, ...] = ()
```

### 3.4 CapabilityStatus

```python
class CapabilityStatus(str, Enum):
    DRAFT = "DRAFT"
    TESTING = "TESTING"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    DEPRECATED = "DEPRECATED"
    RETIRED = "RETIRED"
```

This unifies the existing `BlueprintStatus` (DRAFT → APPROVED → ACTIVE → REVOKED) and `SkillStatus` (DRAFT → TESTING → APPROVED → PUBLISHED → DEPRECATED → RETIRED) into a single lifecycle.

### 3.5 CapabilityRef

```python
@dataclass(frozen=True)
class CapabilityRef:
    """A pinned reference to a specific capability version."""

    capability_id: str
    version: str

    def __str__(self) -> str:
        return f"{self.capability_id}@{self.version}"
```

## 4. Lifecycle and governance

### 4.1 State transitions

```
DRAFT → TESTING → APPROVED → PUBLISHED → DEPRECATED → RETIRED
```

| Transition | Required evidence | Authorizer |
|---|---|---|
| DRAFT → TESTING | Test cases defined | capability.test |
| TESTING → APPROVED | Evaluation passed | capability.approve |
| APPROVED → PUBLISHED | Review complete | capability.publish |
| PUBLISHED → DEPRECATED | Migration path documented | capability.deprecate |
| DEPRECATED → RETIRED | No active dependents | capability.retire |

### 4.2 Governance integration

The contract does not replace the existing `authorizer` callback. Instead, it adds a **capability governance layer** that:

1. Validates that all referenced capabilities exist and are PUBLISHED
2. Checks that input/output interfaces match between connected capabilities
3. Verifies that the composer has permission to compose the referenced capabilities
4. Records the composition as a new capability version with its own provenance

### 4.3 Immutable publication

Once a capability reaches PUBLISHED, its `CapabilityContract` is frozen. Any change requires a new version. The registry enforces this by rejecting updates to published versions.

## 5. Composition

### 5.1 CapabilityComposer

The `CapabilityComposer` takes a specification (a workflow or agent blueprint) and produces a composed capability.

```python
class CapabilityComposer:
    def compose(
        self,
        specification: dict[str, Any],
        *,
        composer_id: str,
    ) -> CapabilityContract:
        """Validate and assemble a composed capability.

        1. Parse the specification
        2. Resolve all CapabilityRef references
        3. Validate that all references are PUBLISHED
        4. Check interface compatibility between connected capabilities
        5. Produce a new CapabilityContract with:
           - capability_type=WORKFLOW (or AGENT)
           - implementation_ref pointing to the specification
           - composability metadata recording the composition graph
        6. Return the new contract in DRAFT status
        """
```

### 5.2 Interface matching

When capability A produces output that feeds into capability B, the composer validates:

```python
def _check_interface_match(
    producer: CapabilityContract,
    consumer: CapabilityContract,
) -> bool:
    """Check that producer's output schema is compatible with consumer's input schema."""
    # Deterministic schema compatibility check
    # Returns True if the producer's output can satisfy the consumer's input
    ...
```

### 5.3 Composition provenance

Every composed capability records its composition graph:

```python
@dataclass(frozen=True)
class CompositionGraph:
    """Records what capabilities were composed and how."""

    nodes: tuple[CapabilityRef, ...]      # all referenced capabilities
    edges: tuple[tuple[str, str], ...]    # (producer_step, consumer_step)
    specification_hash: str               # hash of the specification
```

## 6. Discovery

### 6.1 CapabilityIndex

The `CapabilityIndex` maintains a searchable index of all published capabilities.

```python
class CapabilityIndex:
    def search(
        self,
        *,
        capability_type: CapabilityType | None = None,
        tags: tuple[str, ...] = (),
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        roles: tuple[str, ...] = (),
    ) -> list[CapabilityContract]:
        """Search for published capabilities matching the criteria."""
```

### 6.2 Discovery by intent

The index supports semantic search over capability descriptions, input/output schemas, and tags. This lets agents and humans find capabilities by intent rather than by exact identifier.

## 7. Migration path

### 7.1 AgentFactory

- `Blueprint` → `CapabilityContract` with `capability_type=AGENT`
- `BlueprintStatus` → `CapabilityStatus`
- `skill_refs` and `workflow_refs` → `CompositionGraph`
- Existing `authorizer` callback → governance layer

### 7.2 SkillRegistry

- `SkillSpec` → `CapabilityContract` with `capability_type=SKILL`
- `SkillStatus` → `CapabilityStatus`
- `implementation` → `implementation_ref`
- `inputs`/`outputs` → `inputs`/`outputs` (already JSON schemas)

### 7.3 WorkflowRegistry

- `WorkflowDefinition` → `CapabilityContract` with `capability_type=WORKFLOW`
- `steps` → `CompositionGraph`
- `permissions` → `permissions`

### 7.4 Backward compatibility

Existing registries continue to work. The contract is a **projection** — registries can adopt it incrementally without breaking existing code.

## 8. Testing strategy

### 8.1 Unit tests

- Contract validation (malformed IDs, versions, schemas)
- State transition enforcement (illegal transitions rejected)
- Immutable publication (published versions cannot change)
- Interface matching (compatible and incompatible schemas)
- Composition validation (missing references, circular dependencies)

### 8.2 Integration tests

- Compose a skill + workflow into a new capability
- Search the index by type, tags, and interface
- Verify that composed capabilities are themselves composable
- Verify that governance rejects unauthorized compositions

### 8.3 Property-based tests

- For any valid specification, the composer produces a valid contract
- For any published capability, the index can find it by its metadata
- For any composition, the composition graph is acyclic

## 9. File structure

```
src/agency/capability/
├── __init__.py
├── contract.py          # CapabilityContract, CapabilityType, CapabilityStatus
├── ref.py               # CapabilityRef
├── composition.py       # CapabilityComposer, CompositionGraph
├── index.py             # CapabilityIndex
└── governance.py        # CapabilityGovernance layer
```

## 10. Open questions

1. Should `implementation_ref` be a content-addressed hash (e.g., SHA-256 of the implementation)?
2. How do we handle capability versioning across registry boundaries? (e.g., a skill references a workflow — if the workflow updates, does the skill need re-approval?)
3. Should the index be a separate SQLite table or an in-memory projection of the registries?
4. How do we handle capability deprecation when there are active dependents?

## 11. References

- `SOURCE_CONSOLIDATED_BRIEF.md` section 7.3
- `SPEC_AGENT_FACTORY_V1.md`
- AWS specification-driven composition: https://aws.amazon.com/blogs/architecture/specification-driven-composition-for-flexible-data-workflows/
- Traverse capability registry: https://github.com/traverse-framework/traverse/blob/main/specs/005-capability-registry/spec.md
- cap-protocol: https://github.com/webspotme/cap-protocol/
- Invokta capability packages: https://docs.invokta.dev/guides/capability-packages/
- Microsoft SkillOpt: https://arxiv.org/abs/2605.23904
