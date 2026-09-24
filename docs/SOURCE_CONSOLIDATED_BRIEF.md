# The Agency: Consolidated Architecture, Product Direction, and Implementation Brief

> **Purpose:** This document consolidates the architectural, product, engineering, and roadmap decisions discussed for **The Agency** so a coding agent can use it as a single source of truth while implementing and improving the repository.

Repository:
- `https://github.com/athena254/The-Agency`

Related but separate repository:
- `https://github.com/athena254/Athena-global-skills`

---

## 1. Executive Summary

**The Agency** is intended to become a serious, general-purpose autonomous agent framework and system.

It should not merely be another chatbot wrapper or an orchestration shell around third-party agents. It should be capable of:

- creating and managing its own agents;
- creating, storing, versioning, composing, and executing reusable skills;
- creating, storing, versioning, composing, and executing reusable workflows;
- executing workflows deterministically wherever deterministic execution is possible;
- providing a human-facing Butler interface;
- supporting multiple independent conversation threads and persistent work contexts;
- coordinating agents through a deterministic coordination/governance layer;
- auditing and challenging agent work;
- operating independently rather than depending on ATHENA or external agent frameworks;
- building software through an internal software factory called **The Forge**;
- supporting coding agents that can use Forge to build and modify software;
- maintaining strong security, isolation, provenance, validation, and governance;
- eventually competing directly at the product/system level with platforms such as OpenClaw, Hermes, Claude Code, and Grok Bot.

The goal is not to reproduce those systems. The goal is to build a coherent system whose architecture gives users reasons to choose The Agency because of its combination of:

1. native agent creation;
2. reusable skills and deterministic workflows;
3. persistent multi-threaded work;
4. built-in governance and adversarial QA;
5. software-building capability through Forge;
6. security and independence;
7. composability and extensibility;
8. transparent execution and provenance.

---

# 2. Critical Platform Boundary: The Agency vs ATHENA

## 2.1 ATHENA is separate

**ATHENA and The Agency are peer platforms.**

Do not treat ATHENA as a subsystem, dependency, parent process, or component of The Agency.

ATHENA has:

- its own agents;
- its own orchestration capabilities;
- an ATHENA agent responsible for orchestration;
- the ability to orchestrate external agents.

The Agency has:

- its own agents;
- its own Agent Factory;
- its own skills;
- its own workflows;
- its own governance;
- its own Butler;
- its own software factory, Forge;
- its own internal execution and coordination architecture.

The Agency must be able to operate independently.

## 2.2 Do not couple The Agency to ATHENA

Do not introduce architectural assumptions such as:

- The Agency requiring ATHENA to function;
- The Agency delegating all agent creation to ATHENA;
- The Agency treating ATHENA as its central controller;
- ATHENA owning Agency state;
- ATHENA becoming the hidden source of truth for Agency agents, skills, or workflows.

If an integration with ATHENA exists in the future, it should be an explicit integration boundary.

The Agency should remain useful and operational without it.

## 2.3 Independence is a feature

The Agency was conceived with security and independence as foundational properties.

In the broader ecosystem, The Agency may act as an independent control, audit, or counterbalance capability.

That requires genuine separation of concerns.

The Agency should not automatically inherit decisions, assumptions, or authority from the system it may eventually be expected to audit or challenge.

---

# 3. What The Agency Is

The Agency should be understood as a **general-purpose agent operating system/framework**, not merely an LLM application.

A useful conceptual model is:

```text
                         HUMAN
                           |
                         BUTLER
                           |
                 Conversation / Workspaces
                           |
                    Intent + Context
                           |
                Agency Coordination Layer
                           |
        +------------------+------------------+
        |                  |                  |
      Agents            Workflows           Skills
        |                  |                  |
        +------------------+------------------+
                           |
                    Tool / Runtime Layer
                           |
          +----------------+----------------+
          |                |                |
       Sandbox          Forge           External APIs
                           |
                     Software Output
```

Cross-cutting all of this:

```text
Security
Governance
Policy
Provenance
Auditability
Observability
Validation
Adversarial QA
Memory / Context Management
```

---

# 4. Existing Repository Direction

The repository currently describes The Agency as a decentralized multi-agent orchestration system built around a shared graph/"Lattice" model.

Existing concepts that should be retained where they are architecturally sound include:

- Butler gateway;
- Buddy/UI concepts;
- sandbox;
- QA Critic / adversarial QA;
- governance;
- Lattice;
- domain agents;
- foundation services;
- specification and architecture documentation.

However, the implementation and documentation should evolve toward the broader architecture described in this document.

Do not blindly preserve an old design merely because it already exists.

The repository should converge on one internally consistent architecture.

---

# 5. Butler

## 5.1 Butler's role

The Butler is the human-facing conversational and interaction layer.

It should:

- receive user requests;
- understand conversation context;
- manage conversational threads;
- maintain work context;
- resolve references;
- route work into the Agency execution system;
- present progress and results;
- surface approvals and blocked actions;
- explain what the Agency is doing;
- preserve continuity across long-running work.

The Butler should **not** become the universal super-agent responsible for everything.

It should be an intelligent interface and context manager around the Agency's actual execution architecture.

## 5.2 Multiple conversation threads

The Butler should support multiple concurrent conversation threads.

A user should be able to have:

```text
Workspace: Project Alpha

  Thread A: Architecture
  Thread B: Backend implementation
  Thread C: Research
  Thread D: Debugging
  Thread E: Documentation
```

Threads should have:

- unique IDs;
- titles;
- timestamps;
- lifecycle state;
- parent workspace/project;
- message history;
- summaries;
- context references;
- active agents;
- active workflows;
- linked artifacts;
- linked tasks;
- permissions;
- provenance.

A thread must not accidentally contaminate another thread's context.

## 5.3 Thread branching

The system should eventually support branching a conversation/work context.

For example:

```text
Main Thread
   |
   +-- Architecture Option A
   |
   +-- Architecture Option B
   |
   +-- Experimental Implementation
```

Branches should preserve provenance and make it possible to compare outcomes.

## 5.4 Persistent workspaces

A conversation should not be the only unit of work.

Introduce a concept such as:

```text
Workspace
    Project
        Thread
            Task
                Agent Run
                    Tool Calls
                        Artifacts
```

This allows a user to return days later without losing the project's state.

---

# 6. Context Architecture

The Agency should separate:

- conversation context;
- task context;
- workspace/project context;
- agent context;
- workflow context;
- skill context;
- tool context;
- persistent memory;
- artifact state.

Do not dump all available information into every model invocation.

Context should be selected deliberately.

A context manager should answer:

1. What does this agent need to know?
2. What does this task need to know?
3. What does the current thread need to know?
4. What persistent information is relevant?
5. What information must explicitly NOT be exposed?
6. Which artifacts are authoritative?
7. Which context is stale?
8. Which context came from untrusted sources?

---

# 7. Agent Factory

## 7.1 Core requirement

The Agency must be able to create its **own agents**.

It must not require an external agent platform to manufacture agents.

External models/providers can be used as implementation resources, but the Agency owns the agent lifecycle.

## 7.2 Agent Factory responsibilities

The Agent Factory should support:

- agent specification;
- agent templates;
- role definitions;
- system instructions;
- capabilities;
- tool permissions;
- memory configuration;
- context configuration;
- model configuration;
- security policies;
- sandbox requirements;
- evaluation suites;
- workflow compatibility;
- versioning;
- deployment;
- retirement;
- rollback;
- provenance.

Conceptually:

```text
Agent Specification
        |
        v
Validation
        |
        v
Capability Resolution
        |
        v
Security Policy
        |
        v
Evaluation
        |
        v
Agent Package
        |
        v
Registry
        |
        v
Runtime
```

## 7.3 Agent Factory should generate more than agents

The Factory should become a general reusable capability factory.

It should be able to produce:

- agents;
- skills;
- workflows;
- potentially tool adapters and evaluation packages later.

This creates a coherent lifecycle:

```text
Create
  -> Validate
  -> Evaluate
  -> Version
  -> Register
  -> Deploy
  -> Observe
  -> Improve
  -> Retire
```

---

# 8. Skills

## 8.1 Skills as reusable capabilities

A skill should represent a reusable capability rather than a full autonomous agent.

Examples:

- inspect a repository;
- summarize a document;
- run a test suite;
- generate a migration;
- research a topic;
- validate a configuration;
- analyze logs;
- produce a security review.

Skills should be:

- discoverable;
- composable;
- versioned;
- permission-aware;
- testable;
- portable;
- auditable.

## 8.2 Skill registry

The Agency should have a registry containing:

```text
Skill
- id
- name
- version
- description
- inputs
- outputs
- dependencies
- permissions
- implementation
- model requirements
- deterministic status
- tests
- provenance
- compatibility
```

## 8.3 Skill execution

Prefer deterministic implementation where possible.

For example:

```text
Input validation -> deterministic
File discovery -> deterministic
Git diff generation -> deterministic
JSON transformation -> deterministic
Schema validation -> deterministic
Hashing -> deterministic
```

Use an LLM only where judgment or semantic reasoning is required.

---

# 9. Workflows

## 9.1 Workflows are first-class objects

A workflow should not merely be a prompt describing what an agent should do.

It should be an executable object.

Example:

```text
Workflow
  Step 1: validate input
  Step 2: retrieve artifacts
  Step 3: execute deterministic transformation
  Step 4: ask agent for analysis
  Step 5: validate analysis
  Step 6: execute deterministic action
  Step 7: produce artifact
  Step 8: audit
```

## 9.2 Deterministic where possible

This is a major design principle.

If part of a workflow can be deterministic, it should be.

Do not unnecessarily invoke an LLM for:

- branching that can be expressed as rules;
- validation;
- formatting;
- data transformation;
- file operations;
- state transitions;
- retries;
- permission checks;
- schema checks;
- artifact hashing;
- known tool sequences.

LLMs should handle tasks requiring:

- interpretation;
- planning;
- synthesis;
- ambiguous reasoning;
- natural-language generation;
- novel problem solving.

## 9.3 Hybrid workflows

The ideal architecture is:

```text
Deterministic orchestration
        +
Selective agent reasoning
        +
Deterministic validation
        +
Auditable state transitions
```

This provides more predictable behavior than an architecture where every step is delegated to an LLM.

## 9.4 Workflow registry

Store:

- workflow ID;
- version;
- graph/DAG;
- steps;
- conditions;
- inputs;
- outputs;
- required skills;
- required agents;
- permissions;
- deterministic steps;
- nondeterministic steps;
- retry rules;
- timeout rules;
- compensation/rollback behavior;
- evaluation suite;
- provenance.

---

# 10. Agent + Skill + Workflow Relationship

The architecture should distinguish these three primitives:

```text
AGENT
Reasoning actor / autonomous specialist

SKILL
Reusable capability

WORKFLOW
Reusable execution procedure
```

They compose:

```text
Agent
  |
  +-- uses Skill A
  +-- uses Skill B
  |
  +-- executes Workflow X
          |
          +-- Skill C
          +-- Agent Y
          +-- deterministic Tool
```

A workflow may invoke agents.

An agent may invoke skills.

A skill may invoke deterministic tools.

The architecture should avoid circular ownership and unclear lifecycle semantics.

---

# 11. The Forge

## 11.1 Rename the Agency's internal software factory

The Agency's internal software-building factory should be called:

# The Forge

This is distinct from the user's separate, general-purpose software factory.

The external/general software factory is being developed independently and is intended to build arbitrary software.

**Forge is specifically the software factory embedded within The Agency.**

## 11.2 Forge mission

Forge should enable The Agency to build, modify, test, review, package, and maintain software.

It should eventually support:

```text
Request
  |
  v
Requirements
  |
  v
Planning
  |
  v
Architecture
  |
  v
Implementation
  |
  v
Tests
  |
  v
Static analysis
  |
  v
Security review
  |
  v
Adversarial QA
  |
  v
Build/package
  |
  v
Review
  |
  v
Release
```

## 11.3 Forge coding agent

The Agency should have a coding agent capable of operating Forge.

The coding agent should not be a giant monolithic prompt.

It should be an agent with:

- coding skills;
- repository inspection skills;
- planning skills;
- test execution;
- debugging;
- dependency analysis;
- security analysis;
- Git operations;
- artifact management;
- code review;
- documentation generation;
- workflow execution.

## 11.4 Forge should leverage the skills repository

Use:

`https://github.com/athena254/Athena-global-skills`

as a source of patterns and candidate capabilities for the Forge coding-agent skill system.

Do not blindly copy it.

Instead:

1. inspect the existing skills;
2. classify them;
3. identify which are relevant to coding/software-factory operation;
4. adapt them to The Agency's architecture;
5. make them Agency-native;
6. give them explicit inputs/outputs;
7. add deterministic implementations where possible;
8. add tests;
9. register them through the Agency skill system.

The result should feel native to The Agency rather than bolted on.

---

# 12. Forge Architecture

A useful conceptual architecture:

```text
                         FORGE
                           |
                +----------+----------+
                |                     |
          Forge Orchestrator     Coding Agent
                |                     |
                +----------+----------+
                           |
                     Skill Registry
                           |
              +------------+------------+
              |            |            |
          Repository     Testing     Security
          Operations     System      Analysis
              |            |            |
              +------------+------------+
                           |
                     Artifact Store
                           |
                    Review / QA Gate
                           |
                        Release
```

Forge should use deterministic workflows heavily.

For example, a software change pipeline can be represented as a workflow rather than trusting an agent to remember every step.

---

# 13. Lattice and Coordination

The existing Lattice concept should remain important, but its responsibility must be explicit.

The Lattice should be a **coordination/governance substrate**, not merely a message bus.

It should help determine:

- which agents are involved;
- what tasks exist;
- ownership;
- dependencies;
- state;
- approvals;
- evidence;
- conflicts;
- escalation;
- completion criteria.

Where possible, these decisions should be deterministic.

The Lattice should not become an opaque LLM conversation channel.

---

# 14. Governance

Governance should define:

- who may act;
- what an agent may do;
- what requires approval;
- what requires multiple agents;
- what must be logged;
- what must be validated;
- when execution must stop;
- when work must escalate.

Potential decision states:

```text
ALLOW
BLOCK
REQUIRE_APPROVAL
RETRY
ESCALATE
QUARANTINE
```

These should remain explicit machine-readable states.

---

# 15. QA Critic / Adversarial Review

The existing QA Critic concept is valuable and should be expanded.

It should be capable of reviewing:

- agent outputs;
- workflow results;
- code;
- artifacts;
- plans;
- security-sensitive operations;
- evidence;
- tests.

It should evaluate multiple dimensions such as:

- correctness;
- completeness;
- consistency;
- security;
- reliability;
- policy compliance;
- reproducibility;
- test coverage;
- provenance;
- requirement satisfaction.

The QA layer should be capable of blocking work.

Do not make it a decorative "second opinion" LLM.

---

# 16. Sandbox

The sandbox should isolate risky or untrusted execution.

Existing concepts include:

- Clean Room;
- Agency Mirror.

Retain the useful ideas.

The sandbox should support:

- filesystem isolation;
- network policy;
- process isolation;
- resource limits;
- reproducibility;
- artifact capture;
- execution logs;
- cleanup.

The Agency should make a clear distinction between:

```text
trusted deterministic operations
trusted agent execution
untrusted external content
untrusted code
high-impact actions
```

---

# 17. Security Model

Security should be foundational rather than an addon.

Every agent, skill, workflow, and tool should have explicit permissions.

Consider:

```text
Agent
  -> Skill
      -> Tool
          -> Resource
```

Permissions should be enforceable at every level.

Do not rely on system prompts alone for security.

Security controls should include:

- capability-based permissions;
- sandboxing;
- secret isolation;
- least privilege;
- audit logs;
- provenance;
- input validation;
- output validation;
- policy enforcement;
- approval gates;
- immutable or append-only event records where appropriate;
- isolation between workspaces;
- isolation between untrusted and trusted content.

---

# 18. Provenance and Auditability

Every meaningful action should be traceable.

A useful event model:

```text
Who/what acted?
What did it do?
Why?
Under which task?
Under which workflow?
Using which skill?
Using which tools?
Using which model?
Against which artifacts?
What inputs were used?
What outputs were produced?
What validation occurred?
What approvals occurred?
What changed?
```

This becomes particularly important for:

- code generation;
- software releases;
- autonomous actions;
- security-sensitive operations;
- agent-generated agents;
- workflow-generated artifacts.

---

# 19. Memory

Memory should be separated by scope.

Suggested hierarchy:

```text
Global Agency Knowledge
    |
Workspace Memory
    |
Project Memory
    |
Thread Memory
    |
Task Memory
    |
Agent Run Memory
```

Do not automatically expose all memory to all agents.

Memory retrieval should be:

- relevant;
- permission-aware;
- provenance-aware;
- scoped;
- auditable.

---

# 20. Model Abstraction

The Agency should not hard-code itself to one model provider.

Create a model abstraction layer supporting:

- provider;
- model;
- capabilities;
- context limits;
- tool support;
- cost metadata;
- latency metadata;
- reliability;
- structured-output support.

Agent definitions should reference capabilities and policies rather than hard-coded assumptions wherever practical.

---

# 21. External Agents

The Agency should be capable of integrating external agents when useful.

However:

**external agents are optional integrations, not architectural dependencies.**

The Agency's own:

- Agent Factory;
- skills;
- workflows;
- governance;
- Butler;
- Forge;

must remain operational without external agent frameworks.

External agents should pass through explicit adapters and trust boundaries.

---

# 22. Competitive Positioning

The intended comparison set is:

- OpenClaw
- Hermes
- Claude Code
- Grok Bot

The Agency should not simply claim superiority.

Instead, document concrete differences.

The central question is:

> Why would someone choose The Agency?

Potential differentiators to validate through implementation:

### Native agent creation
The Agency can create and manage its own agents rather than being only an interface over external agents.

### Skills + workflows as first-class assets
Capabilities can become reusable system assets instead of remaining trapped in individual prompts or sessions.

### Deterministic execution
Workflows can combine deterministic software with agent reasoning.

### Persistent multi-threaded work
Users can maintain multiple independent lines of work within persistent projects/workspaces.

### Built-in governance
Agent work can be inspected, challenged, blocked, approved, and audited.

### Forge
The Agency can operate an internal software-building factory.

### Independence
The Agency does not depend on ATHENA or another orchestration system.

### Security architecture
Capabilities, tools, agents, workflows, and sandboxes can be governed explicitly.

These are design goals and architectural differentiators, not claims that the implementation already provides all of them.

---

# 23. Repository Documentation

The documentation should eventually include or update:

```text
README.md

docs/
  ARCHITECTURE.md
  SPECIFICATION.md
  SPEC_SUMMARY.md
  SPEC_GATEWAY.md
  SPEC_BUDDY.md
  SPEC_SANDBOX.md
  SPEC_ADVERSARIAL.md
  ROADMAP.md
  HEALTH.md

Potential new documents:

  AGENCY_MODEL.md
  AGENT_FACTORY.md
  SKILLS.md
  WORKFLOWS.md
  FORGE.md
  BUTLER.md
  GOVERNANCE.md
  SECURITY.md
  MEMORY.md
  COMPETITIVE_POSITIONING.md
  DEVELOPMENT_MODEL.md
```

Documentation should describe the architecture that actually exists.

Do not create fictional implementation claims.

Clearly distinguish:

- implemented;
- partially implemented;
- planned;
- experimental.

---

# 24. Suggested Git Branch / PR Structure

Do not put all architectural changes directly on `main`.

Suggested branches:

```text
architecture/agency-core
feature/butler-multithreading
feature/agent-factory
feature/skills-workflows
feature/the-forge
feature/forge-skills
docs/competitive-positioning
docs/roadmap
docs/security-governance
docs/agency-system-model
```

Each should target `main` through a PR.

Suggested dependency order:

```text
1. architecture/agency-core
          |
          +----> 2. feature/butler-multithreading
          |
          +----> 3. feature/agent-factory
                       |
                       +----> 4. feature/skills-workflows
                                      |
                                      +----> 5. feature/the-forge
                                                     |
                                                     +----> 6. feature/forge-skills

Documentation PRs can proceed alongside these where they accurately describe the current design.
```

The GitHub connector previously exposed branch creation and repository write capabilities but returned HTTP 403 when an actual branch creation was attempted. Do not claim that branches or PRs exist unless GitHub confirms the mutation succeeded.

---

# 25. Engineering Principles

## Principle 1: Native capability over dependency

Build core Agency capabilities internally.

External systems should be integrations, not foundations.

## Principle 2: Deterministic where possible

Do not use an LLM where ordinary software can perform the operation reliably.

## Principle 3: Agents where judgment is required

Use agents for reasoning, ambiguity, synthesis, planning, and novel problem solving.

## Principle 4: Explicit state

Avoid hiding critical state inside prompts.

Use structured data.

## Principle 5: Everything important should be inspectable

Tasks, agents, workflows, skills, permissions, tool calls, outputs, and transitions should be observable.

## Principle 6: Security at the execution layer

Prompt instructions are not security boundaries.

## Principle 7: Composition over monoliths

Build small reusable primitives that can compose.

## Principle 8: Provenance by default

Know where artifacts and decisions came from.

## Principle 9: Failure must be recoverable

Support retries, checkpoints, escalation, rollback, and quarantine.

## Principle 10: Documentation follows reality

Never document planned functionality as implemented functionality.

---

# 26. Proposed Core Object Model

A possible starting model:

```text
Agency
 ├── Workspace
 │    ├── Project
 │    │    ├── Thread
 │    │    │    ├── Message
 │    │    │    ├── Task
 │    │    │    └── Artifact
 │    │    └── WorkflowRun
 │    └── Memory
 │
 ├── Agent
 │    ├── AgentVersion
 │    ├── Capabilities
 │    ├── Policies
 │    └── MemoryPolicy
 │
 ├── Skill
 │    ├── SkillVersion
 │    ├── Inputs
 │    ├── Outputs
 │    ├── Permissions
 │    └── Tests
 │
 ├── Workflow
 │    ├── WorkflowVersion
 │    ├── Steps
 │    ├── Conditions
 │    ├── Policies
 │    └── Tests
 │
 ├── Tool
 │    ├── Adapter
 │    └── PermissionPolicy
 │
 ├── Forge
 │    ├── Repository
 │    ├── Build
 │    ├── TestRun
 │    ├── Review
 │    └── Release
 │
 ├── Governance
 ├── Security
 ├── Audit
 ├── Memory
 └── Lattice
```

This is a conceptual model, not a mandate to force every object into one database schema.

---

# 27. Agent Lifecycle

Recommended lifecycle:

```text
DRAFT
  |
VALIDATING
  |
EVALUATING
  |
APPROVED
  |
DEPLOYED
  |
ACTIVE
  |
DEPRECATED
  |
RETIRED
```

A failed evaluation should prevent deployment unless an explicit override/approval exists.

---

# 28. Workflow Lifecycle

Recommended lifecycle:

```text
DRAFT
  |
VALIDATING
  |
TESTING
  |
APPROVED
  |
AVAILABLE
  |
DEPRECATED
  |
RETIRED
```

Every workflow version should be immutable once published.

Changes should create new versions.

---

# 29. Skill Lifecycle

Recommended lifecycle:

```text
DRAFT
  |
TESTING
  |
APPROVED
  |
PUBLISHED
  |
DEPRECATED
  |
RETIRED
```

Skills should have compatibility metadata so agents and workflows know whether a new version can safely replace an older one.

---

# 30. Software Factory Workflow

Forge should make software production a repeatable workflow.

Example:

```text
USER REQUIREMENT
       |
       v
REQUIREMENT ANALYSIS
       |
       v
SPECIFICATION
       |
       v
ARCHITECTURE
       |
       v
TASK DECOMPOSITION
       |
       v
IMPLEMENTATION
       |
       v
UNIT TESTS
       |
       v
INTEGRATION TESTS
       |
       v
STATIC ANALYSIS
       |
       v
SECURITY ANALYSIS
       |
       v
ADVERSARIAL REVIEW
       |
       v
DOCUMENTATION
       |
       v
BUILD
       |
       v
RELEASE CANDIDATE
       |
       v
APPROVAL
       |
       v
RELEASE
```

Many of these steps should be deterministic.

---

# 31. Coding Agent Behavior

The Forge coding agent should:

1. inspect before changing;
2. understand repository conventions;
3. create a plan;
4. identify affected components;
5. make minimal coherent changes;
6. run relevant tests;
7. inspect failures;
8. fix failures;
9. run static/security checks;
10. review its own diff;
11. generate/update documentation;
12. provide provenance;
13. commit changes on a feature branch;
14. open a PR;
15. never silently alter unrelated files.

The coding agent should not assume that generated code is correct merely because a model produced it.

---

# 32. Self-Improvement

The Agency may eventually use its own agents to improve Agency components.

This must be controlled.

Potential loop:

```text
Problem detected
     |
     v
Research / diagnosis
     |
     v
Proposed change
     |
     v
Forge implementation
     |
     v
Tests
     |
     v
Adversarial QA
     |
     v
Review
     |
     v
PR
     |
     v
Human/system approval
     |
     v
Merge
```

Do not implement uncontrolled self-modification.

---

# 33. Testing Strategy

Testing should exist at multiple levels.

### Unit tests

For:

- skills;
- workflow nodes;
- state transitions;
- permissions;
- data models.

### Integration tests

For:

- agent/tool interactions;
- workflow execution;
- Butler routing;
- Lattice coordination;
- Forge pipelines.

### System tests

For:

- full user workflows;
- multi-agent tasks;
- recovery;
- concurrency;
- persistence.

### Adversarial tests

For:

- prompt injection;
- malicious artifacts;
- privilege escalation;
- tool misuse;
- data leakage;
- workflow bypass;
- confused-deputy behavior.

### Regression tests

Every significant bug should ideally become a regression test.

---

# 34. Observability

The system should expose:

- active tasks;
- running agents;
- workflow state;
- tool calls;
- latency;
- failures;
- retries;
- approvals;
- blocked actions;
- resource usage;
- model usage;
- artifacts.

The Butler should present useful summaries without hiding the underlying execution trace.

---

# 35. Human-in-the-Loop

Not every action should be autonomous.

The Agency should support configurable approval levels.

Example:

```text
LOW RISK
automatic

MEDIUM RISK
agent + policy validation

HIGH RISK
human approval

CRITICAL
human approval + additional validation
```

The actual policy should be configurable rather than hard-coded around arbitrary assumptions.

---

# 36. Multi-Agent Collaboration

Agents should have explicit roles.

Avoid having five agents all independently doing the same thing simply because "multi-agent" sounds impressive.

Useful roles include:

- planner;
- researcher;
- implementer;
- tester;
- reviewer;
- security analyst;
- critic;
- synthesizer;
- specialist.

The Lattice should define ownership and handoffs.

---

# 37. Conflict Resolution

When agents disagree:

1. preserve both claims;
2. identify evidence;
3. determine whether the disagreement is factual, interpretive, or policy-based;
4. invoke validation;
5. escalate when unresolved;
6. preserve the decision provenance.

Do not silently average incompatible agent opinions.

---

# 38. Artifact Model

Artifacts should be first-class.

Examples:

- files;
- code patches;
- reports;
- datasets;
- plans;
- test results;
- binaries;
- workflow outputs;
- research notes.

Artifacts should have:

- IDs;
- hashes where appropriate;
- provenance;
- creator;
- creation time;
- workspace;
- task;
- permissions;
- version;
- parent artifacts.

---

# 39. Research and External Information

When agents research external information:

- preserve source URLs;
- record retrieval time;
- distinguish source content from agent inference;
- retain citations/provenance;
- avoid presenting unverified claims as facts;
- make source trust configurable.

---

# 40. Failure Handling

Every long-running task should support:

- checkpointing;
- retries;
- timeout;
- cancellation;
- resume;
- escalation;
- quarantine;
- rollback where possible.

Failure states should be explicit.

Example:

```text
RUNNING
WAITING
BLOCKED
FAILED
RETRYING
ESCALATED
CANCELLED
COMPLETED
```

---

# 41. Performance

Do not make every operation an expensive model invocation.

Use:

- deterministic functions;
- caching;
- structured state;
- incremental context;
- event-driven execution;
- asynchronous workflows;
- batching;
- model routing.

Model choice should depend on task requirements.

---

# 42. Cost Control

Track:

- model calls;
- token usage;
- tool calls;
- execution time;
- compute;
- external API usage.

Workflows should be able to enforce budgets.

An agent should not be allowed to burn unlimited resources because it became emotionally attached to solving a typo.

---

# 43. Developer Experience

The Agency should eventually provide clear developer primitives for:

```text
create_agent(...)
create_skill(...)
create_workflow(...)
run_agent(...)
run_skill(...)
run_workflow(...)
register_tool(...)
create_workspace(...)
create_thread(...)
create_task(...)
```

These APIs should remain simple even if the internal system is sophisticated.

---

# 44. CLI / Developer Interface

A useful future CLI might expose:

```bash
agency agent list
agency agent create
agency agent test
agency agent deploy

agency skill list
agency skill create
agency skill test
agency skill publish

agency workflow list
agency workflow validate
agency workflow run
agency workflow publish

agency workspace list
agency thread list

agency forge plan
agency forge build
agency forge test
agency forge review
agency forge release
```

Exact commands are implementation decisions.

---

# 45. Immediate Implementation Priorities

The coding agent should first inspect the existing repository and map the current implementation against this document.

Produce a gap matrix:

| Area | Existing | Partial | Missing | Recommended action |
|---|---|---|---|---|
| Butler | | | | |
| Threads | | | | |
| Workspaces | | | | |
| Agent Factory | | | | |
| Skills | | | | |
| Workflows | | | | |
| Deterministic execution | | | | |
| Lattice | | | | |
| Governance | | | | |
| QA Critic | | | | |
| Sandbox | | | | |
| Security | | | | |
| Memory | | | | |
| Forge | | | | |
| Coding Agent | | | | |
| Provenance | | | | |
| Observability | | | | |
| Testing | | | | |
| Documentation | | | | |

Do not assume a feature exists because the README says it exists.

Inspect the actual implementation.

---

# 46. Recommended First Engineering Sequence

### Phase 1: Architecture reconciliation

- inspect current repository;
- identify duplicated or conflicting architecture;
- define canonical component boundaries;
- update architecture documentation.

### Phase 2: Butler and workspaces

- introduce workspace/project/thread model;
- isolate contexts;
- support persistent threads;
- support thread branching where practical.

### Phase 3: Agent Factory

- formalize agent specification;
- create registry;
- lifecycle management;
- validation/evaluation.

### Phase 4: Skills and workflows

- implement skill registry;
- implement workflow registry;
- create deterministic execution engine;
- add workflow versioning;
- connect agents, skills, and workflows.

### Phase 5: Forge

- define Forge architecture;
- create coding-agent profile;
- build software factory workflows;
- integrate repository operations;
- integrate tests and QA.

### Phase 6: Security and governance

- formalize capability permissions;
- enforce sandboxing;
- improve audit trail;
- implement approval gates.

### Phase 7: Competitive maturity

- benchmark real capabilities;
- identify missing areas;
- improve developer/user experience;
- document concrete differentiation.

---

# 47. What NOT to Do

Do not:

- turn Butler into an all-powerful hidden orchestrator;
- make ATHENA a dependency;
- depend entirely on external agents;
- make every workflow LLM-driven;
- store critical state only in prompts;
- claim planned features are already implemented;
- add multiple agents where one deterministic component is sufficient;
- create an enormous monolithic agent;
- bypass security for convenience;
- silently modify unrelated repositories/files;
- merge generated changes without testing;
- erase provenance;
- make self-improvement uncontrolled;
- build marketing claims before validating implementation.

---

# 48. Definition of a Mature Agency

A mature version of The Agency should be able to do something approximately like this:

```text
User:
    "Build me a SaaS application that does X."

Butler:
    Understands the request and creates a project/thread.

Agency:
    Decomposes requirements.

Planner Agent:
    Produces architecture and implementation plan.

Workflow Engine:
    Executes deterministic planning/validation steps.

Forge:
    Creates implementation tasks.

Coding Agents:
    Implement components.

Skills:
    Provide reusable capabilities.

Workflow:
    Coordinates implementation.

Tests:
    Run deterministically.

Security Agent:
    Reviews the implementation.

QA Critic:
    Challenges the result.

Governance:
    Determines whether release is allowed.

Forge:
    Builds release artifact.

Butler:
    Presents the result, evidence, tests, and outstanding issues.

User:
    Approves release.

Agency:
    Executes release workflow.

Everything:
    Is auditable and traceable.
```

That is the target system.

---

# 49. Final Architectural Principle

The Agency should not be thought of as:

> "an LLM that can call tools."

It should be thought of as:

> **a programmable, governed, persistent multi-agent execution system in which agents, skills, workflows, tools, memory, and software-building capabilities are first-class components.**

The most important architectural distinction is:

```text
LLM = reasoning capability

Agent = governed reasoning actor

Skill = reusable capability

Workflow = reusable procedure

Tool = executable operation

Lattice = coordination/governance substrate

Butler = human interaction/context layer

Forge = software production system

Agency = the system that composes and governs all of them
```

The Agency should use AI where AI provides leverage and ordinary software where ordinary software is more reliable.

That combination is the core engineering philosophy.

---

# 50. Coding Agent Instructions

When implementing this document:

1. **Inspect first.**
   Never assume the repository matches the documentation.

2. **Preserve working functionality.**
   Refactor incrementally.

3. **Prefer small, reviewable changes.**

4. **Use feature branches.**
   Do not directly dump large architectural changes into `main`.

5. **Create tests with new functionality.**

6. **Update documentation alongside implementation.**

7. **Do not fake completeness.**
   Mark incomplete systems explicitly.

8. **Use deterministic implementations whenever practical.**

9. **Keep the Agency independent of ATHENA.**

10. **Treat Agent Factory, Skill Registry, Workflow Registry, Butler, Lattice, Governance, QA, Sandbox, and Forge as explicit architectural boundaries.**

11. **Treat security and provenance as cross-cutting infrastructure.**

12. **When introducing a new autonomous capability, define:**
    - inputs;
    - outputs;
    - permissions;
    - failure modes;
    - tests;
    - provenance;
    - lifecycle;
    - observability.

13. **When introducing an LLM-powered component, document why an LLM is required instead of deterministic code.**

14. **When modifying an existing architecture, update the relevant specifications.**

15. **When a change is substantial, create a PR rather than silently merging it.**

16. **Do not claim GitHub mutations succeeded unless the GitHub API confirms them.**

---

# 51. North Star

The Agency should become a system where a user can say:

> "Build this, research this, analyze this, automate this, or solve this."

and the system can:

```text
understand
   ↓
plan
   ↓
create/use the right agents
   ↓
select skills
   ↓
select or construct workflows
   ↓
execute deterministic work
   ↓
use reasoning where required
   ↓
validate
   ↓
challenge
   ↓
govern
   ↓
produce artifacts
   ↓
remember the work
   ↓
continue it later
```

The result should be a coherent autonomous work environment, not a pile of agents yelling into a shared chatroom.

---

## Status of this document

This is a **target architecture and implementation brief**.

The coding agent must compare it against the actual repository before making claims about implementation status.

The existing repository is the source of truth for what is currently implemented.

This document is the source of truth for the **intended direction and requirements discussed for The Agency**.
