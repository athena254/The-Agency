import { useQuery } from '@tanstack/react-query'
import type { LucideIcon } from 'lucide-react'
import {
  Activity,
  AlertTriangle,
  Bot,
  Brain,
  FolderKanban,
  Gauge,
  RefreshCw,
  ScrollText,
  ShieldCheck,
  Sparkles,
  Wrench,
} from 'lucide-react'
import type { ReactNode } from 'react'
import {
  fetchObserveOverview,
  fetchObserveSkills,
  fetchObserveTools,
  type AgentsView,
  type AuditView,
  type MemoryView,
  type OwnerView,
  type RiskView,
  type RuntimeStatusView,
  type SkillsView,
  type TasksView,
  type ToolsView,
} from '../api/observe'
import { useCsrf } from '../api/pilot'
import { useAuth } from '../stores/auth'
import { Chip, LoadingBlock, PanelRow, PilotErrorNote, PilotGate, StateChip } from './PilotShared'

type Tone = 'signal' | 'muted' | 'amber' | 'green' | 'red'

type SectionState =
  | { kind: 'loading' }
  | { kind: 'error'; error: unknown }
  | { kind: 'unavailable'; reason: string | null }
  | { kind: 'ready' }

function localState(available: boolean, reason: string | null): SectionState {
  return available ? { kind: 'ready' } : { kind: 'unavailable', reason }
}

function queryState(query: {
  isLoading: boolean
  error: unknown
  data: { available: boolean; reason: string | null } | undefined
}): SectionState {
  if (query.isLoading) return { kind: 'loading' }
  if (query.error) return { kind: 'error', error: query.error }
  if (!query.data) return { kind: 'loading' }
  return localState(query.data.available, query.data.reason)
}

function Section({
  title,
  icon: Icon,
  endpoint,
  state,
  children,
}: {
  title: string
  icon: LucideIcon
  endpoint: string
  state: SectionState
  children: ReactNode
}) {
  return (
    <section className="mc-panel flex flex-col p-5">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-text-1">
          <Icon size={14} className="text-text-3" /> {title}
        </h3>
        <div className="flex items-center gap-2">
          <span className="mc-label">{endpoint}</span>
          {state.kind === 'ready' ? <Chip tone="green">available</Chip> : null}
          {state.kind === 'loading' ? <Chip tone="muted">loading</Chip> : null}
          {state.kind === 'error' ? <Chip tone="red">error</Chip> : null}
          {state.kind === 'unavailable' ? <Chip tone="amber">unavailable</Chip> : null}
        </div>
      </div>
      {state.kind === 'loading' ? (
        <LoadingBlock />
      ) : state.kind === 'error' ? (
        <PilotErrorNote error={state.error} />
      ) : state.kind === 'unavailable' ? (
        <p className="text-xs leading-relaxed text-text-3">
          This component could not be read from the local runtime
          {state.reason ? ` (${state.reason})` : ''}. The rest of the snapshot is
          unaffected, and nothing here was written or retried.
        </p>
      ) : (
        children
      )}
    </section>
  )
}

function EmptyRow({ label }: { label: string }) {
  return <div className="px-1 py-6 text-center text-xs text-text-3">{label}</div>
}

function yesNo(value: boolean) {
  return value ? <span className="text-green">yes</span> : <span className="text-amber">no</span>
}

function RuntimeBody({ view }: { view: RuntimeStatusView }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
      <PanelRow label="Status">
        <StateChip state={view.status} />
      </PanelRow>
      <PanelRow label="Butler started">{yesNo(view.butler_started)}</PanelRow>
      <PanelRow label="Pilot started">{yesNo(view.pilot_started)}</PanelRow>
      <PanelRow label="Pilot configured">{yesNo(view.pilot_configured)}</PanelRow>
    </dl>
  )
}

function OwnerBody({ view }: { view: OwnerView }) {
  return (
    <>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
        <PanelRow label="Owner">{view.owner_id}</PanelRow>
        <PanelRow label="Subject">{view.subject_id}</PanelRow>
        <PanelRow label="Owner tasks">{view.total}</PanelRow>
        <PanelRow label="Uncertain">{view.uncertain}</PanelRow>
        <PanelRow label="Threads">{view.thread_total}</PanelRow>
      </dl>
      {view.threads.length > 0 ? (
        <div className="mt-4 border-t border-line pt-3">
          <div className="mc-label mb-2">Threads</div>
          <ul className="space-y-1">
            {view.threads.map((thread) => (
              <li key={thread.id} className="flex items-center gap-2 text-xs">
                <span className="truncate text-text-2">{thread.title}</span>
                <StateChip state={thread.state} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </>
  )
}

function AgentsBody({ view }: { view: AgentsView }) {
  if (view.agents.length === 0) {
    return <EmptyRow label="No agents are registered in the local identity registry." />
  }
  return (
    <>
      <div className="mc-value mb-2 text-[11px] text-text-3">
        {view.total} agents · {view.revoked} revoked
      </div>
      <ul className="divide-y divide-line">
        {view.agents.map((agent) => (
          <li key={agent.id} className="flex items-start gap-3 px-1 py-2.5">
            <Bot size={14} className="mt-0.5 flex-none text-text-3" />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate text-sm text-text-1">{agent.name}</span>
                {agent.revoked ? <Chip tone="red">revoked</Chip> : null}
              </div>
              <div className="mc-value mt-0.5 text-[11px] text-text-3">
                {agent.domain} · {agent.trust_level} ·{' '}
                {agent.runtime_status ?? 'no runtime record'}
              </div>
              {agent.capabilities.length > 0 ? (
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {agent.capabilities.map((cap) => (
                    <Chip key={cap.name} tone="muted">
                      {cap.name} · {cap.max_level}
                    </Chip>
                  ))}
                </div>
              ) : null}
            </div>
            <div className="flex-none text-right">
              <div className="mc-value text-[11px] text-text-3">
                failures {agent.consecutive_failures}
              </div>
              {agent.current_task_id ? (
                <div className="mc-value text-[10.5px] text-text-3">
                  {agent.current_task_id}
                </div>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </>
  )
}

function TasksBody({ view }: { view: TasksView }) {
  if (view.tasks.length === 0) {
    return <EmptyRow label="No local tasks have been created yet." />
  }
  return (
    <ul className="divide-y divide-line">
      {view.tasks.map((task) => (
        <li key={task.task_id} className="flex items-center gap-3 px-1 py-2.5">
          <FolderKanban size={14} className="flex-none text-text-3" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm text-text-1">{task.title}</div>
            <div className="mc-value text-[11px] text-text-3">
              {task.task_id} · {task.created_by} · {task.message_count} messages
            </div>
          </div>
          <Chip tone="muted">priority {task.priority}</Chip>
          <StateChip state={task.status} />
        </li>
      ))}
    </ul>
  )
}

function MemoriesBody({ view }: { view: MemoryView }) {
  if (view.items.length === 0) {
    return <EmptyRow label="The local memory store returned no items." />
  }
  return (
    <ul className="divide-y divide-line">
      {view.items.map((item) => (
        <li key={item.id} className="px-1 py-2.5">
          <div className="flex items-center gap-2">
            <span className="mc-value text-[11px] text-text-3">{item.agent_id}</span>
            <Chip tone="signal">{item.tier}</Chip>
            <span className="mc-value text-[11px] text-text-3">
              importance {item.importance.toFixed(2)}
            </span>
          </div>
          <div className="mt-1 text-xs leading-relaxed text-text-2">{item.content}</div>
          {item.tags.length > 0 ? (
            <div className="mt-1 flex flex-wrap gap-1">
              {item.tags.map((tag) => (
                <Chip key={tag}>{tag}</Chip>
              ))}
            </div>
          ) : null}
        </li>
      ))}
    </ul>
  )
}

function severityTone(severity: string): Tone {
  switch (severity.toLowerCase()) {
    case 'critical':
      return 'red'
    case 'high':
      return 'amber'
    case 'medium':
      return 'signal'
    default:
      return 'muted'
  }
}

function EvidenceBody({ view }: { view: RiskView }) {
  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Chip tone="muted">{view.method}</Chip>
        <Chip tone="amber">heuristic only</Chip>
        <span className="mc-value text-[11px] text-text-3">
          {view.total} findings · {view.assessed} assessed · max impact{' '}
          {view.max_score === null ? '—' : view.max_score.toFixed(3)}
        </span>
      </div>
      {view.dimensions.length > 0 ? (
        <div className="mb-4 flex flex-wrap gap-1.5">
          {view.dimensions.map((dim) => (
            <Chip key={dim.label} tone="muted">
              {dim.label} {dim.value.toFixed(3)}
            </Chip>
          ))}
        </div>
      ) : null}
      {view.findings.length === 0 ? (
        <EmptyRow label="No local findings are stored." />
      ) : (
        <ul className="divide-y divide-line">
          {view.findings.map((finding) => (
            <li key={finding.id} className="px-1 py-2.5">
              <div className="flex items-center gap-2">
                <span className="truncate text-sm text-text-1">{finding.target}</span>
                {finding.severity ? (
                  <Chip tone={severityTone(finding.severity)}>{finding.severity}</Chip>
                ) : null}
                <Chip tone="muted">
                  {finding.strongest_level ?? 'LEVEL_0_HYPOTHESIS'}
                </Chip>
                <span className="mc-value ml-auto flex-none text-[11px] text-text-3">
                  {finding.risk_category} · exposure {finding.risk_exposure.toFixed(2)}
                </span>
              </div>
              <div className="mt-1 text-xs leading-relaxed text-text-2">
                {finding.evidence}
              </div>
              <div className="mc-value mt-1 text-[10.5px] text-text-3">
                {finding.verification_status} · {finding.evidence_count} evidence ·
                confidence {finding.confidence.toFixed(2)}
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

function AuditBody({ view }: { view: AuditView }) {
  if (view.entries.length === 0) {
    return <EmptyRow label="The local audit log returned no entries." />
  }
  return (
    <ul className="divide-y divide-line">
      {view.entries.map((entry) => (
        <li key={entry.entry_id} className="flex items-center gap-3 px-1 py-2">
          <ScrollText size={14} className="flex-none text-text-3" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm text-text-1">
              {entry.action ?? 'unclassified'}
            </div>
            <div className="mc-value text-[11px] text-text-3">
              {entry.agent ?? '—'} · {entry.target ?? '—'} · {entry.timestamp ?? '—'}
            </div>
          </div>
          <Chip
            tone={
              entry.result === 'allowed'
                ? 'green'
                : entry.result === 'denied'
                  ? 'red'
                  : 'muted'
            }
          >
            {entry.result}
          </Chip>
        </li>
      ))}
    </ul>
  )
}

function SkillsBody({ view }: { view: SkillsView }) {
  if (view.skills.length === 0) {
    return <EmptyRow label="No local skill versions are registered." />
  }
  return (
    <>
      {view.truncated ? (
        <p className="mb-3 text-xs text-amber">
          Showing the first {view.total} skill versions; the local registry is larger.
        </p>
      ) : null}
      <ul className="divide-y divide-line">
        {view.skills.map((skill) => (
          <li key={`${skill.skill_id}@${skill.version}`} className="px-1 py-2.5">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm text-text-1">{skill.name}</span>
              <Chip tone="muted">{skill.version}</Chip>
              <Chip tone="signal">{skill.status}</Chip>
            </div>
            <div className="mt-1 text-xs leading-relaxed text-text-2">
              {skill.description}
            </div>
            <div className="mc-value mt-1 text-[10.5px] text-text-3">
              {skill.skill_id} · {skill.provenance}
              {skill.permissions.length > 0 ? ` · ${skill.permissions.join(', ')}` : ''}
            </div>
          </li>
        ))}
      </ul>
    </>
  )
}

function ToolsBody({ view }: { view: ToolsView }) {
  if (view.tools.length === 0) {
    return <EmptyRow label="No local tool specs are registered." />
  }
  return (
    <ul className="divide-y divide-line">
      {view.tools.map((tool) => (
        <li key={tool.name} className="flex items-center gap-3 px-1 py-2.5">
          <Wrench size={14} className="flex-none text-text-3" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm text-text-1">{tool.name}</div>
            <div className="text-xs text-text-3">{tool.description}</div>
          </div>
          <Chip tone="muted">{tool.risk}</Chip>
        </li>
      ))}
    </ul>
  )
}

export function ObservabilityPage() {
  const token = useAuth((s) => s.ownerToken)
  const csrf = useCsrf()
  const enabled = Boolean(token)

  const overview = useQuery({
    queryKey: ['observe-overview', token],
    queryFn: () => fetchObserveOverview(csrf),
    enabled,
    refetchInterval: 15_000,
  })
  const skills = useQuery({
    queryKey: ['observe-skills', token],
    queryFn: () => fetchObserveSkills(csrf),
    enabled,
    refetchInterval: 30_000,
  })
  const tools = useQuery({
    queryKey: ['observe-tools', token],
    queryFn: () => fetchObserveTools(csrf),
    enabled,
    refetchInterval: 30_000,
  })

  const data = overview.data

  const refresh = () => {
    void overview.refetch()
    void skills.refetch()
    void tools.refetch()
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-text-1">Observability</h2>
          <p className="mt-0.5 text-xs text-text-3">
            Local read-only runtime state. No writes, no authority, no independent
            verification.
          </p>
        </div>
        <button
          type="button"
          className="mc-btn flex-none"
          onClick={refresh}
          disabled={overview.isFetching}
        >
          <RefreshCw size={13} className={overview.isFetching ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      <div className="mc-panel flex items-start gap-3 px-5 py-4">
        <AlertTriangle size={15} className="mt-0.5 flex-none text-amber" />
        <div className="min-w-0 text-xs leading-relaxed text-text-3">
          <span className="text-text-1">Read-only local snapshot.</span> Peer authority is
          unavailable: the <span className="mc-value">/v1</span> business boundary stays
          closed, and no value here is independently verified or authorized. Risk numbers
          are the local legacy heuristic&apos;s estimate over local findings; memory text
          is the local store&apos;s own.
        </div>
        <div className="ml-auto flex flex-none flex-wrap items-center justify-end gap-1.5">
          <Chip tone="amber">read_only</Chip>
          <Chip tone="muted">peer authority unavailable</Chip>
          <Chip tone="muted">write_controls false</Chip>
        </div>
      </div>

      <PilotGate>
        {overview.isLoading ? (
          <LoadingBlock label="Reading local runtime state…" />
        ) : overview.error ? (
          <div className="mc-panel p-4">
            <PilotErrorNote error={overview.error} />
          </div>
        ) : !data ? null : (
          <div className="space-y-4">
            <Section
              title="Evidence & risk"
              icon={ShieldCheck}
              endpoint="GET /api/pilot/observe/overview"
              state={localState(data.evidence.available, data.evidence.reason)}
            >
              <EvidenceBody view={data.evidence} />
            </Section>
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              <Section
                title="Runtime"
                icon={Activity}
                endpoint="GET /api/pilot/observe/overview"
                state={localState(true, null)}
              >
                <RuntimeBody view={data.runtime} />
              </Section>
              <Section
                title="Owner"
                icon={Gauge}
                endpoint="GET /api/pilot/observe/overview"
                state={localState(data.owner.available, data.owner.reason)}
              >
                <OwnerBody view={data.owner} />
              </Section>
              <Section
                title="Agents"
                icon={Bot}
                endpoint="GET /api/pilot/observe/overview"
                state={localState(data.agents.available, data.agents.reason)}
              >
                <AgentsBody view={data.agents} />
              </Section>
              <Section
                title="Tasks"
                icon={FolderKanban}
                endpoint="GET /api/pilot/observe/overview"
                state={localState(data.tasks.available, data.tasks.reason)}
              >
                <TasksBody view={data.tasks} />
              </Section>
              <Section
                title="Memories"
                icon={Brain}
                endpoint="GET /api/pilot/observe/overview"
                state={localState(data.memories.available, data.memories.reason)}
              >
                <MemoriesBody view={data.memories} />
              </Section>
              <Section
                title="Audit"
                icon={ScrollText}
                endpoint="GET /api/pilot/observe/overview"
                state={localState(data.audit.available, data.audit.reason)}
              >
                <AuditBody view={data.audit} />
              </Section>
              <Section
                title="Skills"
                icon={Sparkles}
                endpoint="GET /api/pilot/observe/skills"
                state={queryState(skills)}
              >
                {skills.data ? <SkillsBody view={skills.data} /> : null}
              </Section>
              <Section
                title="Tools"
                icon={Wrench}
                endpoint="GET /api/pilot/observe/tools"
                state={queryState(tools)}
              >
                {tools.data ? <ToolsBody view={tools.data} /> : null}
              </Section>
            </div>
            <div className="mc-label text-right">
              collected {data.collected_at} · read-only · no write controls
            </div>
          </div>
        )}
      </PilotGate>
    </div>
  )
}
