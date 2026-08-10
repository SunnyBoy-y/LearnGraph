// Verify fixed production build (minify:false) renders without crash
import { createRequire } from 'node:module'
import path from 'node:path'
const req = createRequire(import.meta.url)
const { chromium } = req(path.join('C:', 'Users', '13600', 'AppData', 'Local', 'npm-cache', '_npx', '423231821c231c73', 'node_modules', 'playwright'))
;(async () => {
  const b = await chromium.launch({ headless: true, executablePath: 'C:/Users/13600/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe' })
  const p = await b.newPage()
  const errs = []
  p.on('pageerror', e => errs.push(String(e).slice(0, 200)))
  const t0 = Date.now()
  await p.goto('http://127.0.0.1:5175/auth/login', { waitUntil: 'domcontentloaded', timeout: 120000 }).catch(e => errs.push('goto:' + String(e).slice(0, 120)))
  await p.waitForTimeout(20000)
  const title = await p.locator('#auth-title').textContent().catch(() => '(none)')
  console.log('RESULT title:', title, '| errors:', JSON.stringify(errs.slice(0, 3)), '| loadMs:', Date.now() - t0)
  await b.close()
})().catch(e => { console.error('FAIL', e); process.exit(1) })
