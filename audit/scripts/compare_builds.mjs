// Compare HEAD-clean build (5176) vs working-tree build (5175) crash behavior.
import { createRequire } from 'node:module'
import path from 'node:path'
const req = createRequire(import.meta.url)
const { chromium } = req(path.join('C:', 'Users', '13600', 'AppData', 'Local', 'npm-cache', '_npx', '423231821c231c73', 'node_modules', 'playwright'))

const EXE = path.join('C:', 'Users', '13600', 'AppData', 'Local', 'ms-playwright', 'chromium-1234', 'chrome-win64', 'chrome.exe')
const out = {}
;(async () => {
  const b = await chromium.launch({ headless: true, executablePath: EXE })
  for (const [label, base] of [['HEAD-clean', 'http://127.0.0.1:5176'], ['working-tree', 'http://127.0.0.1:5175']]) {
    const p = await b.newPage()
    const errs = []
    p.on('pageerror', (e) => errs.push(String(e).slice(0, 200)))
    const t0 = Date.now()
    await p.goto(base + '/auth/login', { waitUntil: 'domcontentloaded', timeout: 25000 }).catch((e) => errs.push('goto:' + String(e).slice(0, 120)))
    await p.waitForTimeout(5000)
    const title = await p.locator('#auth-title').textContent().catch(() => '(none)')
    out[label] = { title, errors: errs.slice(0, 3), loadMs: Date.now() - t0 }
    console.log('RESULT', JSON.stringify(out[label]))
    await p.close()
  }
  await b.close()
  console.log('DONE', JSON.stringify(out))
})().catch((e) => { console.error('FAIL', e); process.exit(1) })
