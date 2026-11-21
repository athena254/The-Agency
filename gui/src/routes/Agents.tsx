import { useQuery } from '@tanstack/react-query'
import { Bot } from 'lucide-react'
import { api, type AgentDescriptor } from '../api/client'
import { EmptyState } from './Dashboard'

export function AgentsPage() {
  const agents = useQuery({
    queryKey: ['agents'],
    queryFn: () => api.get<{ agents: AgentDescriptor[] }>('/api/agents'),
    refetchInterval: 15_000,
  })

  const list = agents.data?.agents ?? []

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-text-1">Registered agents</h2>
          <p className="mt-0.5 text-xs text-text-3">
            Identities known to the runtime. Domain routing is keyword-based unless an LLM
            classifier is configured.
          </p>
        </div>
        <span className="mc-value text-xs text-text-3">{list.length} total</span>
      </div>

      <div className="mc-panel overflow-hidden">
        {list.length === 0 ? (
          <EmptyState
            title="No agents registered"
            detail="Agents are seeded at startup. The list will appear once the surface reports them."
          />
        ) : (
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-line">
                <th className="mc-label px-5 py-3 font-normal">Agent</th>
                <th className="mc-label px-3 py-3 font-normal">Domain</th>
                <th className="mc-label px-3 py-3 font-normal">Status</th>
                <th className="mc-label px-5 py-3 font-normal">ID</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {list.map((agent) => (
                <tr key={agent.id} className="hover:bg-ink-1">
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-2.5">
                      <span className="flex h-7 w-7 items-center justify-center rounded-control border border-line bg-ink-1 text-text-3">
                        <Bot size={13} />
                      </span>
                      <span className="text-sm text-text-1">{agent.name}</span>
                    </div>
                  </td>
                  <td className="px-3 py-3">
                    <span className="rounded-control border border-line bg-ink-1 px-2 py-0.5 font-mono text-[11px] text-text-2">
                      {agent.domain}
                    </span>
                  </td>
                  <td className="px-3 py-3">
                    <span className="flex items-center gap-2 text-xs text-text-2">
                      <span className="mc-status-dot bg-green" />
                      {agent.status ?? 'idle'}
                    </span>
                  </td>
                  <td className="px-5 py-3 font-mono text-[11px] text-text-3">{agent.id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
