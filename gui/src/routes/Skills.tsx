import { Sparkles } from 'lucide-react'
import { Header } from './Memories'
import { LockedPanel } from './Locked'

export function SkillsPage() {
  return (
    <div className="space-y-4">
      <Header
        title="Skills"
        detail="Versioned capability registry: metadata, lifecycle evidence and explicit permissions."
      />
      <LockedPanel
        icon={Sparkles}
        title="Skill runtime binding is pending"
        detail="The registry stores versioned skill metadata, but binding a published skill to real execution and evidence is a P4 gate. Imported skills are metadata-only until then."
        gates={['P4 capability lifecycle', 'P2 peer authority']}
      />
    </div>
  )
}
