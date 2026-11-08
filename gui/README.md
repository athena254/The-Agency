# Mission Control — The Agency console

The Agency's operator interface: a React 18 + TypeScript single-page app built
with Vite and Tailwind CSS. It is served by the Agency FastAPI prototype app at
`/mission-control` and wires to the loopback owner-pilot surface.

## Stack

- React 18 + TypeScript
- Vite (build) + `@tailwindcss/vite` (Tailwind v4)
- Zustand (UI state) + React Query (server state)
- React Router (client routing, basename `/mission-control`)
- Lucide icons

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

## Design

The design system lives in `src/index.css` (tokens under `@theme`). It is a
console, not a marketing page: one signal colour, monospace data, quiet chrome,
and hierarchy carried by weight and spacing. Keep pages consistent with it and
avoid the generic dark-SaaS card kit.

Pages under `src/routes/` map to the console's navigation. Features behind the
peer-authority gate (P2) render an honest "locked" state rather than a fake or
disabled control.

## Notes

- The owner bearer token is held in memory only (see `src/stores/auth.ts`).
- The main `/v1` business routes are closed until peer authority is available;
  the console shows locked states for those surfaces.
