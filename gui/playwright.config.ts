/*
  Playwright configuration for Mission Control.

  The E2E suite runs against the real prototype app (loopback, echo LLM
  provider) so journeys exercise actual endpoints and honesty states —
  never mocks. The backend command mirrors the documented run recipe:

    PYTHONPATH=src <venv-python> -m uvicorn agency.prototype.app:create_app \
      --factory --port 8001

  AGENCY_PYTHON overrides the interpreter (worktrees reuse the main
  checkout's .venv, where the relative default does not exist). The static
  bundle is committed, so the tests can also run against `vite dev`; here we
  serve the FastAPI app directly at /mission-control like production does.
*/

import { defineConfig, devices } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PYTHON =
  process.env.AGENCY_PYTHON ?? path.resolve(HERE, '..', '.venv', 'Scripts', 'python.exe')
const PORT = Number(process.env.AGENCY_E2E_PORT ?? 8211)
const BASE = `http://127.0.0.1:${PORT}`

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  fullyParallel: false,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: BASE,
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['Pixel 7'] } },
  ],
  webServer: {
    command: `"${PYTHON}" -m uvicorn agency.prototype.app:create_app --factory --port ${PORT}`,
    url: `${BASE}/api/health`,
    reuseExistingServer: true,
    timeout: 60_000,
    cwd: path.resolve(HERE, '..'),
    env: {
      PYTHONPATH: 'src',
      AGENCY_LLM_PROVIDER: 'echo',
      AGENCY_LLM_MODEL: 'echo',
      PYTHONIOENCODING: 'utf-8',
    },
  },
})
