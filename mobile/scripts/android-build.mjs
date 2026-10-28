import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const tasks = {
  debug: ':app:assembleDebug',
  compact: ':app:assembleCompact',
  release: ':app:assembleRelease',
  test: ':app:testDebugUnitTest',
}
const task = tasks[process.argv[2] ?? 'debug']
if (!task) throw new Error('Expected debug, compact, release or test')
const windows = process.platform === 'win32'
if (process.argv[2] !== 'test') {
  const renderer = spawnSync(process.execPath, [fileURLToPath(new URL('./build-native-renderer.mjs', import.meta.url))], { stdio: 'inherit' })
  if (renderer.error) throw renderer.error
  if (renderer.status !== 0) process.exit(renderer.status ?? 1)
}
const args = [task, '--no-daemon']
// gradlew reads JAVA_HOME itself; passing it through cmd arguments breaks paths with spaces.
const result = spawnSync(windows ? 'gradlew.bat' : 'bash', windows ? args : ['./gradlew', ...args], {
  cwd: fileURLToPath(new URL('../native/', import.meta.url)),
  stdio: 'inherit',
  shell: windows,
})
if (result.error) throw result.error
process.exit(result.status ?? 1)
