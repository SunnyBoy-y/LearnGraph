const path = require('path')
const { createRequire } = require('node:module')
const req = createRequire(path.join(process.cwd(), 'x.js'))
const { chromium } = req(path.join('C:','Users','13600','AppData','Local','npm-cache','_npx','423231821c231c73','node_modules','playwright'))
;(async () => {
  const b = await chromium.launch({ headless: true, executablePath: 'C:/Users/13600/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe' })
  const p = await b.newPage({ viewport: { width: 1440, height: 900 } })
  const authResp = []
  p.on('response', async r => { if (r.url().includes('/api/v1/auth/')) authResp.push(r.status() + ' ' + r.url().slice(-50)) })
  p.on('pageerror', e => console.log('PAGEERROR:', e.message))
  await p.goto('http://127.0.0.1:5175/auth/login', { waitUntil: 'domcontentloaded', timeout: 30000 })
  await p.waitForSelector('#auth-title', { timeout: 30000 })
  await p.locator('button').filter({ hasText: /创建新账号/ }).click()
  await p.waitForSelector('#register-username', { timeout: 10000 })
  const uname='dbgc2'+Date.now().toString(36), pw='Dbg@Pass2026!x'
  await p.locator('#display-name').fill('Dbg'); await p.locator('#register-username').fill(uname)
  await p.locator('#register-email').fill(`d${Date.now()}@t.local`); await p.locator('#register-password').fill(pw)
  await p.locator('#register-password-confirmation').fill(pw)
  await p.getByRole('button', { name: /注册并开始学习/ }).click()
  await p.waitForTimeout(8000)
  console.log('url:', p.url())
  console.log('auth resp:', JSON.stringify(authResp))
  console.log('body:', ((await p.textContent('body')||'').replace(/\s+/g,' ').slice(0,200)))
  const ws = p.url().match(/\/w\/([^/]+)/)?.[1]
  if (ws) {
    await p.goto(`http://127.0.0.1:5175/w/${ws}/chat/new`, { waitUntil: 'domcontentloaded', timeout: 30000 })
    await p.waitForTimeout(5000)
    console.log('chat url:', p.url())
    console.log('chat body:', ((await p.textContent('body')||'').replace(/\s+/g,' ').slice(0,200)))
    console.log('inputs:', await p.locator('textarea, [contenteditable="true"], input').count())
    await p.screenshot({ path: '../evidence/e2e/prod-chat-debug.png' })
  }
  await b.close()
})().catch(e=>{console.error('FATAL', e.message); process.exit(1)})
