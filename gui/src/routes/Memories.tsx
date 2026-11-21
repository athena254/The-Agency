import { useQuery } from '@tanstack/react-query'
import { Brain, RefreshCw } from 'lucide-react'
import { pilotErrorMessage, useCsrf } from '../api/pilot'
import { fetchObserveMemories, type MemoryView } from '../api/observe'
import {
  Badge,
  EmptyState,
  Notice,
  PageHeader as Header,
} from '../components/ui'
import { formatAge, formatDateTime } from '../lib/format'
import { useAuth } from '../stores/auth'
import { PilotGate } from './PilotShared'

/*
  Read-only view over the memory store (observe surface). Recall/search and
  writes stay behind the /v1 gates — this page lists what is stored, with
  provenance, and never implies recall is callable from here.
*/

export function MemoriesPage() {
  const token = useAuth((state) => state.ownerToken)
  const csrf = useCsrf()
  const memories = useQuery({
    queryKey: ['observe-memories', token],
    queryFn: () => fetchObserveMemories(csrf),
    enabled: Boolean(token && csrf),
    refetchInterval: 30_000,
  })

  const view = memories.data

  return (
    <div className="space-y-4">
      <Header
        title="Memory"
        detail="Persistent, owner-scoped recall with full-text search and tier lifecycle."
      />

      <Notice tone="info">
        <Brain size={14} className="mt-0.5 flex-none" aria-hidden="true" />
        <span>
          Read-only listing of what is stored. Recall (FTS search) and writes arrive with the
          /v1 memory gate; nothing here can be invoked from the console.
        </span>
      </Notice>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-1.5">
          {view?.available
            ? Object.entries(view.by_tier).map(([tier, count]) => (
                <Badge key={tier} tone="muted">
                  {tier} · {count}
                </Badge>
              ))
            : null}
        </div>
        <div className="flex items-center gap-2">
          <span className="mc-value text-xs text-text-3" aria-live="polite">
            {view?.available ? `${view.total} records` : ''}
          </span>
          <button
            type="button"
            className="mc-icon-btn"
            onClick={() => memories.refetch()}
            disabled={memories.isFetching || !token}
            aria-label="Refresh memories"
          >
            <RefreshCw size={13} className={memories.isFetching ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      <PilotGate>
        <MemoriesBody
          view={memories.data}
          error={memories.error}
          pending={memories.isPending}
          retry={() => memories.refetch()}
        />
      </PilotGate>
    </div>
  )
}

function MemoriesBody({
  view,
  error,
  pending,
  retry,
}: {
  view: MemoryView | undefined
  error: unknown
  pending: boolean
  retry: () => void
}) {
  if (pending) {
    return (
      <div className="mc-panel px-5 py-10 text-center text-sm text-text-3" role="status">
        Loading memories…
      </div>
    )
  }
  if (error) {
    return (
      <div className="mc-notice mc-notice-warning" role="alert">
        <span>Memory store unavailable: {pilotErrorMessage(error)}</span>
        <button type="button" className="mc-btn mc-btn-primary flex-none" onClick={retry}>
          Retry
        </button>
      </div>
    )
  }
  if (!view?.available) {
    return (
      <div className="mc-panel px-5 py-4">
        <p className="mc-caption">Memory store unavailable: {view?.reason ?? 'not collected'}</p>
      </div>
    )
  }
  if (view.items.length === 0) {
    return (
      <div className="mc-panel">
        <EmptyState
          title="No memories stored"
          detail="Records appear here as agents persist recall during execution."
        />
      </div>
    )
  }

  return (
    <div className="mc-panel overflow-hidden">
      <div
        className="mc-table-wrap"
        tabIndex={0}
        role="region"
        aria-label="Scrollable data table"
      >
        <table className="w-full text-left">
          <caption className="sr-only">Memory records</caption>
          <thead>
            <tr className="border-b border-line">
              <th scope="col" className="mc-label px-5 py-3 font-normal">
                Content
              </th>
              <th scope="col" className="mc-label px-3 py-3 font-normal">
                Agent
              </th>
              <th scope="col" className="mc-label px-3 py-3 font-normal">
                Tier
              </th>
              <th scope="col" className="mc-label px-3 py-3 font-normal">
                Importance
              </th>
              <th scope="col" className="mc-label px-5 py-3 font-normal">
                Created
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {view.items.map((item) => (
              <tr key={item.id} className="hover:bg-ink-1">
                <td className="max-w-96 px-5 py-3">
                  <div className="truncate text-sm text-text-1" title={item.content}>
                    {item.content}
                  </div>
                  {item.tags.length > 0 ? (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {item.tags.slice(0, 4).map((tag) => (
                        <Badge key={tag} tone="muted">
                          {tag}
                        </Badge>
                      ))}
                    </div>
                  ) : null}
                </td>
                <td className="px-3 py-3">
                  <span className="mc-value text-[11px] text-text-2">{item.agent_id}</span>
                </td>
                <td className="px-3 py-3">
                  <Badge tone="muted">{item.tier}</Badge>
                </td>
                <td className="px-3 py-3">
                  <span className="mc-value text-[11px] text-text-2">
                    {item.importance.toFixed(2)}
                  </span>
                </td>
                <td className="px-5 py-3">
                  <span
                    className="mc-value text-[11px] text-text-3"
                    title={item.created_at ? formatDateTime(item.created_at, true) : undefined}
                  >
                    {formatAge(item.created_at)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {view.db_path ? (
        <div className="mc-caption border-t border-line px-5 py-2">
          store: <span className="mc-value">{view.db_path}</span>
        </div>
      ) : null}
    </div>
  )
}
