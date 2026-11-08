import { useCallback, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus, RefreshCw } from 'lucide-react'
import {
  createThread,
  fetchMessages,
  fetchThreads,
  isPilotAuthFailure,
  pilotErrorMessage,
  useCsrf,
} from '../api/pilot'
import { useAuth } from '../stores/auth'
import { EmptyState } from './Dashboard'
import { LoadingBlock, PilotErrorNote, PilotGate, StateChip } from './PilotShared'

export function ThreadsPage() {
  const token = useAuth((s) => s.ownerToken)
  const setOwnerToken = useAuth((s) => s.setOwnerToken)
  const setAuthNotice = useAuth((s) => s.setAuthNotice)
  const csrf = useCsrf()
  const [selected, setSelected] = useState<string | null>(null)
  const [requestId, setRequestId] = useState(() => `ui-${Date.now()}`)
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const threads = useQuery({
    queryKey: ['pilot-threads', token],
    queryFn: () => fetchThreads(csrf),
    enabled: Boolean(token),
    refetchInterval: 15_000,
  })

  const messages = useQuery({
    queryKey: ['pilot-thread-messages', selected, token],
    queryFn: () => fetchMessages(csrf, selected as string),
    enabled: Boolean(token) && Boolean(selected),
  })

  const create = useCallback(async () => {
    const id = requestId.trim()
    const name = title.trim()
    if (!id || !name || busy) return
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const result = await createThread(csrf, id, name)
      setNotice(
        result.request.replayed
          ? 'Replayed: this request ID already produced the thread.'
          : 'Thread created.',
      )
      setTitle('')
      setRequestId(`ui-${Date.now()}`)
      setSelected(result.thread.id)
      await threads.refetch()
    } catch (err) {
      if (isPilotAuthFailure(err)) {
        setAuthNotice(pilotErrorMessage(err))
        setOwnerToken(null)
      }
      setError(pilotErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }, [busy, requestId, setAuthNotice, setOwnerToken, csrf, title, threads])

  const list = threads.data?.threads ?? []
  const selectedThread = list.find((thread) => thread.id === selected) ?? null
  const messageList = messages.data?.messages ?? []

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-text-1">Owner pilot threads</h2>
          <p className="mt-0.5 text-xs text-text-3">
            Durable owner-scoped conversations. Threads are created with a request ID and
            replay safely.
          </p>
        </div>
        <span className="mc-value text-xs text-text-3">{list.length} total</span>
      </div>

      <PilotGate>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
          {/* Thread list */}
          <section className="mc-panel flex min-h-0 flex-col lg:col-span-2">
            <div className="flex items-center justify-between border-b border-line px-4 py-3">
              <h3 className="text-sm font-semibold text-text-1">Threads</h3>
              <button
                type="button"
                className="mc-btn flex-none"
                onClick={() => threads.refetch()}
                disabled={threads.isFetching}
              >
                <RefreshCw size={13} className={threads.isFetching ? 'animate-spin' : ''} />
                Refresh
              </button>
            </div>

            <form
              className="space-y-3 border-b border-line p-4"
              onSubmit={(e) => {
                e.preventDefault()
                void create()
              }}
            >
              <div className="mc-label">New thread</div>
              <div>
                <label htmlFor="thread-request" className="mc-label mb-1 block">
                  Request ID
                </label>
                <input
                  id="thread-request"
                  className="mc-input"
                  value={requestId}
                  onChange={(e) => setRequestId(e.target.value)}
                  autoComplete="off"
                />
              </div>
              <div>
                <label htmlFor="thread-title" className="mc-label mb-1 block">
                  Title
                </label>
                <input
                  id="thread-title"
                  className="mc-input"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="Thread title"
                  autoComplete="off"
                />
              </div>
              <button
                type="submit"
                className="mc-btn mc-btn-primary w-full justify-center"
                disabled={busy || !csrf || !title.trim() || !requestId.trim()}
              >
                <Plus size={13} /> Create thread
              </button>
              {error ? (
                <div className="pt-1">
                  <PilotErrorNote error={error} />
                </div>
              ) : null}
              {notice ? (
                <div className="rounded-control border border-green/30 bg-green/10 px-3 py-2 text-xs text-green">
                  {notice}
                </div>
              ) : null}
            </form>

            <div className="min-h-0 flex-1 overflow-y-auto">
              {threads.isLoading ? (
                <LoadingBlock />
              ) : threads.error ? (
                <div className="p-4">
                  <PilotErrorNote error={threads.error} />
                </div>
              ) : list.length === 0 ? (
                <EmptyState
                  title="No threads yet"
                  detail="Create a thread with a request ID and title to start an owner-scoped conversation."
                />
              ) : (
                <ul className="divide-y divide-line">
                  {list.map((thread) => (
                    <li key={thread.id}>
                      <button
                        type="button"
                        className={`block w-full px-4 py-3 text-left transition-colors ${
                          selected === thread.id ? 'bg-signal-dim' : 'hover:bg-ink-1'
                        }`}
                        onClick={() => setSelected(thread.id)}
                      >
                        <div className="flex items-center gap-2">
                          <span className="truncate text-sm text-text-1">{thread.title}</span>
                          <StateChip state={thread.state} />
                        </div>
                        <div className="mc-value mt-1 text-[10.5px] text-text-3">
                          {thread.id}
                          {thread.parent_thread_id
                            ? ` · child of ${thread.parent_thread_id}`
                            : ''}
                        </div>
                        <div className="mc-value mt-0.5 text-[10.5px] text-text-3">
                          {thread.updated_at}
                        </div>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>

          {/* Messages */}
          <section className="mc-panel flex min-h-0 flex-col lg:col-span-3">
            <div className="flex items-center justify-between border-b border-line px-5 py-3">
              <div className="min-w-0">
                <h3 className="truncate text-sm font-semibold text-text-1">
                  {selectedThread ? selectedThread.title : 'Messages'}
                </h3>
                <div className="mc-label mt-0.5">
                  {selected ?? 'GET /api/pilot/threads/{id}/messages'}
                </div>
              </div>
              <button
                type="button"
                className="mc-btn flex-none"
                onClick={() => messages.refetch()}
                disabled={!selected || messages.isFetching}
              >
                <RefreshCw size={13} className={messages.isFetching ? 'animate-spin' : ''} />
                Refresh
              </button>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
              {!selected ? (
                <EmptyState
                  title="No thread selected"
                  detail="Select a thread on the left to read its messages."
                />
              ) : messages.isLoading ? (
                <LoadingBlock />
              ) : messages.error ? (
                <PilotErrorNote error={messages.error} />
              ) : messageList.length === 0 ? (
                <EmptyState
                  title="No messages"
                  detail="This thread has no messages yet. Submit an owner task or calculator task against it."
                />
              ) : (
                <ul className="space-y-4">
                  {messageList.map((message) => (
                    <li key={message.id} className="flex gap-3">
                      <span
                        className={`mt-1.5 h-2 w-2 flex-none rounded-full ${
                          message.role === 'user'
                            ? 'bg-signal'
                            : message.role === 'assistant'
                              ? 'bg-green'
                              : 'bg-text-3'
                        }`}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="mc-label">{message.role}</span>
                          <span className="mc-value text-[10.5px] text-text-3">
                            {message.created_at}
                          </span>
                        </div>
                        <div className="mt-1 whitespace-pre-wrap text-sm text-text-1">
                          {message.content}
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>
        </div>
      </PilotGate>
    </div>
  )
}
