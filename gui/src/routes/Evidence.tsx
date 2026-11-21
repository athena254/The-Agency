import { useQuery } from '@tanstack/react-query'
import { RefreshCw, ScrollText } from 'lucide-react'
import { pilotErrorMessage, useCsrf } from '../api/pilot'
import { fetchObserveEvidence, type FindingView, type RiskView } from '../api/observe'
import {
  Badge,
  EmptyState,
  Notice,
  PageHeader as Header,
  StateBadge,
} from '../components/ui'
import { formatAge, formatDateTime } from '../lib/format'
import { useAuth } from '../stores/auth'
import { PilotGate } from './PilotShared'

/*
  Read-only view over the evidence store and legacy risk engine (observe
  surface). Risk scores are heuristics and are always labelled as such; a
  finding is never presented as verified without an independent check.
*/

export function EvidencePage() {
  const token = useAuth((state) => state.ownerToken)
  const csrf = useCsrf()
  const evidence = useQuery({
    queryKey: ['observe-evidence', token],
    queryFn: () => fetchObserveEvidence(csrf),
    enabled: Boolean(token && csrf),
    refetchInterval: 30_000,
  })

  const view = evidence.data

  return (
    <div className="space-y-4">
      <Header
        title="Evidence"
        detail="Findings, execution receipts and risk assessments with honest verification status."
      />

      <Notice tone="warning">
        <ScrollText size={14} className="mt-0.5 flex-none" aria-hidden="true" />
        <span>
          Risk scoring is the legacy heuristic engine — indicative only, never authorization.
          Writes to the evidence store remain peer-authority gated.
        </span>
      </Notice>

      <div className="flex items-center justify-end gap-2">
        <span className="mc-value text-xs text-text-3" aria-live="polite">
          {view?.available
            ? `${view.total} findings · ${view.assessed} assessed · max impact ${
                view.max_score === null ? '—' : view.max_score.toFixed(3)
              }`
            : ''}
        </span>
        <button
          type="button"
          className="mc-icon-btn"
          onClick={() => evidence.refetch()}
          disabled={evidence.isFetching || !token}
          aria-label="Refresh evidence"
        >
          <RefreshCw size={13} className={evidence.isFetching ? 'animate-spin' : ''} />
        </button>
      </div>

      {view?.available ? <EvidenceSummary view={view} /> : null}

      <PilotGate>
        <EvidenceBody
          view={evidence.data}
          error={evidence.error}
          pending={evidence.isPending}
          retry={() => evidence.refetch()}
        />
      </PilotGate>
    </div>
  )
}

function EvidenceSummary({ view }: { view: RiskView }) {
  const rows: [string, Record<string, number>][] = [
    ['Severity', view.by_severity],
    ['Verification', view.by_verification_status],
    ['Risk category', view.by_risk_category],
  ]
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      {rows.map(([label, counts]) => (
        <div key={label} className="mc-panel px-4 py-3">
          <div className="mc-label mb-2">{label}</div>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(counts).length === 0 ? (
              <span className="mc-value text-[11px] text-text-3">none recorded</span>
            ) : (
              Object.entries(counts).map(([key, count]) => (
                <Badge key={key} tone="muted">
                  {key} · {count}
                </Badge>
              ))
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

function EvidenceBody({
  view,
  error,
  pending,
  retry,
}: {
  view: RiskView | undefined
  error: unknown
  pending: boolean
  retry: () => void
}) {
  if (pending) {
    return (
      <div className="mc-panel px-5 py-10 text-center text-sm text-text-3" role="status">
        Loading findings…
      </div>
    )
  }
  if (error) {
    return (
      <div className="mc-notice mc-notice-warning" role="alert">
        <span>Evidence unavailable: {pilotErrorMessage(error)}</span>
        <button type="button" className="mc-btn mc-btn-primary flex-none" onClick={retry}>
          Retry
        </button>
      </div>
    )
  }
  if (!view?.available) {
    return (
      <div className="mc-panel px-5 py-4">
        <p className="mc-caption">Evidence unavailable: {view?.reason ?? 'not collected'}</p>
      </div>
    )
  }
  if (view.findings.length === 0) {
    return (
      <div className="mc-panel">
        <EmptyState
          title="No findings recorded"
          detail="Execution findings and risk assessments will appear here as tasks run."
        />
      </div>
    )
  }

  return (
    <ul className="space-y-3">
      {view.findings.map((finding) => (
        <FindingRow key={finding.id} finding={finding} />
      ))}
    </ul>
  )
}

function FindingRow({ finding }: { finding: FindingView }) {
  return (
    <li className="mc-panel px-5 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="truncate text-sm text-text-1" title={finding.evidence}>
          {finding.target}
        </span>
        {finding.severity ? <StateBadge state={finding.severity} /> : null}
        <StateBadge state={finding.verification_status} />
        <Badge tone="muted">{finding.strongest_level ?? 'LEVEL_0_HYPOTHESIS'}</Badge>
        <span className="mc-value ml-auto text-[11px] text-text-3">
          {finding.risk_category} · exposure {finding.risk_exposure.toFixed(2)}
        </span>
      </div>
      <p className="mc-body mt-2 line-clamp-2 text-xs">{finding.evidence}</p>
      <div className="mc-caption mt-2 flex flex-wrap items-center gap-x-4 gap-y-1">
        {finding.affected_component ? <span>component: {finding.affected_component}</span> : null}
        <span>confidence {finding.confidence.toFixed(2)}</span>
        <span>evidence entries {finding.evidence_count}</span>
        {finding.timestamp ? (
          <span
            title={formatDateTime(finding.timestamp, true)}
          >
            {formatAge(finding.timestamp)} ago
          </span>
        ) : null}
        {finding.provenance_source ? <span>source: {finding.provenance_source}</span> : null}
      </div>
    </li>
  )
}
