import { create } from 'zustand'

interface AuthState {
  /** Owner bearer token for the /api/pilot surface. Kept in memory only. */
  ownerToken: string | null
  setOwnerToken: (token: string | null) => void
}

export const useAuth = create<AuthState>((set) => ({
  ownerToken: null,
  setOwnerToken: (token) => set({ ownerToken: token }),
}))

/**
  Pilot requests carry the owner bearer plus the CSRF token from /api/session.
  The token is held in memory and never persisted to disk or logs.
*/
export async function pilotRequest<T>(
  path: string,
  options: { method?: string; body?: unknown; csrf?: string | null } = {},
): Promise<T> {
  const { ownerToken } = useAuth.getState()
  if (!ownerToken) throw new Error('No owner token configured. Add it in Settings.')
  const headers: Record<string, string> = {
    Accept: 'application/json',
    Authorization: `Bearer ${ownerToken}`,
  }
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  if (options.method && options.method !== 'GET') {
    if (!options.csrf) throw new Error('CSRF token missing.')
    headers['X-Prototype-CSRF'] = options.csrf
  }
  const res = await fetch(path, {
    method: options.method ?? 'GET',
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  })
  const text = await res.text()
  if (!res.ok) throw new Error(`${res.status}: ${text.slice(0, 200)}`)
  return (text ? JSON.parse(text) : undefined) as T
}
