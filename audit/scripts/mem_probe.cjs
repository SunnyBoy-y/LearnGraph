// 生产构建内存增长排查：20 轮图谱↔首页切换 + 强制 GC + 对比
// 用法: node mem_probe.cjs [BASE] [rounds]
const path = require('path')
const { createRequire } = require('node:module')
const req = createRequire(path.join(process.cwd(), 'x.js'))
const { chromium } = req(path.join('C:','Users','13600','AppData','Local','npm-cache','_npx','423231821c231c73','node_modules','playwright'))
const EXE = 'C:/Users/13600/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe'
const BASE = process.argv[2] || 'http://127.0.0.1:5175'
const ROUNDS = parseInt(process.argv[3] || '20', 10)

async function main() {
  const b = await chromium.launch({ headless: true, executablePath: EXE })
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } })
  const p = await ctx.newPage()
  const errs = []
  p.on('pageerror', e => errs.push(e.message.slice(0, 100)))
  await p.goto(BASE + '/auth/login', { waitUntil: 'domcontentloaded', timeout: 30000 })
  await p.waitForSelector('#auth-title', { timeout: 30000 })
  await p.locator('button').filter({ hasText: /创建新账号/ }).click()
  await p.waitForSelector('#register-username', { timeout: 10000 })
  const uname = 'mem' + Date.now().toString(36), pw = 'Mem@Pass2026!x'
  await p.locator('#display-name').fill('Mem')
  await p.locator('#register-username').fill(uname)
  await p.locator('#register-email').fill(`mem${Date.now()}@t.local`)
  await p.locator('#register-password').fill(pw)
  await p.locator('#register-password-confirmation').fill(pw)
  await p.getByRole('button', { name: /注册并开始学习/ }).click()
  await p.waitForURL(/\/w\//, { timeout: 30000 })
  const ws = p.url().match(/\/w\/([^/]+)/)[1]
  const mem = []
  for (let i = 0; i < ROUNDS; i++) {
    await p.goto(`${BASE}/w/${ws}/graphs`, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {})
    await p.waitForTimeout(700)
    await p.goto(`${BASE}/w/${ws}/home`, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {})
    await p.waitForTimeout(700)
    try { await p.evaluate(() => { if (window.gc) window.gc() }) } catch { /* headless 无 --js-flags */ }
    const h = await p.evaluate(() => performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null)
    mem.push(h)
  }
  // 结束前再强制 GC 一次看回落
  try { await p.evaluate(() => { if (window.gc) window.gc() }) } catch {}
  await p.waitForTimeout(1000)
  const finalH = await p.evaluate(() => performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null)
  console.log(`BASE=${BASE} rounds=${ROUNDS}`)
  console.log('mem:', JSON.stringify(mem))
  console.log('first:', mem[0], 'last:', mem[mem.length-1], 'delta:', mem[mem.length-1]-mem[0], 'post-gc:', finalH)
  console.log('pageerrors:', errs.length ? errs.slice(0,3) : 'none')
  await ctx.close()
  await b.close()
}

main().catch(e => { console.error('FATAL', e.message); process.exit(1) })