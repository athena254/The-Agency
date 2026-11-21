/*
  Shared settle-polling for owner-pilot tasks.

  Tasks.tsx and Coding.tsx previously carried copy-pasted setTimeout loops.
  This hook polls GET /api/pilot/tasks/:id until the admission state reaches
  a terminal value, then fetches the task's specific "settle" payload
  (verification receipt, coding receipt) and hands both back.
*/

import { useEffect, useState } from 'react'
import {
  fetchTask,
  isPilotAuthFailure,
  pilotErrorMessage,
  type OwnerTaskView,
} from './pilot'

const TERMINAL = new Set(['COMPLETED', 'FAILED', 'DENIED', 'CANCELLED', 'UNCERTAIN'])

export function useTaskSettle<T>(opts: {
  csrf: string | null
  taskId: string | null
  /** Poll cadence in ms (default 2s). */
  intervalMs?: number
  /** Attempts before declaring a timeout (default 30). */
  maxAttempts?: number
  /** Terminal-state payload fetcher (receipts). */
  settle?: (taskId: string) => Promise<T>
  /** Auth failures bubble here so the page can clear the dead token. */
  onAuthFailure?: (message: string) => void
}) {
  const { csrf, taskId, intervalMs = 2000, maxAttempts = 30, settle, onAuthFailure } = opts
  const [view, setView] = useState<OwnerTaskView | null>(null)
  const [settled, setSettled] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setSettled(null)
    setError(null)
    if (!taskId) {
      setView(null)
      return
    }
    let cancelled = false
    let timer: number | undefined
    let attempts = 0

    const tick = async () => {
      attempts += 1
      try {
        const next = await fetchTask(csrf, taskId)
        if (cancelled) return
        setView(next)
        if (TERMINAL.has(next.state)) {
          if (settle) {
            const result = await settle(next.task_id)
            if (!cancelled) setSettled(result)
          }
          return
        }
      } catch (err) {
        if (cancelled) return
        if (isPilotAuthFailure(err)) onAuthFailure?.(pilotErrorMessage(err))
        setError(pilotErrorMessage(err))
        return
      }
      if (attempts >= maxAttempts) {
        if (!cancelled) setError('Timed out waiting for the task to settle.')
        return
      }
      timer = window.setTimeout(tick, intervalMs)
    }

    timer = window.setTimeout(tick, 1000)
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [csrf, taskId, intervalMs, maxAttempts, settle, onAuthFailure])

  return { view, settled, error }
}
