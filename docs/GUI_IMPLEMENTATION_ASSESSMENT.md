# GUI Implementation Assessment — The Agency

Internal working document. Source of truth: the checked-out repository at `feat/wave1-on-main`
(HEAD `5bf5f3a`). All claims below were verified by reading code and docs, not inferred from page names.

---

## 1. Existing frontend architecture

| Concern | Reality |
|---|---|
| Location | `gui/src/` — the only React app. `agency-landing/` is a static HTML page; `src/agency/prototype/static/` holds vanilla-JS legacy UI plus the **committed build output** of `gui/`. |
| Stack (from `gui/package.json` + lockfile) | React **19.3.1**, React DOM 19.3.1, TypeScript **7.0.2**, Vite **8.3.6**, Tailwind **4.3.5** (`@tailwindcss/vite`), React Router **7.18.5**, TanStack Query **5.107.1**, Zustand **5.1.0**, lucide-react 1.70.0, oxlint 1.90→1.92. |
| Docs claim | Docs say "React 18" (`gui/README.md:3`, `docs/CURRENT_STATE.md:47`); source is authoritative. |
| Scripts (`gui/package.json:6-11`) | `npm run dev` → `vite`; `npm run build` → `tsc -b && vite build`; `npm run lint` → `oxlint`; `npm run preview`. **No test script, no test dependencies.** |
| Build output (`gui/vite.config.ts`) | `base: '/assets/mission-control/'`, outDir `../src/agency/prototype/static/mission-control`. Served by the prototype app at `/mission-control` (`src/agency/prototype/app.py:495-505`). |
| Dev proxy (`gui/vite.config.ts:19-24`) | `/api` → `127.0.0.1:8001`, `/v1` → `127.0.0.1:8000`. **Stale:** current launcher binds **8766** (`docs/OWNER_PILOT_LAUNCHER.md:26`). |
| Routing (`gui/src/App.tsx`) | `BrowserRouter basename="/mission-control"`. 13 static routes, no lazy loading, no guards. `PageShell` = `Topbar` + scrollable `<main>`. |
| State | One Zustand store (`stores/auth.ts`) holding an **in-memory-only** owner bearer token + auth notice. TanStack Query for all reads (polling 10–30s). No `useMutation`, no cache invalidation; writes are raw async + `useState` + manual `refetch()`. |
| API layer | `api/client.ts` (fetch wrapper, `ApiError`), `api/pilot.ts` (pilot endpoints + `useCsrf`), `api/observe.ts` (read-only observe views), `stores/auth.ts:pilotRequest` (bearer + `X-Prototype-CSRF`). No `/v1` calls in the GUI. |
| Styling | `gui/src/index.css`: Tailwind v4 `@theme` tokens (`ink-*`, `line`, `text-*`, `signal*`, `amber`, `green`, `red`, `purple`, fonts, radii) + `@layer components` `mc-*` classes + reduced-motion rule. **No `tailwind.config.*`, no postcss config, no path aliases, no CSS modules.** No inline styles or hardcoded colors in TSX. |
| Tests | No frontend tests exist. Backend has extensive pytest coverage of the endpoints; `tests/*ui.test.mjs` tests the *legacy static* UIs, not `gui/src`. `FACTORY_RULES.md:10` requires browser/UI testing for UI changes. |

---

## 2. Route inventory and current page state

| Route | Page | Data / behaviour | State |
|---|---|---|---|
| `/` | Dashboard `/` | `/api/health`, `/api/agents`, pilot capabilities/spending | **Mixed.** Health strip + agent list real. `dashboard-stats` query is `Promise.resolve({agents:0,tasks:0,memories:0,evidence:0})` and the Activity feed is three hard-coded JSX items — **fabricated telemetry** (`Dashboard.tsx:36-40,129-147`). No loading/error branches. |
| `/observability` | Observability | 3 read-only GETs: overview/skills/tools | **Implemented, honest.** Per-section `available`/`reason` degradation, all four states. 9 sections. Best-built page in the app. |
| `/ide` | IDE | `/api/session` + `POST /api/chat` | **Implemented**, model-only chat. Honest "no tools/memory/Lattice" disclosures. Hard-coded model `mercury-3` and assistant role `remex`. Busy state shows a `Square` icon that is **not** a stop control (button disabled). |
| `/agents` | Agents | `/api/agents` | **Implemented**, read-only table. Status dot unconditionally green. No refresh, filters, loading, or error state. |
| `/tasks` | Tasks | pilot tasks/threads/verification + calculator submit/cancel | **Implemented**, fully wired, `PilotGate`. Good loading/empty/error coverage. |
| `/coding` | Coding | coding-status + threads + coding task submit/receipt | **Implemented**, runtime-gated by `status.data.available` → `LockedPanel`. Hard-coded `profile_id`/default task text. |
| `/threads` | Threads | threads + messages, create thread | **Implemented**, `PilotGate`. No message composer (by design). |
| `/memories` | Memories | none | **LockedPanel** stub, but **not marked locked in nav**. |
| `/evidence` | Evidence | none | **LockedPanel** stub (a real read-only evidence view exists on Observability but is unused here). Not marked in nav. |
| `/skills` | Skills | none | **LockedPanel** stub (real skill versions shown on Observability). Not marked in nav. |
| `/lattice` | Lattice | none | **LockedPanel** with P2 badge in nav. |
| `/forge` | Forge | none | **LockedPanel** + hard-coded illustrative CLI transcript. P2 badge in nav. |
| `/settings` | Settings | `/api/health` + token form | **Implemented**. Only control in the app with an `aria-label`. Static "authority model" copy. |
| `*` | — | redirect to `/` | — |

**Nav items are 13; 5 pages are locked stubs; 8 are functional.**

---

## 3. Backend capabilities (verified)

Two FastAPI apps:
- **Prototype app** `agency.prototype.app` — serves `/mission-control`, `/api/*` (pilot + observe), port **8766** per launcher (GUI proxy still says 8001). Owns the GUI.
- **Main API** `agency.api.server` — port 8000; every `/v1/*` route except `GET /v1/health` and ballot POST returns **503 "Peer authorization is unavailable"** (`server.py:282-299`).

Real, durable pilot data (SQLite `pilot.db`, `quota.db`, `audit.db`, optional `spend.db`):
threads/messages, tasks with replay & terminal immutability, task-scoped cancel, calculator verification receipts, coding fixture receipts, capabilities, spending snapshot, read-only observability overview/skills/tools, health, agent roster, session CSRF, isolated `/api/chat` (model call, no tools/memory/Lattice).

Honest-by-design limitations:
- `observe` risk numbers are `heuristic_only=true`, `legacy_risk_engine/v1`.
- Coding is an opt-in single-fixture (`literal-assignment-v1`), `tests_run`/`release_authorized` fixed false.
- Agent/evidence/memory/skill/tool stores are in-memory in the launcher deployment; observe degrades honestly.
- `/api/health` has **no `components` field** — Dashboard's "Components online 0/0" reads a nonexistent key (`docs/CURRENT_STATE.md:52`).

Backend capabilities **not exposed** in GUI: `GET /api/pilot/tool-status`, standalone observe sub-routes, generic `POST /api/pilot/tasks`, opt-in tool configuration/invocation, `/v1` routes, legacy `/pilot` UI, CLI/Forge/Tui/Lattice subsystems.

Gates: **P1** request identity/budget/audit (incomplete), **P2** peer application authority (**core blocker**, `/v1` closed), **P3** isolation/storage/privacy, **P4** capability lifecycle. Decisions D-01…D-05 (approved) define the 4-validator/3-of-4 quorum, signed membership, attributable inputs, task-scoped grants, and local-only bounded resources. Docs are emphatic: locked surfaces must show honest locked states, never fake controls; placeholder telemetry must become live data or honest empty states.

---

## 4. UI/UX gap analysis

**Trust & honesty (highest severity)**
1. Dashboard `stats` query and Activity feed are fabricated (`Dashboard.tsx:36-40,129-147`) — directly violates the product's anti-fabrication identity and `KNOWN_ISSUES.md` KI-05.
2. Health "Components online" reads a field the backend never returns — a permanently wrong 0/0.
3. IDE shows `Square` (stop icon) on a disabled button; no stop action exists.
4. Locked pages (Memories/Evidence/Skills) are not marked in nav, so they appear operational until clicked.
5. Hard-coded editorial assertions ("Loopback · owner", "Local runtime v0.1.0", "D-01–D-05 approved", "mercury-3") are not derived from data.

**Interaction & state coverage**
6. Agents, Settings, Dashboard lack loading/error states.
7. Refresh affordances are duplicated ad hoc (`RefreshCw` in Tasks/Threads/Observability) instead of one primitive.
8. No global search, command menu, notifications, breadcrumbs, or toasts — consistent with current backend capability, so these should NOT be invented.

**Accessibility (WCAG 2.2 AA)**
9. Only one `aria-label` in the whole app (Settings eye toggle).
10. Sidebar nav lacks `aria-current`; locked nav items lack `aria-disabled`.
11. No skip link; `<main>` has no id; no landmark labeling; icon-only refresh buttons have no accessible name.
12. No focus-visible styles for inputs/selects/textarea/links (only `.mc-btn` and `.mc-nav-item`).
13. Color-only status encoding (green dot) without text alternatives in some places.

**Responsiveness**
14. Sidebar is fixed `w-60` with no mobile collapse — horizontal overflow on tablet/mobile.
15. Wide two-column grids use `xl:`/`lg:` only; no graceful stacking for narrow widths in all pages.
16. Tables (Agents) have no responsive treatment.

**Design system**
17. Tokens exist but are incomplete: no spacing scale, no typography scale, no shadow/z-index/transition tokens, no disabled token, no semantic state tokens beyond 4 colors.
18. `mc-panel-raised` is defined but unused; component primitives are duplicated across pages (`PanelRow`, `Chip`, `StateChip`, `EmptyState`, `LoadingBlock`, `PilotErrorNote`, `Header`, `Section` are each local to one or two files).
19. Two chat icons, three icons for "Tasks", `Shield`/`ShieldCheck` ambiguity, `Gauge` overload, inconsistent icon typing (`typeof X` vs `LucideIcon`).

**Technical risks**
20. Vite dev proxy port (8001) mismatches launcher (8766) — local verification needs a corrected proxy or a launcher on 8001.
21. No frontend tests and no browser tooling installed; browser verification requires adding a runner or using an external harness.
22. TS 7 + Vite 8 are very new; strict flags (`noUnusedLocals`, `erasableSyntaxOnly`) mean casual edits break `tsc -b`.
23. Token is memory-only: a refresh logs the user out — expected, but UX must communicate it.
24. `enabled: Boolean(token)` silently renders nothing in `PilotOverview` panels if used outside `PilotGate`.

---

## 5. Proposed information architecture (no new routes)

Keep the 13 real routes. Group nav into labelled sections so the hierarchy is legible without adding unavailable features:

- **Operate** — Dashboard (`/`), Observability (`/observability`), IDE (`/ide`)
- **Work** — Threads (`/threads`), Tasks (`/tasks`), Coding (`/coding`), Agents (`/agents`)
- **Inspect** — Evidence, Memories, Skills (locked, marked as such)
- **Platform** — Lattice, Forge (locked), Settings

Mark all five locked routes with `aria-disabled` + a visible lock/P2 badge; keep them navigable only to their explanatory `LockedPanel` (honest, not fake-operational). Do not add items for nonexistent functionality.

Per-page journey contract to apply: objective → primary information → primary action → secondary actions → failure modes → result feedback → recovery. Every data-driven page must implement loading / refresh / empty / unavailable / error / locked / offline states.

---

## 6. Recommended implementation order

1. **Phase 3 — Design system** (this doc → tokens, typography, primitives). Lowest risk, unblocks everything.
2. **Trust fixes** — remove fabricated Dashboard telemetry; derive real counts from pilot tasks/agents; honest empty states; fix health `components`; remove fake stop affordance.
3. **Phase 4 — Shell** — responsive sidebar/drawer, nav grouping, `aria-current`, skip link, focus styles, labelled landmarks, consistent page headers.
4. **Phase 5 — Pages**, highest value first: Dashboard → Observability → Tasks → Threads → Coding → Agents → IDE → Settings → locked pages. Each: real data, all states, accessibility, responsive check.
5. **Phase 6 — Visual refinement** with real browser screenshots at desktop/laptop/tablet/mobile.
6. **Phase 7 — QA**: `npm run lint`, `npm run build` (includes `tsc -b`), then browser interaction checks. Add a minimal test runner only if needed to satisfy `FACTORY_RULES.md:10`.
7. **Phase 8 — Honest completion report** distinguishing implemented / unavailable / unverified / blocked.

---

## 7. Critical risks to watch

- Any change that makes a locked surface look operational, or displays unverified success, violates the product's core identity — reject it.
- Never hardcode backend data; never bypass bearer/CSRF; keep the token out of storage, logs and URLs.
- Preserve React Query/Zustand patterns; do not add a redundant state layer or replace the framework.
- `tsc -b` with TS 7 strict flags is the build gate — no suppressed errors.
- The Vite proxy port must be reconciled before browser verification.
