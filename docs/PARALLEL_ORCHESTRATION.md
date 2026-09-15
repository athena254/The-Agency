# Parallel Orchestration Pattern — Design Document

## Core Concept

When a conversational agent receives a complex request, it:
1. **Decomposes** it into independent sub-queries
2. **Routes** each sub-query to the best specialist agent
3. **Executes ALL in parallel** (asyncio.gather)
4. **Synthesizes** results into a unified, richer response
5. **Cross-references** insights from multiple agents

**Result**: Richer than any single agent could produce alone.

---

## Architecture

```
User: "I want a full finance reviews report"
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│  Personal Agent (Orchestrator)                          │
│  ┌───────────────────────────────────────────────────┐  │
│  │ 1. DECOMPOSE request into sub-queries             │  │
│  │ 2. ROUTE each to best specialist agent            │  │
│  │ 3. EXECUTE all in parallel (asyncio.gather)       │  │
│  │ 4. SYNTHESIZE results into unified report        │  │
│  │ 5. ADD personal context/insights                 │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
         │           │           │           │           │
         ▼           ▼           ▼           ▼           ▼
    ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐
    │Finance  │ │Business │ │Work     │ │Research │ │Tech     │
    │Agent    │ │Agent    │ │Agent    │ │Agent    │ │Agent    │
    │         │ │         │ │         │ │         │ │         │
    │Raw      │ │Revenue  │ │Project  │ │Market   │ │Tool     │
    │numbers  │ │trends   │ │budgets  │ │benchmark│ │costs    │
    │Ledger   │ │Growth   │ │Hiring   │ │Competitor│ │Infra    │
    │Cashflow │ │metrics  │ │plans    │ │analysis │ │spend    │
    └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘
         │           │           │           │           │
         └───────────┴───────────┴───────────┴───────────┘
                             │
                             ▼
              ┌─────────────────────────────┐
              │  SYNTHESIZED UNIFIED REPORT │
              │  • Cross-references         │
              │  • Flags anomalies          │
              │  • Adds personal context    │
              │  • Generates recommendations│
              └─────────────────────────────┘
```

---

## Decomposition Strategy

### Request Analysis

```python
class ConversationalAgent:
    async def analyze_complexity(self, message: str) -> ComplexityScore:
        """Determine if request needs parallel orchestration."""
        
        prompt = f"""
        Analyze this user request for complexity.
        
        Request: {message}
        
        Score 0.0-1.0 on:
        - Multiple domains involved? (finance + business + work = high)
        - Cross-referencing needed? (comparing metrics = high)
        - Time-sensitive? (real-time data = high)
        - Ambiguity requiring disambiguation? (vague terms = high)
        
        Return: {{"score": 0.0-1.0, "reasoning": "..."}}
        """
        
        return await self.llm.generate(prompt, response_format="json")
    
    async def decompose_request(self, message: str) -> List[SubQuery]:
        """Break complex request into independent sub-queries."""
        
        available_agents = self.get_available_agents()  # All registered agents
        
        prompt = f"""
        Break this user request into independent sub-queries for different agents.
        
        User request: {message}
        
        Available agents and their specializations:
        {self.format_agent_capabilities(available_agents)}
        
        Return JSON array:
        [
          {{"agent": "finance", "query": "...", "priority": "required"}},
          {{"agent": "business", "query": "...", "priority": "required"}},
          {{"agent": "work", "query": "...", "priority": "optional"}},
          ...
        ]
        
        Rules:
        - Each sub-query must be answerable independently
        - Target the BEST agent for each piece
        - Include ALL relevant angles
        - Mark "required" for must-have, "optional" for nice-to-have
        - Avoid overlap between sub-queries
        """
        
        return await self.llm.generate(prompt, response_format="json")
```

### Decomposition Tree Example

```
User: "I want a full finance reviews report"

Decomposition tree:
├─ Financial performance (Finance agent) [required]
│  ├─ Revenue analysis (last 90 days)
│  ├─ Expense breakdown by category
│  ├─ Profit margins and trends
│  └─ Cash flow statement
│
├─ Business venture health (Business agent) [required]
│  ├─ Revenue trends (3-month)
│  ├─ Growth metrics (CAC, LTV, churn)
│  └─ Unit economics
│
├─ Upcoming financial commitments (Work agent) [required]
│  ├─ Active project budgets
│  ├─ Hiring pipeline costs
│  ├─ Planned investments
│  └─ Timeline cash impact
│
├─ Market context (Research agent) [optional]
│  ├─ Industry benchmarks
│  ├─ Competitor financial performance
│  └─ Economic trends
│
└─ Operational costs (Technology agent) [optional]
   ├─ Tool subscriptions
   ├─ Infrastructure spend
   └─ Upcoming tech investments
```

---

## Parallel Execution

```python
class ConversationalAgent:
    async def execute_parallel(
        self,
        sub_queries: List[SubQuery],
        timeout: float = 30.0
    ) -> List[SubResult]:
        """Execute ALL sub-queries in parallel."""
        
        tasks = []
        for sub_q in sub_queries:
            agent = sub_q.agent
            query = sub_q.query
            
            # Fire off task to each agent
            task = asyncio.create_task(
                self.bridge.ask(
                    agent=agent,
                    query=query,
                    timeout=timeout
                )
            )
            tasks.append(task)
        
        # Wait for ALL to complete (or timeout)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Separate successes from failures
        processed = []
        for sub_q, result in zip(sub_queries, results):
            if isinstance(result, Exception):
                processed.append(SubResult(
                    agent=sub_q.agent,
                    status="error",
                    error=str(result),
                    data=None
                ))
            else:
                processed.append(SubResult(
                    agent=sub_q.agent,
                    status="success",
                    data=result,
                    error=None
                ))
        
        return processed
    
    async def handle_complex_request(
        self,
        message: str,
        session: Session
    ) -> str:
        """Full parallel orchestration flow."""
        
        # 1. Enrich with context from other agents
        enriched = await self.enrich_with_shared_context(message)
        
        # 2. Decompose into sub-queries
        sub_queries = await self.decompose_request(enriched)
        
        # 3. Execute ALL in parallel
        sub_results = await self.execute_parallel(sub_queries, timeout=30.0)
        
        # 4. Synthesize results
        response = await self.synthesize(message, sub_results, session)
        
        # 5. Save to session
        session.append_message(role="user", content=message)
        session.append_message(role="assistant", content=response)
        
        return response
```

---

## Synthesis Logic

```python
class ConversationalAgent:
    async def synthesize(
        self,
        original_message: str,
        sub_results: List[SubResult],
        session: Session
    ) -> str:
        """Aggregate results from multiple agents into unified response."""
        
        # 1. Collect all data points
        data_points = {}
        for result in sub_results:
            if result.status == "success":
                data_points[result.agent] = result.data
        
        # 2. Cross-reference for anomalies
        anomalies = self.detect_anomalies(data_points)
        
        # 3. Build unified narrative
        synthesis_prompt = f"""
        Synthesize these agent results into a unified response.
        
        User request: {original_message}
        
        Agent results:
        {self.format_sub_results(sub_results)}
        
        Detected anomalies: {anomalies}
        
        Rules:
        - Combine insights into a cohesive narrative
        - Cross-reference data between agents
        - Flag any discrepancies or anomalies
        - Add personal context from session history
        - Generate actionable recommendations
        - Use emojis and formatting for readability
        
        Return: Formatted response string
        """
        
        response = await self.llm.generate(synthesis_prompt)
        
        # 4. If anomalies detected, optionally launch follow-up
        if anomalies and self.config.enable_follow_up:
            follow_up = await self.handle_anomalies(anomalies, sub_results)
            response += f"\n\n---\n🔍 Follow-up: {follow_up}"
        
        return response
    
    def detect_anomalies(self, data_points: dict) -> List[Anomaly]:
        """Cross-reference data to find discrepancies."""
        
        anomalies = []
        
        # Example: Cash flow check
        if "finance" in data_points and "work" in data_points:
            finance = data_points["finance"]
            work = data_points["work"]
            
            # If committed spending > cash on hand
            if work.get("committed_spend", 0) > finance.get("cash_on_hand", 0):
                anomalies.append(Anomaly(
                    type="cash_flow_warning",
                    severity="high",
                    message=f"Committed spend (${work['committed_spend']}) exceeds cash (${finance['cash_on_hand']})",
                    agents_involved=["finance", "work"]
                ))
        
        # Example: Revenue discrepancy
        if "finance" in data_points and "business" in data_points:
            finance_rev = data_points["finance"].get("revenue", 0)
            business_rev = data_points["business"].get("revenue", 0)
            
            if abs(finance_rev - business_rev) > 0.05 * finance_rev:
                anomalies.append(Anomaly(
                    type="revenue_discrepancy",
                    severity="medium",
                    message=f"Finance reports ${finance_rev} but Business reports ${business_rev}",
                    agents_involved=["finance", "business"]
                ))
        
        return anomalies
```

---

## Iterative Deep-Dive

```python
class ConversationalAgent:
    async def handle_anomalies(
        self,
        anomalies: List[Anomaly],
        previous_results: List[SubResult]
    ) -> str:
        """Launch follow-up parallel queries when anomalies detected."""
        
        follow_up_tasks = []
        
        for anomaly in anomalies:
            if anomaly.type == "cash_flow_warning":
                # Launch investigation across all spend-tracking agents
                follow_up_tasks.append(self.bridge.ask("finance", "Show all expenses >$500 last 30 days"))
                follow_up_tasks.append(self.bridge.ask("work", "List all paid invoices last month"))
                follow_up_tasks.append(self.bridge.ask("technology", "Show subscription renewals last month"))
                follow_up_tasks.append(self.bridge.ask("personal", "Any large personal transactions?"))
            
            elif anomaly.type == "revenue_discrepancy":
                follow_up_tasks.append(self.bridge.ask("finance", "Show revenue recognition policy"))
                follow_up_tasks.append(self.bridge.ask("business", "Show revenue calculation method"))
        
        if not follow_up_tasks:
            return ""
        
        # Execute follow-up in parallel
        follow_up_results = await asyncio.gather(*follow_up_tasks, return_exceptions=True)
        
        # Synthesize findings
        return await self.synthesize_anomalies(anomalies, follow_up_results)
    
    async def synthesize_anomalies(
        self,
        anomalies: List[Anomaly],
        follow_up_results: List[SubResult]
    ) -> str:
        """Explain what was found during investigation."""
        
        findings = []
        for result in follow_up_results:
            if result.status == "success":
                findings.append(f"[{result.agent}]: {result.data}")
        
        return f"🔍 Investigation results:\n" + "\n".join(findings)
```

---

## Configuration

### Agent Capability Registry

```yaml
# ~/.athena/agents/index.yaml
agents:
  personal:
    mode: conversational
    class: PersonalAgent
    platforms:
      - telegram:personal_chat
    capabilities:
      - life_management
      - relationship_tracking
      - health_monitoring
      - calendar_management
    orchestrates:
      - finance
      - business
      - work
      - research
      - technology
    context_needs:
      business:
        rooms: [ventures, revenue_goals]
        max_age_days: 7
      work:
        rooms: [active_projects]
        max_age_days: 30
    
  finance:
    mode: stateless
    class: FinanceAgent
    capabilities:
      - financial_analysis
      - ledger_management
      - budget_tracking
      - tax_computation
    provides:
      - revenue_data
      - expense_data
      - cashflow_data
      - profit_margins
    
  business:
    mode: conversational
    class: BusinessAgent
    platforms:
      - slack:direct_messages
    capabilities:
      - venture_management
      - revenue_tracking
      - growth_analysis
      - strategic_planning
    orchestrates:
      - finance
      - work
      - research
    
  work:
    mode: stateless
    class: WorkAgent
    capabilities:
      - project_management
      - task_tracking
      - sprint_planning
      - deliverable_management
    provides:
      - project_status
      - task_completion
      - budget_consumption
      - timeline_tracking
```

### Orchestration Settings

```yaml
# ~/.athena/gateway/orchestration.yaml
orchestration:
  # When to trigger parallel orchestration
  complexity_threshold: 0.7  # Score above this triggers parallel execution
  
  # Execution settings
  max_parallel_agents: 5     # Max agents to query simultaneously
  timeout_seconds: 30        # Timeout for each agent response
  retry_failed: true         # Retry failed agents once
  min_successful: 3          # Min successful responses needed
  
  # Follow-up settings
  enable_follow_up: true
  max_rounds: 2              # Max rounds of follow-up queries
  
  # Anomaly detection
  enable_anomaly_detection: true
  anomaly_severity_threshold: medium
  
  # Context enrichment
  enable_context_enrichment: true
  max_context_snippets: 3
  max_context_chars: 500
```

---

## Flow Diagram

```
User Message
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. ENRICH WITH SHARED CONTEXT                              │
│    - Check context_sharing.yaml for allowed sources         │
│    - Pull relevant snippets from other agents' wings        │
│    - Append as system message prefix                        │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. ANALYZE COMPLEXITY                                      │
│    - LLM scores request 0.0-1.0                             │
│    - Below threshold → handle as simple message             │
│    - Above threshold → decompose                            │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. DECOMPOSE REQUEST                                       │
│    - LLM breaks into independent sub-queries                │
│    - Each sub-query targets best agent                      │
│    - Mark required vs optional                              │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. EXECUTE IN PARALLEL                                      │
│    - asyncio.gather(*tasks, timeout=30s)                    │
│    - Each agent processes independently                     │
│    - Collect successes + failures                           │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. DETECT ANOMALIES                                        │
│    - Cross-reference data between agents                    │
│    - Flag discrepancies (cash gaps, revenue mismatches)     │
│    - Identify missing data                                  │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. SYNTHESIZE RESULTS                                       │
│    - Aggregate all data points                              │
│    - Build unified narrative                                │
│    - Add personal context from session history              │
│    - Generate actionable recommendations                    │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 7. HANDLE ANOMALIES (if any)                                │
│    - Launch follow-up parallel queries                      │
│    - Investigate discrepancies                               │
│    - Append findings to response                            │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ 8. DELIVER RESPONSE                                        │
│    - Format with emojis, sections, links                   │
│    - Save to session history                                │
│    - Return to user                                         │
└─────────────────────────────────────────────────────────────┘
```

---

## Implementation Files

```
athena/core/orchestrator/
├── __init__.py                    # Orchestrator exports
├── base.py                        # ConversationalAgent base class
├── decomposition.py               # Request decomposition logic
├── execution.py                   # Parallel execution engine
├── synthesis.py                   # Result aggregation
├── anomalies.py                   # Anomaly detection
├── follow_up.py                   # Iterative deep-dive
└── config.py                      # Orchestration config models

athena/agents/
├── personal/
│   ├── agent.yaml                 # Agent config (mode, capabilities)
│   └── personal_agent.py          # PersonalAgent class
├── business/
│   ├── agent.yaml
│   └── business_agent.py
├── work/
│   ├── agent.yaml
│   └── work_agent.py
└── ...

~/.athena/gateway/
├── orchestration.yaml             # Global orchestration settings
└── context_sharing.yaml           # Context sharing policies
```

---

## Comparison: Old vs New

| Aspect | Sequential Delegation | Parallel Orchestration |
|--------|----------------------|------------------------|
| **Finance request** | Personal → Finance (single hop) | Personal → {Finance, Business, Work, Research, Tech} (5 parallel) |
| **Result richness** | Just numbers from Finance | Numbers + business context + commitments + market + costs |
| **Insights** | "You spent $30K" | "You're beating industry benchmarks but cash flow will tighten from upcoming projects" |
| **Time** | ~5 seconds (1 agent) | ~6 seconds (5 agents parallel) |
| **Agent roles** | Personal = messenger | Personal = orchestrator + synthesizer |
| **Specialist usage** | Finance only | All specialists contribute |
| **Cross-links** | None | Business growth ↔ Finance revenue, Work spend ↔ Finance cash |
| **Anomaly detection** | None | Automatic cross-reference |
| **Follow-up** | Manual | Automatic when anomalies detected |

---

## Key Design Principles

1. **Parallel by Default**: All independent sub-queries execute simultaneously
2. **Best Agent for Each Piece**: Each sub-query targets the most capable agent
3. **Graceful Degradation**: If optional agents fail, still deliver with available data
4. **Anomaly Detection**: Cross-reference results to find discrepancies
5. **Iterative Deep-Dive**: Follow-up rounds investigate anomalies
6. **Context Enrichment**: Pull only permitted context from other agents
7. **Configurable**: All settings (thresholds, timeouts, policies) in YAML
8. **Extensible**: New agents auto-register capabilities, orchestrator discovers them

---

*Signed-off-by: Hermes Agent <hermes@nousresearch.com>*
*Date: 2026-09-16*
