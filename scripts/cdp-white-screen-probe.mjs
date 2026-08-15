// 临时诊断脚本 v3：注入 demo 登录态后逐个加载深层页面，抓取白屏异常
import { spawn } from 'node:child_process'

const CHROME = process.env.LG_CHROME || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const PORT = 9225
const profile = `C:\\temp\\lg-chrome-${Date.now()}`

// demo 登录响应（curl 获取）
const LOGIN = {
  access_token: process.env.LG_TOKEN || '',
  workspace_id: 'demo-workspace',
  user_id: 'demo-user',
  username: 'demo',
  display_name: 'Demo User',
  session_id: '',
  must_change_password: false,
}

const urls = process.argv.slice(2)
if (urls.length === 0) {
  urls.push(
    'http://127.0.0.1:18000/w/demo-workspace/home',
    'http://127.0.0.1:18000/w/demo-workspace/chat/new',
    'http://127.0.0.1:18000/w/demo-workspace/settings/usage',
    'http://127.0.0.1:18000/w/demo-workspace/practice',
  )
}

const chrome = spawn(CHROME, [
  '--headless=new',
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`,
  '--no-first-run',
  '--disable-gpu',
  '--disable-extensions',
  '--disable-background-networking',
  '--remote-allow-origins=*',
  'about:blank',
], { stdio: 'ignore' })

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function getWsUrl() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: 'PUT' })
      const j = await r.json()
      if (j.webSocketDebuggerUrl) return j.webSocketDebuggerUrl
    } catch { /* retry */ }
    await sleep(200)
  }
  throw new Error('CDP endpoint not ready')
}

const ws = new WebSocket(await getWsUrl())
let seq = 0
const pending = new Map()
const events = []
ws.onmessage = (e) => {
  let m
  try { m = JSON.parse(typeof e.data === 'string' ? e.data : e.data.toString()) } catch { return }
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
  else if (m.method) events.push(m)
}
await new Promise((r) => { ws.onopen = r })
const send = (method, params = {}) =>
  new Promise((r) => { const i = ++seq; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })) })

await send('Runtime.enable')
await send('Page.enable')
await send('Network.enable')

async function loadAndProbe(url, label) {
  const before = events.length
  await send('Page.navigate', { url })
  await sleep(8000)
  const slice = events.slice(before)
  const exceptions = slice.filter((e) => e.method === 'Runtime.exceptionThrown')
  const errors = slice.filter((e) => e.method === 'Runtime.consoleAPICalled' && e.params.type === 'error')
  const failed = slice.filter((e) => e.method === 'Network.loadingFailed' && !e.params.canceled)
  const evalRes = await send('Runtime.evaluate', {
    expression: `JSON.stringify({url: location.href, rootLen: document.getElementById('root')?.innerHTML.length ?? -1, text: (document.getElementById('root')?.innerText || '').slice(0, 120)})`,
    returnByValue: true,
  })
  let state = 'n/a'
  try { state = JSON.parse(evalRes.result.result.value) } catch { state = evalRes.result?.result?.value ?? 'n/a' }

  console.log(`\n========== ${label} : ${url} ==========`)
  console.log('STATE:', JSON.stringify(state))
  console.log('EXCEPTIONS:', exceptions.length)
  for (const e of exceptions.slice(0, 3)) {
    const d = e.params.exceptionDetails
    console.log(`  [${d.exception?.className}] ${d.text}`)
    console.log(`  ${(d.exception?.description || '').split('\n').slice(0, 4).join('\n  ')}`)
    if (d.stackTrace) for (const f of d.stackTrace.callFrames.slice(0, 6)) console.log(`    at ${f.functionName} (${f.url}:${f.lineNumber}:${f.columnNumber})`)
  }
  console.log('CONSOLE ERRORS:', errors.length)
  for (const e of errors.slice(0, 5)) console.log('  ' + e.params.args.map((a) => a.value ?? a.description ?? '').join(' ').slice(0, 400))
  console.log('NETWORK FAILURES:', failed.length)
  for (const e of failed.slice(0, 10)) console.log(`  ${e.params.type} ${e.params.errorText} ${e.params.blockedReason || ''}`)
}

// 1) 先加载同源页面建立 origin 并注入 sessionStorage
await send('Page.navigate', { url: 'http://127.0.0.1:18000/auth/login' })
await sleep(4000)
const injectExpr = `(() => {
  const s = sessionStorage;
  s.setItem('learngraph.access_token', ${JSON.stringify(LOGIN.access_token)});
  s.setItem('learngraph.workspace_id', ${JSON.stringify(LOGIN.workspace_id)});
  s.setItem('learngraph.user_id', ${JSON.stringify(LOGIN.user_id)});
  s.setItem('learngraph.username', ${JSON.stringify(LOGIN.username)});
  s.setItem('learngraph.display_name', ${JSON.stringify(LOGIN.display_name)});
  s.setItem('learngraph.session_id', ${JSON.stringify(LOGIN.session_id)});
  s.setItem('learngraph.must_change_password', 'false');
  return 'injected';
})()`
const inj = await send('Runtime.evaluate', { expression: injectExpr, returnByValue: true })
console.log('inject:', inj.result?.result?.value ?? JSON.stringify(inj).slice(0, 200))

for (const [i, u] of urls.entries()) {
  await loadAndProbe(u, `page${i + 1}`)
}

ws.close()
chrome.kill()
