import { Brain } from 'lucide-react'
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

export function Header({ title, detail }: { title: string; detail: string }) {
  return (
    <div>
      <h2 className="text-sm font-semibold text-text-1">{title}</h2>
      <p className="mt-0.5 text-xs text-text-3">{detail}</p>
    </div>
  )
}
