import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { defineConfig, devices } from '@playwright/test'

// End-to-end smoke test (D39): the real UI against the scripted mock backend, no LLM.
// Its own ports, so it never reuses a `make dev` already running on 8000 / 5173, and a fresh
// server per run, because the mock keeps its audit log in memory (INV-MG-001 is the first ID).
const BACKEND_PORT = '8100'
const FRONTEND_PORT = '5180'

export default defineConfig({
  testDir: './e2e',
  // *.e2e.ts, not *.spec.ts: Vitest's default include would pick up a .spec.ts file.
  testMatch: '**/*.e2e.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    // localhost, not 127.0.0.1: Vite may listen on ::1 only.
    baseURL: `http://localhost:${FRONTEND_PORT}`,
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `uv --directory ../backend run uvicorn app.main:app --port ${BACKEND_PORT}`,
      url: `http://127.0.0.1:${BACKEND_PORT}/api/health`,
      // The command line wins over the root .env (AGENT_BACKEND=real there).
      env: { AGENT_BACKEND: 'mock', STATE_DIR: join(tmpdir(), 'revenue-leakage-agent-e2e') },
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: 'npm run dev',
      url: `http://localhost:${FRONTEND_PORT}`,
      env: { BACKEND_PORT, FRONTEND_PORT },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
})
