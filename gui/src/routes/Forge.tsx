import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ClipboardList, RefreshCw, Shield } from 'lucide-react'
import {
  dispatchForgeJob,
  fetchForgeDispatch,
  fetchForgeJob,
  fetchForgeMetrics,
  fetchForgeStatus,
  submitForgeJob,
  useCsrf,
  type ForgeDispatchResult,
  type ForgeJob,
  type ForgeJobInput,
} from '../api/pilot'
import { useAuth } from '../stores/auth'
import {
  ConfirmDialog,
  EmptyState,
  LoadingBlock,
  PageHeader as Header,
  Notice,
  Row,
} from '../components/ui'
import { useToast } from '../components/toast'
import {
  Chip,
  PilotErrorNote,
  PilotGate,
  StateChip,
} from './PilotShared'

const HONESTY_NOTE = 'dispatch runs an external agent; no ship/merge/release from this UI'

function bound(value: string, limit = 240): string {
  const text = value.trim()
  return text.length > limit ? `${text.slice(0, limit)}…` : text
}

function listOrDash(values: string[] | undefined): string {
  return values && values.length > 0 ? values.join(', ') : '—'
}

function rate(value: number): string {
  return `${(value * 100).toFixed(0)}%`
}

function blockedCount(blocked: number | string[]): number {
  return Array.isArray(blocked) ? blocked.length : blocked
}

function shortId(jobId: string): string {
  return jobId.slice(0, 8)
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-control border border-line bg-ink-1 px-3 py-2">
      <div className="mc-label">{label}</div>
      <div className="mc-value mt-1 text-sm text-text-1">{value}</div>
    </div>
  )
}

function EvidenceRefs({ job }: { job: ForgeJob }) {
  const refs = [
    ...(job.report_ref ? [`report ${job.report_ref}`] : []),
    ...(job.report_id ? [`report id ${job.report_id}`] : []),
    ...(job.merge_report_id ? [`merge report ${job.merge_report_id}`] : []),
    ...(job.artifact_sha256 ? [`artifact ${job.artifact_sha256}`] : []),
    ...(job.evidence_refs ?? []).map((ref) => `evidence ${ref}`),
  ]
  return <Row label="Evidence / report refs" value={listOrDash(refs)} stacked />
}

export function ForgePage() {
  const token = useAuth((s) => s.ownerToken)
  const csrf = useCsrf()
  const toast = useToast()
  const queryClient = useQueryClient()

  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [lastResult, setLastResult] = useState<ForgeDispatchResult | null>(null)
  const [requestId, setRequestId] = useState<string>(() => crypto.randomUUID())
  const [title, setTitle] = useState('')
  const [task, setTask] = useState('')
  const [language, setLanguage] = useState('')
  const [acceptance, setAcceptance] = useState('')
  const [testPaths, setTestPaths] = useState('')

  const status = useQuery({
    queryKey: ['pilot-forge-status', token],
    queryFn: () => fetchForgeStatus(csrf),
    enabled: Boolean(token && csrf),
    refetchInterval: 12_000,
  })
  const metrics = useQuery({
    queryKey: ['pilot-forge-metrics', token],
    queryFn: () => fetchForgeMetrics(csrf),
    enabled: Boolean(token && csrf),
    refetchInterval: 12_000,
  })
  const detail = useQuery({
    queryKey: ['pilot-forge-job', selectedJobId, token],
    queryFn: () => fetchForgeJob(csrf, selectedJobId as string),
    enabled: Boolean(token && csrf && selectedJobId),
  })
  const dispatch = useQuery({
    queryKey: ['pilot-forge-dispatch', token],
    queryFn: () => fetchForgeDispatch(csrf),
    enabled: Boolean(token && csrf),
    refetchInterval: 5_000,
  })

  const runDispatch = useMutation({
    mutationFn: () => dispatchForgeJob(csrf),
    onSuccess: (result) => {
      setLastResult(result)
      setConfirmOpen(false)
      toast(
        `${result.replayed ? 'Replayed' : 'Started'} dispatch · ${result.caveats.join(', ') || HONESTY_NOTE}`,
        'success',
      )
      void queryClient.invalidateQueries({ queryKey: ['pilot-forge-dispatch'] })
      void queryClient.invalidateQueries({ queryKey: ['pilot-forge-status'] })
      void queryClient.invalidateQueries({ queryKey: ['pilot-forge-metrics'] })
    },
    onError: () => toast('Forge dispatch failed', 'danger'),
  })

  const submit = useMutation({
    mutationFn: (input: ForgeJobInput) => submitForgeJob(csrf, input),
    onSuccess: (result) => {
      toast(
        `${result.replayed ? 'Replayed' : 'Queued'} job ${shortId(result.job.job_id)} · ${HONESTY_NOTE}`,
        'success',
      )
      setRequestId(crypto.randomUUID())
      setTitle('')
      setTask('')
      setLanguage('')
      setAcceptance('')
      setTestPaths('')
      void queryClient.invalidateQueries({ queryKey: ['pilot-forge-status'] })
      void queryClient.invalidateQueries({ queryKey: ['pilot-forge-metrics'] })
    },
    onError: () => toast('Forge job submission failed', 'danger'),
  })

  const statusCaveats = status.data?.caveats ?? []
  const metricCaveats = metrics.data?.caveats ?? []
  const caveats = [...new Set([...statusCaveats, ...metricCaveats])]
  const jobs = status.data?.status.recent ?? []
  const job = detail.data?.job
  const valid = Boolean(csrf && requestId.trim() && title.trim() && task.trim())
  const canDispatch = Boolean(token && csrf)
  const dispatchRunning = Boolean(dispatch.data?.running)
  const dispatchUnavailable = Boolean(dispatch.error)
  const dispatchDisabled =
    !canDispatch || dispatchRunning || dispatchUnavailable || runDispatch.isPending
  const last = dispatch.data?.last ?? null

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!valid || submit.isPending) return
    submit.mutate({
      request_id: requestId.trim(),
      title: title.trim(),
      task: task.trim(),
      language: language.trim() || undefined,
      acceptance: acceptance
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean),
      test_paths: testPaths
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean),
    })
  }

  return (
    <div className="space-y-4">
      <Header
        title="Forge"
        detail="Governed coding jobs: status, metrics, bounded submission and job detail. Owner token required."
      />
      <Notice tone="warning">{HONESTY_NOTE}</Notice>

      <PilotGate>
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <section className="mc-panel p-5" aria-label="Forge status summary">
            <div className="mb-4 flex items-center justify-between gap-3">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-text-1">
                <Shield size={14} className="text-text-3" /> Status summary
              </h3>
              <div className="flex items-center gap-2">
                <span className="mc-label">GET /api/pilot/forge/status</span>
                <button
                  type="button"
                  className="mc-icon-btn"
                  aria-label="Refresh Forge status"
                  onClick={() => void status.refetch()}
                  disabled={status.isFetching}
                >
                  <RefreshCw size={13} className={status.isFetching ? 'animate-spin' : ''} />
                </button>
              </div>
            </div>
            {status.isPending ? (
              <LoadingBlock />
            ) : status.error ? (
              <PilotErrorNote error={status.error} />
            ) : (
              <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                <Metric label="Total jobs" value={String(status.data?.status.total ?? 0)} />
                <Metric
                  label="Blocked"
                  value={String(blockedCount(status.data?.status.blocked ?? 0))}
                />
                {Object.entries(status.data?.status.by_state ?? {}).length === 0 ? (
                  <Metric label="States" value="—" />
                ) : (
                  Object.entries(status.data?.status.by_state ?? {})
                    .sort(([a], [b]) => a.localeCompare(b))
                    .map(([state, count]) => (
                      <Metric key={state} label={state} value={String(count)} />
                    ))
                )}
              </dl>
            )}
          </section>

          <section className="mc-panel p-5" aria-label="Forge metrics">
            <div className="mb-4 flex items-center justify-between gap-3">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-text-1">
                <ClipboardList size={14} className="text-text-3" /> Operational metrics
              </h3>
              <div className="flex items-center gap-2">
                <span className="mc-label">GET /api/pilot/forge/metrics</span>
                <button
                  type="button"
                  className="mc-icon-btn"
                  aria-label="Refresh Forge metrics"
                  onClick={() => void metrics.refetch()}
                  disabled={metrics.isFetching}
                >
                  <RefreshCw size={13} className={metrics.isFetching ? 'animate-spin' : ''} />
                </button>
              </div>
            </div>
            {metrics.isPending ? (
              <LoadingBlock />
            ) : metrics.error ? (
              <PilotErrorNote error={metrics.error} />
            ) : (
              <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                <Metric label="Jobs" value={String(metrics.data?.metrics.total_jobs ?? 0)} />
                <Metric label="Passed" value={String(metrics.data?.metrics.passed ?? 0)} />
                <Metric
                  label="First-pass rate"
                  value={rate(metrics.data?.metrics.first_pass_rate ?? 0)}
                />
                <Metric label="Rework rate" value={rate(metrics.data?.metrics.rework_rate ?? 0)} />
                <Metric
                  label="Average attempts"
                  value={(metrics.data?.metrics.avg_attempts ?? 0).toFixed(2)}
                />
                <Metric label="Blocked" value={String(metrics.data?.metrics.blocked ?? 0)} />
                <div className="col-span-2 sm:col-span-3">
                  <Metric
                    label="Verdict mix"
                    value={
                      Object.entries(metrics.data?.metrics.verdict_mix ?? {})
                        .sort(([a], [b]) => a.localeCompare(b))
                        .map(([verdict, count]) => `${verdict}: ${count}`)
                        .join(', ') || '—'
                    }
                  />
                </div>
              </dl>
            )}
          </section>
        </div>

        <section className="mc-panel px-5 py-4" aria-label="Caveats">
          <div className="mc-label mb-2">Caveats</div>
          {caveats.length > 0 ? (
            <ul className="space-y-1 text-xs text-amber">
              {caveats.map((caveat) => (
                <li key={caveat}>{caveat}</li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-text-3">
              No caveats reported by the backend. {HONESTY_NOTE}.
            </p>
          )}
        </section>

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <section className="mc-panel flex flex-col p-5" aria-label="Forge dispatch">
            <div className="mb-4 flex items-center justify-between gap-3">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-text-1">
                <Shield size={14} className="text-text-3" /> Forge dispatch
              </h3>
              <span className="mc-label">POST /api/pilot/forge/dispatch</span>
            </div>
            <p className="mb-3 text-xs leading-relaxed text-text-3">
              Dispatch the next queued job. Runs an external agent; no ship, merge or release.
            </p>
            <div className="mb-3 flex flex-wrap items-center gap-2">
              {dispatchRunning ? <Chip tone="amber">running</Chip> : null}
              {dispatchUnavailable ? <Chip tone="amber">unavailable</Chip> : null}
              {canDispatch ? (
                <Chip tone="green">owner token ready</Chip>
              ) : (
                <Chip tone="amber">CSRF session required</Chip>
              )}
            </div>
            <button
              type="button"
              className="mc-btn mc-btn-primary"
              onClick={() => setConfirmOpen(true)}
              disabled={dispatchDisabled}
              aria-busy={runDispatch.isPending}
            >
              <Shield size={13} /> Dispatch next queued job
            </button>
            <p className="mt-2 text-[11px] text-text-3">
              {!canDispatch
                ? 'Add the owner token and CSRF session in Settings to dispatch.'
                : dispatchUnavailable
                  ? 'Dispatch is unavailable in this runtime; no job was started.'
                  : dispatchRunning
                    ? 'A dispatch is already running.'
                    : HONESTY_NOTE}
            </p>
            {runDispatch.error ? (
              <div className="mt-3" role="alert">
                <PilotErrorNote error={runDispatch.error} />
              </div>
            ) : null}
          </section>

          <section className="mc-panel flex flex-col p-5" aria-label="Last dispatch">
            <div className="mb-4 flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold text-text-1">Last dispatch</h3>
              <div className="flex items-center gap-2">
                <span className="mc-label">GET /api/pilot/forge/dispatch</span>
                <button
                  type="button"
                  className="mc-icon-btn"
                  aria-label="Refresh dispatch status"
                  title="Refresh dispatch status"
                  onClick={() => void dispatch.refetch()}
                  disabled={dispatch.isFetching}
                >
                  <RefreshCw
                    size={13}
                    className={dispatch.isFetching ? 'animate-spin' : ''}
                    aria-hidden="true"
                  />
                </button>
              </div>
            </div>
            {dispatchUnavailable ? (
              <p className="text-xs leading-relaxed text-text-3">
                Dispatch status is unavailable in this runtime. Nothing was started.
              </p>
            ) : last ? (
              <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2" aria-live="polite">
                <Row label="Result">
                  {last.ok ? <Chip tone="green">ok</Chip> : <Chip tone="red">failed</Chip>}
                </Row>
                <Row label="Job" value={last.job_id ?? '—'} />
                <Row label="State" value={last.state ?? '—'} />
                <Row label="Error" value={last.error ?? '—'} />
              </dl>
            ) : (
              <p className="text-xs leading-relaxed text-text-3">
                No dispatch has been recorded by this runtime.
              </p>
            )}
            {lastResult ? (
              <div className="mt-3 border-t border-line pt-3 text-[11px] text-text-3" aria-live="polite">
                Accepted here: started {String(lastResult.started)} · replayed{' '}
                {String(lastResult.replayed)} ·{' '}
                {lastResult.caveats.join(', ') || 'no caveats'}
              </div>
            ) : null}
          </section>
        </div>

        <ConfirmDialog
          open={confirmOpen}
          title="Dispatch next queued job?"
          body="This runs an external agent on the next queued Forge job. No ship, merge or release is performed."
          confirmLabel="Confirm dispatch"
          busy={runDispatch.isPending}
          onConfirm={() => runDispatch.mutate()}
          onCancel={() => setConfirmOpen(false)}
        />

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <section className="mc-panel overflow-hidden" aria-label="Recent Forge jobs">
            <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-4">
              <h3 className="text-sm font-semibold text-text-1">Recent jobs</h3>
              <button
                type="button"
                className="mc-btn"
                onClick={() => void status.refetch()}
                disabled={status.isFetching}
              >
                <RefreshCw size={13} className={status.isFetching ? 'animate-spin' : ''} />
                Refresh jobs
              </button>
            </div>
            {status.isPending ? (
              <LoadingBlock />
            ) : status.error ? (
              <div className="p-4">
                <PilotErrorNote error={status.error} />
              </div>
            ) : jobs.length === 0 ? (
              <EmptyState
                title="No jobs yet"
                detail="Queue a bounded job with the form, then use Forge dispatch when it is available."
              />
            ) : (
              <ul className="divide-y divide-line">
                {jobs.map((item) => (
                  <li key={item.job_id} className="px-5 py-3">
                    <div className="flex flex-wrap items-center gap-3">
                      <button
                        type="button"
                        className="mc-btn flex-none"
                        aria-pressed={selectedJobId === item.job_id}
                        onClick={() =>
                          setSelectedJobId((current) =>
                            current === item.job_id ? null : item.job_id,
                          )
                        }
                      >
                        {shortId(item.job_id)}
                      </button>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm text-text-1">{item.title}</div>
                        <div className="mc-value text-[11px] text-text-3">
                          {`${item.attempts} attempts · ${item.updated_at || '—'}`}
                        </div>
                      </div>
                      <StateChip state={item.state} />
                    </div>
                    {item.failure_detail ? (
                      <p className="mt-2 text-[11px] text-amber">
                        {bound(item.failure_detail)}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="mc-panel flex flex-col p-5" aria-label="Forge job detail">
            <div className="mb-4 flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold text-text-1">Job detail</h3>
              <div className="flex items-center gap-2">
                <span className="mc-label">GET /api/pilot/forge/jobs/:id</span>
                <button
                  type="button"
                  className="mc-icon-btn"
                  aria-label="Refresh job detail"
                  onClick={() => void detail.refetch()}
                  disabled={!selectedJobId || detail.isFetching}
                >
                  <RefreshCw size={13} className={detail.isFetching ? 'animate-spin' : ''} />
                </button>
              </div>
            </div>
            {!selectedJobId ? (
              <p className="text-xs leading-relaxed text-text-3">
                Select a job to read its acceptance criteria, test paths and evidence or report
                references.
              </p>
            ) : detail.isPending ? (
              <LoadingBlock />
            ) : detail.error ? (
              <PilotErrorNote error={detail.error} />
            ) : job ? (
              <div className="space-y-4" aria-live="polite">
                <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
                  <Row label="Job" value={job.job_id} stacked />
                  <Row label="State">
                    <StateChip state={job.state} />
                  </Row>
                  <Row label="Attempts" value={String(job.attempts)} />
                  <Row label="Updated">{job.updated_at || '—'}</Row>
                  <Row label="Gate">
                    {job.gate ? <Chip tone="muted">{job.gate}</Chip> : '—'}
                  </Row>
                  <Row label="Acceptance" value={listOrDash(job.acceptance)} stacked />
                  <Row label="Test paths" value={listOrDash(job.test_paths)} stacked />
                  <Row label="Changed paths" value={listOrDash(job.changed_paths)} stacked />
                  <EvidenceRefs job={job} />
                </dl>
                {job.failure_detail ? (
                  <div>
                    <div className="mc-label mb-1">Failure detail</div>
                    <div className="whitespace-pre-wrap rounded-control border border-line bg-ink-1 px-3 py-2 text-xs text-amber">
                      {bound(job.failure_detail)}
                    </div>
                  </div>
                ) : null}
              </div>
            ) : null}
          </section>
        </div>

        <section className="mc-panel flex flex-col p-5" aria-label="Submit Forge job">
          <div className="mb-4 flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold text-text-1">Queue a bounded job</h3>
            <span className="mc-label">POST /api/pilot/forge/jobs</span>
          </div>
          <form className="grid grid-cols-1 gap-3 sm:grid-cols-2" onSubmit={onSubmit}>
            <div>
              <label htmlFor="forge-request" className="mc-label mb-1 block">
                Request ID
              </label>
              <input
                id="forge-request"
                className="mc-input"
                value={requestId}
                onChange={(event) => setRequestId(event.target.value)}
                autoComplete="off"
                spellCheck={false}
              />
            </div>
            <div>
              <label htmlFor="forge-language" className="mc-label mb-1 block">
                Language (optional)
              </label>
              <input
                id="forge-language"
                className="mc-input"
                value={language}
                onChange={(event) => setLanguage(event.target.value)}
                placeholder="python"
                autoComplete="off"
              />
            </div>
            <div className="sm:col-span-2">
              <label htmlFor="forge-title" className="mc-label mb-1 block">
                Title
              </label>
              <input
                id="forge-title"
                className="mc-input"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                autoComplete="off"
              />
            </div>
            <div className="sm:col-span-2">
              <label htmlFor="forge-task" className="mc-label mb-1 block">
                Task
              </label>
              <textarea
                id="forge-task"
                className="mc-textarea"
                value={task}
                onChange={(event) => setTask(event.target.value)}
                rows={3}
                placeholder="Describe the bounded change"
              />
            </div>
            <div>
              <label htmlFor="forge-acceptance" className="mc-label mb-1 block">
                Acceptance (comma-separated, optional)
              </label>
              <input
                id="forge-acceptance"
                className="mc-input"
                value={acceptance}
                onChange={(event) => setAcceptance(event.target.value)}
                autoComplete="off"
              />
            </div>
            <div>
              <label htmlFor="forge-test-paths" className="mc-label mb-1 block">
                Test paths (comma-separated, optional)
              </label>
              <input
                id="forge-test-paths"
                className="mc-input"
                value={testPaths}
                onChange={(event) => setTestPaths(event.target.value)}
                autoComplete="off"
              />
            </div>
            <div className="sm:col-span-2">
              <button
                type="submit"
                className="mc-btn mc-btn-primary w-full justify-center"
                disabled={!valid || submit.isPending}
                aria-busy={submit.isPending}
              >
                <Shield size={13} /> Queue Forge job
              </button>
              <p className="mt-2 text-[11px] text-text-3">{HONESTY_NOTE}</p>
            </div>
          </form>
          {submit.error ? (
            <div className="mt-3" role="alert">
              <PilotErrorNote error={submit.error} />
            </div>
          ) : null}
        </section>
      </PilotGate>
    </div>
  )
}
