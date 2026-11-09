/*
  Typed read-only observability surface calls.

  Every call is a GET through pilotRequest, which carries the in-memory owner
  bearer. There is deliberately no write helper here: the surface has no write
  route, and the console must not present write controls. Failures surface as
  PilotError with the HTTP status.
*/

import { pilotRequest } from '../stores/auth'

export interface ObserveMode {
  mode: 'read_only'
  peer_authority: 'unavailable'
  write_controls: false
  notice: string
}

export interface RuntimeStatusView extends ObserveMode {
  status: string
  butler_started: boolean
  pilot_started: boolean
  pilot_configured: boolean
}

export interface AgentCapabilityView {
  name: string
  max_level: string
}

export interface AgentView {
  id: string
  name: string
  domain: string
  trust_level: string
  revoked: boolean
  capabilities: AgentCapabilityView[]
  runtime_status: string | null
  current_task_id: string | null
  consecutive_failures: number
  last_heartbeat: string | null
  available: boolean
}

export interface AgentsView extends ObserveMode {
  agents: AgentView[]
  total: number
  by_domain: Record<string, number>
  by_trust_level: Record<string, number>
  revoked: number
  available: boolean
  reason: string | null
}

export interface TaskView {
  task_id: string
  title: string
  status: string
  created_by: string
  parent_id: string | null
  priority: number
  message_count: number
  error: string | null
  created_at: string | null
  updated_at: string | null
}

export interface TasksView extends ObserveMode {
  tasks: TaskView[]
  total: number
  by_status: Record<string, number>
  available: boolean
  reason: string | null
}

export interface MemoryItemView {
  id: string
  agent_id: string
  tier: string
  importance: number
  tags: string[]
  content: string
  created_at: string | null
  accessed_at: string | null
}

export interface MemoryView extends ObserveMode {
  items: MemoryItemView[]
  total: number
  limit: number
  by_tier: Record<string, number>
  by_agent: Record<string, number>
  db_path: string | null
  available: boolean
  reason: string | null
}

export interface FindingView {
  id: string
  target: string
  evidence: string
  methodology: string
  confidence: number
  affected_component: string | null
  reproduction_status: string | null
  severity: string | null
  remediation: string | null
  verification_status: string
  timestamp: string | null
  provenance_agent: string | null
  provenance_source: string | null
  evidence_count: number
  strongest_level: string | null
  risk_category: string
  risk_impact: number
  risk_likelihood: number
  risk_confidence: number
  risk_exposure: number
  risk_blast_radius: number
  risk_exploitability: number
  risk_detectability: number
  risk_reversibility: number
  risk_affected_assets: string[]
}

export interface RiskDimensionView {
  label: string
  value: number
}

export interface RiskView extends ObserveMode {
  findings: FindingView[]
  total: number
  limit: number
  by_verification_status: Record<string, number>
  by_severity: Record<string, number>
  by_risk_category: Record<string, number>
  by_strongest_level: Record<string, number>
  assessed: number
  unassessed: number
  max_score: number | null
  dimensions: RiskDimensionView[]
  method: string
  heuristic_only: boolean
  available: boolean
  reason: string | null
}

export interface AuditEntryView {
  entry_id: string
  timestamp: string | null
  agent: string | null
  task: string | null
  target: string | null
  authorization: string | null
  capability: string | null
  action: string | null
  result: string
}

export interface AuditView extends ObserveMode {
  entries: AuditEntryView[]
  total: number
  limit: number
  by_result: Record<string, number>
  by_action: Record<string, number>
  available: boolean
  reason: string | null
}

export interface ThreadView {
  id: string
  title: string
  parent_thread_id: string | null
  state: string
  created_at: string | null
  updated_at: string | null
  message_count: number | null
}

export interface OwnerTaskView {
  task_id: string
  request_id: string
  thread_id: string
  state: string
  uncertain: boolean
  replayed: boolean
  error_code: string | null
  model_attempts: number
  tool_attempts: number
  created_at: string | null
  updated_at: string | null
}

export interface OwnerView extends ObserveMode {
  available: boolean
  reason: string | null
  tasks: OwnerTaskView[]
  total: number
  by_state: Record<string, number>
  uncertain: number
  threads: ThreadView[]
  thread_total: number
  owner_id: string
  subject_id: string
}

export interface SkillView {
  skill_id: string
  version: string
  name: string
  description: string
  status: string
  permissions: string[]
  provenance: string
  event_count: number | null
}

export interface SkillsView extends ObserveMode {
  skills: SkillView[]
  total: number
  available: boolean
  reason: string | null
  truncated: boolean
}

export interface ToolView {
  name: string
  description: string
  risk: string
}

export interface ToolsView extends ObserveMode {
  tools: ToolView[]
  total: number
  available: boolean
  reason: string | null
}

export interface OverviewView extends ObserveMode {
  runtime: RuntimeStatusView
  agents: AgentsView
  tasks: TasksView
  memories: MemoryView
  evidence: RiskView
  risk: RiskView
  audit: AuditView
  owner: OwnerView
  collected_at: string
}

/** One-call local snapshot. Each section degrades independently. */
export function fetchObserveOverview(
  csrf: string | null,
  limit = 50,
): Promise<OverviewView> {
  return pilotRequest<OverviewView>(`/api/pilot/observe/overview?limit=${limit}`, { csrf })
}

export function fetchObserveSkills(csrf: string | null): Promise<SkillsView> {
  return pilotRequest<SkillsView>('/api/pilot/observe/skills', { csrf })
}

export function fetchObserveTools(csrf: string | null): Promise<ToolsView> {
  return pilotRequest<ToolsView>('/api/pilot/observe/tools', { csrf })
}
