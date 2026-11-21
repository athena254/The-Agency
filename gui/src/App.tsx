import { useState, type ReactNode } from 'react'
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom'
import { RouteErrorBoundary } from './components/ErrorBoundary'
import { ToastRegion } from './components/ui'
import { Sidebar, Topbar } from './components/Sidebar'
import { DashboardPage } from './routes/Dashboard'
import { IdePage } from './routes/Ide'
import { AgentsPage } from './routes/Agents'
import { TasksPage } from './routes/Tasks'
import { CodingPage } from './routes/Coding'
import { ThreadsPage } from './routes/Threads'
import { MemoriesPage } from './routes/Memories'
import { ObservabilityPage } from './routes/Observability'
import { EvidencePage } from './routes/Evidence'
import { SkillsPage } from './routes/Skills'
import { LatticePage } from './routes/Lattice'
import { ForgePage } from './routes/Forge'
import { SettingsPage } from './routes/Settings'

/*
  One route table drives the sidebar and the router so a page can never be
  reachable but missing from navigation (or vice versa).
*/

const PAGES: { path: string; title: string; subtitle: string; element: ReactNode }[] = [
  { path: '/', title: 'Dashboard', subtitle: 'System overview', element: <DashboardPage /> },
  {
    path: '/observability',
    title: 'Observability',
    subtitle: 'Read-only local runtime state',
    element: <ObservabilityPage />,
  },
  { path: '/ide', title: 'IDE', subtitle: 'Chat and execution', element: <IdePage /> },
  { path: '/agents', title: 'Agents', subtitle: 'Identities and status', element: <AgentsPage /> },
  { path: '/tasks', title: 'Tasks', subtitle: 'Owner pilot tasks', element: <TasksPage /> },
  {
    path: '/coding',
    title: 'Coding',
    subtitle: 'Bounded factory coding task',
    element: <CodingPage />,
  },
  {
    path: '/threads',
    title: 'Threads',
    subtitle: 'Owner pilot conversations',
    element: <ThreadsPage />,
  },
  {
    path: '/memories',
    title: 'Memories',
    subtitle: 'Read-only memory store view',
    element: <MemoriesPage />,
  },
  {
    path: '/evidence',
    title: 'Evidence',
    subtitle: 'Findings and risk · read-only',
    element: <EvidencePage />,
  },
  {
    path: '/skills',
    title: 'Skills',
    subtitle: 'Capability registry · read-only',
    element: <SkillsPage />,
  },
  {
    path: '/lattice',
    title: 'Lattice',
    subtitle: 'Governance and consensus',
    element: <LatticePage />,
  },
  { path: '/forge', title: 'Forge', subtitle: 'Governed coding agent', element: <ForgePage /> },
  {
    path: '/settings',
    title: 'Settings',
    subtitle: 'Runtime configuration',
    element: <SettingsPage />,
  },
]

export default function App() {
  return (
    <BrowserRouter basename="/mission-control">
      <AppShell />
    </BrowserRouter>
  )
}

function AppShell() {
  const [navOpen, setNavOpen] = useState(false)
  const openNav = () => setNavOpen(true)
  const closeNav = () => setNavOpen(false)

  return (
    <div className="mc-app flex h-dvh overflow-hidden">
      <a href="#main-content" className="mc-skip-link">
        Skip to content
      </a>
      <Sidebar open={navOpen} onClose={closeNav} />
      <ToastRegion />
      <div className="flex min-w-0 flex-1 flex-col">
        <Routes>
          {PAGES.map((page) => (
            <Route
              key={page.path}
              path={page.path}
              element={
                <PageShell
                  title={page.title}
                  subtitle={page.subtitle}
                  onOpenNav={openNav}
                  navOpen={navOpen}
                >
                  <RouteErrorBoundary page={page.title}>{page.element}</RouteErrorBoundary>
                </PageShell>
              }
            />
          ))}
          <Route path="*" element={<NotFound />} />
        </Routes>
      </div>
    </div>
  )
}

function NotFound() {
  return (
    <main id="main-content" tabIndex={-1} className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">
      <div className="mc-panel mx-auto mt-10 max-w-md px-6 py-10 text-center" role="status">
        <div className="mc-value text-3xl text-text-3">404</div>
        <p className="mc-body mt-3">
          This console has no page at that address. Use the navigation to get back.
        </p>
        <Link to="/" className="mc-btn mc-btn-primary mt-5">
          Back to Dashboard
        </Link>
      </div>
    </main>
  )
}

function PageShell({
  title,
  subtitle,
  children,
  onOpenNav,
  navOpen,
}: {
  title: string
  subtitle: string
  children: ReactNode
  onOpenNav: () => void
  navOpen: boolean
}) {
  return (
    <>
      <Topbar title={title} subtitle={subtitle} onOpenNav={onOpenNav} navOpen={navOpen} />
      <main
        id="main-content"
        tabIndex={-1}
        className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6"
      >
        {children}
      </main>
    </>
  )
}
