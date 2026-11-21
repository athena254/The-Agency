import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, Lock } from 'lucide-react'
import { isPilotAuthFailure, pilotErrorMessage } from '../api/pilot'
import { useAuth } from '../stores/auth'

/*
  The generic primitives (badges, state chips, rows, loading/empty states)
  live in components/ui.tsx; these aliases keep the historical import names
  stable while everything renders from the single canonical implementation.
*/
export { LoadingBlock } from '../components/ui'
export { Row as PanelRow } from '../components/ui'
export { Badge as Chip } from '../components/ui'
export { StateBadge as StateChip } from '../components/ui'

/**
 * Renders a pilot failure and, for 401/403, clears the in-memory token so the
 * surface falls back to its locked state instead of retrying a dead credential.
 */
export function PilotErrorNote({ error }: { error: unknown }) {
  const setOwnerToken = useAuth((s) => s.setOwnerToken)
  const setAuthNotice = useAuth((s) => s.setAuthNotice)
  const failed = isPilotAuthFailure(error)

  useEffect(() => {
    if (!failed) return
    setAuthNotice(pilotErrorMessage(error))
    setOwnerToken(null)
  }, [failed, error, setAuthNotice, setOwnerToken])

  if (!error) return null
  return (
    <div
      role="alert"
      className={`flex items-start gap-2 rounded-control border px-3 py-2 text-xs ${
        failed
          ? 'border-red/30 bg-red/10 text-red'
          : 'border-amber/30 bg-amber/10 text-amber'
      }`}
    >
      <AlertTriangle size={13} className="mt-0.5 flex-none" />
      <span>{pilotErrorMessage(error)}</span>
    </div>
  )
}

/**
 * Gate for owner-scoped surfaces. Shows the auth failure notice when there is
 * one, otherwise an honest locked state pointing at Settings.
 */
export function PilotGate({ children }: { children: ReactNode }) {
  const token = useAuth((s) => s.ownerToken)
  const notice = useAuth((s) => s.authNotice)

  if (!token) {
    return (
      <div className="mc-panel px-5 py-4" role="status">
        <p className="sr-only">This module is locked; no data is shown.</p>
        {notice ? (
          <div className="mb-3 flex items-start gap-2 rounded-control border border-red/30 bg-red/10 px-3 py-2 text-xs text-red">
            <AlertTriangle size={13} className="mt-0.5 flex-none" />
            <span>{notice}</span>
          </div>
        ) : null}
        <div className="flex items-center gap-3">
          <span className="mc-status-dot bg-amber" />
          <div className="min-w-0">
            <div className="text-sm text-text-2">Owner token required</div>
            <div className="mt-0.5 text-xs text-text-3">
              Pilot data is owner-scoped. Add the bearer token in Settings — it stays in
              memory for this session only.
            </div>
          </div>
          <Link to="/settings" className="mc-btn mc-btn-primary ml-auto flex-none">
            <Lock size={13} /> Settings
          </Link>
        </div>
      </div>
    )
  }

  return <>{children}</>
}
