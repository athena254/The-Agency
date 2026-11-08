import { Layers } from 'lucide-react'
import { Header } from './Memories'
import { LockedPanel } from './Locked'

export function LatticePage() {
  return (
    <div className="space-y-4">
      <Header
        title="Lattice"
        detail="Decentralized coordination: membership, epochs, proposals, votes and grants."
      />
      <LockedPanel
        icon={Layers}
        title="Peer authority is not built yet"
        detail="Today the Lattice is a single-process SQLite prototype — not consensus and not peer governance. The path to a real decentralized orchestrator is specified, and the owner decisions are approved. This page will show live peers, epochs and quorum once P2 lands."
        gates={['D-01 consensus policy', 'D-02 membership', 'P2 peer application authority']}
      />
    </div>
  )
}
