import { useCallback, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Calculator, FolderKanban, RefreshCw, ShieldCheck } from 'lucide-react'
import {
  cancelTask,
  fetchTasks,
  fetchThreads,
  fetchVerification,
  isPilotAuthFailure,
  pilotErrorMessage,
  submitCalculatorTask,
  useCsrf,
  type OwnerTaskView,
  type VerificationView,
} from '../api/pilot'
import { useTaskSettle } from '../api/useTaskSettle'
import { useAuth } from '../stores/auth'
import { ConfirmDialog, EmptyState } from '../components/ui'
import { useToast } from '../components/toast'
import { stateTone } from '../components/tones'
import { Chip, LoadingBlock, PanelRow, PilotErrorNote, PilotGate, StateChip } from './PilotShared'

export function TasksPage() {
  const token = useAuth((s) => s.ownerToken)
  const setOwnerToken = useAuth((s) => s.setOwnerToken)
  const setAuthNotice = useAuth((s) => s.setAuthNotice)
  const csrf = useCsrf()
  const toast = useToast()

  const [verifyTaskId, setVerifyTaskId] = useState<string | null>(null)
  const [selectedThread, setSelectedThread] = useState('')
  const [calcRequestId, setCalcRequestId] = useState(() => `ui-${Date.now()}`)
  const [expression, setExpression] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [calcError, setCalcError] = useState<string | null>(null)
  const [cancelTarget, setCancelTarget] = useState<OwnerTaskView | null>(null)
  const [cancelBusy, setCancelBusy] = useState(false)

  const tasks = useQuery({
    queryKey: ['pilot-tasks', token],
    queryFn: () => fetchTasks(csrf),
    enabled: Boolean(token),
    refetchInterval: 10_000,
  })

  const threads = useQuery({
    queryKey: ['pilot-threads', token],
    queryFn: () => fetchThreads(csrf),
    enabled: Boolean(token),
    refetchInterval: 15_000,
  })

  const verification = useQuery({
    queryKey: ['pilot-verification', verifyTaskId, token],
    queryFn: () => fetchVerification(csrf, verifyTaskId as string),
    enabled: Boolean(token) && Boolean(verifyTaskId),
  })

  const handleAuthFailure = useCallback(
    (message: string) => {
      setAuthNotice(message)
      setOwnerToken(null)
    },
    [setAuthNotice, setOwnerToken],
  )

  const settleVerification = useCallback(
    (taskId: string) => fetchVerification(csrf, taskId),
    [csrf],
  )

  const [calcTaskId, setCalcTaskId] = useState<string | null>(null)
  const calcState = useTaskSettle<VerificationView>({
    csrf,
    taskId: calcTaskId,
    settle: settleVerification,
    onAuthFailure: handleAuthFailure,
  })

  const confirmCancel = useCallback(async () => {
    if (!cancelTarget) return
    setCancelBusy(true)
    try {
      await cancelTask(csrf, cancelTarget.task_id, `ui-${Date.now()}`)
      toast(`Cancellation requested for ${cancelTarget.task_id}`, 'success')
      setCancelTarget(null)
      void tasks.refetch()
    } catch (err) {
      if (isPilotAuthFailure(err)) {
        handleAuthFailure(pilotErrorMessage(err))
      }
      toast(`Cancellation failed: ${pilotErrorMessage(err)}`, 'danger')
    } finally {
      setCancelBusy(false)
    }
  }, [cancelTarget, csrf, handleAuthFailure, tasks, toast])

  const submitCalc = useCallback(async () => {
    const requestId = calcRequestId.trim()
    const expr = expression.trim()
    if (!requestId || !selectedThread || !expr || submitting) return
    setSubmitting(true)
    setCalcError(null)
    try {
      const view = await submitCalculatorTask(csrf, {
        request_id: requestId,
        thread_id: selectedThread,
        expression: expr,
      })
      setCalcTaskId(view.task_id)
      setCalcRequestId(`ui-${Date.now()}`)
      setExpression('')
      toast('Calculator task submitted', 'success')
    } catch (err) {
      if (isPilotAuthFailure(err)) {
        handleAuthFailure(pilotErrorMessage(err))
      }
      setCalcError(pilotErrorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }, [
    calcRequestId,
    csrf,
    expression,
    handleAuthFailure,
    selectedThread,
    submitting,
    toast,
  ])

  const list = tasks.data?.tasks ?? []
  const threadList = threads.data?.threads ?? []
  const calcView = calcState.view
  const calcReceipt = calcState.settled
  const calcPollError = calcState.error

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
          aria-label="Refresh tasks"
        >
          <RefreshCw size={13} className={tasks.isFetching ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      <PilotGate>
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {/* Task list */}
          <section className="mc-panel overflow-hidden">
            {tasks.isLoading ? (
              <LoadingBlock />
            ) : tasks.error ? (
              <div className="p-4">
                <PilotErrorNote error={tasks.error} />
              </div>
            ) : list.length === 0 ? (
              <EmptyState
                title="No tasks yet"
                detail="Submit a task through the owner pilot API and it will appear here."
              />
            ) : (
              <ul className="divide-y divide-line">
                {list.map((task) => (
                  <li
                    key={task.task_id}
                    className={`flex items-center gap-3 px-5 py-3${verifyTaskId === task.task_id ? ' bg-ink-1' : ''}`}
                  >
                    <FolderKanban size={15} className="flex-none text-text-3" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm text-text-1">{task.request_id}</div>
                      <div className="mc-value text-[11px] text-text-3">{task.task_id}</div>
                    </div>
                    <StateChip state={task.state} />
                    <button
                      type="button"
                      className="mc-btn flex-none"
                      onClick={() =>
                        setVerifyTaskId((current) =>
                          current === task.task_id ? null : task.task_id,
                        )
                      }
                      aria-pressed={verifyTaskId === task.task_id}
                    >
                      {verifyTaskId === task.task_id ? 'Hide receipt' : 'Verify'}
                    </button>
                    {task.state === 'RUNNING' || task.state === 'ADMITTED' ? (
                      <button
                        type="button"
                        className="mc-btn mc-btn-danger flex-none"
                        onClick={() => setCancelTarget(task)}
                      >
                        Cancel
                      </button>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* Verification + calculator */}
          <div className="space-y-4">
            <section className="mc-panel flex flex-col p-5">
              <div className="mb-4 flex items-center justify-between gap-3">
                <h3 className="flex items-center gap-2 text-sm font-semibold text-text-1">
                  <ShieldCheck size={14} className="text-text-3" /> Task verification
                </h3>
                <div className="flex items-center gap-2">
                  <span className="mc-label">GET /api/pilot/tasks/:id/verification</span>
                  <button
                    type="button"
                    className="mc-icon-btn"
                    aria-label="Refresh verification"
                    onClick={() => verification.refetch()}
                    disabled={!verifyTaskId || verification.isFetching}
                  >
                    <RefreshCw
                      size={13}
                      className={verification.isFetching ? 'animate-spin' : ''}
                    />
                  </button>
                </div>
              </div>

              {!verifyTaskId ? (
                <p className="text-xs leading-relaxed text-text-3">
                  Choose Verify on a task to read its independent calculator receipt.
                </p>
              ) : verification.isLoading ? (
                <LoadingBlock />
              ) : verification.error ? (
                <PilotErrorNote error={verification.error} />
              ) : verification.data ? (
                <VerificationBody data={verification.data} />
              ) : null}
            </section>

            <section className="mc-panel flex flex-col p-5">
              <div className="mb-4 flex items-center justify-between gap-3">
                <h3 className="flex items-center gap-2 text-sm font-semibold text-text-1">
                  <Calculator size={14} className="text-text-3" /> Calculator task
                </h3>
                <span className="mc-label">POST /api/pilot/calculator-tasks</span>
              </div>

              <form
                className="space-y-3"
                onSubmit={(e) => {
                  e.preventDefault()
                  void submitCalc()
                }}
              >
                <div>
                  <label htmlFor="calc-request" className="mc-label mb-1 block">
                    Request ID
                  </label>
                  <input
                    id="calc-request"
                    className="mc-input"
                    value={calcRequestId}
                    onChange={(e) => setCalcRequestId(e.target.value)}
                    autoComplete="off"
                  />
                </div>
                <div>
                  <label htmlFor="calc-thread" className="mc-label mb-1 block">
                    Thread
                  </label>
                  <select
                    id="calc-thread"
                    className="mc-input"
                    value={selectedThread}
                    onChange={(e) => setSelectedThread(e.target.value)}
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
                  <label htmlFor="calc-expression" className="mc-label mb-1 block">
                    Expression
                  </label>
                  <input
                    id="calc-expression"
                    className="mc-input"
                    value={expression}
                    onChange={(e) => setExpression(e.target.value)}
                    placeholder="2 + 3 * 4"
                    autoComplete="off"
                  />
                  <p className="mt-1 text-[11px] text-text-3">
                    Bounded integer arithmetic: + - * and parentheses.
                  </p>
                </div>
                <button
                  type="submit"
                  className="mc-btn mc-btn-primary w-full justify-center"
                  aria-busy={submitting}
                  disabled={
                    submitting ||
                    !csrf ||
                    !selectedThread ||
                    !expression.trim() ||
                    !calcRequestId.trim()
                  }
                >
                  <Calculator size={13} /> Submit calculator task
                </button>
              </form>

              {calcError || calcPollError ? (
                <div className="mt-3" role="alert">
                  <PilotErrorNote error={calcError ?? calcPollError} />
                </div>
              ) : null}

              {calcView ? (
                <div className="mt-4 border-t border-line pt-4" aria-live="polite">
                  <div className="mc-label mb-2">Task result</div>
                  <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
                    <PanelRow label="Task">{calcView.task_id}</PanelRow>
                    <PanelRow label="State">
                      <StateChip state={calcView.state} />
                    </PanelRow>
                    <PanelRow label="Replayed">{calcView.replayed ? 'yes' : 'no'}</PanelRow>
                    <PanelRow label="Uncertain">{calcView.uncertain ? 'yes' : 'no'}</PanelRow>
                    <PanelRow label="Error code">{calcView.error_code ?? '—'}</PanelRow>
                    <PanelRow label="Usage">
                      {`${calcView.usage.model_attempts} model / ${calcView.usage.tool_attempts} tool`}
                    </PanelRow>
                  </dl>
                  {calcView.response ? (
                    <div className="mt-3">
                      <div className="mc-label mb-1">Response</div>
                      <div className="whitespace-pre-wrap rounded-control border border-line bg-ink-1 px-3 py-2 text-sm text-text-1">
                        {calcView.response}
                      </div>
                    </div>
                  ) : null}
                  {calcView.assessment ? (
                    <div className="mt-3">
                      <div className="mc-label mb-1">Assessment</div>
                      <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2">
                        <PanelRow label="Execution">
                          {calcView.assessment.execution_status}
                        </PanelRow>
                        <PanelRow label="Verification">
                          {calcView.assessment.verification}
                        </PanelRow>
                        <PanelRow label="Risk">{calcView.assessment.risk_status}</PanelRow>
                        <PanelRow label="Independent">
                          {calcView.assessment.independently_verified ? 'yes' : 'no'}
                        </PanelRow>
                      </dl>
                    </div>
                  ) : null}
                </div>
              ) : null}

              {calcReceipt ? (
                <div className="mt-4 border-t border-line pt-4">
                  <div className="mc-label mb-2">Verification receipt</div>
                  <VerificationBody data={calcReceipt} />
                </div>
              ) : null}
            </section>
          </div>
        </div>

        <ConfirmDialog
          open={cancelTarget !== null}
          title="Cancel this task?"
          body={`Request cancellation of task ${cancelTarget?.task_id ?? ''}. Cancellation is task-scoped and recorded in the audit log; it may not take effect instantly.`}
          confirmLabel="Request cancellation"
          busy={cancelBusy}
          onConfirm={() => void confirmCancel()}
          onCancel={() => setCancelTarget(null)}
        />
      </PilotGate>
    </div>
  )
}

function VerificationBody({ data }: { data: VerificationView }) {
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
        <PanelRow label="Task">{data.task_id}</PanelRow>
        <PanelRow label="Execution">{data.execution_state}</PanelRow>
        <PanelRow label="Request">{data.request_id}</PanelRow>
        <PanelRow label="Thread">{data.thread_id}</PanelRow>
        <PanelRow label="Reason">
          <Chip tone={stateTone(data.reason === 'available' ? 'passed' : 'uncertain')}>
            {data.reason}
          </Chip>
        </PanelRow>
      </dl>

      {data.receipt ? (
        <div className="border-t border-line pt-4">
          <div className="mc-label mb-2">Calculator receipt</div>
          <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2">
            <PanelRow label="Status">
              <Chip tone={stateTone(data.receipt.status)}>{data.receipt.status}</Chip>
            </PanelRow>
            <PanelRow label="Reason">{data.receipt.reason}</PanelRow>
            <PanelRow label="Expression">
              <span className="mc-value text-[11.5px] text-text-1">
                {data.receipt.expression}
              </span>
            </PanelRow>
            <PanelRow label="Expected">{data.receipt.expected}</PanelRow>
            <PanelRow label="Observed">{data.receipt.observed ?? '—'}</PanelRow>
            <PanelRow label="Method">{data.receipt.method}</PanelRow>
          </dl>
          <div className="mt-3 space-y-1 text-[10.5px] text-text-3">
            <div className="mc-value">criteria {data.receipt.criteria_sha256}</div>
            <div className="mc-value">output {data.receipt.output_sha256}</div>
          </div>
        </div>
      ) : (
        <p className="border-t border-line pt-4 text-xs leading-relaxed text-text-3">
          No receipt is bound to this task (reason: {data.reason}). A receipt is only issued
          for admitted calculator expressions on completed tasks.
        </p>
      )}
    </div>
  )
}
