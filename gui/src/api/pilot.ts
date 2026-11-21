/*
  Typed owner-pilot surface calls.

  Every call goes through pilotRequest, which carries the in-memory owner
  bearer, the CSRF token for writes, and the loopback origin. Nothing here
  fabricates a response; failures surface as PilotError with the HTTP status.
*/

import { useEffect, useState } from 'react'
import { api, type SessionResponse } from './client'
import { pilotRequest, PilotError } from '../stores/auth'

export interface PilotLimits {
  requests_per_hour: number
  requests_per_day: number
  model_calls_per_hour: number
  model_calls_per_day: number
  model_calls_per_request: number
  tool_calls_per_request: number
}

export interface PilotCapabilitiesView {
  enabled: boolean
  ready: boolean
  mode: string
  tools: string[]
  disabled: string[]
  limits: PilotLimits
  provider_ready: boolean
  uncertainty_blocked: boolean
}

export interface SpendingSnapshot {
  enabled: boolean
  currency: string
  limit_usd: string | null
  reserved_usd: string
  reported_cost_usd: string
  prompt_tokens: number
  completion_tokens: number
  unresolved_calls: number
  pricing_source: string | null
}

export interface ThreadView {
  id: string
  title: string
  parent_thread_id: string | null
  state: string
  created_at: string
  updated_at: string
}

export interface MessageView {
  id: string
  thread_id: string
  role: 'user' | 'assistant' | 'system' | string
  content: string
  created_at: string
}

export interface RequestReceipt {
  request_id: string
  state: string
  replayed: boolean
  resource_id: string | null
}

export interface CreateThreadResponse {
  request: RequestReceipt
  thread: ThreadView
}

export interface ThreadListResponse {
  threads: ThreadView[]
}

export interface MessageListResponse {
  messages: MessageView[]
}

export interface TaskListResponse {
  tasks: OwnerTaskView[]
}

export interface PilotUsage {
  model_attempts: number
  tool_attempts: number
}

export interface AssessmentSummary {
  schema_version: string
  task_id: string
  execution_status: string
  output_sha256: string
  verification: string
  risk_status: string
  independently_verified: boolean
  subtasks: unknown[]
}

export interface OwnerTaskView {
  request_id: string
  task_id: string
  thread_id: string
  state: string
  replayed: boolean
  cancel_requested: boolean
  response: string | null
  assessment: AssessmentSummary | null
  error_code: string | null
  uncertain: boolean
  usage: PilotUsage
  created_at: string
  updated_at: string
}

export interface CalculatorReceipt {
  schema_version: string
  method: string
  task_id: string
  request_id: string
  thread_id: string
  expression: string
  criteria_sha256: string
  output_sha256: string
  status: string
  reason: string
  expected: string
  observed: string | null
}

export interface VerificationView {
  task_id: string
  request_id: string
  thread_id: string
  execution_state: string
  receipt: CalculatorReceipt | null
  reason: string
}

export interface CalculatorTaskInput {
  request_id: string
  thread_id: string
  expression: string
}

/** Fetch the loopback CSRF token once per surface. Never persisted. */
export function useCsrf(): string | null {
  const [csrf, setCsrf] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    api
      .get<SessionResponse>('/api/session')
      .then((session) => {
        if (alive) setCsrf(session.csrf_token)
      })
      .catch(() => {
        if (alive) setCsrf(null)
      })
    return () => {
      alive = false
    }
  }, [])
  return csrf
}

export function isPilotAuthFailure(err: unknown): boolean {
  return err instanceof PilotError && (err.status === 401 || err.status === 403)
}

export function pilotErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

export function fetchCapabilities(csrf: string | null): Promise<PilotCapabilitiesView> {
  return pilotRequest<PilotCapabilitiesView>('/api/pilot/capabilities', { csrf })
}

export function fetchSpending(csrf: string | null): Promise<SpendingSnapshot> {
  return pilotRequest<SpendingSnapshot>('/api/pilot/spending', { csrf })
}

export function fetchThreads(csrf: string | null): Promise<ThreadListResponse> {
  return pilotRequest<ThreadListResponse>('/api/pilot/threads', { csrf })
}

export function createThread(
  csrf: string | null,
  request_id: string,
  title: string,
): Promise<CreateThreadResponse> {
  return pilotRequest<CreateThreadResponse>('/api/pilot/threads', {
    method: 'POST',
    body: { request_id, title },
    csrf,
  })
}

export function fetchMessages(csrf: string | null, threadId: string): Promise<MessageListResponse> {
  return pilotRequest<MessageListResponse>(`/api/pilot/threads/${threadId}/messages`, { csrf })
}

export function fetchTasks(csrf: string | null, limit = 50): Promise<TaskListResponse> {
  return pilotRequest<TaskListResponse>(`/api/pilot/tasks?limit=${limit}`, { csrf })
}

export function cancelTask(
  csrf: string | null,
  taskId: string,
  requestId: string,
): Promise<{ task: OwnerTaskView }> {
  return pilotRequest<{ task: OwnerTaskView }>(`/api/pilot/tasks/${taskId}/cancel`, {
    method: 'POST',
    body: { request_id: requestId },
    csrf,
  })
}

export function fetchVerification(csrf: string | null, taskId: string): Promise<VerificationView> {
  return pilotRequest<VerificationView>(`/api/pilot/tasks/${taskId}/verification`, { csrf })
}

export function fetchTask(csrf: string | null, taskId: string): Promise<OwnerTaskView> {
  return pilotRequest<OwnerTaskView>(`/api/pilot/tasks/${taskId}`, { csrf })
}

export function submitCalculatorTask(
  csrf: string | null,
  input: CalculatorTaskInput,
): Promise<OwnerTaskView> {
  return pilotRequest<OwnerTaskView>('/api/pilot/calculator-tasks', {
    method: 'POST',
    body: input,
    csrf,
  })
}

export interface CodingBinding {
  schema_version: string
  owner_id: string
  workspace_id: string
  request_id: string
  task_id: string
  thread_id: string
  profile_id: string
  profile_sha256: string
  blueprint_id: string
  blueprint_version: string
  runtime_agent_id: string
  base_commit: string
  checker_id: string
  checker_version: string
  checker_sha256: string
  criteria_sha256: string
}

export interface CodingReceipt {
  schema_version: string
  binding: CodingBinding
  plan_sha256: string
  forge_report_id: string
  forge_report_sha256: string
  artifact_sha256: string | null
  patch_sha256: string | null
  objective_status: 'passed' | 'failed' | 'unable_to_verify'
  reason: string
  release_authorized: boolean
  tests_run: boolean
}

export interface CodingReceiptView {
  task_id: string
  request_id: string
  thread_id: string
  execution_state: string
  receipt: CodingReceipt | null
  reason: string
}

export interface CodingStatus {
  available: boolean
  reason: string
  profile_id: string | null
}

export interface CodingTaskInput {
  request_id: string
  thread_id: string
  profile_id: 'literal-assignment-v1'
  task: string
}

export function fetchCodingStatus(csrf: string | null): Promise<CodingStatus> {
  return pilotRequest<CodingStatus>('/api/pilot/coding-status', { csrf })
}

export function submitCodingTask(
  csrf: string | null,
  input: CodingTaskInput,
): Promise<OwnerTaskView> {
  return pilotRequest<OwnerTaskView>('/api/pilot/coding-tasks', {
    method: 'POST',
    body: input,
    csrf,
  })
}

export function fetchCodingReceipt(csrf: string | null, taskId: string): Promise<CodingReceiptView> {
  return pilotRequest<CodingReceiptView>(`/api/pilot/coding-tasks/${taskId}`, { csrf })
}
