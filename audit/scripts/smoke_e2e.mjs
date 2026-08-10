// LearnGraph E2E 冒烟（git 旁路：audit/ 不入库）
// 用法: node smoke_e2e.mjs [BASE_URL]  (默认 http://127.0.0.1:5175)
// 链路: 登录页 → 注册 → 首页 → 图谱页 → 对话页(composer)
// 产出: audit/evidence/e2e/smoke-<ts>.json + 截图；任一步失败 exit 1
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const __dirname = path.dirname(fileURLToPath(import.meta.url))
const candidates = [
  path.join('C:', 'Users', '13600', 'AppData', 'Local', 'npm-cache', '_npx', '423231821c231c73', 'node_modules', 'playwright'),
  path.join('C:', 'Users', '13600', 'AppData', 'Local', 'npm-cache', '_npx', 'e41f203b7505f1fb', 'node_modules', 'playwright'),
]
let chromium = null
for (const c of candidates) { try { chromium = require(c).chromium; break } catch {} }
if (!chromium) { console.error('[smoke] playwright not found'); process.exit(2) }

const BASE = process.argv[2] || 'http://127.0.0.1:5175'
const EXE = path.join('C:', 'Users', '13600', 'AppData', 'Local', 'ms-playwright', 'chromium-1234', 'chrome-win64', 'chrome.exe')
const EVID = path.join(__dirname, '..', 'evidence', 'e2e')
fs.mkdirSync(EVID, { recursive: true })
const ts = Date.now()
const out = { base: BASE, ts, steps: [], passed: true }

function step(name, ok, detail, ms) {
  out.steps.push({ name, ok, detail: String(detail).slice(0, 300), ms: Math.round(ms) })
  if (!ok) out.passed = false
  console.log(`[${ok ? 'PASS' : 'FAIL'}] ${name} (${Math.round(ms)}ms)${ok ? '' : ' -> ' + detail}`)
}

async function main() {
  const browser = await chromium.launch({ headless: true, executablePath: EXE })
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const pageErrors = []
  page.on('pageerror', (e) => pageErrors.push(String(e).slice(0, 200)))

  // 1. 登录页渲染
  let t = Date.now()
  try {
    await page.goto(BASE + '/auth/login', { waitUntil: 'domcontentloaded', timeout: 45000 })
    await page.waitForSelector('#auth-title', { timeout: 30000 })
    step('登录页渲染(#auth-title)', true, '', Date.now() - t)
  } catch (e) {
    step('登录页渲染(#auth-title)', false, e.message + ' | pageerrors=' + JSON.stringify(pageErrors.slice(0, 2)), Date.now() - t)
    await page.screenshot({ path: path.join(EVID, `smoke-${ts}-login-fail.png`) }).catch(() => {})
    await browser.close()
    process.exit(out.passed ? 0 : 1)
  }
  await page.screenshot({ path: path.join(EVID, `smoke-${ts}-01-login.png`) })

  // 2. 注册新用户（UI 全链路）
  t = Date.now()
  const uname = 'smoke' + ts.toString(36)
  const pw = 'Smoke@Pass2026!x'
  try {
    await page.locator('button').filter({ hasText: /创建新账号/ }).click()
    await page.waitForSelector('#register-username', { timeout: 10000 })
    await page.locator('#display-name').fill('Smoke User')
    await page.locator('#register-username').fill(uname)
    await page.locator('#register-email').fill(`smoke${ts}@test.local`)
    await page.locator('#register-password').fill(pw)
    await page.locator('#register-password-confirmation').fill(pw)
    await page.getByRole('button', { name: /注册并开始学习/ }).click()
    await page.waitForURL(/\/w\//, { timeout: 20000 })
    step('注册→进入工作区', true, page.url(), Date.now() - t)
  } catch (e) {
    step('注册→进入工作区', false, e.message, Date.now() - t)
  }
  await page.screenshot({ path: path.join(EVID, `smoke-${ts}-02-registered.png`) })

  // 3. 首页渲染（学习空间/侧栏）
  t = Date.now()
  try {
    await page.waitForSelector('text=学习空间', { state: 'attached', timeout: 40000 })
    step('首页渲染(学习空间)', true, '', Date.now() - t)
  } catch (e) {
    step('首页渲染(学习空间)', false, e.message + ' | body=' + (await page.textContent('body')).slice(0, 120), Date.now() - t)
  }
  await page.screenshot({ path: path.join(EVID, `smoke-${ts}-03-home.png`) })

  // 4. 图谱页
  t = Date.now()
  try {
    const ws = page.url().match(/\/w\/([^/]+)/)?.[1]
    await page.goto(`${BASE}/w/${ws}/graphs`, { waitUntil: 'domcontentloaded', timeout: 30000 })
    await page.waitForSelector('text=图谱', { state: 'attached', timeout: 40000 })
    step('图谱页渲染', true, '', Date.now() - t)
  } catch (e) {
    step('图谱页渲染', false, e.message, Date.now() - t)
  }
  await page.screenshot({ path: path.join(EVID, `smoke-${ts}-04-graphs.png`) })

  // 5. 对话页（新会话 composer）
  t = Date.now()
  try {
    const ws = page.url().match(/\/w\/([^/]+)/)?.[1]
    await page.goto(`${BASE}/w/${ws}/chat/new`, { waitUntil: 'domcontentloaded', timeout: 30000 })
    await page.waitForTimeout(2000)
    const hasComposer = await page.locator('textarea, [contenteditable="true"]').first().isVisible().catch(() => false)
    step('对话页 composer', hasComposer, hasComposer ? '' : '未找到输入框', Date.now() - t)
  } catch (e) {
    step('对话页 composer', false, e.message, Date.now() - t)
  }
  await page.screenshot({ path: path.join(EVID, `smoke-${ts}-05-chat.png`) })

  // 汇总
  out.pageErrors = pageErrors.slice(0, 5)
  fs.writeFileSync(path.join(EVID, `smoke-${ts}.json`), JSON.stringify(out, null, 2), 'utf-8')
  console.log(`[smoke] ${out.passed ? 'PASS' : 'FAIL'} 共 ${out.steps.length} 步，pageerrors=${pageErrors.length}`)
  await browser.close()
  process.exit(out.passed ? 0 : 1)
}

main().catch((e) => { console.error('[smoke] FATAL', e); process.exit(1) })
