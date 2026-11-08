import type { LucideIcon } from 'lucide-react'
import { Lock } from 'lucide-react'

export function LockedPanel({
  icon: Icon,
  title,
  detail,
  gates,
}: {
  icon: LucideIcon
  title: string
  detail: string
  gates: string[]
}) {
  return (
    <div className="mc-panel flex flex-col items-center px-6 py-12 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-panel border border-amber/30 bg-amber/10 text-amber">
        <Icon size={20} />
      </div>
      <h3 className="mt-4 text-sm font-semibold text-text-1">{title}</h3>
      <p className="mt-2 max-w-lg text-xs leading-relaxed text-text-3">{detail}</p>
      <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
        {gates.map((gate) => (
          <span
            key={gate}
            className="flex items-center gap-1.5 rounded-control border border-line bg-ink-1 px-2 py-1 font-mono text-[10.5px] text-text-2"
          >
            <Lock size={10} className="text-amber" />
            {gate}
          </span>
        ))}
      </div>
    </div>
  )
}
