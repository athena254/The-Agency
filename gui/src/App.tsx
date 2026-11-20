import { useState, type ReactNode } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
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
      <div className="flex min-w-0 flex-1 flex-col">
        <Routes>
          <Route
            path="/"
            element={
              <PageShell title="Dashboard" subtitle="System overview" onOpenNav={openNav} navOpen={navOpen}>
                <DashboardPage />
              </PageShell>
            }
          />
          <Route
            path="/observability"
            element={
              <PageShell title="Observability" subtitle="Read-only local runtime state" onOpenNav={openNav} navOpen={navOpen}>
                <ObservabilityPage />
              </PageShell>
            }
          />
          <Route
            path="/ide"
            element={
              <PageShell title="IDE" subtitle="Chat and execution" onOpenNav={openNav} navOpen={navOpen}>
                <IdePage />
              </PageShell>
            }
          />
          <Route
            path="/agents"
            element={
              <PageShell title="Agents" subtitle="Identities and status" onOpenNav={openNav} navOpen={navOpen}>
                <AgentsPage />
              </PageShell>
            }
          />
          <Route
            path="/tasks"
            element={
              <PageShell title="Tasks" subtitle="Owner pilot tasks" onOpenNav={openNav} navOpen={navOpen}>
                <TasksPage />
              </PageShell>
            }
          />
          <Route
            path="/coding"
            element={
              <PageShell title="Coding" subtitle="Bounded factory coding task" onOpenNav={openNav} navOpen={navOpen}>
                <CodingPage />
              </PageShell>
            }
          />
          <Route
            path="/threads"
            element={
              <PageShell title="Threads" subtitle="Owner pilot conversations" onOpenNav={openNav} navOpen={navOpen}>
                <ThreadsPage />
              </PageShell>
            }
          />
          <Route
            path="/memories"
            element={
              <PageShell title="Memories" subtitle="Persistent recall" onOpenNav={openNav} navOpen={navOpen}>
                <MemoriesPage />
              </PageShell>
            }
          />
          <Route
            path="/evidence"
            element={
              <PageShell title="Evidence" subtitle="Findings and risk" onOpenNav={openNav} navOpen={navOpen}>
                <EvidencePage />
              </PageShell>
            }
          />
          <Route
            path="/skills"
            element={
              <PageShell title="Skills" subtitle="Versioned capability registry" onOpenNav={openNav} navOpen={navOpen}>
                <SkillsPage />
              </PageShell>
            }
          />
          <Route
            path="/lattice"
            element={
              <PageShell title="Lattice" subtitle="Governance and consensus" onOpenNav={openNav} navOpen={navOpen}>
                <LatticePage />
              </PageShell>
            }
          />
          <Route
            path="/forge"
            element={
              <PageShell title="Forge" subtitle="Governed coding agent" onOpenNav={openNav} navOpen={navOpen}>
                <ForgePage />
              </PageShell>
            }
          />
          <Route
            path="/settings"
            element={
              <PageShell title="Settings" subtitle="Runtime configuration" onOpenNav={openNav} navOpen={navOpen}>
                <SettingsPage />
              </PageShell>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </div>
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
