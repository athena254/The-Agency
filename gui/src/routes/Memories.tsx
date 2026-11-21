import { Brain } from 'lucide-react'
import { PageHeader as Header } from '../components/ui'
import { LockedPanel } from './Locked'

export function MemoriesPage() {
  return (
    <div className="space-y-4">
      <Header
        title="Memory"
        detail="Persistent, owner-scoped recall with full-text search and tier lifecycle."
      />
      <LockedPanel
        icon={Brain}
        title="Memory routes are gated"
        detail="The main memory API is closed until peer authority is available. The pilot scope is read-only and does not expose recall yet. This page will wire to /v1/memory/search when the gate opens."
        gates={['P1 identity + audit', 'P2 peer authority']}
      />
    </div>
  )
}
