/*
  Shared Mission Control primitives.

  One canonical implementation of the small building blocks every page needs,
  styled with the `.mc-*` classes from index.css. Pages must not redefine
  their own tone maps, label/value rows, empty states or status dots — import
  from here so the console stays visually and semantically consistent. Tone
  maps live in components/tones.ts; the toast store in components/toast.ts.
*/

import { useEffect, useRef, type ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, Info, X } from 'lucide-react'
import { BADGE_TONES, DOT_TONES, stateTone, type Tone } from './tones'
import { useToastStore } from './toast'

export type { Tone }

/* ------------------------------------------------------------------ */
/* Badges and dots                                                     */
/* ------------------------------------------------------------------ */

export function Badge({
  children,
  tone = 'muted',
  className,
}: {
  children: ReactNode
  tone?: Tone
  className?: string
}) {
  return (
    <span className={`mc-badge ${BADGE_TONES[tone]}${className ? ` ${className}` : ''}`}>
      {children}
    </span>
  )
}

/**
 * Status dot with an accessible name. Colour alone never carries the
 * meaning — pass the state text via `label` (visually hidden) or render
 * the state as text next to the dot.
 */
export function StatusDot({ tone, label }: { tone: Tone; label: string }) {
  return (
    <span className="inline-flex items-center">
      <span className={`mc-status-dot ${DOT_TONES[tone]}`} aria-hidden="true" />
      <span className="sr-only">{label}</span>
    </span>
  )
}

/** Badge for any known domain state (pilot, task, agent, skill, audit…). */
export function StateBadge({ state }: { state: string }) {
  return <Badge tone={stateTone(state)}>{state}</Badge>
}

/* ------------------------------------------------------------------ */
/* Feedback states                                                     */
/* ------------------------------------------------------------------ */

export function LoadingBlock({ label = 'Loading…' }: { label?: string }) {
  return (
    <div role="status" className="px-5 py-10 text-center text-sm text-text-3">
      {label}
    </div>
  )
}

export function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="px-5 py-10 text-center">
      <div className="text-sm text-text-2">{title}</div>
      <div className="mt-1 text-xs text-text-3">{detail}</div>
    </div>
  )
}

/**
 * Error panel with optional retry. Renders a sanitized message — callers
 * pass already-safe strings (ApiError/PilotError messages never echo raw
 * exception text).
 */
export function ErrorBlock({
  title = 'Unavailable',
  detail,
  onRetry,
}: {
  title?: string
  detail: string
  onRetry?: () => void
}) {
  return (
    <div className="mc-notice mc-notice-warning" role="alert">
      <AlertTriangle size={14} className="mt-0.5 flex-none" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <div className="text-xs font-medium">{title}</div>
        <div className="mt-0.5 text-xs opacity-90">{detail}</div>
      </div>
      {onRetry ? (
        <button type="button" className="mc-btn ml-2 flex-none" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  )
}

export function Notice({
  tone = 'info',
  children,
}: {
  tone?: 'info' | 'warning' | 'danger' | 'success'
  children: ReactNode
}) {
  return (
    <div className={`mc-notice mc-notice-${tone}`} role={tone === 'danger' ? 'alert' : 'status'}>
      {children}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Layout pieces                                                       */
/* ------------------------------------------------------------------ */

/** Label/value row — the single definition previously copy-pasted three times. */
export function Row({
  label,
  children,
  value,
  stacked,
}: {
  label: string
  children?: ReactNode
  value?: ReactNode
  stacked?: boolean
}) {
  const content = children ?? value
  if (stacked) {
    return (
      <div className="flex flex-col gap-1">
        <dt className="mc-label">{label}</dt>
        <dd className="mc-value text-[11.5px] text-text-1">{content}</dd>
      </div>
    )
  }
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="mc-label">{label}</dt>
      <dd className="mc-value text-[11.5px] text-text-1">{content}</dd>
    </div>
  )
}

/** Page header — moved out of Memories.tsx where it was mis-homed. */
export function PageHeader({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="mb-5">
      <h2 className="mc-section-title">{title}</h2>
      <p className="mc-caption mt-1">{detail}</p>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Confirm dialog (destructive actions)                                */
/* ------------------------------------------------------------------ */

export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel = 'Confirm',
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean
  title: string
  body: string
  confirmLabel?: string
  busy?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const confirmRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    confirmRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onCancel])

  if (!open) return null
  return (
    <div className="fixed inset-0 z-[var(--mc-z-overlay)] flex items-center justify-center p-4">
      <div className="mc-overlay" onClick={busy ? undefined : onCancel} aria-hidden="true" />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="mc-confirm-title"
        aria-describedby="mc-confirm-body"
        className="mc-panel-raised relative z-10 w-full max-w-sm p-5 shadow-[var(--mc-shadow-overlay)]"
      >
        <h3 id="mc-confirm-title" className="mc-card-title">
          {title}
        </h3>
        <p id="mc-confirm-body" className="mc-body mt-2">
          {body}
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="mc-btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            ref={confirmRef}
            type="button"
            className="mc-btn mc-btn-danger"
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Toast region                                                        */
/* ------------------------------------------------------------------ */

const TOAST_ICON = {
  success: CheckCircle2,
  danger: AlertTriangle,
  info: Info,
} as const

/** Mount once in the app shell; renders the live toast queue. */
export function ToastRegion() {
  const toasts = useToastStore((state) => state.toasts)
  const dismiss = useToastStore((state) => state.dismiss)
  if (toasts.length === 0) return null
  return (
    <div
      aria-live="polite"
      className="fixed bottom-4 right-4 z-[var(--mc-z-toast)] flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2"
    >
      {toasts.map((toast) => {
        const Icon = TOAST_ICON[toast.tone]
        return (
          <div
            key={toast.id}
            className={`mc-notice ${
              toast.tone === 'success'
                ? 'mc-notice-success'
                : toast.tone === 'danger'
                  ? 'mc-notice-danger'
                  : 'mc-notice-info'
            } shadow-[var(--mc-shadow-overlay)]`}
          >
            <Icon size={14} className="mt-0.5 flex-none" aria-hidden="true" />
            <span className="min-w-0 flex-1 text-xs">{toast.text}</span>
            <button
              type="button"
              className="mc-icon-btn h-6 w-6 flex-none"
              onClick={() => dismiss(toast.id)}
              aria-label="Dismiss notification"
            >
              <X size={12} aria-hidden="true" />
            </button>
          </div>
        )
      })}
    </div>
  )
}
