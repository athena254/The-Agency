# GUI Implementation Report — The Agency

Status: **implemented and verified within the current backend gates**. This is an honest
completion report; see `docs/GUI_IMPLEMENTATION_ASSESSMENT.md` for the Phase 1 assessment.

## What changed

**Design system (`gui/src/index.css`)**
- Added semantic state tokens (success/warning/danger/info/disabled), spacing, type scale,
  overlay shadow, z-index layers and transition tokens alongside the existing Tailwind v4 `@theme` palette.
- Added reusable primitives: `mc-icon-btn`, `mc-select`, `mc-textarea`, `mc-badge`,
  `mc-notice` (+ four tones), `mc-table-wrap`, `mc-skeleton`, `mc-divider`, `mc-skip-link`,
  `mc-overlay`, and typography classes (`mc-page-title`, `mc-section-title`, `mc-card-title`, `mc-body`, `mc-caption`).
- One global `:focus-visible` treatment now covers links, buttons, inputs, selects, textareas
  and focusable elements; disabled states and reduced-motion are respected.

**Application shell (`gui/src/App.tsx`, `gui/src/components/Sidebar.tsx`)**
- Nav regrouped into Operate / Work / Inspect / Platform. All five locked pages are now visibly
  marked (lock icon + gate badge) and exposed to assistive tech via `aria-disabled`, screen-reader text and tooltips.
- Responsive off-canvas navigation with hamburger, backdrop, close button and Escape-to-close;
  desktop sidebar unchanged in placement.
- Skip link, focusable `<main id="main-content">`, real heading hierarchy, honest topbar
  session-token state (replacing a decorative static label), status footer de-decorated.

**Trust fixes (`gui/src/routes/Dashboard.tsx`)**
- Removed the fabricated `dashboard-stats` query and the three hard-coded Activity feed items.
- Task counts now come from `GET /api/pilot/tasks` when an owner token is set; otherwise `—` with an explicit "Owner token required" note. Memories/Evidence show `—` with gate notes instead of fake zeros.
- Removed the "Components online 0/0" metric that read a field the backend never returns; replaced with an honest "not reported" explanation.
- Added real loading/error states, a refresh control, agent-status-aware dots, and a "Status snapshot" panel that states it is a snapshot, not an event timeline.
- IDE no longer claims a specific unconfigured model (`mercury-3` → "model configured by runtime") and no longer shows a non-functional stop icon; assistant identity is "Butler", not a hard-coded alias.

**Accessibility / interaction states across all pages**
- Agents: loading/error/retry, labelled table with `scope="col"`, responsive table wrapper, real status tones.
- Tasks, Threads, Coding: labelled form controls, `aria-label` on icon-only buttons, `aria-busy` on submit, `role="status"`/`role="alert"`, `aria-live` result regions. No logic or endpoint changes.
- Observability: labelled refresh, live regions, status footer. PilotGate/LockedPanel: role semantics and screen-reader clarification. Forge transcript explicitly marked illustrative.
- Settings: associated token label, live status region, autocomplete/spellcheck hardened. Token handling unchanged (memory-only).

**Documentation**
- `docs/GUI_IMPLEMENTATION_ASSESSMENT.md` — architecture, route inventory, capability matrix, gap analysis, IA, phased plan, risks.
- `docs/GUI_IMPLEMENTATION_REPORT.md` — this report.
- Rebuilt committed static assets under `src/agency/prototype/static/mission-control/` to match the source.

## Backend integrations

No backend code, endpoints, contracts or authorization paths were changed. No new dependencies
were added. No `/v1` calls were introduced. The GUI still uses the existing `/api/*` prototype
surface with bearer + CSRF via the existing Zustand store and TanStack Query.

## Tests executed (actually run)

| Check | Result |
|---|---|
| `npm run build` (`tsc -b && vite build`) | PASS — TS 7 strict build clean |
| `npm run lint` (`oxlint`) | PASS — 0 warnings, 0 errors |
| `pytest tests/test_prototype_app.py -q` | 33 passed |
| Playwright (Edge, headless) across 1440×900, 1280×800, 820×1180, 390×844 | 0 horizontal overflow, 0 console errors |
| Browser assertions | fabricated activity absent; 5 locked nav items marked; skip link is first Tab stop; mobile drawer opens and closes with Escape; 6 real agent rows; refresh/locked-page semantics present |

Browser screenshots were captured and visually reviewed (`ui-desktop.png`, `ui-mobile.png`,
`ui-mobile-drawer.png`, `ui-ide.png`, `ui-agents.png`, `ui-locked.png`).

## Honest limitations / remaining issues

- **No frontend test suite exists** in this repo, so no unit or component tests were added;
  browser verification used an external, temporary Playwright harness (not committed).
- **No full axe/WCAG audit was run.** Accessibility work was manual (semantics, labels, focus,
  contrast-conscious tokens, reduced motion) and browser-verified; formal automated auditing remains.
- **Pilot pages could not be exercised with a real owner token** — no credentials were available.
  Those pages were rendered in their honest locked state and were not claimed as verified end-to-end.
- Memories, Evidence, Skills, Lattice and Forge remain locked by design (P1–P4 gates). No fake controls were added.
- Coding remains the single `literal-assignment-v1` fixture; no test or release success is implied.
- The Vite dev proxy still targets port **8001** while the supported launcher defaults to **8766**.
  Left unchanged deliberately: changing it would break the existing 8001 dev workflow; reconcile with the team.
- The `gui/README.md` and several docs still say "React 18"; the installed version is React 19.3.1. Docs were not broadly rewritten.
- No new features were invented: no global search, command palette, toasts, notifications or breadcrumbs were added, because the current backend does not support them.
