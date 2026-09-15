# The Agency — GUI Design Document

## Tech Stack

| Layer | Technology |
|-------|------------|
| **Desktop** | Electron (main process + renderer) |
| **Frontend** | React 18 + TypeScript |
| **Build** | Vite + electron-vite |
| **Styling** | Tailwind CSS (GitHub Dark theme) |
| **State** | Zustand (global) + React Query (server) |
| **Icons** | Lucide React |
| **Terminal** | xterm.js (PTY attachment) |
| **Editor** | Monaco Editor |
| **Charts** | Recharts |
| **Graph** | Cytoscape.js (lattice visualization) |
| **Drag-drop** | @dnd-kit/core (Kanban) |

---

## Architecture

```
mission-control-gui/
├── electron/
│   ├── main.ts           # Electron main process
│   ├── preload.ts        # IPC bridge
│   └── menu.ts           # App menu
├── src/
│   ├── App.tsx           # Root + Router
│   ├── main.tsx          # Entry point
│   ├── routes/
│   │   ├── Dashboard.tsx         # System overview
│   │   ├── IDE.tsx               # Terminal + Chat
│   │   ├── Memories.tsx          # MemPalace explorer
│   │   ├── Calendar.tsx          # Calendar view
│   │   ├── Tasks.tsx             # Kanban board
│   │   ├── Personal.tsx          # Personal domain
│   │   ├── Work.tsx              # Work domain (Multica embed)
│   │   ├── Finance.tsx           # Finance domain
│   │   ├── Learning.tsx          # Learning domain
│   │   ├── Technology.tsx        # Technology domain
│   │   ├── Social.tsx            # Social domain
│   │   ├── Research.tsx          # Research domain
│   │   └── Business.tsx          # Business domain (Paperclip embed)
│   ├── components/
│   │   ├── Sidebar.tsx
│   │   ├── AgentStatusCard.tsx
│   │   ├── BridgePanel.tsx
│   │   ├── TaskCard.tsx
│   │   ├── KanbanBoard.tsx
│   │   ├── TerminalWidget.tsx
│   │   ├── LatticeGraph.tsx
│   │   ├── DeltaReport.tsx
│   │   ├── WebViewContainer.tsx
│   │   ├── SubagentSpawnPanel.tsx
│   │   └── BridgeStatusOverlay.tsx
│   ├── stores/
│   │   ├── agentStore.ts
│   │   ├── taskStore.ts
│   │   └── uiStore.ts
│   ├── api/
│   │   ├── athenaClient.ts
│   │   ├── paperclipClient.ts
│   │   └── multicaClient.ts
│   ├── hooks/
│   │   ├── useAgents.ts
│   │   ├── useBridges.ts
│   │   ├── useTasks.ts
│   │   └── useSubagents.ts
│   └── types/
│       ├── agent.ts
│       ├── task.ts
│       └── bridge.ts
└── package.json
```

---

## Page Mockups

### 1. Dashboard Page

```
┌─────────────────────────────────────────────────────────────────┐
│  Mission Control                               🚀 v0.1.0  ⚙️  │
├─────────────────────────────────────────────────────────────────┤
│  [🏠 Dash] [💼 Work] [💰 Fin] [📚 Learn] [💻 Tech] ... [🏢 Biz]│
├─────────────┬───────────────────────────────────────────────────┤
│             │                                                   │
│  AGENTS     │  ┌─────────────────────────────────────────────┐ │
│  ─────────  │  │ 🚀 SYSTEM OVERVIEW                         │ │
│  🏠 Personal│  │                                             │ │
│    ● Ready  │  │  Agents: 8 online │ Bridges: 7 active       │ │
│  💼 Work    │  │  Tasks pending: 12 │ Tokens today: 45K       │ │
│    ● Busy   │  │  Cost today: $12.45                        │ │
│  💰 Finance │  │                                             │ │
│    ● Ready  │  └─────────────────────────────────────────────┘ │
│  📚 Learning│                                                   │
│    ○ Idle   │  ┌─────────────────────────────────────────────┐ │
│  💻 Tech    │  │ 📊 ACTIVITY FEED                           │ │
│    ● Ready  │  │                                             │ │
│  👥 Social  │  │  2m  Work Agent: Task #123 completed       │ │
│    ● Ready  │  │  5m  Finance Agent: Budget updated         │ │
│  🔬 Research│  │  12m CodexBridge: Generated login code     │ │
│    ● Ready  │  │  18m Business Agent: New company added     │ │
│  🏢 Business│  │                                             │ │
│    ● Ready  │  └─────────────────────────────────────────────┘ │
│             │                                                   │
│  BRIDGES    │  ┌─────────────────────────────────────────────┐ │
│  ─────────  │  │ 🔌 ACTIVE BRIDGES                          │ │
│  🔵 Codex   │  │                                             │ │
│    ● Ready  │  │  Codex      ● Ready   Queue: 0   Cost: $0 │ │
│  🟣 Claude  │  │  Claude     ● Ready   Queue: 0   Cost: $0 │ │
│    ● Ready  │  │  Hermes     ● Ready   Queue: 1   Cost: $0 │ │
│  🟢 Hermes  │  │  Gemini     ◐ Busy    Queue: 3   Cost: $2 │ │
│    ○ Idle   │  │                                             │ │
│  🔴 Gemini  │  └─────────────────────────────────────────────┘ │
│    ◐ Busy   │                                                   │
│             │  ┌─────────────────────────────────────────────┐ │
│  QUICK      │  │ ⚡ QUICK ACTIONS                           │ │
│  ACTIONS    │  │                                             │ │
│  ─────────  │  │  [New Task]  [Start Bridge]  [Sync Mem]   │ │
│  [New Task] │  │  [Open IDE]  [Run Report]  [View Logs]    │ │
│  [Open IDE] │  └─────────────────────────────────────────────┘ │
│  [Sync]     │                                                   │
│             └───────────────────────────────────────────────────┘
└─────────────────────────────────────────────────────────────────┘
```

### 2. IDE Page

```
┌─────────────────────────────────────────────────────────────────┐
│  Mission Control                               🚀 v0.1.0  ⚙️  │
├─────────────────────────────────────────────────────────────────┤
│  [🏠] [💼 Work IDE] [💰] [📚] [💻] ... [🏢]   🖥️ Terminal  ✏️ C│
├─────────────┬───────────────────────────────────────────────────┤
│             │                                                   │
│  WORKSPACES  │  ┌─────────────────────────────────────────────┐ │
│  ─────────   │  │  [CodexBridge]                            │ │
│  ● task-123  │  │  Working on: Implement OAuth flow         │ │
│  ● feat-login│  │                                           │ │
│  ● bug-456   │  │  ┌─────────────────────────────────────┐ │ │
│  ● docs-read │  │  │                                     │ │ │
│             │  │  │   $ python -m athena.bridges.codex  │ │ │
│  AGENTS      │  │  │   > Task received: Implement...     │ │ │
│  ─────────   │  │  │   > Generating OAuth code...        │ │ │
│  ● Codex    │  │  │   ✓ Code generated (245 tokens)     │ │ │
│  ● Claude   │  │  │   Cost: $0.0045                      │ │ │
│  ● Hermes   │  │  │                                     │ │ │
│  ● Gemini   │  │  └─────────────────────────────────────┘ │ │
│             │  │                                           │ │
│  FILES       │  │  Chat:                                    │ │
│  ─────────   │  │  > Implement refresh token flow          │ │
│  📁 src/    │  │  [Send]  [Stop]  [Restart]               │ │
│  📁 tests/  │  │                                           │ │
│  📁 package │  └─────────────────────────────────────────────┘ │
│             │                                                   │
│  TOOLS       │  ┌─────────────────────────────────────────────┐ │
│  ─────────   │  │ 📝 DIFF PREVIEW                            │ │
│  ⌘ Files    │  │  ─────────────────────────────────────     │ │
│  ⌘ Search   │  │  @@ -1,7 +1,8 @@                          │ │
│  ⌘ MCP      │  │  + import refresh_token                    │ │
│  ⌘ Terminal │  │  +                                       │ │
│             │  └─────────────────────────────────────────────┘ │
│             └───────────────────────────────────────────────────┘
└─────────────────────────────────────────────────────────────────┘
```

### 3. Business Page (Paperclip Embed)

```
┌─────────────────────────────────────────────────────────────────┐
│  Mission Control — Business Agent            🏢  ● Ready    │
├─────────────┬───────────────────────────────────────────────────┤
│             │                                                   │
│  LATTICE    │  ╔══════════════════════════════════════════════╗ │
│  ─────────  │  ║                                              ║ │
│  [Graph]   │  ║           PAPERCLIP UI                       ║ │
│  ● Personal │  ║  ──────────────────────────────────────      ║ │
│  ● Work     │  ║  Dashboard: SaaS Co      $18K MRR           ║ │
│  ● Finance  │  ║  Org Chart: [CEO]→[CTO]→[Engineer]         ║ │
│  ● Business │  ║  Tasks: Build login (#123) In Progress      ║ │
│    ◄ YOU    │  ║                                              ║ │
│  ● Learning │  ║  [Full Paperclip interface loads here]      ║ │
│  ...        │  ║  React app running on localhost:5173        ║ │
│             │  ║                                              ║ │
│  SUBAGENTS  │  ╚══════════════════════════════════════════════╝ │
│  ─────────  │                                                   │
│  ○ market   │  🏢 COMPANIES (summary)                         │
│    research │  • SaaS Co       $18K MRR  ● Active            │
│  ○ financial│  • E-com Store   $6K revenue ● Active          │
│    analyst  │  • Consulting    $12K/mo   ● Paused            │
│             │  [Open in Full Screen]                          │
│  ACTIONS    │                                                   │
│  ─────────  │  🔄 QUICK ACTIONS                               │
│  ⌘ Open     │  [Sync Paperclip] [Spawn Subagent] [View Delta] │
│    Paperclip│                                                   │
│  ⌘ Sync     └───────────────────────────────────────────────────┘
│  ⌘ Spawn                                                         │
│    Subagent                                                       │
└─────────────────────────────────────────────────────────────────┘
```

### 4. Work Page (Multica Embed)

```
┌─────────────────────────────────────────────────────────────────┐
│  Mission Control — Work Agent                💼  ● Busy (12m)│
├─────────────┬───────────────────────────────────────────────────┤
│             │                                                   │
│  LATTICE    │  ╔══════════════════════════════════════════════╗ │
│  ─────────  │  ║           MULTICA UI                         ║ │
│  [Graph]   │  ║  ──────────────────────────────────────      ║ │
│  ● Personal │  ║  Issues Board: SaaS Co                      ║ │
│  ● Finance  │  ║  ┌─────────┐ ┌─────────┐ ┌─────────┐        ║ │
│  ● Business │  ║  │To Do    │ │In Prog  │ │Review   │        ║ │
│    ◄ YOU    │  ║  │#123 OAuth│ │#124 Auth│ │#122 API │        ║ │
│  ● Research │  ║  │[Codex]  │ │[Claude] │ │[Hermes] │        ║ │
│  ...        │  ║  └─────────┘ └─────────┘ └─────────┘        ║ │
│             │  ║                                              ║ │
│  SUBAGENTS  │  ║  Agents: Frontend Dev ●, Backend Dev ●      ║ │
│  ─────────  │  ║  Skills: React pattern ✓, OAuth flow ✓     ║ │
│  ● frontend │  ║                                              ║ │
│    dev      │  ║  [Full Multica interface loads here]        ║ │
│    (active) │  ║  React app running on localhost:3000        ║ │
│  ● backend  │  ║                                              ║ │
│    dev      │  ╚══════════════════════════════════════════════╝ │
│    (idle)   │                                                   │
│             │  📂 ACTIVE PROJECTS                             │
│  ACTIONS    │  • Athena Mission Control GUI (primary)         │
│  ─────────  │  • Budget tracker — Maintenance                 │
│  ⌘ Spawn    │                                                   │
│    Subagent │  🔄 QUICK ACTIONS                               │
│  ⌘ Assign   │  [Spawn Subagent] [View Lattice] [Sync Multica]│
│    Issue    └───────────────────────────────────────────────────┘
│  ⌘ Sync                                                          │
└─────────────────────────────────────────────────────────────────┘
```

### 5. Domain Agent Pages (Personal, Finance, Learning, Technology, Social, Research)

Standard layout with domain-specific metrics:

```
┌─────────────────────────────────────────────────────────────────┐
│  Mission Control — Personal Agent           🏠  ● Ready    │
├─────────────┬───────────────────────────────────────────────────┤
│             │                                                   │
│  LATTICE    │  🏠 PERSONAL METRICS                            │
│  ─────────  │  ┌──────────────┬──────────────┬─────────────────┐│
│  [Graph]   │  │ Habits Today  │ 6 / 8        │ ●────────────  ││
│  ● Work     │  │ Avg Mood      │ 7.2 / 10     │ ○──────         ││
│  ● Finance  │  │ Health Score  │ 82 / 100     │ ●────────────  ││
│  ● Business │  │ Relationships │ 3 active     │ ○──             ││
│    ◄ YOU    │  └──────────────┴──────────────┴─────────────────┘│
│  ● Learning │                                                   │
│  ...        │  ⚡ QUICK ACTIONS                                │
│             │  [Log Mood] [Add Habit] [Schedule Family]        │
│  SUBAGENTS  │                                                   │
│  ─────────  │  📝 RECENT ACTIVITY                             │
│  ○ fitness  │  • Logged morning run (6am)                     │
│    coach    │  • Completed "Read 30 min" habit               │
│  ○ meal     │  • Scheduled dinner with Sarah (May 2)          │
│    planner  │  • Water intake: 1.8L (below target)            │
│             └───────────────────────────────────────────────────┘
└─────────────────────────────────────────────────────────────────┘
```

---

## Build Order (6 Weeks)

| Week | Deliverable | Pages |
|------|-------------|-------|
| **Week 1** | Scaffold + Layout | Sidebar, routing, Dashboard skeleton, theme |
| **Week 2** | Dashboard + Agent Status | AgentStatusCard, BridgePanel, ActivityFeed, global search |
| **Week 3** | IDE + Memories | TerminalWidget (xterm.js), chat panel, MemPalace explorer, LatticeGraph |
| **Week 4** | Calendar + Tasks + Personal | Calendar view, Kanban board, Personal metrics |
| **Week 5** | Finance + Learning + Tech + Social + Research | 5 domain pages with specific metrics |
| **Week 6** | Business + Work + Polish | Paperclip WebView, Multica WebView, themes, packaging |

---

## Design System

### Colors (Dark Theme)

```css
:root {
  --bg-primary: #0d1117;
  --bg-secondary: #161b22;
  --bg-tertiary: #21262d;
  --text-primary: #f0f6fc;
  --text-secondary: #8b949e;
  --text-muted: #6e7681;
  --accent-blue: #58a6ff;
  --accent-green: #3fb950;
  --accent-yellow: #d29922;
  --accent-red: #f85149;
  --accent-purple: #a371f7;
  --border: #30363d;
}
```

### Typography

- Font: Inter (sans) + Fira Code (mono)
- Sizes: 12px (body), 14px (headers), 10px (labels)

### Spacing

- 4px grid: margins/padding in multiples of 4
- Components: 8px, 12px, 16px, 24px gaps

---

## State Management

### Zustand Stores

```typescript
// stores/agentStore.ts
interface AgentStore {
  agents: Agent[];
  bridges: Bridge[];
  selectedAgent: string | null;
  setSelectedAgent: (id: string) => void;
  updateAgentStatus: (id: string, status: AgentStatus) => void;
}

// stores/taskStore.ts
interface TaskStore {
  tasks: Task[];
  columns: { backlog: Task[]; inProgress: Task[]; review: Task[]; done: Task[] };
  moveTask: (taskId: string, from: string, to: string) => void;
}

// stores/uiStore.ts
interface UIStore {
  theme: 'dark' | 'light';
  sidebarOpen: boolean;
  toggleSidebar: () => void;
  setTheme: (theme: 'dark' | 'light') => void;
}
```

### React Query Hooks

```typescript
// hooks/useAgents.ts
export const useAgents = () => {
  return useQuery(['agents'], () => athenaClient.getAgents());
};

export const useAgentStatus = (agentId: string) => {
  return useQuery(['agent', agentId], () => athenaClient.getAgentStatus(agentId), {
    refetchInterval: 5000, // Poll every 5 seconds
  });
};

// hooks/useBridges.ts
export const useBridges = () => {
  return useQuery(['bridges'], () => athenaClient.getBridges());
};

// hooks/useTasks.ts
export const useTasks = (agentId?: string) => {
  return useQuery(['tasks', agentId], () => athenaClient.getTasks(agentId));
};
```

---

## Integration Points

### Athena HTTP API Client

```typescript
// api/athenaClient.ts
class AthenaClient {
  private baseUrl = 'http://localhost:8000';
  
  async getAgents(): Promise<Agent[]> { ... }
  async getAgentStatus(id: string): Promise<AgentStatus> { ... }
  async getBridges(): Promise<Bridge[]> { ... }
  async getTasks(agentId?: string): Promise<Task[]> { ... }
  async executeTask(bridge: string, task: any): Promise<any> { ... }
  async spawnSubagent(agentName: string, data: any): Promise<any> { ... }
}

export const athenaClient = new AthenaClient();
```

### WebView Integration

```typescript
// components/WebViewContainer.tsx
interface WebViewContainerProps {
  src: string;
  jwt?: string;
  onMessage?: (event: any) => void;
}

const WebViewContainer: React.FC<WebViewContainerProps> = ({ src, jwt, onMessage }) => {
  const webviewRef = useRef<Electron.WebviewTag>(null);
  
  useEffect(() => {
    const webview = webviewRef.current;
    if (!webview) return;
    
    // Inject JWT for auth
    if (jwt) {
      webview.addEventListener('did-finish-load', () => {
        webview.executeJavaScript(`localStorage.setItem('token', '${jwt}');`);
      });
    }
    
    // Listen for messages from webview
    if (onMessage) {
      webview.addEventListener('ipc-message', onMessage);
    }
  }, [jwt, onMessage]);
  
  return (
    <webview
      ref={webviewRef}
      src={src}
      style={{ width: '100%', height: '100%' }}
    />
  );
};
```

---

## Key Features

1. **Real-time Updates**: WebSocket or polling every 5s for agent/bridge status
2. **Global Search**: Cmd+K to search across all agents, tasks, memories
3. **Terminal**: xterm.js with PTY bridge to spawn bridge processes
4. **Code Editor**: Monaco Editor for viewing/editing code
5. **Kanban Board**: Drag-and-drop tasks between columns
6. **Lattice Visualization**: Cytoscape.js graph of agent relationships
7. **Delta Reports**: Show changes in agent state over time
8. **Subagent Spawning**: Form + governance workflow for creating subagents
9. **Multi-platform WebView**: Embed Paperclip and Business UIs seamlessly
10. **Theme System**: Dark/light mode with GitHub-inspired palette

---

*Signed-off-by: Hermes Agent <hermes@nousresearch.com>*
*Date: 2026-09-16*
