import { useEffect } from 'react'
import {
  Activity,
  Bot,
  Brain,
  Code,
  Crosshair,
  FolderKanban,
  Gauge,
  Layers,
  Lock,
  Menu,
  MessageSquareText,
  MessagesSquare,
  ScrollText,
  Settings,
  Shield,
  Sparkles,
  X,
  type LucideIcon,
} from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { useAuth } from '../stores/auth'

export interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  locked?: boolean
  gate?: string
}

const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: 'Operate',
    items: [
      { to: '/', label: 'Dashboard', icon: Gauge },
      { to: '/observability', label: 'Observability', icon: Activity },
      { to: '/ide', label: 'IDE', icon: MessagesSquare },
    ],
  },
  {
    label: 'Work',
    items: [
      { to: '/threads', label: 'Threads', icon: MessageSquareText },
      { to: '/tasks', label: 'Tasks', icon: FolderKanban },
      { to: '/coding', label: 'Coding', icon: Code },
      { to: '/agents', label: 'Agents', icon: Bot },
    ],
  },
  {
    label: 'Inspect',
    items: [
      { to: '/memories', label: 'Memories', icon: Brain, locked: true, gate: 'P1 + P2' },
      { to: '/evidence', label: 'Evidence', icon: ScrollText, locked: true, gate: 'P1 + P2' },
      { to: '/skills', label: 'Skills', icon: Sparkles, locked: true, gate: 'P2 + P4' },
    ],
  },
  {
    label: 'Platform',
    items: [
      { to: '/lattice', label: 'Lattice', icon: Layers },
      { to: '/forge', label: 'Forge', icon: Shield, locked: true, gate: 'P2 + P3 + P4' },
      { to: '/settings', label: 'Settings', icon: Settings },
    ],
  },
]

function NavGroups({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <>
      {NAV_GROUPS.map((group) => (
        <div key={group.label} className="mb-4">
          <div className="mc-label px-2 pb-1.5">{group.label}</div>
          <ul className="space-y-0.5">
            {group.items.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.to === '/'}
                  onClick={onNavigate}
                  aria-disabled={item.locked || undefined}
                  title={item.locked ? `Locked — readiness gate ${item.gate}` : undefined}
                  className={({ isActive }) =>
                    `mc-nav-item w-full${isActive ? ' active' : ''}`
                  }
                >
                  <item.icon size={15} className="flex-none" aria-hidden="true" />
                  <span className="flex-1 text-left">
                    {item.label}
                    {item.locked ? <span className="sr-only"> (locked)</span> : null}
                  </span>
                  {item.locked ? (
                    <span className="inline-flex items-center gap-1 text-amber">
                      <Lock size={10} aria-hidden="true" />
                      <span className="mc-label text-amber">{item.gate ?? 'Locked'}</span>
                    </span>
                  ) : null}
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </>
  )
}

function SidebarContent({
  onNavigate,
  onClose,
}: {
  onNavigate?: () => void
  onClose?: () => void
}) {
  return (
    <>
      <div className="flex items-center gap-3 border-b border-line px-4 py-4">
        <div className="flex h-8 w-8 flex-none items-center justify-center rounded-panel border border-signal/40 bg-signal-dim">
          <Crosshair size={16} className="text-signal-strong" aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-semibold tracking-tight text-text-1">
            Mission Control
          </div>
          <div className="mc-label">Agency Console</div>
        </div>
        {onClose ? (
          <button
            type="button"
            className="mc-icon-btn lg:hidden"
            onClick={onClose}
            aria-label="Close navigation"
          >
            <X size={15} aria-hidden="true" />
          </button>
        ) : null}
      </div>

      <nav aria-label="Primary" className="flex-1 overflow-y-auto px-2 py-3">
        <NavGroups onNavigate={onNavigate} />
      </nav>

      <div className="border-t border-line px-4 py-3">
        <div className="mc-label">Runtime</div>
        <div className="mt-1 text-xs text-text-2">Local prototype</div>
        <div className="mc-value mt-0.5 text-[10.5px] text-text-3">
          v0.1.0 · single-owner
        </div>
      </div>
    </>
  )
}

export function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  return (
    <>
      <aside className="hidden w-60 flex-none flex-col border-r border-line bg-ink-1 lg:flex">
        <SidebarContent />
      </aside>
      {open ? (
        <>
          <div className="mc-overlay lg:hidden" onClick={onClose} aria-hidden="true" />
          <aside
            id="mc-sidebar"
            className="fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-line bg-ink-1 shadow-xl lg:hidden"
            aria-label="Navigation"
          >
            <SidebarContent onNavigate={onClose} onClose={onClose} />
          </aside>
        </>
      ) : (
        <span id="mc-sidebar" hidden />
      )}
    </>
  )
}

export function Topbar({
  title,
  subtitle,
  onOpenNav,
  navOpen,
}: {
  title: string
  subtitle?: string
  onOpenNav: () => void
  navOpen: boolean
}) {
  const token = useAuth((state) => state.ownerToken)

  return (
    <header className="flex items-center gap-3 border-b border-line bg-ink-1/80 px-4 py-3 backdrop-blur sm:px-6">
      <button
        type="button"
        className="mc-icon-btn lg:hidden"
        onClick={onOpenNav}
        aria-label="Open navigation"
        aria-controls="mc-sidebar"
        aria-expanded={navOpen}
      >
        <Menu size={16} aria-hidden="true" />
      </button>
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-[15px] font-semibold tracking-tight text-text-1">
          {title}
        </h1>
        {subtitle ? <div className="mc-label mt-0.5 truncate">{subtitle}</div> : null}
      </div>
      <div className="flex flex-none items-center gap-2">
        <span
          className={`mc-status-dot ${token ? 'bg-green' : 'bg-amber'}`}
          aria-hidden="true"
        />
        <span className="mc-label hidden sm:inline">
          {token ? 'Owner token · session' : 'No owner token'}
        </span>
      </div>
    </header>
  )
}
