import { ScrollText } from 'lucide-react'
import { Header } from './Memories'
import { LockedPanel } from './Locked'

export function EvidencePage() {
  return (
    <div className="space-y-4">
      <Header
        title="Evidence"
        detail="Findings, execution receipts and risk assessments with honest verification status."
      />
      <LockedPanel
        icon={ScrollText}
        title="Evidence routes are gated"
        detail="Findings and risk reports live behind the main API, which is closed until peer authority is available. No finding is presented as verified without an independent check."
        gates={['P1 identity + audit', 'P2 peer authority']}
      />
    </div>
  )
}
