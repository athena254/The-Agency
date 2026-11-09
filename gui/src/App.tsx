import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Sidebar, Topbar } from './components/Sidebar'
import { DashboardPage } from './routes/Dashboard'
import { IdePage } from './routes/Ide'
import { AgentsPage } from './routes/Agents'
import { TasksPage } from './routes/Tasks'
import { CodingPage } from './routes/Coding'
import { ThreadsPage } from './routes/Threads'
import { MemoriesPage } from './routes/Memories'
import { EvidencePage } from './routes/Evidence'
import { SkillsPage } from './routes/Skills'
import { LatticePage } from './routes/Lattice'
import { ForgePage } from './routes/Forge'
import { SettingsPage } from './routes/Settings'

export default function App() {
  return (
    <BrowserRouter basename="/mission-control">
      <div className="mc-app flex h-screen overflow-hidden">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <Routes>
            <Route
              path="/"
              element={
                <PageShell title="Dashboard" subtitle="System overview">
                  <DashboardPage />
                </PageShell>
              }
            />
            <Route
              path="/ide"
              element={
                <PageShell title="IDE" subtitle="Chat and execution">
                  <IdePage />
                </PageShell>
              }
            />
            <Route
              path="/agents"
              element={
                <PageShell title="Agents" subtitle="Identities and status">
                  <AgentsPage />
                </PageShell>
              }
            />
            <Route
              path="/tasks"
              element={
                <PageShell title="Tasks" subtitle="Owner pilot tasks">
                  <TasksPage />
                </PageShell>
              }
            />
            <Route
              path="/coding"
              element={
                <PageShell title="Coding" subtitle="Bounded factory coding task">
                  <CodingPage />
                </PageShell>
              }
            />
            <Route
              path="/threads"
              element={
                <PageShell title="Threads" subtitle="Owner pilot conversations">
                  <ThreadsPage />
                </PageShell>
              }
            />
            <Route
              path="/memories"
              element={
                <PageShell title="Memories" subtitle="Persistent recall">
                  <MemoriesPage />
                </PageShell>
              }
            />
            <Route
              path="/evidence"
              element={
                <PageShell title="Evidence" subtitle="Findings and risk">
                  <EvidencePage />
                </PageShell>
              }
            />
            <Route
              path="/skills"
              element={
                <PageShell title="Skills" subtitle="Versioned capability registry">
                  <SkillsPage />
                </PageShell>
              }
            />
            <Route
              path="/lattice"
              element={
                <PageShell title="Lattice" subtitle="Governance and consensus">
                  <LatticePage />
                </PageShell>
              }
            />
            <Route
              path="/forge"
              element={
                <PageShell title="Forge" subtitle="Governed coding agent">
                  <ForgePage />
                </PageShell>
              }
            />
            <Route
              path="/settings"
              element={
                <PageShell title="Settings" subtitle="Runtime configuration">
                  <SettingsPage />
                </PageShell>
              }
            />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </div>
      </div>
    </BrowserRouter>
  )
}

function PageShell({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle: string
  children: React.ReactNode
}) {
  return (
    <>
      <Topbar title={title} subtitle={subtitle} />
      <main className="min-h-0 flex-1 overflow-y-auto p-6">{children}</main>
    </>
  )
}
