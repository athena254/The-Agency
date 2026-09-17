# THE AGENCY — SPEC.md

> An independent, autonomous, security-first general agent system for
> understanding, testing, defending, verifying, and improving complex systems.

**Status:** BUILD SPECIFICATION
**Version:** 1.0
**Project:** The Agency
**Relationship to ATHENA:** Independent peer
**Primary role:** Security, adversarial analysis, verification, resilience
**Architecture:** General agent platform with a security-first specialization

---

## 1. Vision

THE AGENCY is an autonomous agent operating system designed to:

1. Understand complex systems.
2. Build a continuously updated model of those systems.
3. Identify weaknesses, inconsistencies, risks, and failure modes.
4. Safely test systems in authorized environments.
5. Red-team software, infrastructure, agents, models, policies, and workflows.
6. Defend and harden systems against discovered weaknesses.
7. Verify whether fixes actually work.
8. Continuously monitor for regressions.
9. Coordinate specialized security agents.
10. Operate independently from ATHENA.
11. Challenge decisions made by ATHENA and other ecosystem systems.
12. Provide evidence rather than simply asserting that something is secure.
13. Escalate consequential decisions to humans.

THE AGENCY is not merely a collection of cybersecurity agents.

It is a general agent system whose specialization is:

> "Assume something may fail. Find out how. Prove it. Fix it. Verify the fix."

---

## 2. Core Design Principle

ATHENA and THE AGENCY must not become the same system.

ATHENA answers:

> "What should the ecosystem do?"

THE AGENCY asks:

> "What could go wrong with what the ecosystem is doing?"

ATHENA is primarily:
- orchestration
- governance
- coordination
- planning
- execution
- resource allocation

THE AGENCY is primarily:
- adversarial analysis
- security
- verification
- risk analysis
- resilience
- testing
- challenge
- containment

Neither system should have absolute authority.

---

## 3. Architectural Relationship

```
                  HUMAN
                  |
         +--------+--------+
         |                 |
       ATHENA          AGENCY
         |                 |
   orchestration       adversarial
   governance          verification
         |                 |
         +--------+--------+
                  |
             ECOSYSTEM
                  |
    +-------------+-------------+
    |             |             |
  NOESIS         DMRX       GHOST FACTORY
    |             |             |
 memory        routing       engineering
```

ATHENA and THE AGENCY are peers.

NOESIS provides persistent memory.
DMRX provides model/resource routing.
Ghost Factory provides software engineering.
GLUE connects external agents.
DANNY and ZOEY remain user-facing/personality systems.

---

## 4. Independence Requirement

THE AGENCY MUST remain independently deployable.

It must NOT require:
- ATHENA
- NOESIS
- DMRX
- Ghost Factory
- DANNY
- ZOEY

to function.

Integrations are optional.

Minimum standalone operation:

```
THE AGENCY
   |
   +-- local memory
   +-- local policy
   +-- agent runtime
   +-- model adapters
   +-- security tools
   +-- sandbox
   +-- evidence store
   +-- risk engine
   +-- reporting
```

If NOESIS is unavailable:

THE AGENCY continues operating using:
- local identity
- local configuration
- local task state
- local evidence
- local findings
- local short/medium-term memory

When NOESIS returns:

```
local state
      |
      v
reconciliation
      |
      v
   NOESIS
```

No work should disappear.

---

## 5. Security Philosophy

THE AGENCY follows:

**Assume breach**

Any system may contain:
- bugs
- unsafe assumptions
- malicious inputs
- compromised dependencies
- model failures
- privilege mistakes
- configuration errors
- prompt injection
- data leakage
- supply-chain problems
- agent collusion
- policy bypasses

**Verify independently**

No security claim is accepted merely because:
- ATHENA says it is safe
- Ghost Factory says tests passed
- an LLM says it is safe
- a developer says it is safe

Claims require evidence.

**Least privilege**

Every agent receives:
- minimum permissions
- minimum filesystem access
- minimum network access
- minimum credentials
- minimum model capabilities
- minimum execution privileges

**Containment first**

Unknown actions occur inside controlled environments whenever possible.

**Evidence over confidence**

The Agency should prefer:
- "Evidence insufficient"

over:
- "Probably safe."

---

## 6. Primary Capabilities

THE AGENCY must eventually support:

### 6.1 System reconnaissance

Understand:
- repositories
- applications
- APIs
- infrastructure
- services
- dependencies
- agents
- models
- databases
- networks
- identities
- permissions
- workflows

Only authorized targets may be assessed.

---

### 6.2 Threat modeling

Automatically construct threat models involving:
- assets
- actors
- trust boundaries
- entry points
- sensitive data
- privileges
- dependencies
- failure modes
- attack surfaces
- security controls

---

### 6.3 Code security analysis

Analyze:
- source code
- dependencies
- configuration
- secrets exposure
- authentication logic
- authorization logic
- input validation
- unsafe data flows
- insecure defaults
- dangerous integrations

Use multiple analysis approaches.

---

### 6.4 Agent security

Test AI agents for:
- prompt injection
- instruction conflicts
- privilege escalation
- tool misuse
- unsafe autonomy
- memory poisoning
- context manipulation
- data exfiltration
- policy bypass
- hallucinated authorization
- cross-agent trust failures
- malicious external agents

---

### 6.5 Model security

Evaluate:
- adversarial robustness
- jailbreak resistance
- prompt injection resistance
- tool-use safety
- output filtering
- sensitive-data leakage
- unsafe autonomous behavior
- model routing failures
- model substitution risks

---

### 6.6 Infrastructure security

Analyze authorized:
- containers
- hosts
- services
- cloud resources
- networks
- identity systems
- storage
- CI/CD
- secrets management
- monitoring

---

### 6.7 Supply-chain security

Track:
- dependencies
- package versions
- repositories
- container images
- model weights
- model adapters
- plugins
- MCP servers
- external agents
- build artifacts

Build a provenance graph.

---

### 6.8 Red teaming

THE AGENCY can create controlled adversarial scenarios against authorized systems.

The red-team process:

```
Understand
   ↓
Threat model
   ↓
Hypothesis
   ↓
Controlled test
   ↓
Evidence
   ↓
Finding
   ↓
Remediation
   ↓
Retest
   ↓
Verification
```

The system should emphasize safe simulation and validation rather than uncontrolled exploitation.

---

## 7. Defensive Counterpart

THE AGENCY should contain a blue-team subsystem.

Red: "How could this fail?"
Blue: "How do we detect and prevent it?"
Purple: "Can the defense actually stop it?"

Therefore:

```
               AGENCY
               |
      +--------+--------+
      |        |        |
     RED     BLUE     PURPLE
      |        |        |
   attack    defend   validate
   model     model     both
```

---

## 8. General Agent Architecture

THE AGENCY is not one giant agent.

It consists of:

```
Agency Kernel
      |
Agent Registry
      |
Planner
      |
Task Graph
      |
Specialist Agents
      |
Tools / Sandboxes
      |
Evidence
      |
Risk Engine
      |
Verification
      |
Human escalation
```

---

## 9. Agency Kernel

The kernel provides deterministic infrastructure.

Responsibilities:
- identity
- permissions
- policies
- task lifecycle
- agent lifecycle
- capability registry
- resource management
- isolation
- audit logging
- evidence management
- approval gates
- risk thresholds
- emergency shutdown
- provenance

LLMs must NOT be the source of truth for kernel security decisions.

---

## 10. Agent Types

Initial agent families:

### 10.1 Scout

Purpose: Understand the target.

Outputs:
- architecture
- components
- dependencies
- interfaces
- assets
- trust boundaries
- unknowns

---

### 10.2 Mapper

Maintains the system world model.

```
System
  |
  +-- services
  +-- APIs
  +-- agents
  +-- models
  +-- data
  +-- identities
  +-- dependencies
  +-- controls
```

---

### 10.3 Threat Modeler

Converts the world model into:
- threats
- attack surfaces
- trust boundaries
- likely failure modes
- security assumptions

---

### 10.4 Code Auditor

Analyzes source code and configuration.

---

### 10.5 Dependency Auditor

Analyzes:
- dependencies
- versions
- provenance
- known vulnerabilities
- transitive relationships
- abandoned components

---

### 10.6 Agent Auditor

Tests agent architectures.

---

### 10.7 Model Auditor

Evaluates AI model behavior and safeguards.

---

### 10.8 Infrastructure Auditor

Reviews infrastructure configuration and exposure.

---

### 10.9 Privacy Auditor

Looks for:
- unnecessary data collection
- leakage
- excessive retention
- permission violations
- cross-user contamination
- sensitive-data exposure

---

### 10.10 Red Team Planner

Creates controlled test plans.

It must NOT directly execute unrestricted actions.

---

### 10.11 Red Team Executor

Runs authorized tests inside approved boundaries.

Capabilities are explicitly scoped.

---

### 10.12 Blue Team

Analyzes:
- detection
- logging
- prevention
- isolation
- recovery
- response

---

### 10.13 Purple Team

Combines red and blue validation.

---

### 10.14 Incident Analyst

Investigates:
- alerts
- logs
- suspicious behavior
- anomalous agent actions
- compromised components

---

### 10.15 Forensics Agent

Creates evidence timelines.

---

### 10.16 Risk Analyst

Transforms technical findings into:
- likelihood
- impact
- affected assets
- confidence
- exploitability
- exposure
- remediation urgency

Avoid pretending risk scores are objective truth.

---

### 10.17 Remediation Agent

Proposes fixes.

It does not automatically deploy high-impact fixes without authorization.

---

### 10.18 Verification Agent

Tests whether remediation actually solved the problem.

---

### 10.19 Watcher

Continuously monitors:
- changes
- dependencies
- configurations
- models
- agents
- policies
- new vulnerabilities
- regressions

---

## 11. Security World Model

The Agency needs a persistent internal model.

```
WORLD
  |
  +-- PEOPLE
  +-- IDENTITIES
  +-- AGENTS
  +-- SERVICES
  +-- CODE
  +-- MODELS
  +-- DATA
  +-- NETWORKS
  +-- DEVICES
  +-- DEPENDENCIES
  +-- POLICIES
  +-- TRUST BOUNDARIES
  +-- FINDINGS
  +-- CONTROLS
  +-- INCIDENTS
  +-- EVIDENCE
```

Every relationship should have provenance.

```
Agent A
   |
   | can-call
   v
DMRX
   |
   | routes-to
   v
Model X
```

---

## 12. Evidence System

Every finding must contain:

```
Finding
  |
  +-- target
  +-- timestamp
  +-- evidence
  +-- methodology
  +-- confidence
  +-- affected component
  +-- reproduction status
  +-- severity
  +-- remediation
  +-- verification status
  +-- provenance
```

No finding should exist only as free-form LLM text.

---

## 13. Evidence Levels

| Level | Description |
|-------|-------------|
| **LEVEL 0** | Hypothesis only. |
| **LEVEL 1** | Static evidence. |
| **LEVEL 2** | Controlled behavioral evidence. |
| **LEVEL 3** | Repeated controlled evidence. |
| **LEVEL 4** | Independently reproduced. |
| **LEVEL 5** | Verified remediation and regression test. |

The system should clearly distinguish these.

---

## 14. Risk Engine

Risk should be multidimensional.

Do not reduce everything to one number.

Represent:
- impact
- likelihood
- confidence
- exposure
- affected assets
- exploitability
- detectability
- reversibility
- blast radius

Then derive an overall decision category if required.

---

## 15. Safety Boundary

THE AGENCY MUST understand:
- authorized
- unauthorized
- uncertain

Targets.

If authorization is uncertain:
**STOP** and request clarification/approval.

---

## 16. Action Classes

Actions have capability levels.

| Level | Class | Examples |
|-------|-------|----------|
| **L0** | Observation | reading approved source, analyzing logs, inspecting documentation |
| **L1** | Safe analysis | static analysis, dependency analysis, configuration analysis, sandboxed model evaluation |
| **L2** | Controlled testing | isolated application testing, synthetic inputs, simulated attack scenarios, controlled robustness tests |
| **L3** | High-impact testing | Requires explicit authorization and stronger controls |
| **L4** | Production-impacting | Requires human approval |
| **L5** | Destructive | Never autonomous |

---

## 17. Permission Model

Every agent receives:
- identity
- capabilities
- target scope
- environment
- time limit
- resource limit
- network policy
- filesystem policy
- data policy

Example:
```
red-agent-42

target:
    staging/api

capabilities:
    inspect
    simulate
    test

network:
    staging-only

filesystem:
    workspace-only

duration:
    30 minutes
```

---

## 18. Sandbox Architecture

All risky execution should use isolated environments.

```
Agency
   |
Sandbox Manager
   |
Container / VM
   |
Target
   |
Synthetic data
```

Isolation may include:
- containers
- microVMs
- network namespaces
- ephemeral environments
- disposable credentials
- filesystem snapshots
- resource limits

---

## 19. Agent Communication

Agents communicate through structured messages.

```json
{
  "task_id": "task-123",
  "from": "threat-modeler",
  "to": "red-planner",
  "type": "hypothesis",
  "content": {},
  "evidence": [],
  "confidence": 0.74
}
```

Avoid uncontrolled free-form agent-to-agent communication.

---

## 20. Agent Debate

Multiple agents may independently analyze a finding.

```
Auditor A
     |
Auditor B
     |
Auditor C
     |
     v
Evidence Synthesizer
     |
     v
Finding
```

The system should actively seek disagreement.

---

## 21. Adversarial Independence

When THE AGENCY evaluates ATHENA:

ATHENA must not control:
- the evaluator
- evidence storage
- verdict generation
- verification

Likewise, THE AGENCY must not control ATHENA's governance.

This prevents:
```
system
   ↓
evaluating itself
   ↓
approving itself
```

---

## 22. Agency vs ATHENA

**ATHENA:**
- Plan
- Coordinate
- Govern
- Allocate
- Execute

**AGENCY:**
- Challenge
- Test
- Verify
- Monitor
- Harden

Interaction:

```
ATHENA
   |
   | proposed action
   v
AGENCY
   |
   +-- approve
   +-- approve with conditions
   +-- request changes
   +-- escalate
   |
   v
ATHENA
```

THE AGENCY does not become ATHENA's subordinate security plugin.

---

## 23. DMRX Integration

DMRX is the execution/resource routing layer.

THE AGENCY asks: "What capability do I need?"

DMRX determines:
- model
- GPU
- CPU
- accelerator
- local/remote backend
- latency
- cost
- memory
- concurrency
- modality

```
Security task
     |
     v
Agency Planner
     |
     v
Capability requirement
     |
     v
   DMRX
     |
+----+----+
|         |
K3       small model
|         |
complex    fast reasoning
classification
```

---

## 24. Model Pool

The Agency should NOT depend on one model.

**Model classes:**

| Class | Examples |
|-------|----------|
| **Large reasoning** | Kimi K3, DeepSeek, frontier models |
| **Fast reasoning** | GLM Flash, small DeepSeek, small Mistral, small Qwen |
| **Security specialists** | Security fine-tunes, code models, classification models |
| **Non-LLM** | Static analyzers, symbolic tools, scanners, graph algorithms, anomaly detection, embeddings, CV, OCR, network telemetry |

LLMs should orchestrate reasoning where useful.
They should not replace deterministic security tooling.

---

## 25. Multi-Model Verification

Important findings should use multiple independent mechanisms.

```
Finding
   |
   +-- static analyzer
   |
   +-- reasoning model
   |
   +-- behavioral test
   |
   +-- second reasoning model
   |
   v
Evidence synthesis
```

This reduces dependence on a single model's judgment.

---

## 26. Continuous Security

THE AGENCY should run continuously.

```
WATCHER
   |
   +-- Git change
   +-- dependency change
   +-- model update
   +-- infrastructure change
   +-- agent update
   +-- policy update
   |
   v
Risk evaluation
   |
   v
targeted security tests
   |
   v
verification
```

---

## 27. Ghost Factory Integration

Ghost Factory builds.
THE AGENCY evaluates what Ghost Factory builds.

Pipeline:

```
Ghost Factory
     |
   build
     |
     v
  Agency
     |
 security test
     |
     v
pass/fail/findings
     |
     v
Ghost Factory
     |
  remediation
     |
     v
  Agency
     |
 verification
```

This creates: **Build → Attack/Test → Fix → Retest → Verify**

---

## 28. Autonomous Software Security Loop

For authorized repositories:

```
SCOUT
  ↓
UNDERSTAND
  ↓
THREAT MODEL
  ↓
IDENTIFY RISKS
  ↓
DESIGN TESTS
  ↓
RUN CONTROLLED TESTS
  ↓
COLLECT EVIDENCE
  ↓
CREATE FINDINGS
  ↓
PROPOSE FIXES
  ↓
GHOST FACTORY IMPLEMENTS
  ↓
AGENCY RETESTS
  ↓
REGRESSION TEST
  ↓
SECURITY SIGN-OFF
```

---

## 29. ATHENA Security Loop

ATHENA proposes: "Deploy X"

AGENCY asks:
- What can fail?
- What assumptions exist?
- What permissions are required?
- What happens if the model is wrong?
- What happens if the tool is compromised?
- What happens if memory is poisoned?
- What happens if an external agent lies?

Then it produces: **SECURITY REVIEW**

ATHENA can then decide how to proceed.

---

## 30. NOESIS Integration

NOESIS is optional but highly valuable.

Store:
- historical findings
- previous incidents
- architecture changes
- security decisions
- recurring weaknesses
- false positives
- remediation history
- agent behavior
- security baselines

But THE AGENCY must maintain a local security-critical cache.

NOESIS outage: Agency continues.
NOESIS recovery: Agency reconciles state.

---

## 31. Memory Architecture

Separate:
- **Operational memory** — Current task.
- **Security memory** — Known risks and controls.
- **Evidence archive** — Immutable historical evidence.
- **World model** — Current system topology.
- **Lessons** — Generalized knowledge.
- **Incident memory** — Historical incidents.

Never allow an ordinary LLM response to overwrite immutable evidence.

---

## 32. Immutable Audit Log

Every consequential action should produce:
- timestamp
- agent
- task
- target
- authorization
- capability
- action
- result
- evidence
- model
- model version
- tool version
- environment

Logs should be append-only.

---

## 33. Human Control

Human approval is required for consequential actions:
- production changes
- destructive actions
- credential changes
- broad network actions
- data deletion
- high-impact remediation

The Agency should be autonomous in analysis but bounded in authority.

---

## 34. Emergency Kill System

Global controls:
- STOP ALL
- STOP TARGET
- STOP AGENT
- STOP TASK
- REVOKE CAPABILITY
- ISOLATE ENVIRONMENT

Kill signals must not depend exclusively on an LLM.

---

## 35. Anti-Collusion Architecture

Agents must not be able to collectively manufacture evidence.

Important findings should have:
- independent evidence
- independent evaluators
- provenance
- deterministic checks
- audit trails

```
Agent A claims vulnerability
    ↓
Agent B independently evaluates
    ↓
Tool-based verification
    ↓
Evidence store
    ↓
Finding
```

---

## 36. Prompt Injection Defense

Treat external content as untrusted.

Potentially untrusted:
- repositories
- README files
- webpages
- documents
- issue comments
- model outputs
- MCP responses
- external agents
- emails
- generated code

Content must never automatically become authority.

Separate:
- DATA
- from: INSTRUCTIONS
- from: POLICY
- from: AUTHORIZATION

---

## 37. External Agent Security

GLUE can connect external agents.

THE AGENCY must assume external agents are potentially untrusted.

Each external agent receives:
- identity
- trust level
- capabilities
- rate limit
- data scope
- sandbox
- audit trail

No external agent receives ecosystem-wide authority merely because it is connected.

---

## 38. Trust Levels

```
UNKNOWN
   ↓
OBSERVED
   ↓
VERIFIED
   ↓
TRUSTED
   ↓
HIGH TRUST
```

Trust should be earned through evidence.

---

## 39. Agent Reputation

Track:
- historical accuracy
- false positives
- false negatives
- policy violations
- reliability
- reproducibility
- security incidents

Do not let reputation become unrestricted authority.

---

## 40. Security Control Graph

```
Threat
  |
  v
Attack surface
  |
  v
Control
  |
  v
Verification
  |
  v
Residual risk
```

---

## 41. Supply-Chain Security (Expanded)

Build a dependency graph showing:
- who built it
- when
- from what source
- with what dependencies
- signed by whom
- verified by whom

Track:
- package registries
- container registries
- model registries
- plugin registries

---

## 42. LLM Output Verification

Never trust LLM output as fact without:
- cross-reference
- tool verification
- provenance
- confidence scoring

---

## 43. Continuous Verification

Security is not a one-time check.

The Agency should:
- re-test after changes
- monitor for new vulnerabilities
- verify that fixes hold
- detect regressions

---

## 44. Human Escalation Paths

When the Agency should escalate:
- uncertain authorization
- high-impact findings
- consequential actions
- policy conflicts
- novel situations
- system failures

Escalation should include:
- context
- evidence
- options
- recommendation

---

*End of SPEC.md — THE AGENCY*
*Version 1.0 — Peer to ATHENA*
