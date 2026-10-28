import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const frontend = fileURLToPath(new URL('../../frontend/', import.meta.url))
const vite = path.join(frontend, 'node_modules/vite/bin/vite.js')
if (!existsSync(vite)) throw new Error('Install frontend dependencies with npm ci in frontend first')
const root = path.dirname(frontend)
const iconCheck = spawnSync(process.execPath, [fileURLToPath(new URL('../native/tools/sync-web-icons.mjs', import.meta.url)), '--check'], { stdio: 'inherit' })
if (iconCheck.error) throw iconCheck.error
if (iconCheck.status !== 0) process.exit(iconCheck.status ?? 1)
const fonts = JSON.parse(readFileSync(path.join(root, 'mobile/native/tools/web-font-source-manifest.json'), 'utf8'))
const sha = file => createHash('sha256').update(readFileSync(file)).digest('hex')
if (sha(path.join(root, fonts.source)) !== fonts.source_sha256) throw new Error('Web font source changed; regenerate native fonts')
for (const [name, font] of Object.entries(fonts.fonts)) {
  if (sha(path.join(root, 'mobile/native/app/src/main/res/font', `${name}.ttf`)) !== font.sha256) throw new Error(`Native font ${name} differs from its source manifest`)
}
if (process.argv.includes('--check-sources')) { console.log('Web/native icon and font sources verified.'); process.exit(0) }
const result = spawnSync(process.execPath, [vite, 'build', '--config', 'vite.native-renderer.config.ts'], { cwd: frontend, stdio: 'inherit' })
if (result.error) throw result.error
if (result.status !== 0) process.exit(result.status ?? 1)
const output = fileURLToPath(new URL('../native/app/src/main/assets/renderer/', import.meta.url))
const html = readFileSync(path.join(output, 'native-renderer.html'), 'utf8')
if (!html.includes('<!--native-bootstrap-->')) throw new Error('Native auth bootstrap marker missing')
const applicationHtml = readFileSync(path.join(output, 'native-app.html'), 'utf8')
if (!applicationHtml.includes('<!--native-bootstrap-->')) throw new Error('Packaged application auth bootstrap marker missing')
const collect = (folder) => readdirSync(folder, { withFileTypes: true }).flatMap(entry => entry.isDirectory() ? collect(path.join(folder, entry.name)) : [path.join(folder, entry.name)])
const files = collect(output).filter(file => !file.endsWith('manifest-native.json'))
const bytes = files.reduce((sum, file) => sum + statSync(file).size, 0)
const hashes = files.map(file => [path.relative(output, file).replaceAll('\\', '/'), createHash('sha256').update(readFileSync(file)).digest('hex')])
writeFileSync(path.join(output, 'manifest-native.json'), JSON.stringify({ schema: 1, files: Object.fromEntries(hashes), bytes }, null, 2) + '\n')
console.log(`Shared Web renderer ready: ${files.length} local assets, ${(bytes / 1024 / 1024).toFixed(2)} MiB before APK compression; no browser engine.`)
