// LearnGraph audit - Playwright UX journey (isolated instance 5174/5175).
// Evidence: audit/evidence/ux/*.png + ux-report.json
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const candidates = [
  path.join('C:', 'Users', '13600', 'AppData', 'Roaming', 'npm', 'node_modules', 'playwright'),
  path.join('C:', 'Users', '13600', 'AppData', 'Local', 'npm-cache', '_npx', '423231821c231c73', 'node_modules', 'playwright'),
  path.join('C:', 'Users', '13600', 'AppData', 'Local', 'npm-cache', '_npx', 'e41f203b7505f1fb', 'node_modules', 'playwright'),
]
let chromium = null
for (const c of candidates) {
  try { chromium = require(c).chromium; break } catch {}
}
if (!chromium) { console.error('playwright not found'); process.exit(1) }

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const EVID = path.resolve(__dirname, '..', 'evidence', 'ux')
fs.mkdirSync(EVID, { recursive: true })
const BASE = process.env.BASE || 'http://127.0.0.1:5174' // dev (prod build broken - P0)
const API = 'http://127.0.0.1:8002/api/v1'

const out = { pages: {}, network: {}, findings: [] }

function finding(sev, title, detail, evidence) {
  out.findings.push({ sev, title, detail, evidence })
  console.log(`[${sev}] ${title}`)
}

async function main() {
  const browser = await chromium.launch({
    headless: true,
    executablePath: path.join('C:', 'Users', '13600', 'AppData', 'Local', 'ms-playwright', 'chromium-1234', 'chrome-win64', 'chrome.exe'),
  })
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await ctx.newPage()
  const requests = {}
  page.on('request', (r) => {
    const u = new URL(r.url())
    if (u.pathname.startsWith('/api/')) {
      const k = u.pathname
      requests[k] = requests[k] || { count: 0, method: r.method(), times: [] }
      requests[k].count++
      requests[k].times.push(Date.now())
    }
  })

  // --- 1. First-run: login page ---
  const t0 = Date.now()
  const resp = await page.goto(BASE + '/auth/login', { waitUntil: 'networkidle', timeout: 30000 })
  const loginLoadMs = Date.now() - t0
  out.pages.login = { http: resp?.status(), loadMs: loginLoadMs }
  await page.screenshot({ path: path.join(EVID, '01-login.png'), fullPage: true })
  const loginText = (await page.textContent('body') || '').slice(0, 200)
  console.log('[ux] login page loaded', loginLoadMs, 'ms; text:', loginText.replace(/\s+/g, ' ').slice(0, 120))
  finding(loginLoadMs > 2500 ? 'P2' : 'P4',
    `登录页加载 ${loginLoadMs}ms (${resp?.status()})`,
    loginLoadMs > 2500 ? '超过 2.5s 预算' : '预算内', ['01-login.png'])

  // --- 2. Register a fresh user through the UI ---
  const uname = 'ux' + Date.now().toString(36)
  const upass = 'Ux@Pass2026!abc'
  await page.goto(BASE + '/auth/login', { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('#auth-title', { timeout: 60000 })
  // toggle to register mode ("创建新账号" button)
  const toggle = page.locator('button').filter({ hasText: /创建新账号/ }).first()
  await toggle.waitFor({ state: 'visible', timeout: 20000 }).catch(() => {})
  await toggle.click().catch(() => {})
  await page.waitForTimeout(600)
  const inRegister = await page.locator('#register-username').isVisible().catch(() => false)
  if (inRegister) {
    await page.locator('#display-name').fill('UX Audit User')
    await page.locator('#register-username').fill(uname)
    await page.locator('#register-email').fill(`ux${Date.now()}@test.local`)
    await page.locator('#register-password').fill(upass)
    await page.locator('#register-password-confirmation').fill(upass)
    await page.screenshot({ path: path.join(EVID, '02-register-filled.png') })
    await page.getByRole('button', { name: /注册并开始学习/ }).click()
    await page.waitForTimeout(4000)
  } else {
    // fallback: use demo-login credentials if demo unlocked
    await page.screenshot({ path: path.join(EVID, '02-register-failed.png') })
  }
  const afterUrl = page.url()
  console.log('[ux] after register url:', afterUrl, 'registerMode:', inRegister)
  await page.screenshot({ path: path.join(EVID, '03-after-register.png') })
  out.register = { url: afterUrl, inRegisterMode: inRegister }

  // --- 3. Home/dashboard ---
  if (afterUrl.includes('/w/')) {
    await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {})
    const t1 = Date.now()
    await page.goto(afterUrl, { waitUntil: 'networkidle', timeout: 30000 })
    out.pages.home = { loadMs: Date.now() - t1 }
    await page.screenshot({ path: path.join(EVID, '04-home.png'), fullPage: true })
    const homeText = ((await page.textContent('body')) || '').replace(/\s+/g, ' ').slice(0, 300)
    console.log('[ux] home loaded', out.pages.home.loadMs, 'ms; text:', homeText.slice(0, 150))
    finding(out.pages.home.loadMs > 2500 ? 'P2' : 'P4',
      `登录后首页加载 ${out.pages.home.loadMs}ms`, '首页 TTFB 后完整可用耗时', ['04-home.png'])

    // --- 4. Navigate: graph page, chat page, sources page, settings ---
    for (const [label, seg] of [['graphs', '/graphs'], ['sources', '/sources'],
                                ['settings', '/settings/workspace'], ['memory', '/memory']]) {
      const t = Date.now()
      await page.goto(afterUrl.replace(/\/home$/, '') + seg, { waitUntil: 'networkidle', timeout: 30000 }).catch(() => {})
      out.pages[label] = { loadMs: Date.now() - t, url: page.url() }
      await page.screenshot({ path: path.join(EVID, `05-${label}.png`) })
      console.log(`[ux] ${label} page ${out.pages[label].loadMs}ms`)
    }
  }

  // --- 5. Network polling snapshot (2 min on task-ish page; use graph page) ---
  const tPoll0 = Date.now()
  const pollTarget = page.url()
  await page.waitForTimeout(30000) // 30s sampling (scaled down from 2min for budget)
  const pollWindow = (Date.now() - tPoll0) / 1000
  const freq = {}
  for (const [k, v] of Object.entries(requests)) {
    freq[k] = { count: v.count, perMin: Math.round(v.count * 60 / pollWindow), method: v.method }
  }
  out.network.pollWindowSec = Math.round(pollWindow)
  out.network.freq = freq
  const pollers = Object.entries(freq).filter(([, v]) => v.perMin >= 10).sort((a, b) => b[1].perMin - a[1].perMin)
  console.log('[ux] pollers (>=10/min):', JSON.stringify(pollers.slice(0, 8)))
  finding(pollers.length ? 'P3' : 'P4',
    `高频轮询 ${pollers.length} 个端点`, JSON.stringify(pollers.slice(0, 5)), ['network.snapshot'])

  await page.screenshot({ path: path.join(EVID, '06-after-poll.png') })
  fs.writeFileSync(path.join(EVID, 'ux-report.json'), JSON.stringify(out, null, 2), 'utf-8')
  console.log('[ux] done. findings:', out.findings.length)
  await browser.close()
}

main().catch((e) => { console.error('[ux] FAILED', e); process.exit(1) })
