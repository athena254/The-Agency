import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Eye, EyeOff } from 'lucide-react'
import { api, type HealthResponse } from '../api/client'
import { useAuth } from '../stores/auth'
import { Header } from './Memories'

export function SettingsPage() {
  const { ownerToken, setOwnerToken } = useAuth()
  const [draft, setDraft] = useState(ownerToken ?? '')
  const [visible, setVisible] = useState(false)

  const health = useQuery({
    queryKey: ['health'],
    queryFn: () => api.get<HealthResponse>('/api/health'),
    refetchInterval: 10_000,
  })

  return (
    <div className="space-y-4">
      <Header title="Runtime" detail="Configuration and connection state." />

      <section className="mc-panel px-5 py-4">
        <div className="mc-label mb-3">Owner pilot bearer</div>
        <p className="mb-3 text-xs leading-relaxed text-text-3">
          The bearer authorizes the owner-pilot surface. It is held in memory for this session
          only — never written to disk, logs, or the repository.
        </p>
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <input
              className="mc-input pr-10"
              type={visible ? 'text' : 'password'}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="paste owner token"
              autoComplete="off"
            />
            <button
              type="button"
              className="absolute right-2 top-1/2 -translate-y-1/2 text-text-3 hover:text-text-1"
              onClick={() => setVisible((v) => !v)}
              aria-label={visible ? 'Hide token' : 'Show token'}
            >
              {visible ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
          </div>
          <button
            type="button"
            className="mc-btn mc-btn-primary flex-none"
            onClick={() => setOwnerToken(draft.trim() || null)}
          >
            Save
          </button>
          {ownerToken ? (
            <button
              type="button"
              className="mc-btn mc-btn-danger flex-none"
              onClick={() => {
                setOwnerToken(null)
                setDraft('')
              }}
            >
              Clear
            </button>
          ) : null}
        </div>
        <div className="mt-2 flex items-center gap-2 text-xs">
          <span
            className={`mc-status-dot ${ownerToken ? 'bg-green' : 'bg-amber'}`}
          />
          <span className="text-text-3">
            {ownerToken ? 'Token set for this session' : 'No token set'}
          </span>
        </div>
      </section>

      <section className="mc-panel px-5 py-4">
        <div className="mc-label mb-3">Runtime</div>
        <dl className="grid grid-cols-1 gap-x-8 gap-y-2 sm:grid-cols-2">
          <Row label="Status" value={health.data?.status ?? '—'} />
          <Row label="Version" value={health.data?.version ?? '0.1.0'} />
          <Row label="Butler" value={health.data?.butler ?? '—'} />
          <Row label="Agents reported" value={String(health.data?.agents ?? 0)} />
        </dl>
      </section>

      <section className="mc-panel px-5 py-4">
        <div className="mc-label mb-3">Authority model</div>
        <ul className="space-y-2 text-xs text-text-2">
          <li className="flex gap-2">
            <span className="mc-status-dot mt-1.5 bg-amber" />
            Business API routes are closed until peer authority is available.
          </li>
          <li className="flex gap-2">
            <span className="mc-status-dot mt-1.5 bg-green" />
            The prototype surface is loopback-only and model-isolated.
          </li>
          <li className="flex gap-2">
            <span className="mc-status-dot mt-1.5 bg-green" />
            Owner decisions D-01–D-05 are approved and recorded.
          </li>
        </ul>
      </section>
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <dt className="mc-label">{label}</dt>
      <dd className="mc-value text-[11.5px] text-text-1">{value}</dd>
    </div>
  )
}
