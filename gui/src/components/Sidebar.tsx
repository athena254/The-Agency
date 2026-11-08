import {
  AppWindow,
  Bot,
  Brain,
  Crosshair,
  FolderKanban,
  Gauge,
  Layers,
  MessagesSquare,
  ScrollText,
  Settings,
  Shield,
  Sparkles,
} from 'lucide-react'
import { NavLink } from 'react-router-dom'

export interface NavItem {
  to: string
  label: string
  icon: typeof Gauge
  locked?: boolean
}

export const NAV_ITEMS: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: Gauge },
  { to: '/ide', label: 'IDE', icon: MessagesSquare },
  { to: '/agents', label: 'Agents', icon: Bot },
  { to: '/tasks', label: 'Tasks', icon: FolderKanban },
  { to: '/memories', label: 'Memories', icon: Brain },
  { to: '/evidence', label: 'Evidence', icon: ScrollText },
  { to: '/skills', label: 'Skills', icon: Sparkles },
  { to: '/lattice', label: 'Lattice', icon: Layers, locked: true },
  { to: '/forge', label: 'Forge', icon: Shield, locked: true },
  { to: '/settings', label: 'Settings', icon: Settings },
]

export function Sidebar() {
  return (
    <aside className="flex w-60 flex-none flex-col border-r border-line bg-ink-1">
      <div className="flex items-center gap-3 border-b border-line px-4 py-4">
        <div className="flex h-8 w-8 items-center justify-center rounded-panel bg-signal-dim border border-signal/40">
          <Crosshair size={16} className="text-signal-strong" />
        </div>
        <div className="min-w-0">
          <div className="text-[13px] font-semibold tracking-tight text-text-1">
            Mission Control
          </div>
          <div className="mc-label">Agency Console</div>
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 py-3">
        <div className="mc-label px-2 pb-2">Navigate</div>
        <ul className="space-y-0.5">
          {NAV_ITEMS.map((item) => (
            <li key={item.to}>
              <NavLink to={item.to} end={item.to === '/'} className="mc-nav-item w-full">
                <item.icon size={15} className="flex-none" />
                <span className="flex-1 text-left">{item.label}</span>
                {item.locked ? <span className="mc-label text-amber">P2</span> : null}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>

      <div className="border-t border-line px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="mc-status-dot bg-green" />
          <span className="text-xs text-text-2">Local runtime</span>
        </div>
        <div className="mc-value mt-1 text-[10.5px] text-text-3">
          v0.1.0 · single-owner
        </div>
      </div>
    </aside>
  )
}

export function Topbar({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <header className="flex items-center justify-between border-b border-line bg-ink-1/80 px-6 py-3 backdrop-blur">
      <div>
        <h1 className="text-[15px] font-semibold tracking-tight text-text-1">{title}</h1>
        {subtitle ? <div className="mc-label mt-0.5">{subtitle}</div> : null}
      </div>
      <div className="flex items-center gap-2">
        <span className="mc-label hidden sm:inline">Loopback · owner</span>
        <AppWindow size={14} className="text-text-3" />
      </div>
    </header>
  )
}
