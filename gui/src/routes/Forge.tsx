import { Shield } from 'lucide-react'
import { Header } from './Memories'
import { LockedPanel } from './Locked'

export function ForgePage() {
  return (
    <div className="space-y-4">
      <Header
        title="Forge"
        detail="Governed coding agent: isolated worktree, external coding model, inspection, security scan and tests."
      />
      <LockedPanel
        icon={Shield}
        title="Native coding actions wait for P2/P3"
        detail="Forge already inventories files, runs syntax checks, and can drive an external coding agent in an isolated worktree with inspection, security and test gates. Authorizing merge or release requires peer authority and isolation evidence."
        gates={['P2 peer authority', 'P3 isolation', 'P4 capability lifecycle']}
      />
      <div className="mc-panel px-5 py-4">
        <div className="mc-label mb-2">Current capability</div>
        <pre className="overflow-x-auto rounded-control border border-line bg-ink-0 p-3 font-mono text-[11.5px] leading-relaxed text-text-2">
{`agency forge code "<task>" --repo <path>
  -> isolated git worktree
  -> external coding agent (opencode, free models)
  -> inspection + security scan + isolated tests
  -> PASS / FAIL JSON`}
        </pre>
      </div>
    </div>
  )
}
