#!/usr/bin/env node

import { spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import net from 'node:net'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

import { initializeEnvFiles } from './init-env.mjs'

const MINIMUM_NODE_MAJOR = 20
const HEALTH_TIMEOUT_MS = 45_000
const HEALTH_INTERVAL_MS = 500
// Steady-state monitoring does not need sub-second resolution. Poll slowly
// while healthy, then tighten only after a failure so crash recovery stays
// quick without flooding uvicorn access logs during normal development.
const HEALTH_MONITOR_INTERVAL_MS = 15_000
const HEALTH_MONITOR_FAILURE_INTERVAL_MS = 5_000
const HEALTH_MONITOR_FAILURE_LIMIT = 6
// Liveness must tolerate brief event-loop/CPU pressure from long Agent turns.
// A 2-second timeout previously aborted a healthy probe and the supervisor then
// force-killed the backend, guaranteeing an SSE ECONNRESET mid-generation.
const HEALTH_REQUEST_TIMEOUT_MS = 10_000
const HEALTH_FINAL_CONFIRM_TIMEOUT_MS = 30_000
const CHILD_SHUTDOWN_TIMEOUT_MS = 5_000
const BACKEND_RESTART_DELAY_MS = 1_000

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const frontendDir = path.join(repoRoot, 'frontend')
const backendDir = path.join(repoRoot, 'backend')
const children = new Set()

let receivedSignal = null
let resolveSignal
const signalReceived = new Promise((resolve) => {
  resolveSignal = resolve
})

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.once(signal, () => {
    receivedSignal = signal
    resolveSignal(signal)
  })
}

function assertSupportedNode() {
  const major = Number.parseInt(process.versions.node.split('.')[0], 10)
  if (!Number.isInteger(major) || major < MINIMUM_NODE_MAJOR) {
    throw new Error(
      `LearnGraph requires Node.js ${MINIMUM_NODE_MAJOR} or newer; found ${process.versions.node}.`,
    )
  }
}

function parsePort(value, option) {
  const port = Number(value)
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new Error(`${option} must be an integer between 1 and 65535.`)
  }
  return port
}

function parseArguments(argv) {
  const options = {
    backendPort: 8000,
    frontendPort: 5173,
    previewPort: 8001,
    install: false,
    lan: false,
    publicOrigin: process.env.LEARNGRAPH_PUBLIC_ORIGIN?.trim() || '',
    previewPublicOrigin: process.env.LEARNGRAPH_SUBAPP_PREVIEW_ORIGIN?.trim() || '',
    help: false,
  }

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index]
    if (argument === '--') continue
    if (argument === '--install') {
      options.install = true
      continue
    }
    if (argument === '--lan') {
      options.lan = true
      continue
    }
    if (argument === '--help' || argument === '-h') {
      options.help = true
      continue
    }

    const [name, inlineValue] = argument.split('=', 2)
    if (name === '--frontend-port' || name === '--backend-port' || name === '--preview-port') {
      const value = inlineValue ?? argv[++index]
      if (value === undefined) throw new Error(`${name} requires a value.`)
      const port = parsePort(value, name)
      if (name === '--frontend-port') options.frontendPort = port
      else if (name === '--preview-port') options.previewPort = port
      else options.backendPort = port
      continue
    }

    throw new Error(`Unknown option: ${argument}`)
  }

  if (options.frontendPort === options.backendPort) {
    throw new Error('Frontend and backend ports must be different.')
  }
  return options
}

function printHelp() {
  console.log(`Usage: node scripts/dev.mjs [options]

Start the LearnGraph backend, subapp preview, and frontend together.

Options:
  --install                 Install from frontend/package-lock.json and sync backend/uv.lock
  --lan                     Bind all services to all interfaces (explicit remote access)
  --frontend-port <port>    First Vite port to try; uses the next free port if needed (default: 5173)
  --backend-port <port>     Uvicorn main API port (default: 8000)
  --preview-port <port>     Uvicorn subapp preview origin port (default: 8001)
  -h, --help                Show this help

Public access (tunneling / port forwarding) is configured manually via env:
  frontend/.env  LEARNGRAPH_PUBLIC_ORIGIN          public entry for Vite (allowedHosts + origin)
  frontend/.env  LEARNGRAPH_ALLOWED_HOSTS          extra Vite allowed hosts (comma separated)
  backend/.env   LEARNGRAPH_SUBAPP_PREVIEW_ORIGIN  public entry for the subapp preview port`)
}

function canListenOnPort(port) {
  return new Promise((resolve) => {
    const server = net.createServer()
    server.unref()
    server.once('error', () => resolve(false))
    // Probe the same loopback interface Vite uses so an existing 127.0.0.1
    // listener cannot be mistaken for an available port on Windows.
    server.listen({ host: '127.0.0.1', port }, () => {
      server.close(() => resolve(true))
    })
  })
}

async function findAvailableFrontendPort(startPort, backendPort) {
  for (let port = startPort; port <= 65_535; port += 1) {
    if (port === backendPort) continue
    if (await canListenOnPort(port)) return port
  }
  throw new Error(`No available frontend port found from ${startPort} through 65535.`)
}

/** Find a free port for the subapp preview origin, avoiding both app ports. */
async function findAvailablePreviewPort(startPort, backendPort, frontendPort) {
  const envOverride = Number.parseInt(
    process.env.LEARNGRAPH_SUBAPP_PREVIEW_PORT ?? '',
    10,
  )
  const base = Number.isInteger(envOverride) && envOverride > 0 && envOverride <= 65_535
    ? envOverride
    : startPort
  for (let port = base; port <= 65_535; port += 1) {
    if (port === backendPort || port === frontendPort) continue
    if (await canListenOnPort(port)) return port
  }
  throw new Error(`No available preview port found from ${base} through 65535.`)
}

function requireFile(filePath) {
  if (!existsSync(filePath)) {
    throw new Error(`Required file is missing: ${path.relative(repoRoot, filePath)}`)
  }
}

function loadSimpleEnv(filePath) {
  const values = {}
  if (!existsSync(filePath)) return values
  for (const rawLine of readFileSync(filePath, 'utf8').split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith('#')) continue
    const match = /^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(line)
    if (!match) continue
    let value = match[2].trim()
    if (value.length >= 2) {
      const first = value[0]
      const last = value[value.length - 1]
      if ((first === '"' && last === '"') || (first === "'" && last === "'")) {
        value = value.slice(1, -1)
      }
    }
    values[match[1]] = value
  }
  return values
}

function withOriginInCors(existing, origin) {
  if (!origin) return existing
  let origins
  try {
    origins = JSON.parse(existing)
  } catch {
    origins = existing.split(',').map((item) => item.trim()).filter(Boolean)
  }
  if (!Array.isArray(origins)) origins = []
  if (!origins.includes(origin)) origins.push(origin)
  return JSON.stringify(origins)
}

function npmCommand(args) {
  if (process.platform !== 'win32') return { command: 'npm', args }

  // npm is a .cmd shim on Windows. Reuse the npm CLI that launched this
  // script when possible, avoiding shell-specific quoting for repository paths.
  if (process.env.npm_execpath) {
    return {
      command: process.execPath,
      args: [process.env.npm_execpath, ...args],
    }
  }
  return {
    command: process.env.ComSpec || 'cmd.exe',
    args: ['/d', '/s', '/c', 'npm.cmd', ...args],
  }
}

function spawnTracked(label, command, args, options = {}) {
  const child = spawn(command, args, {
    cwd: options.cwd,
    env: options.env ?? process.env,
    stdio: 'inherit',
    windowsHide: true,
    detached: process.platform !== 'win32' && options.detached === true,
  })

  let settled = false
  const exited = new Promise((resolve) => {
    child.once('error', (error) => {
      if (settled) return
      settled = true
      resolve({ label, error, code: null, signal: null })
    })
    child.once('exit', (code, signal) => {
      if (settled) return
      settled = true
      resolve({ label, error: null, code, signal })
    })
  })

  const record = { child, exited, label }
  children.add(record)
  void exited.finally(() => children.delete(record))
  return record
}

async function runChecked(label, invocation, cwd) {
  console.log(`\n==> ${label}`)
  const record = spawnTracked(label, invocation.command, invocation.args, { cwd })
  const result = await record.exited
  if (result.error) {
    throw new Error(`${label} could not start: ${result.error.message}`)
  }
  if (result.code !== 0) {
    throw new Error(
      `${label} failed${result.code === null ? ` after ${result.signal}` : ` with exit code ${result.code}`}.`,
    )
  }
}

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

async function checkHealth(url, timeoutMs = HEALTH_REQUEST_TIMEOUT_MS) {
  const controller = new AbortController()
  const startedAt = Date.now()
  const requestTimeout = setTimeout(
    () => controller.abort(new Error(`liveness probe timed out after ${timeoutMs}ms`)),
    timeoutMs,
  )
  try {
    const response = await fetch(url, {
      cache: 'no-store',
      signal: controller.signal,
    })
    if (!response.ok) {
      return {
        healthy: false,
        problem: `HTTP ${response.status}`,
        durationMs: Date.now() - startedAt,
      }
    }
    const payload = await response.json()
    if (payload?.status === 'ok') {
      return { healthy: true, durationMs: Date.now() - startedAt }
    }
    return {
      healthy: false,
      problem: `HTTP ${response.status} returned status ${JSON.stringify(payload?.status)}`,
      durationMs: Date.now() - startedAt,
    }
  } catch (error) {
    return {
      healthy: false,
      problem: error instanceof Error ? error.message : String(error),
      durationMs: Date.now() - startedAt,
    }
  } finally {
    clearTimeout(requestTimeout)
  }
}

async function pollHealth(url) {
  const deadline = Date.now() + HEALTH_TIMEOUT_MS
  let lastProblem = 'no response'

  while (Date.now() < deadline) {
    const check = await checkHealth(url)
    if (check.healthy) return
    lastProblem = check.problem
    await wait(HEALTH_INTERVAL_MS)
  }

  throw new Error(`Backend did not become healthy within 45 seconds (${lastProblem}): ${url}`)
}

// The uvicorn --reload supervisor stays alive when its worker crashes, so the
// backend process exiting is not a reliable "backend is down" signal. Keep
// polling the health endpoint and report when it stops answering.
function watchHealth(url) {
  let stopped = false
  const unhealthy = (async () => {
    let failures = 0
    let lastProblem = 'no response'
    while (!stopped) {
      // Healthy backends only need a coarse heartbeat. Once a probe fails,
      // switch to the short interval so consecutive failures still surface
      // within roughly HEALTH_MONITOR_FAILURE_LIMIT * failure interval.
      const intervalMs =
        failures > 0
          ? HEALTH_MONITOR_FAILURE_INTERVAL_MS
          : HEALTH_MONITOR_INTERVAL_MS
      await wait(intervalMs)
      if (stopped) break
      const check = await checkHealth(url)
      if (check.healthy) {
        failures = 0
        continue
      }
      failures += 1
      lastProblem = `${check.problem}; probe ${check.durationMs}ms`
      if (failures >= HEALTH_MONITOR_FAILURE_LIMIT) {
        // A destructive restart aborts every in-flight Agent and SSE connection.
        // Confirm with one long probe instead of treating a short scheduling
        // delay as proof that the uvicorn worker is dead.
        const confirmation = await checkHealth(
          url,
          HEALTH_FINAL_CONFIRM_TIMEOUT_MS,
        )
        if (confirmation.healthy) {
          console.warn(
            `\nBackend liveness recovered during final confirmation ` +
              `(${confirmation.durationMs}ms): ${url}`,
          )
          failures = 0
          continue
        }
        lastProblem = `${confirmation.problem}; final probe ${confirmation.durationMs}ms`
        return `Backend liveness failed after ${HEALTH_MONITOR_FAILURE_LIMIT} probes (${lastProblem}): ${url}.`
      }
    }
    return null
  })()
  return {
    unhealthy,
    stop: () => {
      stopped = true
    },
  }
}

function waitForExit(child, timeoutMs) {
  return Promise.race([
    child.exited.then(() => true),
    wait(timeoutMs).then(() => false),
  ])
}

async function runQuietly(command, args) {
  await new Promise((resolve) => {
    const child = spawn(command, args, {
      stdio: 'ignore',
      windowsHide: true,
    })
    child.once('error', resolve)
    child.once('close', resolve)
  })
}

async function stopChild(record) {
  const { child } = record
  if (!child.pid) return

  if (process.platform === 'win32') {
    await runQuietly('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'])
    await waitForExit(record, CHILD_SHUTDOWN_TIMEOUT_MS)
    return
  }

  try {
    process.kill(-child.pid, 'SIGTERM')
  } catch {
    return
  }
  if (await waitForExit(record, CHILD_SHUTDOWN_TIMEOUT_MS)) return

  try {
    process.kill(-child.pid, 'SIGKILL')
  } catch {
    // The process group exited between the timeout and the forced cleanup.
  }
  await waitForExit(record, 1_000)
}

async function stopAllChildren() {
  const active = [...children]
  if (active.length === 0) return
  console.log('\nStopping LearnGraph services...')
  await Promise.allSettled(active.map(stopChild))
}

// Placeholder removed — sandboxd is started inside run() via startSandboxd.

function describeExit(result) {
  if (result.error) return `${result.label} could not start: ${result.error.message}`
  if (result.signal) return `${result.label} exited after ${result.signal}.`
  return `${result.label} exited with code ${result.code}.`
}

async function main() {
  assertSupportedNode()
  const options = parseArguments(process.argv.slice(2))
  if (options.help) {
    printHelp()
    return
  }

  requireFile(path.join(frontendDir, 'package.json'))
  requireFile(path.join(frontendDir, 'package-lock.json'))
  requireFile(path.join(backendDir, 'pyproject.toml'))
  requireFile(path.join(backendDir, 'uv.lock'))
  initializeEnvFiles({ backendDir, frontendDir, repoRoot })

  const frontendEnv = loadSimpleEnv(path.join(frontendDir, '.env'))
  const backendEnv = loadSimpleEnv(path.join(backendDir, '.env'))
  const publicOrigin = (options.publicOrigin || frontendEnv.LEARNGRAPH_PUBLIC_ORIGIN || '').trim()
  const previewPublicOrigin = (
    options.previewPublicOrigin ||
    backendEnv.LEARNGRAPH_SUBAPP_PREVIEW_ORIGIN ||
    ''
  ).trim()
  const allowedHosts = new Set()
  for (const raw of [
    process.env.LEARNGRAPH_ALLOWED_HOSTS,
    frontendEnv.LEARNGRAPH_ALLOWED_HOSTS,
  ]) {
    if (!raw) continue
    for (const host of raw.split(',')) {
      const trimmed = host.trim()
      if (trimmed) allowedHosts.add(trimmed)
    }
  }

  if (options.install) {
    await runChecked('Installing frontend dependencies from package-lock.json', npmCommand(['ci']), frontendDir)
    await runChecked(
      'Synchronizing backend dependencies from uv.lock',
      { command: 'uv', args: ['sync', '--locked'] },
      backendDir,
    )
  } else if (!existsSync(path.join(frontendDir, 'node_modules'))) {
    throw new Error(
      'Frontend dependencies are not installed. Run "npm run dev:install" once for a clean checkout.',
    )
  }

  if (receivedSignal) return

  const frontendPort = await findAvailableFrontendPort(options.frontendPort, options.backendPort)
  if (frontendPort !== options.frontendPort) {
    console.log(`Frontend port ${options.frontendPort} is in use; using ${frontendPort} instead.`)
  }

  const listenHost =
    process.env.LEARNGRAPH_LISTEN_HOST?.trim() ||
    frontendEnv.LEARNGRAPH_LISTEN_HOST?.trim() ||
    (options.lan ? '0.0.0.0' : '127.0.0.1')
  if (listenHost !== '127.0.0.1' && listenHost !== 'localhost' && listenHost !== '::1') {
    console.warn(
      `\nWARNING: LearnGraph development services are exposed on ${listenHost}. ` +
        'Use trusted networks only; LAN access does not grant host-device capabilities.',
    )
  }
  const backendOrigin = `http://127.0.0.1:${options.backendPort}`
  const frontendOrigin = `http://127.0.0.1:${frontendPort}`
  // Default to same-origin '/' so the browser calls the Vite dev proxy and
  // CORS never applies, whichever port the frontend lands on. An explicit
  // VITE_API_BASE_URL still opts into calling the backend directly.
  const apiBaseUrl = process.env.VITE_API_BASE_URL?.trim() || frontendEnv.VITE_API_BASE_URL?.trim() || '/'
  const configuredCorsOrigins =
    process.env.LEARNGRAPH_CORS_ORIGINS?.trim() ||
    backendEnv.LEARNGRAPH_CORS_ORIGINS?.trim() ||
    JSON.stringify([
      `http://localhost:${frontendPort}`,
      frontendOrigin,
    ])
  const corsOrigins = withOriginInCors(configuredCorsOrigins, publicOrigin)

  // Subapp preview origin: a separate process on its own port so the preview
  // iframe origin is distinct from the main API origin. The main backend mints
  // bundle capability URLs against this origin.
  const previewPort = await findAvailablePreviewPort(
    options.previewPort,
    options.backendPort,
    frontendPort,
  )
  if (previewPort !== options.previewPort) {
    console.log(`Preview port ${options.previewPort} is in use; using ${previewPort} instead.`)
  }
  const previewOrigin = `http://${listenHost === '0.0.0.0' ? '127.0.0.1' : listenHost}:${previewPort}`

  // sandboxd control plane (Phase 2+): when the backend is configured with
  // LEARNGRAPH_SANDBOX_BACKEND=sandboxd, manage a local daemon process so the
  // app never talks to Docker Engine directly. A development token is
  // generated under the ignored .sandboxd/ directory.
  const sandboxdEnabled =
    (process.env.LEARNGRAPH_SANDBOX_BACKEND?.trim() || backendEnv.LEARNGRAPH_SANDBOX_BACKEND?.trim() || 'docker') === 'sandboxd'

  const startSandboxd = () => {
    const sandboxdDir = path.join(repoRoot, 'sandboxd')
    const tokenDir = path.join(backendDir, 'data', '.sandboxd')
    const tokenFile = path.join(tokenDir, 'sandboxd-token')
    const stateFile = path.join(tokenDir, 'state.db')
    mkdirSync(tokenDir, { recursive: true })
    if (!existsSync(tokenFile)) {
      writeFileSync(tokenFile, randomBytes(32).toString('hex'), { mode: 0o600 })
    }
    const sandboxdPort = Number(process.env.LEARNGRAPH_SANDBOXD_PORT?.trim() || 8090)
    console.log(`\nStarting sandboxd at http://127.0.0.1:${sandboxdPort} (backend=${backendOrigin}) ...`)
    return spawnTracked(
      'Sandboxd',
      'uv',
      ['run', '--locked', 'python', '-m', 'sandboxd.main'],
      {
        cwd: sandboxdDir,
        detached: true,
        env: {
          ...process.env,
          SANDBOXD_LISTEN_HOST: '127.0.0.1',
          SANDBOXD_PORT: String(sandboxdPort),
          SANDBOXD_TOKEN_FILE: tokenFile,
          SANDBOXD_STATE_PATH: stateFile,
          SANDBOXD_DEPLOYMENT_ID: process.env.LEARNGRAPH_SANDBOXD_DEPLOYMENT_ID?.trim() || 'dev-local',
          SANDBOXD_RUNTIME_IMAGE: process.env.LEARNGRAPH_SANDBOX_IMAGE?.trim() || '',
          // Local dev: egress proxy is optional; keep the daemon fully offline
          // unless the operator explicitly starts one.
          SANDBOXD_EGRESS_ENABLED: process.env.LEARNGRAPH_SANDBOXD_EGRESS_ENABLED?.trim() || 'false',
        },
      },
    )
  }

  const startBackend = () => {
    console.log(`\nStarting backend at ${backendOrigin} ...`)
    return spawnTracked(
      'Backend',
      'uv',
      [
        'run',
        '--locked',
        'python',
        '-m',
        'uvicorn',
        'app.main:app',
        '--reload',
        '--reload-dir',
        path.join(backendDir, 'app'),
        '--host',
        listenHost,
        '--port',
        String(options.backendPort),
      ],
      {
        cwd: backendDir,
        detached: true,
        env: {
          ...process.env,
          LEARNGRAPH_CORS_ORIGINS: corsOrigins,
          // Keep the local preview origin aligned when the operator has not
          // explicitly configured a real domain (persisted frontend config or
          // LEARNGRAPH_SUBAPP_PREVIEW_ORIGIN still wins inside the backend).
          LEARNGRAPH_SUBAPP_PREVIEW_PORT: String(previewPort),
          ...(sandboxdEnabled
            ? {
                LEARNGRAPH_SANDBOX_BACKEND: 'sandboxd',
                LEARNGRAPH_SANDBOXD_URL: `http://127.0.0.1:${process.env.LEARNGRAPH_SANDBOXD_PORT?.trim() || 8090}`,
                LEARNGRAPH_SANDBOXD_TOKEN_FILE: path.join(backendDir, 'data', '.sandboxd', 'sandboxd-token'),
                LEARNGRAPH_SANDBOXD_DEPLOYMENT_ID: process.env.LEARNGRAPH_SANDBOXD_DEPLOYMENT_ID?.trim() || 'dev-local',
              }
            : {}),
          ...(previewPublicOrigin
            ? { LEARNGRAPH_SUBAPP_PREVIEW_ORIGIN: previewPublicOrigin }
            : {}),
        },
      },
    )
  }

  const startPreview = () => {
    console.log(`\nStarting subapp preview at ${previewOrigin} ...`)
    return spawnTracked(
      'Preview',
      'uv',
      [
        'run',
        '--locked',
        'python',
        '-m',
        'uvicorn',
        'app.preview:preview_app',
        '--reload',
        '--reload-dir',
        path.join(backendDir, 'app'),
        '--host',
        listenHost,
        '--port',
        String(previewPort),
      ],
      {
        cwd: backendDir,
        detached: true,
        env: { ...process.env },
      },
    )
  }

  console.log(`Starting frontend at ${frontendOrigin} ...`)
  const frontendInvocation = npmCommand([
    'run',
    'dev',
    '--',
    '--host',
    listenHost,
    '--port',
    String(frontendPort),
    '--strictPort',
  ])
  const frontend = spawnTracked(
    'Frontend',
    frontendInvocation.command,
    frontendInvocation.args,
    {
      cwd: frontendDir,
      detached: true,
      env: {
        ...process.env,
        VITE_API_BASE_URL: apiBaseUrl,
        LEARNGRAPH_BACKEND_ORIGIN: backendOrigin,
        LEARNGRAPH_PUBLIC_ORIGIN: publicOrigin,
        LEARNGRAPH_ALLOWED_HOSTS: allowedHosts.size > 0 ? [...allowedHosts].join(',') : '',
      },
    },
  )

  // Supervisors must use pure process/event-loop liveness. `/health` also checks
  // SQLite/provider readiness and can legitimately wait while Agent work is busy.
  const backendHealthUrl = `${backendOrigin}/api/v1/livez`
  const previewHealthUrl = `${previewOrigin}/api/v1/livez`
  let announcedReady = false

  // Supervise one child with startup polling + steady-state watch + restart on
  // failure. Resolves only on signal or frontend exit; child restarts loop
  // internally so a crashed service is relaunched without tearing everything down.
  const supervise = async (
    label,
    startFn,
    healthUrl,
    onRecovered,
  ) => {
    let current = startFn()
    while (!receivedSignal) {
      const startup = await Promise.race([
        pollHealth(healthUrl)
          .then(() => ({ type: 'healthy' }))
          .catch((error) => ({ type: 'unhealthy', error })),
        current.exited.then((result) => ({ type: 'exit', result })),
        frontend.exited.then((result) => ({ type: 'frontend-exit', result })),
        signalReceived.then((signal) => ({ type: 'signal', signal })),
      ])

      if (startup.type === 'signal') return
      if (startup.type === 'frontend-exit') {
        throw new Error(describeExit(startup.result))
      }
      if (startup.type === 'exit' || startup.type === 'unhealthy') {
        const problem =
          startup.type === 'exit'
            ? describeExit(startup.result)
            : startup.error instanceof Error
              ? startup.error.message
              : String(startup.error)
        console.error(`\n${problem} Restarting ${label} in 1 second...`)
        if (startup.type === 'unhealthy') await stopChild(current)
        const pause = await Promise.race([
          wait(BACKEND_RESTART_DELAY_MS).then(() => ({ type: 'retry' })),
          frontend.exited.then((result) => ({ type: 'frontend-exit', result })),
          signalReceived.then((signal) => ({ type: 'signal', signal })),
        ])
        if (pause.type === 'signal') return
        if (pause.type === 'frontend-exit') {
          throw new Error(describeExit(pause.result))
        }
        current = startFn()
        continue
      }

      if (!announcedReady) {
        console.log(`\nLearnGraph is ready: ${frontendOrigin}`)
        if (publicOrigin) console.log(`Public entry: ${publicOrigin}`)
        console.log(`API health: ${backendHealthUrl}`)
        console.log(`Subapp preview: ${previewPublicOrigin || previewHealthUrl}`)
        console.log(`OpenAPI: ${backendOrigin}/docs`)
        console.log('Press Ctrl+C to stop all services.')
        announcedReady = true
      } else if (onRecovered) {
        onRecovered()
      }

      const monitor = watchHealth(healthUrl)
      const outcome = await Promise.race([
        monitor.unhealthy.then((problem) => ({ type: 'unhealthy', problem })),
        current.exited.then((result) => ({ type: 'exit', result })),
        frontend.exited.then((result) => ({ type: 'frontend-exit', result })),
        signalReceived.then((signal) => ({ type: 'signal', signal })),
      ])
      monitor.stop()
      if (outcome.type === 'signal') return
      if (outcome.type === 'frontend-exit') {
        throw new Error(describeExit(outcome.result))
      }
      const problem =
        outcome.type === 'exit' ? describeExit(outcome.result) : outcome.problem
      console.error(`\n${problem} Restarting ${label} in 1 second...`)
      if (outcome.type === 'unhealthy') await stopChild(current)
      const pause = await Promise.race([
        wait(BACKEND_RESTART_DELAY_MS).then(() => ({ type: 'retry' })),
        frontend.exited.then((result) => ({ type: 'frontend-exit', result })),
        signalReceived.then((signal) => ({ type: 'signal', signal })),
      ])
      if (pause.type === 'signal') return
      if (pause.type === 'frontend-exit') {
        throw new Error(describeExit(pause.result))
      }
      current = startFn()
    }
  }

  // sandboxd control plane runs alongside the app whenever the backend is
  // configured with LEARNGRAPH_SANDBOX_BACKEND=sandboxd.
  const sandboxd = sandboxdEnabled ? startSandboxd() : null

  // Both backends run concurrently; either one surfacing a fatal condition
  // (signal or frontend exit) propagates to the caller.
  await Promise.race([
    supervise('Backend', startBackend, backendHealthUrl, () => {
      console.log(`\nBackend recovered and is healthy: ${backendHealthUrl}`)
    }),
    supervise('Preview', startPreview, previewHealthUrl),
  ])

  if (sandboxd) {
    stopChild(sandboxd)
  }
}

try {
  await main()
  if (receivedSignal === 'SIGINT') process.exitCode = 130
  if (receivedSignal === 'SIGTERM') process.exitCode = 143
} catch (error) {
  console.error(`\nError: ${error instanceof Error ? error.message : String(error)}`)
  process.exitCode = 1
} finally {
  await stopAllChildren()
}
