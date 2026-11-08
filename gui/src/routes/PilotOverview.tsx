import { useQuery } from '@tanstack/react-query'
import { Gauge, Wallet } from 'lucide-react'
import { fetchCapabilities, fetchSpending, useCsrf } from '../api/pilot'
import { useAuth } from '../stores/auth'
import { Chip, LoadingBlock, PanelRow, PilotErrorNote } from './PilotShared'

export function CapabilitiesPanel() {
  const token = useAuth((s) => s.ownerToken)
  const csrf = useCsrf()
  const caps = useQuery({
    queryKey: ['pilot-capabilities', token],
    queryFn: () => fetchCapabilities(csrf),
    enabled: Boolean(token),
    refetchInterval: 30_000,
  })
  const data = caps.data

  return (
    <div className="mc-panel flex flex-col p-5">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-text-1">
          <Gauge size={14} className="text-text-3" /> Capabilities
        </h3>
        <span className="mc-label">GET /api/pilot/capabilities</span>
      </div>

      {caps.isLoading ? (
        <LoadingBlock />
      ) : caps.error ? (
        <PilotErrorNote error={caps.error} />
      ) : !data ? null : (
        <>
          <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
            <PanelRow label="Mode">{data.mode}</PanelRow>
            <PanelRow label="Enabled">{yesNo(data.enabled)}</PanelRow>
            <PanelRow label="Ready">{yesNo(data.ready)}</PanelRow>
            <PanelRow label="Provider ready">{yesNo(data.provider_ready)}</PanelRow>
            <PanelRow label="Uncertainty">
              {data.uncertainty_blocked ? (
                <span className="text-amber">blocked</span>
              ) : (
                <span className="text-green">clear</span>
              )}
            </PanelRow>
          </dl>

          <div className="mt-4">
            <div className="mc-label mb-2">Tools</div>
            {data.tools.length === 0 ? (
              <span className="text-xs text-text-3">none reported</span>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {data.tools.map((tool) => (
                  <Chip key={tool} tone="signal">
                    {tool}
                  </Chip>
                ))}
              </div>
            )}
          </div>

          <div className="mt-3">
            <div className="mc-label mb-2">Disabled</div>
            {data.disabled.length === 0 ? (
              <span className="text-xs text-text-3">none reported</span>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {data.disabled.map((tool) => (
                  <Chip key={tool}>{tool}</Chip>
                ))}
              </div>
            )}
          </div>

          <div className="mt-4 border-t border-line pt-4">
            <div className="mc-label mb-2">Limits</div>
            <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2">
              {Object.entries(data.limits).map(([key, value]) => (
                <PanelRow key={key} label={key.replace(/_/g, ' ')}>
                  {value}
                </PanelRow>
              ))}
            </dl>
          </div>
        </>
      )}
    </div>
  )
}

export function SpendingPanel() {
  const token = useAuth((s) => s.ownerToken)
  const csrf = useCsrf()
  const spending = useQuery({
    queryKey: ['pilot-spending', token],
    queryFn: () => fetchSpending(csrf),
    enabled: Boolean(token),
    refetchInterval: 30_000,
  })
  const data = spending.data

  return (
    <div className="mc-panel flex flex-col p-5">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-text-1">
          <Wallet size={14} className="text-text-3" /> Spending
        </h3>
        <span className="mc-label">GET /api/pilot/spending</span>
      </div>

      {spending.isLoading ? (
        <LoadingBlock />
      ) : spending.error ? (
        <PilotErrorNote error={spending.error} />
      ) : !data ? null : !data.enabled ? (
        <p className="text-xs leading-relaxed text-text-3">
          Spending estimates are not enabled for this runtime. The pilot still enforces
          attempt quotas; no USD ledger is reported.
        </p>
      ) : (
        <>
          <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
            <PanelRow label="Currency">{data.currency}</PanelRow>
            <PanelRow label="Limit">{data.limit_usd ?? '—'}</PanelRow>
            <PanelRow label="Reserved">{data.reserved_usd}</PanelRow>
            <PanelRow label="Reported cost">{data.reported_cost_usd}</PanelRow>
            <PanelRow label="Prompt tokens">{data.prompt_tokens}</PanelRow>
            <PanelRow label="Completion tokens">{data.completion_tokens}</PanelRow>
            <PanelRow label="Unresolved calls">{data.unresolved_calls}</PanelRow>
          </dl>
          <div className="mt-4 border-t border-line pt-3 text-xs leading-relaxed text-text-3">
            {data.pricing_source ? (
              <>
                Pricing source:{' '}
                <span className="mc-value text-text-2">{data.pricing_source}</span>
              </>
            ) : (
              'No pricing source reported.'
            )}
            <div className="mt-1">Estimates are not provider invoices.</div>
          </div>
        </>
      )}
    </div>
  )
}

function yesNo(value: boolean) {
  return value ? <span className="text-green">yes</span> : <span className="text-amber">no</span>
}
