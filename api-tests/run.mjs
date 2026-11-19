// Black-box API tests (D40): start a fresh mock backend, run the Bruno collection against it
// over real HTTP, write JUnit + HTML reports and stop the server.
//
// Usage: node run.mjs            (from api-tests/, or `make api-test` from the root)
// Extra arguments are passed to `bru run`, e.g. `node run.mjs --bail`.
import { spawn, spawnSync } from 'node:child_process'
import { mkdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
// Its own port (not 8000 from `make dev` nor 8100 from the e2e test), so it never hits a
// server that already has state: the mock keeps the audit log in memory (INV-MG-001 first).
const PORT = '8200'
const HEALTH = `http://127.0.0.1:${PORT}/api/health`
const STATE_DIR = join(tmpdir(), 'revenue-leakage-agent-api-tests')
const REPORTS = join(here, 'reports')

async function isUp() {
  try {
    return (await fetch(HEALTH)).ok
  } catch {
    return false
  }
}

async function waitUntilUp(server, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (server.exitCode !== null) throw new Error(`backend exited with code ${server.exitCode}`)
    if (await isUp()) return
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  throw new Error(`backend not up on ${HEALTH} after ${timeoutMs / 1000} s`)
}

function stop(server) {
  if (server.exitCode !== null) return
  // `uv run` starts uvicorn as a child process: kill the whole tree.
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/pid', String(server.pid), '/T', '/F'], { stdio: 'ignore' })
  } else {
    process.kill(-server.pid, 'SIGTERM')
  }
}

if (await isUp()) {
  console.error(`Port ${PORT} is already serving the API: stop that server first.`)
  process.exit(1)
}

rmSync(STATE_DIR, { recursive: true, force: true })
rmSync(REPORTS, { recursive: true, force: true })
mkdirSync(REPORTS, { recursive: true })

const server = spawn(
  'uv',
  ['--directory', join(here, '..', 'backend'), 'run', 'uvicorn', 'app.main:app', '--port', PORT],
  {
    // The command line wins over the root .env (AGENT_BACKEND=real there).
    env: { ...process.env, AGENT_BACKEND: 'mock', STATE_DIR },
    stdio: ['ignore', 'ignore', 'inherit'],
    detached: process.platform !== 'win32',
  },
)

let code = 1
try {
  await waitUntilUp(server, 120_000)
  const bru = join(here, 'node_modules', '@usebruno', 'cli', 'bin', 'bru.js')
  const args = [
    bru,
    'run',
    '-r',
    '--env',
    'ci',
    '--reporter-junit',
    join(REPORTS, 'junit.xml'),
    '--reporter-html',
    join(REPORTS, 'report.html'),
    ...process.argv.slice(2),
  ]
  const result = spawnSync(process.execPath, args, {
    cwd: join(here, 'collection'),
    stdio: 'inherit',
  })
  code = result.status ?? 1
} catch (error) {
  console.error(String(error))
} finally {
  stop(server)
}
process.exit(code)
