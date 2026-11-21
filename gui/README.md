# Mission Control — The Agency console

The Agency's operator interface: a React 19 + TypeScript single-page app built
with Vite and Tailwind CSS 4. It is served by the Agency FastAPI prototype app
at `/mission-control` and wires to the loopback owner-pilot surface.

## Stack

- React 19 + TypeScript
- Vite (build) + `@tailwindcss/vite` (Tailwind v4)
- Zustand (UI state) + React Query (server state)
- React Router (client routing, basename `/mission-control`)
- Lucide icons, self-hosted Inter + JetBrains Mono (`@fontsource/*`)

## Develop

```bash
cd gui
npm install
npm run dev        # http://127.0.0.1:5173/mission-control
```

The dev server proxies `/api` to the prototype surface (8001) and `/v1` to the
main API (8000).

## Build

```bash
npm run build      # outputs to src/agency/prototype/static/mission-control
```

The built assets are committed so a clone can serve the console without a
Node toolchain. Rebuild after changing anything under `gui/`.

## Test

```bash
npm run lint       # oxlint
npm run test:e2e   # Playwright + axe against a real echo-provider backend
```

The E2E suite boots the real prototype app (`AGENCY_PYTHON` overrides the
interpreter; worktrees should point it at the main checkout's `.venv`):
`AGENCY_PYTHON=<venv>/python.exe npm run test:e2e`.

## Design

The design system lives in `src/index.css` (tokens under `@theme` plus the
`--mc-*` semantic layer). Shared React primitives (badges, status dots, rows,
feedback states, confirm dialog, toasts) live in `src/components/ui.tsx` with
tone maps in `src/components/tones.ts`. It is a console, not a marketing page:
one signal colour, monospace data, quiet chrome, and hierarchy carried by
weight and spacing. Keep pages consistent with it and avoid the generic
dark-SaaS card kit. Text must meet WCAG 2.2 AA contrast on the ink surfaces —
axe runs in the E2E suite to keep it that way.

Pages under `src/routes/` map to the console's navigation. Memories, Evidence
and Skills are honest read-only views over the owner-scoped observe surface;
writes stay peer-authority gated. Lattice and Forge render honest locked
states rather than fake or disabled controls.

## Notes

- The owner bearer token is held in memory only (see `src/stores/auth.ts`);
  the shared API client attaches it to `/api` reads (required in pilot mode).
- The main `/v1` business routes are closed until peer authority is available;
  the console shows locked states for those surfaces.
