# The Agency — Project Specification Summary

**Project The Agency** is a decentralized multi-agent AI orchestration system. This document is a quick-reference index to the full specification suite.

---

## 1. Specification Documents

| Document | Purpose | Status |
|----------|---------|--------|
| `docs/ARCHITECTURE.md` | High-level system architecture, component overview, design decisions | ✅ Created |
| `docs/SPECIFICATION.md` | Detailed component specs, APIs, data models, interfaces | ✅ Created |
| `docs/SPEC_SANDBOX.md` | Sandbox addon: full specification (Modes 1 & 2) | Pending |
| `docs/SPEC_ADVERSARIAL.md` | QA Critic addon: full specification | Pending |
| `docs/SPEC_GATEWAY.md` | Gateway/Butler addon: three-mode specification | Pending |
| `docs/SPEC_BUDDY.md` | Buddy UI agent: fork integration spec | Pending |

---

## 2. Component Summary

### 2.1 Gateway (Butler) — `theagency/gateways/`
- **Three modes**: Butler-only, Separate (RECOMMENDED), Merged
- **Responsibilities**: Message routing, governance, health monitoring
- **Does NOT do**: Intelligence, decisions, memory storage
- **Key files**: `launcher.py`, `butler_separate.py`, `butler_only.py`, `butler_merged.py`, `gateway_agent.py`

### 2.2 Buddy — `theagency/gateways/buddy/`
- **What**: UI rendering agent (forked Space-Agent)
- **Two pages**: Mission Control (default) + Buddy Page (floating icon)
- **Rendering**: Charts, tables, cards, forms via component registry
- **Sync**: Git subtree for upstream Space-Agent updates
- **Key files**: `node.py`, `components/registry.py`, `components/renderers.py`, `personality.md`

### 2.3 Sandbox — `addons/sandbox/`
- **Two modes**: Clean Room (Mode 1) + The Agency Mirror/Gemini (Mode 2)
- **Backends**: Docker (primary), Process (fallback), RestrictedPython (optional)
- **Concurrency**: Per-subject asyncio.Semaphore
- **Key files**: `sandbox_api.py`, `sandbox_manager.py`, `backends/docker_backend.py`, `sandbox_service.py`

### 2.4 Adversarial/QA Critic — `addons/adversarial/`
- **What**: Quality enforcement system (NOT red teaming)
- **Dimensions**: 10 quality dimensions (correctness, security, etc.)
- **Scoring**: Severity-weighted (critical=4x, high=3x, medium=2x)
- **Actions**: ALLOW, BLOCK, REQUIRE_APPROVAL, RETRY, ESCALATE, QUARANTINE
- **Key files**: `critic.py`, `enforcer.py`, `registry.py`, `scenarios.py`, `canary.py`

---

## 3. Key Design Patterns

### 3.1 Three-Mode Gateway
```
Butler Only    → Butler → Domain Agents (simple, headless)
Separate       → Butler → Lattice → Buddy + Domain Agents (production)
Merged         → Butler+Buddy → Domain Agents (dev/demo)
```

### 3.2 Two-Mode Sandbox
```
Clean Room     → Empty Python env, test algorithms
The Agency Mirror   → Full codebase clone, test system changes
```

### 3.3 Canary Rollout
```
Phase 1: 1 node       (canary)
Phase 2: 10% of nodes (staging)
Phase 3: 33→66→100%   (gradual)
Phase 4: merge to main (mainstream)
```

### 3.4 QA Critic Enforcement
```
review() → score 0-1 → quality level → enforcement action
```

---

## 4. Cross-Component Integration

```
User → Gateway → Lattice → Domain Agents
                  ↓
                Buddy (UI rendering)
                  ↓
        ┌─────────┴─────────┐
        ↓                   ↓
   Sandbox              QA Critic
   (execution)          (quality review)
        ↓                   ↓
   Metrics ──────────→ Enforcement Decision
   (exit code,         (ALLOW/BLOCK/
    memory, time)       RETRY/ESCALATE)
```

---

## 5. Implementation Priority

### Phase 1: Foundation (Current)
- ✅ Architecture documentation
- ✅ Specification documents
- 🔨 Sandbox addon implementation
- 🔨 QA Critic addon implementation
- 🔨 Gateway system implementation

### Phase 2: Integration
- Connect Sandbox with QA Critic (metrics flow)
- Connect Gateway with Sandbox (agent requests)
- Connect Gateway with QA Critic (every execution reviewed)

### Phase 3: Production
- Standalone HTTP service for Sandbox
- CLI for QA Critic
- Monitoring dashboards
- Canary suite + watch mode

### Phase 4: Advanced Features
- Gap healing (auto-retry failed checks)
- Cross-provider audit (second opinion)
- Scenario-based adversarial testing
- SARIF/HTML reporting

---

## 6. Research Foundations

| Source | Patterns Adopted |
|--------|------------------|
| **Sparfuchs QA** | Multi-layer adapter, preflight gating, coverage babysitting, gap healing, canary suite, cross-provider audit |
| **Cisco Skill Scanner** | Pack-based composition, policy knobs, signature/YARA rules, SARIF reporting |
| **Giskard OSS** | Scenario-based testing, LLM-based checks with Jinja2, hierarchical scoring |

---

## 7. Glossary

| Term | Definition |
|------|------------|
| **Lattice** | Shared graph database (Neo4j + Qdrant) for coordination & memory |
| **Butler** | Gateway agent — dumb message relay, no intelligence |
| **Buddy** | UI rendering agent — forked Space-Agent |
| **Clean Room** | Sandbox Mode 1 — empty Python environment |
| **The Agency Mirror** | Sandbox Mode 2 — full The Agency codebase clone |
| **QA Critic** | Quality enforcement system — reviews every agent execution |
| **Canary** | Lightweight smoke test for continuous monitoring |
| **Gap Healing** | Auto-scheduling re-checks for prior failures |
| **Preflight** | Pre-execution validation (cost/time estimation) |
| **Pack** | Group of related rules (core, security, adversarial) |
| **Scenario** | Multi-step adversarial test case |
| **Severity Weight** | Scoring multiplier (critical=4x, high=3x, etc.) |

---

## 8. Related Repositories

| Repo | Relationship |
|------|-------------|
| [agent0ai/space-agent](https://github.com/agent0ai/space-agent) | Upstream of Buddy fork |
| [Sparfuchs-Corporation/sparfuchs-qa](https://github.com/Sparfuchs-Corporation/sparfuchs-qa) | QA patterns reference |
| [cisco-ai-defense/skill-scanner](https://github.com/cisco-ai-defense/skill-scanner) | Rule engine reference |
| [Giskard-AI/giskard-oss](https://github.com/Giskard-AI/giskard-oss) | Scenario testing reference |
