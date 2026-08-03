#!/usr/bin/env node

import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const MINIMUM_NODE_MAJOR = 20
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const frontendDir = path.join(repoRoot, 'frontend')
const backendDir = path.join(repoRoot, 'backend')

function assertSupportedNode() {
  const major = Number.parseInt(process.versions.node.split('.')[0], 10)
  if (!Number.isInteger(major) || major < MINIMUM_NODE_MAJOR) {
    throw new Error(
      `LearnGraph requires Node.js ${MINIMUM_NODE_MAJOR} or newer; found ${process.versions.node}.`,
    )
  }
}

function parseArguments(argv) {
  const options = {
    install: false,
    skipBackend: false,
    skipFrontend: false,
    help: false,
  }

  for (const argument of argv) {
    if (argument === '--') continue
    if (argument === '--install') options.install = true
    else if (argument === '--skip-backend') options.skipBackend = true
    else if (argument === '--skip-frontend') options.skipFrontend = true
    else if (argument === '--help' || argument === '-h') options.help = true
    else throw new Error(`Unknown option: ${argument}`)
  }

  if (options.skipFrontend && options.skipBackend) {
    throw new Error('Both frontend and backend checks were skipped.')
  }
  return options
}

function printHelp() {
  console.log(`Usage: node scripts/check.mjs [options]

Run deterministic checks without relying on a test framework.

Options:
  --install          Install from the frontend and backend lockfiles first
  --skip-frontend    Skip frontend lint and production build
  --skip-backend     Skip backend syntax and application import checks
  -h, --help         Show this help`)
}

function requireFile(filePath) {
  if (!existsSync(filePath)) {
    throw new Error(`Required file is missing: ${path.relative(repoRoot, filePath)}`)
  }
}

function npmCommand(args) {
  if (process.platform !== 'win32') return { command: 'npm', args }
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

async function runCommand(label, invocation, cwd, env = process.env) {
  console.log(`\n==> ${label}`)
  const result = await new Promise((resolve) => {
    const child = spawn(invocation.command, invocation.args, {
      cwd,
      env,
      stdio: 'inherit',
      windowsHide: true,
    })
    child.once('error', (error) => resolve({ code: null, error, signal: null }))
    child.once('exit', (code, signal) => resolve({ code, error: null, signal }))
  })

  if (result.error) {
    console.error(`FAILED: ${label} could not start: ${result.error.message}`)
    return false
  }
  if (result.code !== 0) {
    const detail = result.code === null ? `signal ${result.signal}` : `exit code ${result.code}`
    console.error(`FAILED: ${label} (${detail})`)
    return false
  }
  console.log(`PASSED: ${label}`)
  return true
}

async function main() {
  assertSupportedNode()
  const options = parseArguments(process.argv.slice(2))
  if (options.help) {
    printHelp()
    return
  }

  const failures = []
  let frontendReady = !options.skipFrontend
  let backendReady = !options.skipBackend

  if (!options.skipFrontend) {
    requireFile(path.join(frontendDir, 'package.json'))
    requireFile(path.join(frontendDir, 'package-lock.json'))
    if (options.install) {
      frontendReady = await runCommand(
        'Installing frontend dependencies from package-lock.json',
        npmCommand(['ci']),
        frontendDir,
      )
      if (!frontendReady) failures.push('Frontend dependency installation')
    } else if (!existsSync(path.join(frontendDir, 'node_modules'))) {
      frontendReady = false
      failures.push('Frontend dependencies are missing (run "npm run check:install")')
    }
  }

  if (!options.skipBackend) {
    requireFile(path.join(backendDir, 'pyproject.toml'))
    requireFile(path.join(backendDir, 'uv.lock'))
    if (options.install) {
      backendReady = await runCommand(
        'Synchronizing backend dependencies from uv.lock',
        { command: 'uv', args: ['sync', '--locked'] },
        backendDir,
      )
      if (!backendReady) failures.push('Backend dependency synchronization')
    }
  }

  if (frontendReady) {
    if (
      !(await runCommand('Frontend lint', npmCommand(['run', 'lint']), frontendDir))
    ) {
      failures.push('Frontend lint')
    }
    if (
      !(await runCommand('Frontend behavior tests', npmCommand(['run', 'test']), frontendDir))
    ) {
      failures.push('Frontend behavior tests')
    }
    if (
      !(await runCommand('Frontend production build', npmCommand(['run', 'build']), frontendDir))
    ) {
      failures.push('Frontend production build')
    }
  }

  if (backendReady) {
    const pythonEnv = {
      ...process.env,
      PYTHONDONTWRITEBYTECODE: '1',
    }
    const syntaxCheck = [
      'from pathlib import Path',
      'files = sorted(Path("app").rglob("*.py"))',
      'assert files, "No backend Python files found"',
      '[compile(file.read_bytes(), str(file), "exec") for file in files]',
      'print(f"Syntax checked {len(files)} Python files.")',
    ].join('; ')

    if (
      !(await runCommand(
        'Backend Python syntax',
        { command: 'uv', args: ['run', '--locked', 'python', '-c', syntaxCheck] },
        backendDir,
        pythonEnv,
      ))
    ) {
      failures.push('Backend Python syntax')
    }

    const importCheck = [
      'from app.main import app',
      'assert app is not None',
      'print("Imported app.main:app successfully.")',
    ].join('; ')
    if (
      !(await runCommand(
        'Backend application import',
        { command: 'uv', args: ['run', '--locked', 'python', '-c', importCheck] },
        backendDir,
        pythonEnv,
      ))
    ) {
      failures.push('Backend application import')
    }

    if (
      !(await runCommand(
        'P0 security regressions',
        {
          command: 'uv',
          args: [
            'run',
            '--locked',
            '--extra',
            'test',
            'pytest',
            '-q',
            'tests/security',
            '--basetemp',
            'data/pytest-p0',
          ],
        },
        backendDir,
        pythonEnv,
      ))
    ) {
      failures.push('P0 security regressions')
    }

    if (
      !(await runCommand(
        'Stored audio transcription regression',
        {
          command: 'uv',
          args: ['run', '--locked', 'python', 'scripts/verify_file_transcription.py'],
        },
        backendDir,
        pythonEnv,
      ))
    ) {
      failures.push('Stored audio transcription regression')
    }

    if (
      !(await runCommand(
        'Dual transcription model routing',
        {
          command: 'uv',
          args: [
            'run',
            '--locked',
            'python',
            'scripts/verify_transcription_model_routing.py',
          ],
        },
        backendDir,
        pythonEnv,
      ))
    ) {
      failures.push('Dual transcription model routing')
    }
  }

  if (failures.length > 0) {
    console.error('\nChecks failed:')
    for (const failure of failures) console.error(`  - ${failure}`)
    process.exitCode = 1
    return
  }

  console.log('\nAll requested checks passed.')
}

try {
  await main()
} catch (error) {
  console.error(`\nError: ${error instanceof Error ? error.message : String(error)}`)
  process.exitCode = 1
}
