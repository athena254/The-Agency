import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  ArrowUpRight,
  Bot,
  Boxes,
  Database,
  RefreshCw,
  ShieldCheck,
  type LucideIcon,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { api, type AgentDescriptor, type HealthResponse } from '../api/client'
import { fetchTasks, pilotErrorMessage, useCsrf } from '../api/pilot'
import { fetchObserveOverview, type AuditEntryView } from '../api/observe'
import { EmptyState, StateBadge, StatusDot } from '../components/ui'
import { formatAge, formatDateTime } from '../lib/format'
import { useAuth } from '../stores/auth'
import { CapabilitiesPanel, SpendingPanel } from './PilotOverview'
import { PilotGate } from './PilotShared'

export function DashboardPage() {
  const token = useAuth((state) => state.ownerToken)
  const csrf = useCsrf()
  const health = useQuery({
    queryKey: ['health'],
    queryFn: () => api.get<HealthResponse>('/api/health'),
    refetchInterval: 10_000,
  })
  const agents = useQuery({
    queryKey: ['agents'],
    queryFn: () => api.get<{ agents: AgentDescriptor[] }>('/api/agents'),
    refetchInterval: 15_000,
  })
  const tasks = useQuery({
    queryKey: ['dashboard-task-count', token],
    queryFn: () => fetchTasks(csrf, 50),
    enabled: Boolean(token),
    refetchInterval: 15_000,
  })
  const observe = useQuery({
    queryKey: ['observe-overview', token],
    queryFn: () => fetchObserveOverview(csrf, 8),
    enabled: Boolean(token && csrf),
    refetchInterval: 15_000,
  })

  const agentList = agents.data?.agents ?? []
  const components = health.data?.components
  const hasComponents = Boolean(components && Object.keys(components).length > 0)
  const overview = observe.data

  const status = health.isPending
    ? 'checking'
    : health.isError
      ? 'unreachable'
      : health.data?.status === 'ok'
        ? 'ok'
        : 'degraded'
  const statusLabel = {
    checking: 'Checking runtime',
    unreachable: 'Runtime unreachable',
    ok: 'Runtime healthy',
    degraded: 'Runtime degraded',
  }[status]

  const agentValue = agents.isPending || agents.isError ? '—' : String(agentList.length)
  const taskValue =
    !token || tasks.isPending || tasks.isError
      ? '—'
      : String(tasks.data?.tasks.length ?? 0)

  /** Real count from the observe surface, or an honest fallback. */
  const observeCount = (
    section: { total: number; available: boolean } | undefined,
  ): { value: string; note?: string } => {
    if (!token) return { value: '—', note: 'Owner token required' }
    if (observe.isPending) return { value: '…' }
    if (observe.isError || !section) return { value: '—', note: 'Pilot surface unavailable' }
    return section.available
      ? { value: String(section.total) }
      : { value: '—', note: 'Section unavailable' }
  }

  const memory = observeCount(overview?.memories)
  const evidence = observeCount(overview?.evidence)

  const auditEntries = overview?.audit.available ? (overview.audit.entries ?? []) : []
  const refresh = () => {
    void health.refetch()
    void agents.refetch()
    if (token) {
      void tasks.refetch()
      void observe.refetch()
    }
  }

  return (
    <div className="space-y-6">
      <section
        className="mc-panel flex flex-wrap items-center gap-x-6 gap-y-3 px-5 py-4"
        aria-label="Runtime status"
      >
        <div className="flex items-center gap-3">
          <span
            className={`mc-status-dot ${status === 'ok' ? 'bg-green' : status === 'checking' ? 'animate-pulse bg-text-3' : 'bg-amber'}`}
            aria-hidden="true"
          />
          <div>
            <div className="text-sm font-medium text-text-1" aria-live="polite">
              {statusLabel}
            </div>
            <div className="mc-label">
              {health.data?.butler ? `Butler ${health.data.butler}` : 'prototype surface'}
            </div>
          </div>
        </div>
        <Divider />
        <Metric
          label="Agents reported"
          value={health.isPending ? '—' : String(health.data?.agents ?? '—')}
        />
        <Divider />
        <Metric label="Owner pilot" value={token ? 'Connected' : 'Not connected'} />
        <Divider />
        <Metric label="Version" value={health.data?.version ?? 'not reported'} />
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            className="mc-icon-btn"
            onClick={refresh}
            aria-label="Refresh system status"
            title="Refresh system status"
          >
            <RefreshCw
              size={14}
              className={health.isFetching || agents.isFetching ? 'animate-spin' : ''}
              aria-hidden="true"
            />
          </button>
          <Link to="/ide" className="mc-btn mc-btn-primary">
            Open IDE <ArrowUpRight size={13} aria-hidden="true" />
          </Link>
        </div>
      </section>

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="mc-panel p-5 lg:col-span-2">
          <div className="mb-4 flex items-center justify-between gap-3">
            <h2 className="mc-section-title">System overview</h2>
            <span className="mc-label">live snapshot</span>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard icon={Bot} label="Agents" value={agentValue} tone="signal" />
            <StatCard
              icon={Boxes}
              label="Tasks"
              value={taskValue}
              tone="purple"
              note={!token ? 'Owner token required' : undefined}
            />
            <StatCard
              icon={Database}
              label="Memories"
              value={memory.value}
              tone="amber"
              note={memory.note}
            />
            <StatCard
              icon={ShieldCheck}
              label="Evidence"
              value={evidence.value}
              tone="green"
              note={evidence.note}
            />
          </div>

          <div className="mt-5 border-t border-line pt-4">
            <div className="mc-label mb-3">Component readiness</div>
            {hasComponents ? (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                {Object.entries(components ?? {}).map(([name, ok]) => (
                  <div
                    key={name}
                    className="flex items-center justify-between rounded-control border border-line bg-ink-1 px-3 py-2"
                  >
                    <span className="text-xs text-text-2">{name.replace(/_/g, ' ')}</span>
                    <span className={`mc-value text-[11px] ${ok ? 'text-green' : 'text-red'}`}>
                      {ok ? 'up' : 'down'}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="mc-caption max-w-2xl">
                The runtime does not publish component-level readiness. Use the status above
                and the Observability page for the local snapshot.
              </p>
            )}
          </div>
        </div>

        <div className="mc-panel p-5">
          <div className="mb-4 flex items-center justify-between gap-3">
            <h2 className="mc-section-title">Status snapshot</h2>
            <Activity size={14} className="text-text-3" aria-hidden="true" />
          </div>
          <ul className="space-y-3">
            <SnapshotRow label="Runtime" value={statusLabel} tone={status === 'ok' ? 'green' : 'amber'} />
            <SnapshotRow
              label="Agent roster"
              value={
                agents.isPending
                  ? 'Checking'
                  : agents.isError
                    ? 'Unavailable'
                    : `${agentList.length} registered`
              }
              tone={agents.isError ? 'amber' : 'signal'}
            />
            <SnapshotRow
              label="Owner pilot"
              value={token ? 'Session token set' : 'Not connected'}
              tone={token ? 'green' : 'amber'}
            />
            <SnapshotRow
              label="Business routes"
              value="Closed · peer authority pending (P2)"
              tone="amber"
            />
          </ul>
          {!token ? (
            <p className="mc-caption mt-4">
              <Link to="/settings" className="mc-link">
                Add an owner token
              </Link>{' '}
              to see task counts and pilot panels.
            </p>
          ) : null}
        </div>
      </section>

      <section className="mc-panel">
        <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3">
          <h2 className="mc-section-title">Recent audit events</h2>
          <span className="mc-label">immutable audit log · read-only</span>
        </div>
        {!token ? (
          <div className="px-5 py-4">
            <p className="mc-caption">
              The audit log is owner-scoped.{' '}
              <Link to="/settings" className="mc-link">
                Add an owner token
              </Link>{' '}
              to view recent events.
            </p>
          </div>
        ) : observe.isPending ? (
          <div className="px-5 py-6 text-center text-sm text-text-3" role="status">
            Loading audit events…
          </div>
        ) : observe.isError ? (
          <div className="px-5 py-4">
            <p className="mc-caption">Audit feed unavailable: {pilotErrorMessage(observe.error)}</p>
          </div>
        ) : !overview?.audit.available ? (
          <div className="px-5 py-4">
            <p className="mc-caption">Audit log unavailable: {overview?.audit.reason ?? 'not collected'}</p>
          </div>
        ) : auditEntries.length === 0 ? (
          <EmptyState
            title="No audit entries yet"
            detail="Recorded authorization decisions and execution events will appear here."
          />
        ) : (
          <ul className="divide-y divide-line">
            {auditEntries.map((entry) => (
              <AuditRow key={entry.entry_id} entry={entry} />
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="mc-section-title">Owner pilot</h2>
          <span className="mc-label">owner-scoped · in-memory session token</span>
        </div>
        <PilotGate>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <CapabilitiesPanel />
            <SpendingPanel />
          </div>
        </PilotGate>
      </section>

      <section className="mc-panel">
        <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3">
          <h2 className="mc-section-title">Agents</h2>
          <Link to="/agents" className="mc-label flex items-center gap-1 hover:text-signal">
            view all <ArrowUpRight size={11} aria-hidden="true" />
          </Link>
        </div>
        {agents.isPending ? (
          <div className="px-5 py-10 text-center text-sm text-text-3">Loading agents…</div>
        ) : agents.isError ? (
          <div className="mc-notice mc-notice-warning m-5" role="status">
            Agent roster unavailable: {pilotErrorMessage(agents.error)}
          </div>
        ) : agentList.length === 0 ? (
          <EmptyState
            title="No agents listed"
            detail="The prototype surface reports no registered agents yet."
          />
        ) : (
          <ul className="divide-y divide-line">
            {agentList.map((agent) => (
              <li key={agent.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
                <span
                  className={`mc-status-dot ${agentTone(agent.status)}`}
                  aria-hidden="true"
                />
                <span className="text-sm text-text-1">{agent.name}</span>
                <span className="mc-value text-[11px] text-text-3">{agent.domain}</span>
                <span className="mc-value ml-auto text-[11px] text-text-3">{agent.id}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

function agentTone(status: string | undefined): string {
  if (!status) return 'bg-green'
  const value = status.toLowerCase()
  if (value.includes('error') || value.includes('fail') || value.includes('down')) return 'bg-red'
  if (value.includes('idle') || value.includes('offline')) return 'bg-text-3'
  return 'bg-green'
}

function AuditRow({ entry }: { entry: AuditEntryView }) {
  const title = entry.action ?? entry.authorization ?? 'event'
  const subject = entry.target ?? entry.agent ?? entry.task ?? ''
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 px-5 py-2.5">
      <span
        className="mc-value flex-none text-[11px] text-text-3"
        title={entry.timestamp ? formatDateTime(entry.timestamp, true) : undefined}
      >
        {formatAge(entry.timestamp)}
      </span>
      <span className="mc-value min-w-0 flex-1 truncate text-[11.5px] text-text-1">{title}</span>
      {subject ? (
        <span className="mc-value hidden min-w-0 max-w-40 truncate text-[11px] text-text-3 sm:inline">
          {subject}
        </span>
      ) : null}
      <StateBadge state={entry.result} />
    </li>
  )
}

function Divider() {
  return <div className="hidden h-8 w-px bg-line sm:block" />
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="mc-value text-base text-text-1">{value}</div>
      <div className="mc-label">{label}</div>
    </div>
  )
}

function StatCard({
  icon: Icon,
  label,
  value,
  tone,
  note,
}: {
  icon: LucideIcon
  label: string
  value: string
  tone: 'signal' | 'purple' | 'amber' | 'green'
  note?: string
}) {
  const tones: Record<string, string> = {
    signal: 'text-signal-strong bg-signal-dim border-signal/30',
    purple: 'text-purple bg-purple/10 border-purple/30',
    amber: 'text-amber bg-amber/10 border-amber/30',
    green: 'text-green bg-green/10 border-green/30',
  }
  return (
    <div className="rounded-panel border border-line bg-ink-1 p-4">
      <div className={`mb-3 inline-flex rounded-control border p-2 ${tones[tone]}`}>
        <Icon size={15} aria-hidden="true" />
      </div>
      <div className="mc-value text-2xl text-text-1">{value}</div>
      <div className="mc-label mt-1">{label}</div>
      {note ? <div className="mc-caption mt-1.5 text-[10.5px]">{note}</div> : null}
    </div>
  )
}

function SnapshotRow({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone: 'green' | 'amber' | 'signal'
}) {
  return (
    <li className="flex items-start gap-2.5">
      <StatusDot tone={tone} label={`${label}: ${value}`} />
      <div className="min-w-0 flex-1">
        <div className="mc-label">{label}</div>
        <div className="text-[13px] text-text-1">{value}</div>
      </div>
    </li>
  )
}
