import { useQuery } from '@tanstack/react-query'
import { RefreshCw, Sparkles } from 'lucide-react'
import { pilotErrorMessage, useCsrf } from '../api/pilot'
import { fetchObserveSkills, type SkillsView } from '../api/observe'
import {
  EmptyState,
  Notice,
  PageHeader as Header,
  StateBadge,
} from '../components/ui'
import { useAuth } from '../stores/auth'
import { PilotGate } from './PilotShared'

/*
  Read-only view over the skill registry (observe surface). Skill *execution*
  stays behind the P4 gate — this page shows metadata only and never implies
  an installed skill can run.
*/

export function SkillsPage() {
  const token = useAuth((state) => state.ownerToken)
  const csrf = useCsrf()
  const skills = useQuery({
    queryKey: ['observe-skills', token],
    queryFn: () => fetchObserveSkills(csrf),
    enabled: Boolean(token && csrf),
    refetchInterval: 30_000,
  })

  return (
    <div className="space-y-4">
      <Header
        title="Skills"
        detail="Versioned capability registry: metadata, lifecycle evidence and explicit permissions."
      />

      <Notice tone="info">
        <Sparkles size={14} className="mt-0.5 flex-none" aria-hidden="true" />
        <span>
          Read-only registry view. Executing a published skill requires the capability-lifecycle
          gate (P4) — metadata here does not mean a skill can run.
        </span>
      </Notice>

      <div className="flex items-center justify-end gap-2">
        <span className="mc-value text-xs text-text-3" aria-live="polite">
          {skills.data?.available ? `${skills.data.total} skills` : ''}
          {skills.data?.truncated ? ' · list truncated' : ''}
        </span>
        <button
          type="button"
          className="mc-icon-btn"
          onClick={() => skills.refetch()}
          disabled={skills.isFetching || !token}
          aria-label="Refresh skills"
        >
          <RefreshCw size={13} className={skills.isFetching ? 'animate-spin' : ''} />
        </button>
      </div>

      <PilotGate>
        <SkillsBody
          view={skills.data}
          error={skills.error}
          pending={skills.isPending}
          retry={() => skills.refetch()}
        />
      </PilotGate>
    </div>
  )
}

function SkillsBody({
  view,
  error,
  pending,
  retry,
}: {
  view: SkillsView | undefined
  error: unknown
  pending: boolean
  retry: () => void
}) {
  if (pending) {
    return (
      <div className="mc-panel px-5 py-10 text-center text-sm text-text-3" role="status">
        Loading skills…
      </div>
    )
  }
  if (error) {
    return (
      <div className="mc-notice mc-notice-warning" role="alert">
        <span>Skill registry unavailable: {pilotErrorMessage(error)}</span>
        <button type="button" className="mc-btn mc-btn-primary flex-none" onClick={retry}>
          Retry
        </button>
      </div>
    )
  }
  if (!view?.available) {
    return (
      <div className="mc-panel px-5 py-4">
        <p className="mc-caption">Registry unavailable: {view?.reason ?? 'not collected'}</p>
      </div>
    )
  }
  if (view.skills.length === 0) {
    return (
      <div className="mc-panel">
        <EmptyState
          title="No skills registered"
          detail="The registry is empty — skills appear here after import or distillation."
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
          <caption className="sr-only">Skill registry</caption>
          <thead>
            <tr className="border-b border-line">
              <th scope="col" className="mc-label px-5 py-3 font-normal">
                Skill
              </th>
              <th scope="col" className="mc-label px-3 py-3 font-normal">
                Version
              </th>
              <th scope="col" className="mc-label px-3 py-3 font-normal">
                Status
              </th>
              <th scope="col" className="mc-label px-3 py-3 font-normal">
                Provenance
              </th>
              <th scope="col" className="mc-label px-5 py-3 font-normal">
                Permissions
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {view.skills.map((skill) => (
              <tr key={`${skill.skill_id}@${skill.version}`} className="hover:bg-ink-1">
                <td className="max-w-72 px-5 py-3">
                  <div className="truncate text-sm text-text-1" title={skill.description}>
                    {skill.name}
                  </div>
                  <div className="mc-value truncate text-[10.5px] text-text-3">
                    {skill.skill_id}
                  </div>
                </td>
                <td className="px-3 py-3">
                  <span className="mc-value text-[11px] text-text-2">{skill.version}</span>
                </td>
                <td className="px-3 py-3">
                  <StateBadge state={skill.status} />
                </td>
                <td className="px-3 py-3">
                  <span className="mc-value text-[11px] text-text-3">{skill.provenance}</span>
                </td>
                <td className="px-5 py-3">
                  {skill.permissions.length > 0 ? (
                    <span className="mc-value text-[11px] text-text-2">
                      {skill.permissions.length} granted
                    </span>
                  ) : (
                    <span className="mc-value text-[11px] text-text-3">none</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
