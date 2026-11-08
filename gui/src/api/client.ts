/*
  Mission Control API client.

  The Agency serves two surfaces from the same origin:
    /api/*     — prototype/owner-pilot surface (port 8001)
    /v1/*      — main API surface (port 8000); business routes are closed
                 behind peer authorization until P2 lands, so the UI treats
                 them as "locked" and shows honest empty/locked states.
*/

export class ApiError extends Error {
  status: number
  body: string

  constructor(status: number, body: string) {
    super(`API ${status}: ${body.slice(0, 200)}`)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
    ...init,
  })
  const text = await res.text()
  if (!res.ok) throw new ApiError(res.status, text)
  if (!text) return undefined as T
  try {
    return JSON.parse(text) as T
  } catch {
    return text as unknown as T
  }
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
}

/* ------------------------------- types ---------------------------------- */

export interface HealthResponse {
  status: 'ok' | 'degraded' | string
  version?: string
  components?: Record<string, boolean>
  butler?: string
  agents?: number
}

export interface AgentDescriptor {
  id: string
  name: string
  domain: string
  status?: string
}

export interface ChatResponse {
  response: string
  assessment?: {
    execution_status: string
    independently_verified: boolean
    verification: string
    risk_status: string
    [key: string]: unknown
  }
}

export interface PilotCapabilities {
  enabled: boolean
  [key: string]: unknown
}

export interface ThreadRow {
  id: string
  title: string
  [key: string]: unknown
}

export interface TaskView {
  task_id: string
  state: string
  title?: string
  [key: string]: unknown
}

export interface SessionResponse {
  csrf_token: string
}
