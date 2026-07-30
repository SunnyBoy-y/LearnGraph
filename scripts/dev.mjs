#!/usr/bin/env node

import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
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
const HEALTH_MONITOR_FAILURE_INTERVAL_MS = 2_000
const HEALTH_MONITOR_FAILURE_LIMIT = 5
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
    install: false,
    help: false,
  }

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index]
    if (argument === '--') continue
    if (argument === '--install') {
      options.install = true
      continue
    }
    if (argument === '--help' || argument === '-h') {
      options.help = true
      continue
    }

    const [name, inlineValue] = argument.split('=', 2)
    if (name === '--frontend-port' || name === '--backend-port') {
      const value = inlineValue ?? argv[++index]
      if (value === undefined) throw new Error(`${name} requires a value.`)
      const port = parsePort(value, name)
      if (name === '--frontend-port') options.frontendPort = port
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

Start the LearnGraph backend and frontend together.

Options:
  --install                 Install from frontend/package-lock.json and sync backend/uv.lock
  --frontend-port <port>    First Vite port to try; uses the next free port if needed (default: 5173)
  --backend-port <port>     Uvicorn port (default: 8000)
  -h, --help                Show this help`)
}

function canListenOnPort(port) {
  return new Promise((resolve) => {
    const server = net.createServer()
    server.unref()
    server.once('error', () => resolve(false))
    server.listen({ host: '0.0.0.0', port }, () => {
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

function requireFile(filePath) {
  if (!existsSync(filePath)) {
    throw new Error(`Required file is missing: ${path.relative(repoRoot, filePath)}`)
  }
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

async function checkHealth(url) {
  const controller = new AbortController()
  const requestTimeout = setTimeout(() => controller.abort(), 2_000)
  try {
    const response = await fetch(url, { signal: controller.signal })
    if (!response.ok) return { healthy: false, problem: `HTTP ${response.status}` }
    const payload = await response.json()
    if (payload?.status === 'ok') return { healthy: true }
    return {
      healthy: false,
      problem: `HTTP ${response.status} returned status ${JSON.stringify(payload?.status)}`,
    }
  } catch (error) {
    return { healthy: false, problem: error instanceof Error ? error.message : String(error) }
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
      lastProblem = check.problem
      if (failures >= HEALTH_MONITOR_FAILURE_LIMIT) {
        return `Backend stopped responding (${lastProblem}): ${url}.`
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

  // Bind both services to all interfaces so another device on the LAN can
  // reach the dev server.  The browser still uses same-origin /api requests.
  const listenHost = process.env.LEARNGRAPH_LISTEN_HOST?.trim() || '0.0.0.0'
  const backendOrigin = `http://127.0.0.1:${options.backendPort}`
  const frontendOrigin = `http://127.0.0.1:${frontendPort}`
  // Default to same-origin '/' so the browser calls the Vite dev proxy and
  // CORS never applies, whichever port the frontend lands on. An explicit
  // VITE_API_BASE_URL still opts into calling the backend directly.
  const apiBaseUrl = process.env.VITE_API_BASE_URL?.trim() || '/'
  const corsOrigins =
    process.env.LEARNGRAPH_CORS_ORIGINS?.trim() ||
    JSON.stringify([
      `http://localhost:${frontendPort}`,
      frontendOrigin,
    ])

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
        },
      },
    )
  }

  let backend = startBackend()

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
      },
    },
  )

  const healthUrl = `${backendOrigin}/api/v1/health`
  let announcedReady = false
  while (!receivedSignal) {
    const startup = await Promise.race([
      pollHealth(healthUrl)
        .then(() => ({ type: 'healthy' }))
        .catch((error) => ({ type: 'backend-unhealthy', error })),
      backend.exited.then((result) => ({ type: 'backend-exit', result })),
      frontend.exited.then((result) => ({ type: 'frontend-exit', result })),
      signalReceived.then((signal) => ({ type: 'signal', signal })),
    ])

    if (startup.type === 'signal') return
    if (startup.type === 'frontend-exit') {
      throw new Error(describeExit(startup.result))
    }
    if (startup.type === 'backend-exit' || startup.type === 'backend-unhealthy') {
      const problem =
        startup.type === 'backend-exit'
          ? describeExit(startup.result)
          : startup.error instanceof Error
            ? startup.error.message
            : String(startup.error)
      console.error(`\n${problem} Restarting backend in 1 second...`)
      if (startup.type === 'backend-unhealthy') await stopChild(backend)
      const pause = await Promise.race([
        wait(BACKEND_RESTART_DELAY_MS).then(() => ({ type: 'retry' })),
        frontend.exited.then((result) => ({ type: 'frontend-exit', result })),
        signalReceived.then((signal) => ({ type: 'signal', signal })),
      ])
      if (pause.type === 'signal') return
      if (pause.type === 'frontend-exit') {
        throw new Error(describeExit(pause.result))
      }
      backend = startBackend()
      continue
    }

    if (!announcedReady) {
      console.log(`\nLearnGraph is ready: ${frontendOrigin}`)
      console.log(`API health: ${healthUrl}`)
      console.log(`OpenAPI: ${backendOrigin}/docs`)
      console.log('Press Ctrl+C to stop both services.')
      announcedReady = true
    } else {
      console.log(`\nBackend recovered and is healthy: ${healthUrl}`)
    }

    const monitor = watchHealth(healthUrl)
    const outcome = await Promise.race([
      monitor.unhealthy.then((problem) => ({ type: 'backend-unhealthy', problem })),
      backend.exited.then((result) => ({ type: 'backend-exit', result })),
      frontend.exited.then((result) => ({ type: 'frontend-exit', result })),
      signalReceived.then((signal) => ({ type: 'signal', signal })),
    ])
    monitor.stop()
    if (outcome.type === 'signal') return
    if (outcome.type === 'frontend-exit') {
      throw new Error(describeExit(outcome.result))
    }
    const problem =
      outcome.type === 'backend-exit' ? describeExit(outcome.result) : outcome.problem
    console.error(`\n${problem} Restarting backend in 1 second...`)
    if (outcome.type === 'backend-unhealthy') await stopChild(backend)
    const pause = await Promise.race([
      wait(BACKEND_RESTART_DELAY_MS).then(() => ({ type: 'retry' })),
      frontend.exited.then((result) => ({ type: 'frontend-exit', result })),
      signalReceived.then((signal) => ({ type: 'signal', signal })),
    ])
    if (pause.type === 'signal') return
    if (pause.type === 'frontend-exit') {
      throw new Error(describeExit(pause.result))
    }
    backend = startBackend()
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
