import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  ArrowUpRight,
  Bot,
  Boxes,
  Cpu,
  Database,
  Gauge,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { api, type AgentDescriptor, type HealthResponse } from '../api/client'
import { CapabilitiesPanel, SpendingPanel } from './PilotOverview'
import { PilotGate } from './PilotShared'

interface DashboardStats {
  agents: number
  tasks: number
  memories: number
  evidence: number
}

export function DashboardPage() {
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
  const stats = useQuery({
    queryKey: ['dashboard-stats'],
    queryFn: (): Promise<DashboardStats> =>
      Promise.resolve({ agents: 0, tasks: 0, memories: 0, evidence: 0 }),
  })

  const agentList = agents.data?.agents ?? []
  const components = health.data?.components ?? {}
  const online = Object.values(components).filter(Boolean).length

  return (
    <div className="space-y-6">
      {/* Status strip */}
      <section className="mc-panel flex flex-wrap items-center gap-x-8 gap-y-3 px-5 py-4">
        <div className="flex items-center gap-3">
          <span
            className={`mc-status-dot ${health.data?.status === 'ok' ? 'bg-green' : 'bg-amber'}`}
          />
          <div>
            <div className="text-sm font-medium text-text-1">
              {health.data?.status === 'ok' ? 'Runtime healthy' : 'Degraded'}
            </div>
            <div className="mc-label">
              {health.data?.butler ? `Butler ${health.data.butler}` : 'prototype surface'}
            </div>
          </div>
        </div>
        <Divider />
        <Metric label="Components online" value={`${online}/${Object.keys(components).length}`} />
        <Divider />
        <Metric label="Agents registered" value={String(agentList.length)} />
        <Divider />
        <Metric label="Version" value={health.data?.version ?? '0.1.0'} />
        <div className="ml-auto flex items-center gap-2">
          <Link to="/ide" className="mc-btn mc-btn-primary">
            <ArrowUpRight size={13} /> Open IDE
          </Link>
        </div>
      </section>

      {/* Overview grid */}
      <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="mc-panel p-5 lg:col-span-2">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-text-1">System overview</h2>
            <span className="mc-label">live</span>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard icon={Bot} label="Agents" value={String(agentList.length)} tone="signal" />
            <StatCard
              icon={Boxes}
              label="Tasks"
              value={String(stats.data?.tasks ?? 0)}
              tone="purple"
            />
            <StatCard
              icon={Database}
              label="Memories"
              value={String(stats.data?.memories ?? 0)}
              tone="amber"
            />
            <StatCard
              icon={ShieldCheck}
              label="Evidence"
              value={String(stats.data?.evidence ?? 0)}
              tone="green"
            />
          </div>

          <div className="mt-5 border-t border-line pt-4">
            <div className="mc-label mb-3">Component readiness</div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {Object.entries(components).map(([name, ok]) => (
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
          </div>
        </div>

        <div className="mc-panel p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-text-1">Activity</h2>
            <Activity size={14} className="text-text-3" />
          </div>
          <ul className="space-y-3">
            <FeedItem
              icon={Cpu}
              title="Runtime boot"
              detail="prototype surface on 127.0.0.1:8001"
              time="now"
            />
            <FeedItem
              icon={Sparkles}
              title="Model path verified"
              detail="Inception mercury-3 responded"
              time="1m"
            />
            <FeedItem
              icon={Gauge}
              title="Peer authority"
              detail="not yet available — business routes closed"
              time="—"
              tone="amber"
            />
          </ul>
        </div>
      </section>

      {/* Owner pilot */}
      <section className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-text-1">Owner pilot</h2>
          <span className="mc-label">owner-scoped · loopback · in-memory token</span>
        </div>
        <PilotGate>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <CapabilitiesPanel />
            <SpendingPanel />
          </div>
        </PilotGate>
      </section>

      {/* Agents preview */}
      <section className="mc-panel">
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <h2 className="text-sm font-semibold text-text-1">Agents</h2>
          <Link to="/agents" className="mc-label flex items-center gap-1 hover:text-signal">
            view all <ArrowUpRight size={11} />
          </Link>
        </div>
        {agentList.length === 0 ? (
          <EmptyState
            title="No agents listed"
            detail="The prototype surface reports no registered agents yet."
          />
        ) : (
          <ul className="divide-y divide-line">
            {agentList.map((agent) => (
              <li key={agent.id} className="flex items-center gap-3 px-5 py-3">
                <span className="mc-status-dot bg-green" />
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
}: {
  icon: typeof Bot
  label: string
  value: string
  tone: 'signal' | 'purple' | 'amber' | 'green'
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
        <Icon size={15} />
      </div>
      <div className="mc-value text-2xl text-text-1">{value}</div>
      <div className="mc-label mt-1">{label}</div>
    </div>
  )
}

function FeedItem({
  icon: Icon,
  title,
  detail,
  time,
  tone,
}: {
  icon: typeof Cpu
  title: string
  detail: string
  time: string
  tone?: 'amber'
}) {
  return (
    <li className="flex gap-3">
      <span
        className={`mt-0.5 flex h-7 w-7 flex-none items-center justify-center rounded-control border ${
          tone === 'amber'
            ? 'border-amber/30 bg-amber/10 text-amber'
            : 'border-line bg-ink-1 text-text-3'
        }`}
      >
        <Icon size={13} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-[13px] text-text-1">{title}</div>
        <div className="truncate text-xs text-text-3">{detail}</div>
      </div>
      <span className="mc-value flex-none text-[10.5px] text-text-3">{time}</span>
    </li>
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
