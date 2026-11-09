import { useCallback, useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Code, Lock } from 'lucide-react'
import {
  fetchCodingReceipt,
  fetchCodingStatus,
  fetchTask,
  fetchThreads,
  isPilotAuthFailure,
  pilotErrorMessage,
  submitCodingTask,
  useCsrf,
  type CodingReceiptView,
  type OwnerTaskView,
} from '../api/pilot'
import { useAuth } from '../stores/auth'
import { EmptyState } from './Dashboard'
import { LockedPanel } from './Locked'
import { Chip, LoadingBlock, PanelRow, PilotErrorNote, PilotGate, StateChip } from './PilotShared'

const TERMINAL = new Set(['COMPLETED', 'FAILED', 'DENIED', 'CANCELLED', 'UNCERTAIN'])

export function CodingPage() {
  const token = useAuth((s) => s.ownerToken)
  const setOwnerToken = useAuth((s) => s.setOwnerToken)
  const setAuthNotice = useAuth((s) => s.setAuthNotice)
  const csrf = useCsrf()

  const [requestId, setRequestId] = useState(() => `ui-${Date.now()}`)
  const [threadId, setThreadId] = useState('')
  const [task, setTask] = useState('Set x to integer 2')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [view, setView] = useState<OwnerTaskView | null>(null)
  const [receipt, setReceipt] = useState<CodingReceiptView | null>(null)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [pollNonce, setPollNonce] = useState(0)

  const status = useQuery({
    queryKey: ['pilot-coding-status', token],
    queryFn: () => fetchCodingStatus(csrf),
    enabled: Boolean(token),
    refetchInterval: 30_000,
  })

  const threads = useQuery({
    queryKey: ['pilot-threads', token],
    queryFn: () => fetchThreads(csrf),
    enabled: Boolean(token),
    refetchInterval: 15_000,
  })

  const submit = useCallback(async () => {
    const id = requestId.trim()
    const text = task.trim()
    if (!id || !threadId || !text || submitting) return
    setSubmitting(true)
    setError(null)
    setNotice(null)
    setReceipt(null)
    try {
      const next = await submitCodingTask(csrf, {
        request_id: id,
        thread_id: threadId,
        profile_id: 'literal-assignment-v1',
        task: text,
      })
      setView(next)
      setTaskId(next.task_id)
      setPollNonce((n) => n + 1)
      setRequestId(`ui-${Date.now()}`)
      setNotice(
        next.replayed
          ? 'Replayed: this request ID already produced the coding task.'
          : 'Coding task accepted.',
      )
    } catch (err) {
      if (isPilotAuthFailure(err)) {
        setAuthNotice(pilotErrorMessage(err))
        setOwnerToken(null)
      }
      setError(pilotErrorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }, [requestId, threadId, task, submitting, csrf, setAuthNotice, setOwnerToken])

  useEffect(() => {
    if (!taskId) return
    let cancelled = false
    let timer: number | undefined
    let attempts = 0

    const tick = async () => {
      attempts += 1
      try {
        const next = await fetchTask(csrf, taskId)
        if (cancelled) return
        setView(next)
        if (TERMINAL.has(next.state)) {
          const row = await fetchCodingReceipt(csrf, next.task_id)
          if (!cancelled) setReceipt(row)
          return
        }
      } catch (err) {
        if (cancelled) return
        if (isPilotAuthFailure(err)) {
          setAuthNotice(pilotErrorMessage(err))
          setOwnerToken(null)
        }
        setError(pilotErrorMessage(err))
        return
      }
      if (attempts >= 30) {
        if (!cancelled) setError('Timed out waiting for the coding task to settle.')
        return
      }
      timer = window.setTimeout(tick, 1500)
    }

    timer = window.setTimeout(tick, 800)
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [taskId, csrf, pollNonce, setAuthNotice, setOwnerToken])

  const threadList = threads.data?.threads ?? []
  const available = status.data?.available ?? false

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-text-1">Owner pilot coding</h2>
          <p className="mt-0.5 text-xs text-text-3">
            Bounded <span className="mc-value">literal-assignment-v1</span> fixture: one edit to
            m.py, one model call, no tests and no release authority.
          </p>
        </div>
        <span className="mc-value text-xs text-text-3">POST /api/pilot/coding-tasks</span>
      </div>

      <PilotGate>
        {status.isLoading ? (
          <LoadingBlock label="Checking coding availability…" />
        ) : status.error ? (
          <PilotErrorNote error={status.error} />
        ) : status.data && !available ? (
          <LockedPanel
            icon={Lock}
            title="Coding is unavailable"
            detail={`This pilot has no coding executor (reason: ${status.data.reason}). No coding task can be submitted and no receipt can be read.`}
            gates={['coding executor not configured']}
          />
        ) : (
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <section className="mc-panel flex flex-col p-5">
              <div className="mb-4 flex items-center justify-between gap-3">
                <h3 className="flex items-center gap-2 text-sm font-semibold text-text-1">
                  <Code size={14} className="text-text-3" /> Coding task
                </h3>
                <span className="mc-label">literal-assignment-v1</span>
              </div>

              <form
                className="space-y-3"
                onSubmit={(e) => {
                  e.preventDefault()
                  void submit()
                }}
              >
                <div>
                  <label htmlFor="coding-request" className="mc-label mb-1 block">
                    Request ID
                  </label>
                  <input
                    id="coding-request"
                    className="mc-input"
                    value={requestId}
                    onChange={(e) => setRequestId(e.target.value)}
                    maxLength={128}
                    autoComplete="off"
                  />
                </div>
                <div>
                  <label htmlFor="coding-thread" className="mc-label mb-1 block">
                    Thread
                  </label>
                  <select
                    id="coding-thread"
                    className="mc-input"
                    value={threadId}
                    onChange={(e) => setThreadId(e.target.value)}
                  >
                    <option value="">— select thread —</option>
                    {threadList.map((thread) => (
                      <option key={thread.id} value={thread.id}>
                        {thread.title}
                      </option>
                    ))}
                  </select>
                  {threadList.length === 0 ? (
                    <p className="mt-1 text-[11px] text-amber">
                      No threads yet — create one on the Threads page.
                    </p>
                  ) : null}
                </div>
                <div>
                  <label htmlFor="coding-task" className="mc-label mb-1 block">
                    Task
                  </label>
                  <textarea
                    id="coding-task"
                    className="mc-input"
                    rows={4}
                    value={task}
                    onChange={(e) => setTask(e.target.value)}
                    maxLength={8000}
                    autoComplete="off"
                  />
                  <p className="mt-1 text-[11px] text-text-3">
                    The planner must propose exactly one bounded edit to m.py (baseline x = 1).
                  </p>
                </div>
                <button
                  type="submit"
                  className="mc-btn mc-btn-primary w-full justify-center"
                  disabled={
                    submitting || !csrf || !threadId || !requestId.trim() || !task.trim()
                  }
                >
                  <Code size={13} /> Submit coding task
                </button>
              </form>

              {error ? (
                <div className="mt-3">
                  <PilotErrorNote error={error} />
                </div>
              ) : null}
              {notice ? (
                <div className="mt-3 rounded-control border border-green/30 bg-green/10 px-3 py-2 text-xs text-green">
                  {notice}
                </div>
              ) : null}
            </section>

            <section className="mc-panel flex flex-col p-5">
              <div className="mb-4 flex items-center justify-between gap-3">
                <h3 className="flex items-center gap-2 text-sm font-semibold text-text-1">
                  <Code size={14} className="text-text-3" /> Receipt &amp; evidence
                </h3>
                <span className="mc-label">GET /api/pilot/coding-tasks/:id</span>
              </div>

              {!view ? (
                <EmptyState
                  title="No coding task yet"
                  detail="Submit a coding task to read its typed receipt and committed diff."
                />
              ) : (
                <>
                  <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
                    <PanelRow label="Task">{view.task_id}</PanelRow>
                    <PanelRow label="State">
                      <StateChip state={view.state} />
                    </PanelRow>
                    <PanelRow label="Replayed">{view.replayed ? 'yes' : 'no'}</PanelRow>
                    <PanelRow label="Uncertain">{view.uncertain ? 'yes' : 'no'}</PanelRow>
                    <PanelRow label="Error code">{view.error_code ?? '—'}</PanelRow>
                    <PanelRow label="Usage">
                      {`${view.usage.model_attempts} model / ${view.usage.tool_attempts} tool`}
                    </PanelRow>
                  </dl>

                  {receipt ? (
                    <ReceiptBody data={receipt} />
                  ) : (
                    <p className="mt-4 border-t border-line pt-4 text-xs leading-relaxed text-text-3">
                      The receipt appears after the task settles.
                    </p>
                  )}

                  {view.response ? (
                    <div className="mt-4 border-t border-line pt-4">
                      <div className="mc-label mb-1">Committed response / diff</div>
                      <pre className="whitespace-pre-wrap rounded-control border border-line bg-ink-1 px-3 py-2 text-[11.5px] text-text-1">
                        {view.response}
                      </pre>
                    </div>
                  ) : null}
                </>
              )}
            </section>
          </div>
        )}
      </PilotGate>
    </div>
  )
}

function ReceiptBody({ data }: { data: CodingReceiptView }) {
  const receipt = data.receipt
  return (
    <div className="mt-4 space-y-4 border-t border-line pt-4">
      <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
        <PanelRow label="Task">{data.task_id}</PanelRow>
        <PanelRow label="Execution">{data.execution_state}</PanelRow>
        <PanelRow label="Reason">
          <Chip tone={data.reason === 'available' ? 'green' : 'amber'}>{data.reason}</Chip>
        </PanelRow>
      </dl>

      {receipt ? (
        <>
          <div>
            <div className="mc-label mb-2">Coding receipt</div>
            <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2">
              <PanelRow label="Objective">
                <Chip tone={objectiveTone(receipt.objective_status)}>
                  {receipt.objective_status}
                </Chip>
              </PanelRow>
              <PanelRow label="Reason">{receipt.reason}</PanelRow>
              <PanelRow label="Plan sha256">
                <Hash value={receipt.plan_sha256} />
              </PanelRow>
              <PanelRow label="Patch sha256">
                <Hash value={receipt.patch_sha256} />
              </PanelRow>
              <PanelRow label="Artifact sha256">
                <Hash value={receipt.artifact_sha256} />
              </PanelRow>
              <PanelRow label="Forge report">{receipt.forge_report_id}</PanelRow>
              <PanelRow label="Forge sha256">
                <Hash value={receipt.forge_report_sha256} />
              </PanelRow>
              <PanelRow label="Release authorized">
                {receipt.release_authorized ? 'yes' : 'no'}
              </PanelRow>
              <PanelRow label="Tests run">{receipt.tests_run ? 'yes' : 'no'}</PanelRow>
            </dl>
          </div>

          <div>
            <div className="mc-label mb-2">Binding</div>
            <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2">
              <PanelRow label="Profile">{receipt.binding.profile_id}</PanelRow>
              <PanelRow label="Blueprint">
                {`${receipt.binding.blueprint_id}@${receipt.binding.blueprint_version}`}
              </PanelRow>
              <PanelRow label="Base commit">
                <Hash value={receipt.binding.base_commit} />
              </PanelRow>
              <PanelRow label="Runtime agent">
                <Hash value={receipt.binding.runtime_agent_id} />
              </PanelRow>
              <PanelRow label="Checker">
                <Hash value={receipt.binding.checker_sha256} />
              </PanelRow>
              <PanelRow label="Criteria">
                <Hash value={receipt.binding.criteria_sha256} />
              </PanelRow>
            </dl>
          </div>
        </>
      ) : (
        <p className="text-xs leading-relaxed text-text-3">
          No receipt is bound to this task (reason: {data.reason}). A receipt is published only for
          a completed coding task with a passed objective.
        </p>
      )}
    </div>
  )
}

function Hash({ value }: { value: string | null }) {
  return <span className="mc-value text-[11.5px] text-text-1">{value ?? '—'}</span>
}

function objectiveTone(status: string): 'green' | 'red' | 'amber' {
  switch (status) {
    case 'passed':
      return 'green'
    case 'failed':
      return 'red'
    default:
      return 'amber'
  }
}
