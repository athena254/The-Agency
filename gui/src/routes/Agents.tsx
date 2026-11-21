import { useQuery } from '@tanstack/react-query'
import { Bot, RefreshCw } from 'lucide-react'
import { api } from '../api/client'
import { pilotErrorMessage, useCsrf } from '../api/pilot'
import { fetchObserveAgents } from '../api/observe'
import { EmptyState, PageHeader as Header, StateBadge, StatusDot } from '../components/ui'
import { formatAge, formatDateTime } from '../lib/format'
import { useAuth } from '../stores/auth'

/*
  Agent directory. The public /api/agents roster is always shown; the
  owner-scoped observe surface (when a token is set) enriches it with trust
  level, real runtime status and health counters.
*/

export function AgentsPage() {
  const token = useAuth((state) => state.ownerToken)
  const csrf = useCsrf()
  const roster = useQuery({
    queryKey: ['agents'],
    queryFn: () => api.get<{ agents: { id: string; name: string; domain: string }[] }>('/api/agents'),
    refetchInterval: 15_000,
  })
  const observe = useQuery({
    queryKey: ['observe-agents', token],
    queryFn: () => fetchObserveAgents(csrf),
    enabled: Boolean(token && csrf),
    refetchInterval: 15_000,
  })

  const view = observe.data
  const list = view?.available
    ? view.agents
    : (roster.data?.agents ?? []).map((agent) => ({ ...agent, observeless: true }))
  const showObserve = Boolean(view?.available)

  return (
    <div className="space-y-4">
      <Header
        title="Registered agents"
        detail="Identities known to the runtime. Domain routing is keyword-based unless an LLM classifier is configured."
      />

      {!token ? (
        <p className="mc-caption">
          Runtime status and trust levels are owner-scoped —{' '}
          <span className="text-text-2">add a token in Settings</span> to see them.
        </p>
      ) : observe.isError ? (
        <p className="mc-caption">Runtime status unavailable: {pilotErrorMessage(observe.error)}</p>
      ) : null}

      <div className="flex items-center justify-end gap-2">
        <span className="mc-value text-xs text-text-3" aria-live="polite">
          {list.length} total
        </span>
        <button
          type="button"
          className="mc-icon-btn"
          onClick={() => {
            void roster.refetch()
            if (token) void observe.refetch()
          }}
          disabled={roster.isFetching}
          aria-label="Refresh agents"
        >
          <RefreshCw size={13} className={roster.isFetching ? 'animate-spin' : ''} />
        </button>
      </div>

      <div className="mc-panel overflow-hidden">
        {roster.isPending ? (
          <div className="px-5 py-10 text-center text-sm text-text-3" role="status">
            Loading agents…
          </div>
        ) : roster.isError ? (
          <div className="mc-notice mc-notice-warning m-5" role="status" aria-live="polite">
            <span>Agent roster unavailable: {pilotErrorMessage(roster.error)}</span>
            <button
              type="button"
              className="mc-btn mc-btn-primary flex-none"
              onClick={() => roster.refetch()}
            >
              Retry
            </button>
          </div>
        ) : list.length === 0 ? (
          <EmptyState
            title="No agents registered"
            detail="Agents are seeded at startup. The list will appear once the surface reports them."
          />
        ) : (
          <div
        className="mc-table-wrap"
        tabIndex={0}
        role="region"
        aria-label="Scrollable data table"
      >
            <table className="w-full text-left">
              <caption className="sr-only">Registered agents</caption>
              <thead>
                <tr className="border-b border-line">
                  <th scope="col" className="mc-label px-5 py-3 font-normal">
                    Agent
                  </th>
                  <th scope="col" className="mc-label px-3 py-3 font-normal">
                    Domain
                  </th>
                  <th scope="col" className="mc-label px-3 py-3 font-normal">
                    Trust
                  </th>
                  <th scope="col" className="mc-label px-3 py-3 font-normal">
                    Status
                  </th>
                  <th scope="col" className="mc-label px-5 py-3 font-normal">
                    ID
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {list.map((agent) => (
                  <tr key={agent.id} className="hover:bg-ink-1">
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-2.5">
                        <span className="flex h-7 w-7 items-center justify-center rounded-control border border-line bg-ink-1 text-text-3">
                          <Bot size={13} aria-hidden="true" />
                        </span>
                        <span className="text-sm text-text-1">{agent.name}</span>
                        {'revoked' in agent && agent.revoked ? (
                          <StateBadge state="REVOKED" />
                        ) : null}
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      <span className="rounded-control border border-line bg-ink-1 px-2 py-0.5 font-mono text-[11px] text-text-2">
                        {agent.domain}
                      </span>
                    </td>
                    <td className="px-3 py-3">
                      {showObserve && 'trust_level' in agent ? (
                        <span className="mc-value text-[11px] text-text-2">{agent.trust_level}</span>
                      ) : (
                        <span className="mc-value text-[11px] text-text-3">—</span>
                      )}
                    </td>
                    <td className="px-3 py-3">
                      {showObserve && 'runtime_status' in agent ? (
                        <span className="flex items-center gap-2 text-xs text-text-2">
                          <StatusDot
                            tone={agentRuntimeTone(agent.runtime_status)}
                            label={`status ${agent.runtime_status ?? 'unknown'}`}
                          />
                          {agent.runtime_status ?? 'unknown'}
                          {'consecutive_failures' in agent && agent.consecutive_failures > 0 ? (
                            <span className="mc-value text-[10.5px] text-amber">
                              · {agent.consecutive_failures} fail
                            </span>
                          ) : null}
                          {'last_heartbeat' in agent && agent.last_heartbeat ? (
                            <span
                              className="mc-value text-[10.5px] text-text-3"
                              title={formatDateTime(agent.last_heartbeat, true)}
                            >
                              · {formatAge(agent.last_heartbeat)}
                            </span>
                          ) : null}
                        </span>
                      ) : (
                        <span className="text-xs text-text-3">owner-scoped</span>
                      )}
                    </td>
                    <td className="px-5 py-3 font-mono text-[11px] text-text-3">{agent.id}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

function agentRuntimeTone(status: string | null): 'green' | 'amber' | 'red' | 'muted' {
  switch (status) {
    case 'busy':
      return 'green'
    case 'draining':
      return 'amber'
    case 'error':
      return 'red'
    case 'idle':
      return 'muted'
    case 'offline':
      return 'muted'
    case null:
      return 'muted'
    default:
      return 'amber'
  }
}
