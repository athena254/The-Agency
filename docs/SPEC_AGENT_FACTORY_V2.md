# Agent Factory v2 — Governed Capability Lifecycle and Composition

**Status:** Proposed implementation specification. This document is a build plan, not a claim that the described functionality exists.

**Target repository:** `athena254/The-Agency`
**Target base:** `main`
**Inspected base commit:** `4c9e9ea38ebfa2395624e0cee3b66a0bb366912a` (`feat: add owner-scoped search service`, 2026-11-09)
**Intended PR scope:** Documentation only. No runtime code, schema, API, UI, permissions, or deployment behavior is changed by the PR that introduces this specification.

> **Instruction to the implementation agent:** Start from the current `main` branch and its current source/tests, not from a stale checkout, a historical branch, this document alone, or a prior architectural sketch. Re-read the files listed in §3 before changing code. Treat this specification as the target design, but preserve the existing security boundaries and fail closed whenever the implementation cannot prove a required condition.

---

## 1. Executive intent

Evolve the Agency's existing Agent Factory into the governed system that creates, validates, composes, evaluates, versions, registers, activates, observes, revokes, and retires Agency-native agents and their reusable capabilities.

The Factory must manage more than an agent's initial blueprint. It must understand the building blocks that an agent is made from, resolve their exact versions, establish that those parts are compatible, prove what was evaluated, and preserve the chain of provenance that explains why a particular agent was approved and what it was allowed to do.

The design borrows established ideas from capability registries, typed manifests, dependency lockfiles, software supply-chain provenance, and evaluation-driven development. It is an Agency-native implementation. It must not import or depend on another project, external agent platform, capability marketplace, or shared runtime.

### 1.1 Desired lifecycle

```text
Idea / request
    |
    v
Draft a typed definition
    |
    v
Validate structure and references
    |
    v
Resolve exact dependency graph
    |
    v
Check interfaces, policy requirements and compatibility
    |
    v
Evaluate in a bounded test environment
    |
    v
Independent approval and publication / agent approval
    |
    v
Create an immutable release record
    |
    v
Activate or deploy through the existing Agency authority path
    |
    v
Observe executions and collect evidence
    |
    v
Propose a new version (never mutate the released version)
    |
    v
Deprecate / revoke / retire with dependency impact analysis
```

Every arrow is a controlled state transition. A language model may propose definitions, changes, test cases, or explanations, but it must not decide that its own work is safe, approve itself, grant permissions, or bypass deterministic checks.

### 1.2 Product outcomes

The completed Factory should be able to:

1. Create and maintain versioned Agency agent blueprints.
2. Discover and resolve existing skills, workflows, and prompts as reusable, typed capabilities.
3. Validate exact references and dependency graphs before an agent or composition is approved.
4. Check compatible inputs/outputs where enough machine-readable information exists, and report `UNKNOWN` rather than guessing where it does not.
5. Evaluate capabilities and composed agents against explicit, versioned suites.
6. Preserve approval, evaluation, provenance, and dependency evidence for every released definition.
7. Build specialised agent variants and, in a later phase, multi-agent team blueprints from version-pinned components.
8. Detect the impact of deprecating or revoking a capability before it becomes an invisible breaking change.
9. Reconstruct what was approved and used for a run without silently switching to a newer capability version.
10. Keep identity, authorization, runtime execution, governance, and user interfaces in their existing Agency-owned boundaries.

---

## 2. Non-negotiable Agency boundaries

These are architectural constraints, not optional future hardening.

### 2.1 Project independence

- The Agency repository is the sole source of truth for this implementation.
- Do not depend on ATHENA, another personal project, another repository, or another agent platform for identity, orchestration, state, memory, governance, capability definitions, or execution.
- No cross-project database, shared runtime, shared source tree, or hidden service dependency.
- An external source may inspire a pattern or provide a candidate artifact for later human-reviewed import, but it is never authoritative for Agency state.

### 2.2 Definition is not authority

Keep these concepts separate:

| Concept | Meaning | Does not imply |
|---|---|---|
| Declared capability | What a blueprint or component says it needs or can perform | Authorization to perform it |
| Capability contract | Machine-readable description of an interface, dependency and provenance | Executable code or permission |
| Published reusable capability | A reviewed immutable version available for reference | Permission for every agent to invoke it |
| Agent blueprint | Versioned design for an agent identity | A running agent or granted permission |
| Runtime identity | Existing `agency.kernel.identity.Agent` registered in Agency registries | A process, sandbox, or unrestricted execution authority |
| Permission | Existing scoped authorization enforced through Agency policy | A capability declaration or evaluation score |
| Evaluation result | Evidence about behavior on a defined suite | Approval, trust, or permission |
| Composition result | A validated graph of pinned components | Deployment or permission inheritance |

The Factory must never issue `agency.kernel.policies.Permission` merely because a capability appears in a blueprint, manifest, skill, workflow, or evaluation result.

### 2.3 No executable manifests

- Registry records are data, not code.
- Never use `eval`, `exec`, dynamic import, arbitrary shell commands, arbitrary URLs, or deserialization capable of constructing executable objects to load a capability.
- Existing `SkillExecutor` and `WorkflowExecutor` allowlisted operation registries remain the only supported execution seams for those operations.
- The name of an implementation is a logical operation reference, not a Python module path or command line.
- Remote skill fetching, automatic marketplace installation, MCP server installation, and package execution are outside this specification.
- Treat capability descriptions, documentation, prompts, model output, tool metadata, and test fixtures as untrusted input. They cannot change policy or grant authority.

### 2.4 Existing governance remains authoritative

- Continue to use the existing trusted authorizer callbacks and policy/kernel enforcement points.
- Actor identity must arrive from a trusted caller. Never accept an agent-provided, prompt-provided, or body-provided `approver` as proof of authority.
- Approval must be attributable, evidence-backed, and separate from creation. A creator cannot approve their own object.
- Unknown or failed authorizer outcomes deny the transition.
- High-impact and production operations remain governed by existing L0–L5 action classes and human approval boundaries.

---

## 3. Exact baseline: what exists on `main`

This specification is additive. Do not replace these subsystems with a second parallel implementation.

### 3.1 Required source and test reconnaissance

Before coding, inspect at least:

| Area | Current source | Current tests / docs |
|---|---|---|
| Agent Factory | `src/agency/factory/service.py` | `tests/test_agent_factory.py`, `docs/SPEC_AGENT_FACTORY_V1.md` |
| Prompt library and composition | `src/agency/factory/prompts.py`, `src/agency/factory/compose.py` | `tests/test_factory_prompt_compose.py`, `tests/test_factory_prompt_refs.py` |
| Skills | `src/agency/skills/registry.py`, `src/agency/skills/executor.py` | `tests/test_skill_registry.py`, `tests/test_skill_executor.py` |
| Workflows | `src/agency/workflows/registry.py`, `src/agency/workflows/executor.py`, `src/agency/workflows/recovery.py` | `tests/test_workflow_registry.py` |
| Runtime identity | `src/agency/kernel/identity.py`, `src/agency/kernel/registry.py`, `src/agency/agents/registry.py` | Related kernel and agent registry tests |
| Policy and permissions | `src/agency/kernel/policies.py` | `tests/test_owner_policy.py` and policy/kernel tests |
| Audit and evidence | `src/agency/kernel/audit.py`, `src/agency/evidence/` | Audit and evidence tests |
| Current architecture/status | `docs/CURRENT_STATE.md`, `docs/ARCHITECTURE.md`, `docs/SOURCE_CONSOLIDATED_BRIEF.md` | `docs/KNOWN_ISSUES.md`, `docs/ROADMAP.md` |
| Proposed shared contract | `docs/SPEC_CAPABILITY_CONTRACT_V1.md` | This v2 spec refines it; v1 is not implemented |

Check the current branch head again when implementation begins. The inspected commit is a reference point, not permission to overwrite newer changes.

### 3.2 Existing behavior to preserve

**Agent Factory v1** (`src/agency/factory/service.py`):

- SQLite-backed immutable blueprint versions keyed by `(blueprint_id, version)`.
- Current blueprint states: `DRAFT`, `APPROVED`, `ACTIVE`, `REVOKED`.
- Pinned skill, workflow and prompt references; bounded deterministic validation.
- Approval through injected trusted authorizer, evidence requirement, and no self-approval.
- Activation creates the existing `agency.kernel.identity.Agent`, with `TrustLevel.UNKNOWN` and L0-only capability claims, then registers it in existing kernel/runtime registries.
- Activation does not issue policy `Permission` records.
- Revocation retains provenance and revokes/removes the runtime identity through existing registries.
- Runtime registries are process-local. The persisted `ACTIVE` record is not automatically rehydrated after restart; Factory currently fails closed instead of silently recreating an agent.

**Prompt library** (`src/agency/factory/prompts.py`, `compose.py`):

- Prompt records are immutable and versioned, with lifecycle `DRAFT → TESTING → APPROVED → PUBLISHED → DEPRECATED → RETIRED`. Current transition/event records retain evidence and timestamps but do not retain an actor ID or enforce a trusted authorizer callback. Native `PUBLISHED` status alone is therefore not proof of attributable, independently authorized publication.
- Prompt composition is deterministic from exact pinned references and bounded slot parameters; the rendered result has a SHA-256 digest.
- Prompt bodies are inert data. Preserve the REAP safety-pairing rule already enforced by the Factory.

**Skills** (`src/agency/skills/`):

- `SkillRegistry` stores immutable, versioned metadata, inputs/outputs, requested permissions, logical implementation name and provenance.
- Skill records are not executable code. `SkillExecutor` resolves a published skill's logical operation through an explicitly registered `OperationRegistry`, checks permissions, and fails closed.

**Workflows** (`src/agency/workflows/`):

- `WorkflowRegistry` stores immutable versioned DAG definitions and run records.
- Definitions are validated for bounded size, unique step IDs, known dependencies and cycles.
- `WorkflowExecutor` runs registered deterministic callables in stable topological order, checking permissions at execution.
- The recoverable-run library APIs preserve explicit state and operation/version/actor binding. They do not promise exactly-once external side effects or generic agent-process recovery.

**Kernel identity and policy**:

- `agency.kernel.identity.Agent` is the runtime identity model. Do not create `CapabilityAgent`, `FactoryAgent`, or another parallel runtime identity class.
- `Capability` names and levels on an Agent are claims/ceilings; actual permission is separate.
- `PolicyEngine` remains the deterministic authority for action authorization.

**Current system status**:

- `docs/CURRENT_STATE.md` describes Factory, skills, workflows, and Forge as partial bounded foundations, not a completed autonomous factory or production-ready deployment platform.
- The status document records its test evidence against a prior checkpoint (`c0a1de9`), not necessarily this exact `main` SHA. Re-run the required suite against the implementation branch and report the exact tested SHA.
- Mission Control contains locked P4 capability lifecycle surfaces. This specification does not itself unlock them.

### 3.3 Important gap: workflow publication semantics

The current `WorkflowDefinition` / `WorkflowRegistry` does not provide the same explicit `DRAFT → TESTING → APPROVED → PUBLISHED` lifecycle as the skill and prompt registries. Existing Factory callback wiring must not silently treat “definition exists in the registry” as equivalent to “approved and published for reuse”.

The implementation agent must make this a first-class design decision before wiring WorkflowRegistry into the shared capability projection:

1. Either add an explicit, evidence-backed workflow lifecycle in a separate, migration-safe slice; or
2. Introduce a clearly typed `REGISTERED_UNVERIFIED` projection state and keep those workflow versions ineligible for dependencies requiring a published capability until a trusted publication record exists.

Do not invent a `published` property, infer approval from existence, or loosen current Factory validation to make tests pass. If adding workflow lifecycle, preserve current workflow execution behavior and add explicit authorizer/evidence checks, event history, concurrency tests, and old-database migration tests.

---

## 4. Architecture decision: shared contract, native sources of truth

`docs/SPEC_CAPABILITY_CONTRACT_V1.md` correctly identifies the need for a common capability contract and composition layer. This v2 refines its storage and lifecycle assumptions.

### 4.1 Canonical ownership

Use a **shared contract as the common interface and read model**, not as a second competing database for every native object.

```text
 AgentFactory / blueprints ─────┐
 PromptRegistry ────────────────┤
 SkillRegistry ─────────────────┼──> Typed capability adapters
 WorkflowRegistry ─────────────┘             |
                                             v
                                  CapabilityContract projection
                                             |
                       +---------------------+--------------------+
                       |                     |                    |
                       v                     v                    v
                CapabilityResolver     CapabilityIndex     Evaluation bindings
                       |
                       v
             Resolved, digest-pinned graph
                       |
                       v
              Composition / Factory validation
                       |
                       v
               Existing governance gates
```

Rules:

- Native registries own type-specific persistence, validation, lifecycle, and historical compatibility.
- A typed adapter maps a native record plus its verified lifecycle evidence into a `CapabilityContract` projection.
- The projection layer must not create a second writable representation of a native blueprint, skill, prompt, or workflow.
- The search index is rebuildable from native registries. It is a cache/read model, not the authority for publication, revocation, permission, or exact version content.
- A resolver must fetch the authoritative native record and re-check state/digest at resolution time. A stale index result is never sufficient to execute or activate anything.
- If two native records collide on the same human-readable ID, type/namespace must disambiguate them.

### 4.2 Lifecycle states are not one universal enum

Do **not** merge agent identity activation and reusable capability publication into one state machine.

| Native object | Current lifecycle | v2 rule |
|---|---|---|
| Agent blueprint | `DRAFT → APPROVED → ACTIVE → REVOKED` | Preserve; evaluation evidence may become a precondition for approval, but `ACTIVE` remains an identity lifecycle state, not a generic publish state |
| Skill | `DRAFT → TESTING → APPROVED → PUBLISHED → DEPRECATED → RETIRED` | Preserve and expose through adapter |
| Prompt | `DRAFT → TESTING → APPROVED → PUBLISHED → DEPRECATED → RETIRED` | Preserve and expose through adapter |
| Workflow | Registered immutable definition and run states; no full publication lifecycle | Resolve as `REGISTERED_UNVERIFIED` until explicit publication evidence exists; do not treat registry presence as publication |
| Evaluation suite | New object, versioned | Its own lifecycle must distinguish draft, approved-for-testing, and retired; a suite cannot approve itself |
| Live runtime Agent | Runtime identity state/revocation | Keep in existing kernel/runtime registries; never conflate with a reusable definition |

A common `CapabilityLifecycleState` may be used for normalized filtering only if it is explicitly a **projection**, preserves native source status, and never drives a native transition by itself. For example, `ACTIVE` blueprint must not be rewritten as `PUBLISHED`. A native `PUBLISHED` label without a valid v2 publication attestation must project as `PUBLISHED_UNATTESTED` (or an equivalent explicit unverified state), not as governed/publish-eligible.

---

## 5. Shared capability model

Implement the smallest typed model that can represent the current Agency primitives without pretending unknown fields are known.

### 5.1 Supported capability types

Initial supported projections:

- `AGENT` — an Agent Factory blueprint version; never a live runtime identity.
- `SKILL` — a `SkillSpec` version.
- `WORKFLOW` — a `WorkflowDefinition` version plus verified publication/lifecycle state (or `REGISTERED_UNVERIFIED`).
- `PROMPT` — a `PromptSpec` version.

Reserved future types (not executable in this phase): `TOOL_ADAPTER`, `EVALUATION_SUITE`, `AGENT_TEAM`, `KNOWLEDGE_PACK`.

Unknown types must be rejected with a typed unsupported-type error. Do not accept arbitrary strings and pass them through to resolvers.

### 5.2 CapabilityRef

A reference identifies a specific native object and exact immutable version.

Suggested immutable fields:

```python
@dataclass(frozen=True)
class CapabilityRef:
    capability_type: CapabilityType
    capability_id: str
    version: str                  # exact X.Y.Z; no "latest" or ranges at execution
    digest: str | None = None     # sha256:<64 lowercase hex>; required in a lockfile/release
```

Reference identity is `(capability_type, capability_id, version)`. Digest is an integrity assertion over the canonical content projection. The resolver must reject a digest mismatch.

Parsing/validation requirements:

- Bound ID length and character set consistently with the native registry.
- Require exact semantic version syntax supported by current code (`X.Y.Z`) for v1 compatibility; do not introduce range semantics into runtime resolution.
- Validate digest algorithm and format. Unsupported algorithms are rejected, not silently ignored.
- Canonical serialization must define Unicode normalization policy, key ordering, number encoding, omitted/default field behavior, and schema version.

### 5.3 CapabilityContract projection

Suggested immutable fields:

```python
@dataclass(frozen=True)
class CapabilityContract:
    schema_version: int
    ref: CapabilityRef
    name: str
    description: str
    lifecycle_state: str             # normalized projection; see §4.2
    native_state: str                # exact native status or REGISTERED_UNVERIFIED
    inputs: dict[str, object] | None
    outputs: dict[str, object] | None
    requested_permissions: tuple[str, ...]
    implementation_ref: str | None  # opaque registered logical name only
    dependencies: tuple[DependencyEdge, ...]
    tags: tuple[str, ...]
    provenance: ProvenanceRef
    content_digest: str
```

The projection must include an `unknown_fields` or capability metadata extension only where that data is validated and size-bounded. It must never manufacture interface schemas, operational claims, publication status, or compatibility claims that are absent from the native record.

For a workflow without declared input/output contracts, return `None`/unknown interfaces; do not infer a schema from arbitrary runtime input examples.

### 5.4 Digest rules

- Compute SHA-256 over a canonical serialization of the immutable definition projection.
- Exclude mutable lifecycle state, event timestamps, approver/evaluation links, and current index metadata from the content digest.
- Include schema version, capability type/ID/version, interface declarations, requested permissions, implementation logical reference, exact dependencies, and immutable definition metadata.
- Store evidence, approval events, evaluation run refs, and signatures separately and bind them to the definition digest.
- Recompute at resolution and before use; mismatch means `INTEGRITY_FAILURE` and fail closed.
- Never describe a hash as a signature, proof of authorship, tamper-proof storage, or proof that the implementation is safe.

### 5.5 DependencyEdge

Represent relationships explicitly:

```python
class DependencyRelation(str, Enum):
    REQUIRES = "requires"              # hard dependency; must resolve
    COMPOSES = "composes"              # member of an immutable composition
    CONFLICTS_WITH = "conflicts_with"  # cannot coexist in one resolved graph
    SUPERSEDES = "supersedes"          # governance/deprecation relationship
    COMPATIBLE_WITH = "compatible_with"# positive compatibility assertion with evidence
```

Each edge must identify the target using a full `CapabilityRef` and may include a bounded interface/role label. `REQUIRES`/`COMPOSES` edges are exact-version dependencies. Never use an unpinned name or a branch/tag alias in a released graph.

### 5.6 ProvenanceRef

Provenance is a structured pointer, not free-form prose alone. At minimum record:

- source kind (`agency_native`, `human_authored`, `generated_candidate`, `reviewed_import`);
- source identifier and immutable source revision/digest where available;
- creator/submitter actor ID from a trusted identity boundary;
- created timestamp;
- optional upstream URL as descriptive metadata only (never fetched automatically);
- applicable license identifier/text evidence for imported material;
- parent definition refs for derived/composed objects.

Do not store secrets, bearer tokens, API keys, unrestricted raw conversations, or unnecessary personal data in provenance.

---

## 6. Capability adapters and registry integration

Implement typed, read-only adapters first. This avoids a risky big-bang migration.

### 6.1 Adapter contract

Suggested structural protocol:

```python
class CapabilityAdapter(Protocol):
    capability_type: CapabilityType

    def get(self, capability_id: str, version: str) -> CapabilityContract: ...
    def list_refs(self) -> list[CapabilityRef]: ...
```

The adapter must:

1. Read the exact native immutable version.
2. Map only fields actually present in that native record.
3. Return the exact native lifecycle status.
4. Attach the trusted lifecycle/approval evidence reference if one exists.
5. Compute or verify the canonical definition digest.
6. Reject corrupt, oversized, invalid, missing, retired, or unverifiable records.
7. Never execute, fetch, install, mutate, publish, approve, or activate an object.

### 6.2 Required adapters

Implement in this order:

1. `SkillCapabilityAdapter` — project `SkillSpec`; a native `PUBLISHED` skill is eligible for governed composition only when a trusted v2 publication attestation is bound to its exact digest. Without that attestation, project it as `PUBLISHED_UNATTESTED`.
2. `PromptCapabilityAdapter` — project `PromptSpec`; apply the same attestation rule to ordinary prompts. Built-in prompt seeds may use a separately defined, code-controlled built-in trust policy bound to exact content digests; a caller-supplied `builtin:` provenance string is not sufficient. Preserve REAP safety-pairing validation in Factory.
3. `WorkflowCapabilityAdapter` — project `WorkflowDefinition`; report `REGISTERED_UNVERIFIED` until a trusted publication record exists. Do not make existence equivalent to publication.
4. `AgentBlueprintCapabilityAdapter` — project an exact blueprint definition. A blueprint may be referenced as an agent component only if the consuming operation explicitly supports agent references and the state policy allows it. `ACTIVE` runtime identity remains distinct from this projection.

Do not build a second SQLite capability database that duplicates all native records. The first phase may have its own small metadata tables for dependency locks, evaluation artifacts, or index rows, but every such row must point back to native authoritative records and be rebuildable or verifiable.

### 6.3 Workflow publication follow-up

If the project elects to support reusable published workflows in this Factory slice, add a separate workflow lifecycle design before adding writes:

- Define legal state transitions and trusted authorizer permissions.
- Require independent approval and evidence; no self-approval.
- Preserve immutable definition content and append-only events.
- Ensure the executor revalidates status and permissions at run time.
- Add backward-compatible migrations for existing workflow DBs.
- Keep execution of existing registered workflow definitions compatible with the current contract unless a separately approved migration intentionally changes it.

Until all of this exists, the adapter must fail closed for dependency classes requiring publication.

### 6.4 Publication attestation gap in existing skill/prompt registries

Current `SkillRegistry.transition/publish` and `PromptRegistry.transition/publish` accept evidence strings but do not accept a trusted actor or call an authorizer. Their event tables record transition, evidence and timestamp, but not the approver identity. Consequently, the current `PUBLISHED` state is useful native lifecycle metadata but is not, by itself, proof of a v2-governed publication decision.

Add a distinct `PublicationAttestation` (or equivalent typed governance evidence) as part of the governed publishing slice. It must bind at least the capability type/ID/version, immutable content digest, trusted approver actor ID, authorization decision/reference, evidence reference, policy/attestation schema version and timestamp. The attestation writer must receive actor identity from a trusted service boundary and invoke the correct authorizer permission (for example, `capability.publish`); no caller-provided actor/permission fields are trusted.

The v2 adapters and resolver require both the native lifecycle state and a valid attestation for governed reusable publication. Legacy published rows without an attestation remain readable and executable only under existing direct contracts, but are classified as unverified by v2 composition. Do not retroactively invent approvers for old rows. Additive actor fields to native event tables may be considered in a separately scoped migration, but historical rows must remain explicitly `actor_unknown`.

---

## 7. Resolver, dependency lock and compatibility

### 7.1 CapabilityResolver

Suggested internal API:

```python
class CapabilityResolver:
    def resolve(self, root: CapabilityRef, *, purpose: str) -> ResolvedCapabilityGraph: ...
    def validate_graph(self, graph: ResolvedCapabilityGraph) -> CompatibilityReport: ...
```

The resolver must use registered adapters and native stores, not remote lookup or fuzzy search.

For each node it must verify:

- the exact object and version exist;
- its native content is valid and digest matches;
- its lifecycle state is eligible for the requested purpose;
- every hard dependency resolves to the exact pinned version;
- no dependency cycle exists;
- no pair of nodes conflicts;
- every dependency type is permitted for the parent type;
- interface compatibility is proven where schemas exist;
- requested permissions can be considered by the caller's policy context (but never granted here);
- deprecation/revocation policy does not block this new use.

### 7.2 Lockfile / resolved graph

A successful resolution returns an immutable lock snapshot:

```python
@dataclass(frozen=True)
class ResolvedNode:
    ref: CapabilityRef             # includes digest
    native_state: str
    adapter_schema_version: int

@dataclass(frozen=True)
class ResolvedCapabilityGraph:
    root: CapabilityRef
    nodes: tuple[ResolvedNode, ...]
    edges: tuple[DependencyEdge, ...]
    graph_digest: str
    resolver_version: str
    resolved_at: str
```

A released blueprint/composition stores the exact graph digest and the exact resolved refs. It must never re-resolve a released version to “whatever is latest”. New versions require a new draft, revalidation, evaluation as required, approval, and a new lock.

### 7.3 Compatibility semantics

Compatibility outcomes must be explicit:

- `COMPATIBLE`: deterministic checks prove the supported contract subset matches.
- `INCOMPATIBLE`: a known mismatch exists.
- `UNKNOWN`: insufficient machine-readable information exists.
- `NOT_CHECKED`: no evaluator was run.

For an edge that is required for execution/activation, `UNKNOWN` is not compatible. A caller may allow a human-reviewed `UNKNOWN` for non-executable documentation or exploratory drafts, but that exception must be explicit, scoped, and recorded as evidence. Never silently coerce unknown to compatible.

Initial JSON Schema compatibility should be a deliberately small supported subset. Document supported keywords and reject unsupported constructs. Prefer conservative assignability: producer output must be demonstrably accepted by consumer input. Do not attempt “best effort” semantic inference.

### 7.4 Cycle, conflict and version rules

- Reject dependency cycles, self-dependencies, duplicate edges, dangling refs, malformed versions, unsupported relation/type combinations, and digest mismatch.
- `CONFLICTS_WITH` must be checked across the entire resolved graph, not just direct siblings.
- `SUPERSEDES` is not an automatic replacement instruction. It informs deprecation/upgrade analysis; do not rewrite existing pins.
- `COMPATIBLE_WITH` claims require evidence bound to both exact digests and the compatibility-checker version.
- Version ranges, floating tags, dynamic aliases, and automatic upgrades are out of scope for released graphs.

---

## 8. Capability discovery index

Create a deterministic `CapabilityIndex` over adapter projections.

### 8.1 Initial search fields

- capability type;
- stable ID and version;
- name/description text;
- tags/domain/role;
- requested permissions;
- lifecycle/native state;
- exact interface schema fields where present;
- provenance kind;
- digest and dependencies.

### 8.2 Search behavior

- First implementation is deterministic metadata search (exact ID, type, tags and bounded text matching).
- Results must identify native state and content digest.
- Drafts/unverified items may be included only when caller explicitly requests them and the response labels them as such.
- The index is a discovery aid, never an authorization check or source of truth. Before composition, approval, evaluation, or execution, re-read through the native adapter/resolver.
- No semantic embeddings, remote index, vector database requirement, or LLM-based search in the first phase.
- Bound result count, query length, tags, payload bytes, and deterministic ordering/pagination.

---

## 9. Evaluation Lab

Evaluation is a new required capability of the Factory, but evaluation must remain separate from governance approval and permission issuance.

### 9.1 Data model

Add immutable, versioned records (names are design guidance; use repository conventions):

- `EvaluationSuite`: suite ID/version, description, supported capability types, evaluator configuration, case refs, required hard gates, provenance, lifecycle.
- `EvaluationCase`: case ID/version, input fixture reference or bounded synthetic input, expected properties/oracle, risk label, tags, and whether it belongs to development or held-out validation.
- `EvaluationRun`: run ID, exact target capability ref/digest, exact suite ref/digest, evaluator version, execution environment/profile, actor, start/end, resource limits, and final status.
- `EvaluationResult`: per-case outcome, structured measurements, failure class, evidence/artifact digests, and optional rubric result.

Do not persist unbounded raw customer/personal inputs in evaluation artifacts. Prefer synthetic fixtures and content-addressed bounded evidence. Any sensitive data use needs a separate approved policy and privacy design.

### 9.2 Evaluation stages

1. **Structural gates** — contract serialization, bounds, references, dependency graph, interface checks, policy metadata, schema validation.
2. **Deterministic tests** — unit and integration cases against registered operations and fixed fixtures.
3. **Security tests** — prompt injection/untrusted metadata, permission escalation attempts, malformed inputs, dependency substitution, digest tampering, privilege boundary checks, resource limits.
4. **Behavioral tests** — only where the capability has an agent/model step; use fixed scenarios, rubric definitions, baselines, repeated trials where necessary, and record exact model/runtime versions.
5. **Regression comparison** — compare candidate against the prior approved version and relevant baselines.
6. **Held-out validation** — cases not used to draft or optimize the candidate. Keep the split identity/digest fixed for a given evaluation release.

### 9.3 Hard gates versus quality metrics

- Hard gates are binary and block approval when failed (e.g., schema invalid, required operation absent, unauthorized tool call, permission escalation, data boundary breach, integrity mismatch).
- Quality metrics (e.g., helpfulness, style, answer preference) are measurements, not substitutes for hard gates.
- Do not collapse multiple metrics into a single score that hides critical failures.
- Report coverage, case count, pass/fail counts, confidence/variance where relevant, model/profile, and known limitations.
- A high score does not imply safety, trust, permission, publication, approval, or production readiness.

### 9.4 Reproducibility

Each evaluation result must be bound to:

- target definition digest;
- exact dependency graph/lock digest;
- suite digest and held-out split identity;
- evaluator implementation version;
- relevant model ID/provider/version and inference settings if an LLM was used;
- deterministic operation version(s);
- runtime/environment version and relevant resource limits;
- test artifacts/output digests;
- actor and evaluation timestamps.

When a component or evaluator changes, old results remain immutable historical evidence; they do not automatically qualify the new digest.

### 9.5 Approval separation

A successful evaluation may make a candidate eligible for review. It must not automatically:

- approve or publish a skill/prompt/workflow;
- approve an agent blueprint;
- change the trust level of a runtime identity;
- issue a policy Permission;
- unlock higher ActionClass levels;
- approve deployment, merge, release, or external side effects.

Approval remains a separate, trusted, evidence-backed operation.

### 9.6 First evaluation slice

Start with an offline, hermetic evaluator for metadata, graph, skill operation contracts, workflow structure and permission boundaries. It must run without live models, credentials, network access, or remote repositories. Behavioral/model-graded evaluation is a later adapter and must be disabled unless a trusted evaluator configuration and metering path are provided.

---

## 10. Composition and agent specialization

### 10.1 Agent composition

The Factory specializes an agent by composing:

- an existing Agency Agent blueprint;
- exact pinned prompt versions and validated prompt parameters;
- exact published skill versions;
- exact workflow versions with eligible publication evidence;
- explicitly declared memory/context configuration references where the current system provides safe contracts;
- model/runtime profile references only once they have typed, validated Agency-native contracts;
- policy and sandbox requirements as declarations for downstream policy/runtime enforcement.

Composition creates a new draft definition (or a new version of an existing blueprint). It does not mutate a live identity or inherit permissions.

### 10.2 Composition contract

A deterministic `CompositionEngine` should:

1. Accept a typed composition request and trusted creator identity.
2. Validate permitted component types and bounded component counts.
3. Resolve exact references and verify their digests/status.
4. Build the dependency graph and compatibility report.
5. Detect cycles, conflicts, unsupported relations and missing hard dependencies.
6. Calculate requested permission union for review only; never grant that union.
7. Emit a draft blueprint plus immutable lock/evidence record.
8. Require the normal validation, evaluation (when configured), approval and activation path.

### 10.3 Agent teams are a later composition type

Do not make general multi-agent team runtime a prerequisite for the first capability contract/resolver/evaluator slices. When team support is implemented, an `AGENT_TEAM` blueprint must define at minimum:

- team ID/version and team purpose;
- member role slots and cardinality constraints;
- exact member blueprint refs and required versions;
- communication/delegation edges and allowed message/data contracts;
- task ownership and completion criteria;
- explicit lead/coordinator responsibilities (which do not confer policy authority);
- per-member and aggregate resource/budget limits;
- approval/escalation/quorum rules, if any;
- failure, timeout, replacement and cancellation behavior;
- team-level evaluation suite and provenance.

A team blueprint is not a running team. A role is not an agent identity. A temporary subagent is not automatically a reusable published capability. Each runtime instance must still receive a distinct Agency identity and only the permissions granted to it through existing authority paths.

---

## 11. Lifecycle, deprecation, revocation and rollback

### 11.1 Immutable definitions

- Existing versions are immutable after registration/publication.
- Changes create new versions.
- Published/approved history must not be rewritten in place to “fix” an old artifact.
- Lifecycle/evidence records are append-only where the native registry supports events. Verify not only whether an event exists, but whether it records a trusted actor and authorization decision. Current skill/prompt events do not record actors; legacy `PUBLISHED` rows therefore require an explicit v2 publication attestation before they can be treated as governed dependencies. Never retroactively invent actors or approvals. Additive actor fields may use `actor_unknown` for historical records.

### 11.2 Deprecation impact analysis

Before deprecating/retiring a reusable capability, compute:

- direct dependents;
- transitive dependents;
- active or approved agent blueprints referencing it;
- compositions/lockfiles containing its digest;
- workflow runs and evaluation evidence that relied on it;
- a proposed migration target, if one exists;
- whether the target has proven compatibility or remains unknown.

Do not block or rewrite historical records. New resolutions should follow an explicit policy for deprecated or revoked versions. Revocation must stop new use; runtime/active consumer invalidation requires an explicit enforcement integration, not only a registry status flag.

### 11.3 Rollback semantics

Rollback means selecting a previously approved, still-valid exact version and re-running required validation/authority checks. It does not mean erasing history or mutating a released record. A revoked identity must not be silently resurrected; a new activation/release event requires its own authority path.

### 11.4 Restart and reconciliation

Preserve the existing Factory v1 fail-closed behavior. Do not implement automatic restoration of `ACTIVE` blueprint rows into process-local kernel/runtime registries in this capability PR unless a separate approved identity-rehydration specification and tests exist.

For later recovery work, the system must be able to distinguish at least:

- durable desired state;
- last recorded activation event;
- runtime identity presence/absence;
- revocation status;
- authority and permission validity;
- uncertain or incomplete transition.

Never interpret a persisted `ACTIVE` label alone as proof that a runtime identity exists or is authorized.

---

## 12. Provenance, integrity and trust

### 12.1 Evidence graph

Every release/evaluation/approval should be traceable through a graph of immutable references:

```text
Capability definition digest
       |
       +----> exact dependency lock digest
       |
       +----> source/provenance record
       |
       +----> evaluation suite + run evidence
       |
       +----> approval actor + evidence reference
       |
       +----> activation/deployment event (if any)
       |
       +----> execution/run evidence (if any)
```

Use existing `kernel.audit` and `agency.evidence` where the contracts fit. Do not create a competing audit ledger. Link evidence using typed IDs and digests; avoid copying large raw payloads into audit records.

### 12.2 Integrity is not authenticity

- A SHA-256 digest can identify content and detect accidental or malicious content substitution when compared against a trusted expected digest.
- A digest by itself does not identify an author, prove a review occurred, prove source origin, or make a mutable SQLite database tamper-proof.
- If signing is added in a future supply-chain phase, define signer identity, trust roots, key custody/rotation, verification policy, revocation, and transparency/audit behavior. Never accept “signature present” without checking the signer identity against policy.
- Do not add Sigstore, OCI registries, or a remote artifact service as an MVP runtime dependency. The first phase borrows their content-addressing/provenance principles only.

### 12.3 Untrusted imports

Any later import flow must begin in quarantine and preserve source provenance/license. It must not auto-fetch transitive URLs, invoke supplied commands, install packages, or execute included scripts. The Agency may parse inert declarative metadata under bounds, run approved tests in an isolated environment only after separate authorization, and promote an imported definition only after its own review and approval.

---

## 13. Permission model and execution boundary

This is a critical safety section.

### 13.1 Three-way permission separation

For every component and composed agent, track separately:

1. `requested_permissions`: the component author says it needs.
2. `approved_permissions`: a trusted governance/authority path has granted these to a specific actor/agent and scope.
3. `effective_permissions`: the permissions the actual executor/policy layer verifies at the moment of the operation.

These sets are not equivalent. `requested_permissions` may inform review but can never mutate `approved_permissions` or `effective_permissions`.

### 13.2 Enforcement

- `CapabilityResolver` returns declared permission requirements, not grants.
- `CompositionEngine` may calculate a permission requirement summary, but must label it `REQUESTED_ONLY`.
- `AgentFactory.activate` continues to create UNKNOWN/L0 identity claims only unless separate governance explicitly changes that contract.
- `SkillExecutor` and `WorkflowExecutor` remain enforcement points for their respective operations and must re-check current permission on execution.
- Every external actuator must check the relevant scoped permission itself; no upstream “the Factory approved it” boolean is sufficient.
- Unknown capability names, unsupported operation names, missing authorizer, exceptions, stale/expired permission, and scope mismatch deny by default.

### 13.3 Risk classification

Capability metadata may declare a minimum/expected action class, but only Agency policy determines the actual authorized class. A manifest cannot self-assign trust level. Raising action class or granting permissions requires a separate governance process and human authority where current L0–L5 policy requires it.

---

## 14. Internal API sketch

Names below are suggested internal contracts. Follow established repository style; do not expose these as HTTP endpoints in this PR or the first implementation phases.

```python
class CapabilityRegistryView(Protocol):
    def get(self, ref: CapabilityRef) -> CapabilityContract: ...
    def list(self, query: CapabilityQuery) -> list[CapabilityContract]: ...

class CapabilityResolver:
    def resolve(self, root: CapabilityRef, *, purpose: ResolutionPurpose) -> ResolvedCapabilityGraph: ...

class CapabilityIndex:
    def rebuild(self) -> IndexBuildReport: ...
    def search(self, query: CapabilityQuery) -> list[CapabilitySearchResult]: ...

class CompatibilityChecker:
    def check(self, graph: ResolvedCapabilityGraph) -> CompatibilityReport: ...

class EvaluationRegistry:
    def register_suite(self, suite: EvaluationSuite, *, actor: str) -> EvaluationSuite: ...
    def get_suite(self, suite_id: str, version: str) -> EvaluationSuite: ...
    def record_run(self, run: EvaluationRun) -> EvaluationRun: ...
    def get_run(self, run_id: str) -> EvaluationRun: ...

class EvaluationRunner:
    def run(self, target: CapabilityRef, suite: CapabilityRef, *, actor: str) -> EvaluationRun: ...

class CompositionEngine:
    def compose_agent(self, request: AgentCompositionRequest, *, creator: str) -> CompositionDraft: ...
    def inspect_impact(self, ref: CapabilityRef) -> DependencyImpactReport: ...
```

Requirements for these APIs:

- Exact, validated typed inputs; no loosely typed `dict[str, Any]` across trust boundaries unless a validated schema is applied immediately.
- Bounded payloads, item counts, recursion depth, runtime, and result sizes.
- Deterministic output ordering and stable error types/codes.
- Explicit trusted actor context injected by service composition; never built from user-supplied free-form fields.
- Read methods must not mutate native registries or lifecycle state.
- No public API/CLI/GUI wiring until service and security contracts are independently reviewed.

---

## 15. Proposed module layout

Prefer a focused new package. Adapt to actual repository patterns rather than creating modules that duplicate existing code.

```text
src/agency/capability/
    __init__.py
    contract.py          # CapabilityType, CapabilityRef, CapabilityContract
    provenance.py        # structured provenance/evidence references
    adapters.py          # read-only native registry adapters
    resolver.py          # exact-version dependency resolver + lockfile
    compatibility.py     # deterministic schema/graph checks
    index.py             # rebuildable deterministic metadata index
    composition.py       # capability/agent composition validation
    evaluation/
        __init__.py
        models.py        # suite, case, run, result
        registry.py      # immutable evaluation artifacts
        runner.py        # hermetic deterministic evaluation harness
        gates.py         # hard-gate/report policy
```

Possible shared constants/utilities should be extracted only if they have two or more real consumers and no behavior change. Do not prematurely refactor `AgentFactory`, `SkillRegistry`, `WorkflowRegistry`, or PromptRegistry into a generic CRUD abstraction.

### 15.1 Persistence

- Use SQLite and the repository's existing patterns for this bounded local-first phase.
- Keep native source tables authoritative.
- Store only new data: evaluation suites/runs/results, resolved graph lock snapshots, dependency index rows/cache, and any explicit workflow lifecycle events if the workflow publication decision is implemented.
- Use transactions for multi-row writes and compare-and-swap state transitions.
- Add indexes only for demonstrated lookup paths.
- No new infrastructure service or cloud dependency.
- Use explicit schema versions and forward-only migrations. Test opening all preexisting DB schemas with the new code. Never drop or rewrite existing blueprint/skill/workflow/prompt history.

### 15.2 Hashing/canonicalization

Provide one canonical serializer with golden-vector tests. It must reject NaN/Infinity, unsupported objects, bytes where not explicitly encoded, excessive nesting, oversized data, and ambiguous version encodings. Test equivalent data ordering to ensure stable digests and test changed semantic content to ensure changed digest.

---

## 16. Phased implementation plan

Implement as small independently reviewable changes. The coding agent must not implement the whole plan as one giant speculative patch.

### Phase 0 — Baseline reconciliation (required first)

- Rebase/branch from the latest `main` and record exact SHA.
- Read all §3 source and tests; map current data models and public/internal call sites.
- Run the baseline verification commands that apply to the checkout and record exact results/SHA.
- Identify any implementation changes on `main` since the inspected baseline.
- Make no code changes until a short design checklist confirms source-of-truth and lifecycle decisions.

**Exit:** baseline matrix lists real contracts, test locations, migrations and known failures; no stale doc was treated as implementation evidence.

### Phase 1 — Shared typed contract and digest (first coding PR)

- Add `CapabilityType`, `CapabilityRef`, `DependencyEdge`, `ProvenanceRef`, `CapabilityContract` and canonical serialization/digest.
- Add read-only adapters for SkillRegistry and PromptRegistry, then Factory blueprint and WorkflowRegistry with explicit `REGISTERED_UNVERIFIED` / `PUBLISHED_UNATTESTED` projection states where appropriate.
- No new writeable shadow registry and no execution path; do not make legacy published rows appear governance-attested.
- Add golden digest vectors, round-trip and invalid-input tests.

**Exit:** every current native record type can be projected without inventing absent metadata; digests stable and verified; native data unchanged.

### Phase 2 — Resolver and rebuildable index

- Add exact-version resolver, digest verification, bounded graph traversal, cycle/conflict checks, and compatibility states.
- Add deterministic metadata index with rebuild/reconciliation and stale-index tests.
- Add dependency lock snapshots and exact pin enforcement.
- Do not add semantic search or floating version constraints.

**Exit:** all invalid, unpublished/unverified, retired/revoked, incompatible, cyclic, missing or tampered references fail closed; a valid lockfile reproduces exact same graph/digest.

### Phase 3 — Permission and lifecycle integration

- Produce requested-permission summaries separately from effective grants.
- Add trusted, digest-bound publication attestations for skill/prompt (and workflow if lifecycle is added): trusted actor, required authorizer permission, evidence reference, policy/schema version and timestamp. Never backfill invented actors for historical rows.
- Verify any new governed execution route checks the relevant publication attestation plus the existing executor's permission authorization at point of use.
- Add impact-analysis queries for deprecation/revocation.
- If workflow publication is in scope, implement its lifecycle as an independently tested migration-backed change; otherwise keep workflow projections unverified for publication purposes.

**Exit:** no manifest/contract/approval path can grant authority; runtime checks remain authoritative; lifecycle events have attributable actors/evidence.

### Phase 4 — Evaluation Lab, offline first

- Add versioned evaluation suite/case/run/result models and registry.
- Implement deterministic structural, dependency, permission, workflow/skill contract and adversarial input tests.
- Record suite/target/dependency/evaluator digests and exact outcomes.
- Require hard-gate pass before an evaluation can be marked `ELIGIBLE_FOR_REVIEW`.
- Keep any model-graded evaluator behind a separate explicit adapter, disabled by default.

**Exit:** offline evaluation is hermetic and reproducible; evidence is immutable; pass is not approval or permission.

### Phase 5 — Agent composition

- Build deterministic `CompositionEngine` for agent blueprint + pinned prompt/skill/workflow dependencies.
- Output a new draft blueprint and dependency lock; do not activate or grant permissions.
- Preserve existing AgentFactory approval/activation flow and prompt composition hash.
- Add exact pin, conflict, interface, dependency impact and regression tests.

**Exit:** composed draft is reproducible from same inputs; all dependencies pinned; standard approval/evaluation gates still apply.

### Phase 6 — Runtime deployment and recovery (separate gated project slice)

- Only after identity, audit, policy, sandbox and peer authority prerequisites are satisfied in current Agency plan.
- Specify active desired-state reconciliation, runtime identity lifecycle, partial failure, crash ambiguity, revocation and operator approval separately.
- Do not silently rehydrate `ACTIVE` blueprint rows or infer runtime presence from SQLite.
- No production deployment claims without OS-isolation and target-host tests.

**Exit:** restart/reconciliation guarantees are backed by failure injection and independent review; existing fail-closed behavior retained until then.

### Phase 7 — Later extensions

- Agent team blueprints and team runtime.
- Tool-adapter and knowledge-pack capability types.
- Approved imports/quarantine workflow and optional artifact signing.
- Skill optimization/SkillOpt-like offline improvement loop with held-out regression gates.
- Remote distribution/marketplace or semantic search only after a separate threat model, identity/auth, supply-chain and privacy spec.

These are not part of the first implementation PR.

---

## 17. Test strategy and required coverage

All tests must be offline, deterministic, and use temporary isolated data paths unless a test explicitly validates a separately configured integration. No provider keys, live network, remote repo, host shell, or external agent is required for core capability tests.

### 17.1 Contract/serialization

- Valid object per supported type.
- Unsupported type rejected.
- Invalid ID/version/digest/enum/relation rejected.
- Oversized fields, arrays, nesting, JSON and invalid Unicode rejected.
- Canonical digest remains stable across dictionary insertion order.
- Digest changes when semantic immutable content changes.
- Lifecycle/evidence timestamp changes do not change immutable content digest.
- Digest mismatch during adapter read/resolution fails closed.

### 17.2 Adapters/native preservation

- Round-trip each adapter against real in-memory/temp native registries.
- Draft/unapproved/unpublished/retired/revoked/unverified records projected accurately.
- Adapter failure or corrupt record denies resolution.
- Adapter does not write to native state, publish, approve, activate, fetch or execute.
- Existing native APIs and test fixtures remain compatible.

### 17.3 Resolver/composition

- Exact pinning and lockfile determinism.
- Missing reference, wrong type, wrong version, wrong digest, stale index, cycle, self-edge, duplicate edge, invalid relation, conflict and unsupported type.
- Deprecated/revoked dependency policy.
- Interface compatible/incompatible/unknown cases, including unsupported JSON Schema keyword.
- A new version registered after lock creation does not alter an existing lock.
- Native source changes are detected by digest mismatch.

### 17.4 Lifecycle and security

- Creator cannot approve itself.
- No authorizer, wrong actor, callback exception, or denied permission fails closed.
- Model-generated actor/permission/approval fields cannot influence authorization.
- Capability requested permissions never appear as granted permissions.
- No `Permission` is issued on agent blueprint projection, composition or evaluation.
- Runtime executor denies missing/stale/expired/out-of-scope grants.
- Revocation blocks new resolution/use as specified without erasing history.
- Concurrent transitions are safe under SQLite locking/compare-and-swap.

### 17.5 Evaluation

- Deterministic evaluation on fixed fixture set yields reproducible results.
- Every result references exact target/suite/lock/evaluator digest.
- Held-out cases are not exposed to candidate optimization path.
- Hard-gate failure blocks `ELIGIBLE_FOR_REVIEW` irrespective of quality metrics.
- Missing tests, incomplete coverage, unknown oracle or unavailable evaluator never count as pass.
- A score cannot transition lifecycle or grant permission.
- Baseline/candidate comparisons preserve old evidence.

### 17.6 Persistence, recovery and migrations

- Open existing pre-v2 databases without data loss.
- Upgrade from current Factory, SkillRegistry, PromptRegistry, WorkflowRegistry schemas.
- Close/reopen and verify exact definition/evidence/lock/run digest.
- Interrupted writes and transaction failures leave a consistent state.
- Rebuild index from native registries and compare contents deterministically.
- Verify no auto-reactivation/identity rehydration occurs.

### 17.7 Quality gates

Follow the current repo's actual CI configuration. At minimum:

```bash
ruff check src/agency tests
ruff format --check src/agency tests
python -m mypy src/
python -m pytest tests/ -q
node --test tests/*.test.mjs
```

Run only commands that exist in the current checkout and report the exact results. If the repository's current test suite has known environment-specific harness failures, distinguish those from product failures and rerun in a supported Linux environment where necessary. Never copy a historic total into the PR as proof of this implementation.

---

## 18. Observability and operator experience