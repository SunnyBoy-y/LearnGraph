#!/usr/bin/env node
/**
 * LearnGraph Host Service Bridge — one-command launcher for the real machine.
 *
 * What it does (idempotent, safe to re-run):
 *   1. Generates (or reuses) LEARNGRAPH_HOST_BRIDGE_TOKEN under ./data/host-bridge/token
 *   2. Writes a disabled-by-default registry template for common local services
 *      (ollama / lm-studio) — flip "enabled": true when the service is ready
 *   3. Starts the bridge: python -m app.services.host_bridge_main
 *
 * Container side needs ZERO configuration for the standard self-hosted shape:
 * the backend auto-derives http://host.docker.internal:34115 on containerized
 * profiles (Settings.effective_host_bridge_url). Start the container stack with:
 *
 *   docker compose up -d --build
 *
 * Usage:
 *   node scripts/host-bridge.mjs          # start (defaults)
 *   node scripts/host-bridge.mjs --no-template   # skip registry templates
 *   node scripts/host-bridge.mjs --port 34115    # custom port
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { randomBytes } from 'node:crypto'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const backendDir = path.join(repoRoot, 'backend')
const dataDir = path.join(repoRoot, 'data', 'host-bridge')
const registryDir = path.join(dataDir, 'host-services')

function pythonExecutable() {
  const windows = path.join(backendDir, '.venv', 'Scripts', 'python.exe')
  const posix = path.join(backendDir, '.venv', 'bin', 'python')
  return existsSync(windows) ? windows : existsSync(posix) ? posix : 'python'
}

function loadOrCreateToken() {
  const tokenFile = path.join(dataDir, 'token')
  if (existsSync(tokenFile)) {
    const existing = readFileSync(tokenFile, 'utf8').trim()
    if (existing) return existing
  }
  const token = randomBytes(32).toString('hex')
  mkdirSync(dataDir, { recursive: true })
  writeFileSync(tokenFile, token + '\n', { mode: 0o600 })
  console.log(`[host-bridge] generated token -> ${path.relative(repoRoot, tokenFile)}`)
  return token
}

const TEMPLATES = [
  {
    id: 'ollama',
    template: {
      id: 'ollama',
      target: 'http://127.0.0.1:11434',
      kind: 'http',
      enabled: false,
      allowed_paths: ['/v1', '/api'],
      headers: {},
    },
  },
  {
    id: 'lm-studio',
    template: {
      id: 'lm-studio',
      target: 'http://127.0.0.1:1234',
      kind: 'http',
      enabled: false,
      allowed_paths: ['/v1'],
      headers: {},
    },
  },
]

function writeTemplates() {
  mkdirSync(registryDir, { recursive: true })
  for (const { id, template } of TEMPLATES) {
    const file = path.join(registryDir, `${id}.json`)
    if (existsSync(file)) continue
    writeFileSync(file, JSON.stringify(template, null, 2) + '\n')
    console.log(
      `[host-bridge] wrote registry template ${path.relative(repoRoot, file)} (enabled: false — flip to true when ready)`,
    )
  }
}

function parseArgs(argv) {
  const args = { port: null, template: true }
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === '--no-template') args.template = false
    else if (arg === '--port') args.port = Number(argv[i + 1])
  }
  return args
}

const args = parseArgs(process.argv.slice(2))
const token = loadOrCreateToken()
if (args.template) writeTemplates()

const python = pythonExecutable()
const bridgeArgs = [
  '-m',
  'app.services.host_bridge_main',
  '--registry-dir',
  registryDir,
  '--audit-log',
  path.join(dataDir, 'audit.jsonl'),
]
if (args.port) bridgeArgs.push('--port', String(args.port))

console.log('[host-bridge] starting:')
console.log(`  python ${bridgeArgs.join(' ')}`)
console.log(`  token file : ${path.join(dataDir, 'token')}`)
console.log('  container  : auto-derives http://host.docker.internal:34115 (zero config)')
console.log('  verify     : curl -H "Authorization: Bearer <token>" http://127.0.0.1:34115/healthz')

const child = spawn(python, bridgeArgs, { cwd: backendDir, stdio: 'inherit', env: { ...process.env, LEARNGRAPH_HOST_BRIDGE_TOKEN: token } })
child.on('exit', (code) => process.exit(code ?? 0))
