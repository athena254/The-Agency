import { useCallback, useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { FolderKanban, RefreshCw } from 'lucide-react'
import { pilotRequest, useAuth } from '../stores/auth'
import { EmptyState } from './Dashboard'

interface TaskRow {
  task_id: string
  state: string
  title?: string
  thread_id?: string
  created_at?: string
}

export function TasksPage() {
  const token = useAuth((s) => s.ownerToken)
  const [csrf, setCsrf] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/session')
      .then((r) => r.json())
      .then((s) => setCsrf(s.csrf_token))
      .catch(() => setCsrf(null))
  }, [])

  const tasks = useQuery({
    queryKey: ['pilot-tasks', token],
    queryFn: () =>
      pilotRequest<{ tasks: TaskRow[] }>('/api/pilot/tasks?limit=50', { csrf }),
    enabled: Boolean(token),
    refetchInterval: 10_000,
  })

  const cancel = useCallback(
    async (taskId: string) => {
      await pilotRequest(`/api/pilot/tasks/${taskId}/cancel`, {
        method: 'POST',
        body: { request_id: `ui-${Date.now()}` },
        csrf,
      })
      tasks.refetch()
    },
    [csrf, tasks],
  )

  if (!token) {
    return (
      <EmptyState
        title="Owner token required"
        detail="Add the owner bearer token in Settings to view pilot tasks."
      />
    )
  }

  const list = tasks.data?.tasks ?? []

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-text-1">Owner pilot tasks</h2>
          <p className="mt-0.5 text-xs text-text-3">
            Durable single-owner tasks. Cancellation is task-scoped and audited.
          </p>
        </div>
        <button
          type="button"
          className="mc-btn"
          onClick={() => tasks.refetch()}
          disabled={tasks.isFetching}
        >
          <RefreshCw size={13} className={tasks.isFetching ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      <div className="mc-panel overflow-hidden">
        {tasks.isLoading ? (
          <div className="px-5 py-10 text-center text-sm text-text-3">Loading…</div>
        ) : list.length === 0 ? (
          <EmptyState
            title="No tasks yet"
            detail="Submit a task through the owner pilot API and it will appear here."
          />
        ) : (
          <ul className="divide-y divide-line">
            {list.map((task) => (
              <li key={task.task_id} className="flex items-center gap-4 px-5 py-3">
                <FolderKanban size={15} className="flex-none text-text-3" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm text-text-1">
                    {task.title ?? task.task_id}
                  </div>
                  <div className="mc-value text-[11px] text-text-3">{task.task_id}</div>
                </div>
                <span
                  className={`rounded-control border px-2 py-0.5 font-mono text-[11px] ${stateTone(task.state)}`}
                >
                  {task.state}
                </span>
                {task.state === 'RUNNING' || task.state === 'ADMITTED' ? (
                  <button
                    type="button"
                    className="mc-btn mc-btn-danger flex-none"
                    onClick={() => void cancel(task.task_id)}
                  >
                    Cancel
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

function stateTone(state: string): string {
  switch (state) {
    case 'COMPLETED':
      return 'border-green/30 bg-green/10 text-green'
    case 'FAILED':
    case 'REJECTED':
      return 'border-red/30 bg-red/10 text-red'
    case 'RUNNING':
      return 'border-signal/30 bg-signal-dim text-signal-strong'
    default:
      return 'border-line bg-ink-1 text-text-2'
  }
}
