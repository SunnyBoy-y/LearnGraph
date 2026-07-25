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

async function pollHealth(url) {
  const deadline = Date.now() + HEALTH_TIMEOUT_MS
  let lastProblem = 'no response'

  while (Date.now() < deadline) {
    const controller = new AbortController()
    const requestTimeout = setTimeout(() => controller.abort(), 2_000)
    try {
      const response = await fetch(url, { signal: controller.signal })
      if (response.ok) {
        const payload = await response.json()
        if (payload?.status === 'ok') return
        lastProblem = `HTTP ${response.status} returned status ${JSON.stringify(payload?.status)}`
      } else {
        lastProblem = `HTTP ${response.status}`
      }
    } catch (error) {
      lastProblem = error instanceof Error ? error.message : String(error)
    } finally {
      clearTimeout(requestTimeout)
    }
    await wait(HEALTH_INTERVAL_MS)
  }

  throw new Error(`Backend did not become healthy within 45 seconds (${lastProblem}): ${url}`)
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

  const backendOrigin = `http://127.0.0.1:${options.backendPort}`
  const frontendOrigin = `http://127.0.0.1:${frontendPort}`
  const apiOrigin = process.env.VITE_API_BASE_URL?.trim() || backendOrigin
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
        '127.0.0.1',
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
    '127.0.0.1',
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
        VITE_API_BASE_URL: apiOrigin,
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

    const outcome = await Promise.race([
      backend.exited.then((result) => ({ type: 'backend-exit', result })),
      frontend.exited.then((result) => ({ type: 'frontend-exit', result })),
      signalReceived.then((signal) => ({ type: 'signal', signal })),
    ])
    if (outcome.type === 'signal') return
    if (outcome.type === 'frontend-exit') {
      throw new Error(describeExit(outcome.result))
    }
    console.error(`\n${describeExit(outcome.result)} Restarting backend in 1 second...`)
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
