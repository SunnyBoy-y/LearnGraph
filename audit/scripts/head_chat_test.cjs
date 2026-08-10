// 测 HEAD 生产构建对话页是否有 recharts 崩溃（登录后进 chat/new）
const path = require('path')
const { createRequire } = require('node:module')
const req = createRequire(path.join(process.cwd(), 'x.js'))
const { chromium } = req(path.join('C:','Users','13600','AppData','Local','npm-cache','_npx','423231821c231c73','node_modules','playwright'))
const BASE = process.argv[2] || 'http://127.0.0.1:5177'
;(async () => {
  const b = await chromium.launch({ headless: true, executablePath: 'C:/Users/13600/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe' })
  const p = await b.newPage({ viewport: { width: 1440, height: 900 } })
  const errs = []
  p.on('pageerror', e => errs.push(e.message.slice(0,120)))
  await p.goto(BASE + '/auth/login', { waitUntil: 'domcontentloaded', timeout: 30000 })
  await p.waitForSelector('#auth-title', { timeout: 60000 })
  await p.locator('button').filter({ hasText: /创建新账号/ }).click()
  await p.waitForSelector('#register-username', { timeout: 10000 })
  const uname='hd'+Date.now().toString(36), pw='Hd@Pass2026!x'
  await p.locator('#display-name').fill('Hd'); await p.locator('#register-username').fill(uname)
  await p.locator('#register-email').fill(`hd${Date.now()}@t.local`); await p.locator('#register-password').fill(pw)
  await p.locator('#register-password-confirmation').fill(pw)
  await p.getByRole('button', { name: /注册并开始学习/ }).click()
  await p.waitForTimeout(7000)
  const ws = p.url().match(/\/w\/([^/]+)/)?.[1]
  if (ws) {
    await p.goto(`${BASE}/w/${ws}/chat/new`, { waitUntil: 'domcontentloaded', timeout: 30000 })
    await p.waitForTimeout(5000)
    console.log('HEAD chat url:', p.url())
    console.log('HEAD chat body:', ((await p.textContent('body')||'').replace(/\s+/g,' ').slice(0,120)))
    console.log('HEAD inputs:', await p.locator('textarea, [contenteditable="true"], input').count())
  } else {
    console.log('register failed, url:', p.url())
  }
  console.log('HEAD pageerrors:', errs.length ? errs.slice(0,4) : 'none')
  await b.close()
})().catch(e=>{console.error('FATAL', e.message); process.exit(1)})
