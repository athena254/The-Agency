import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, Lock } from 'lucide-react'
import { isPilotAuthFailure, pilotErrorMessage } from '../api/pilot'
import { useAuth } from '../stores/auth'

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
      <div className="mc-panel px-5 py-4">
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

export function LoadingBlock({ label = 'Loading…' }: { label?: string }) {
  return <div className="px-5 py-10 text-center text-sm text-text-3">{label}</div>
}

export function PanelRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="mc-label">{label}</dt>
      <dd className="mc-value text-[11.5px] text-text-1">{children}</dd>
    </div>
  )
}

type ChipTone = 'signal' | 'muted' | 'amber' | 'green' | 'red'

export function Chip({ children, tone = 'muted' }: { children: ReactNode; tone?: ChipTone }) {
  const tones: Record<ChipTone, string> = {
    signal: 'border-signal/30 bg-signal-dim text-signal-strong',
    muted: 'border-line bg-ink-1 text-text-3',
    amber: 'border-amber/30 bg-amber/10 text-amber',
    green: 'border-green/30 bg-green/10 text-green',
    red: 'border-red/30 bg-red/10 text-red',
  }
  return (
    <span className={`rounded-control border px-2 py-0.5 font-mono text-[10.5px] ${tones[tone]}`}>
      {children}
    </span>
  )
}

export function StateChip({ state }: { state: string }) {
  return <Chip tone={stateTone(state)}>{state}</Chip>
}

function stateTone(state: string): ChipTone {
  switch (state) {
    case 'COMPLETED':
      return 'green'
    case 'FAILED':
    case 'REJECTED':
    case 'DENIED':
      return 'red'
    case 'RUNNING':
    case 'ADMITTED':
      return 'signal'
    case 'UNCERTAIN':
      return 'amber'
    default:
      return 'muted'
  }
}
