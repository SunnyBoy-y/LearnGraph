const path = require('path')
const { createRequire } = require('node:module')
const req = createRequire(path.join(process.cwd(), 'x.js'))
const { chromium } = req(path.join('C:','Users','13600','AppData','Local','npm-cache','_npx','423231821c231c73','node_modules','playwright'))
;(async () => {
  const b = await chromium.launch({ headless: true, executablePath: 'C:/Users/13600/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe' })
  for (const [label, url] of [['HEAD-login','http://127.0.0.1:5176/auth/login'], ['HEAD-graphs','http://127.0.0.1:5176/w/x/graphs']]) {
    const p = await b.newPage()
    const errs = []
    p.on('pageerror', e => errs.push(e.message.slice(0,100)))
    await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(()=>{})
    await p.waitForTimeout(4000)
    console.log(label, '| pageerrors:', errs.length ? errs.slice(0,3) : 'none')
    await p.close()
  }
  await b.close()
})().catch(e=>{console.error('FATAL', e); process.exit(1)})
