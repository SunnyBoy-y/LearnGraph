import { spawn } from 'node:child_process'
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const PORT = 9232
const profile = `C:/temp/lg-chrome-${Date.now()}`
const TOKEN = '0o5AYDpompQEMsCjHTvC7kDcv4a8hYYSv1AJfCj_bMofvD_JbJR4aNaHArhLJu2V'
const BASE = 'http://127.0.0.1:18000/w/demo-workspace'
const pages = [
  ['home', `${BASE}/home`],
  ['chat-new', `${BASE}/chat/new`],
  ['chat-versions-4lvl', `${BASE}/chat/22e0f55d-6908-4fd7-af99-9de7334cd26e/versions`],
  ['practice-3lvl', `${BASE}/practice`],
  ['practice-q-4lvl', `${BASE}/practice/set1/q1`],
  ['evidence-3lvl', `${BASE}/evidence/review`],
  ['memory-3lvl', `${BASE}/memory`],
  ['capabilities-3lvl', `${BASE}/capabilities`],
  ['learn-joint-3lvl', `${BASE}/learn/joint`],
  ['goals-confirm-4lvl', `${BASE}/goals/g1/confirm`],
  ['goals-roadmap-4lvl', `${BASE}/goals/g1/roadmap`],
  ['research-task-4lvl', `${BASE}/research/tasks/t1`],
  ['documents-4lvl', `${BASE}/documents/f1`],
  ['settings-migrate-4lvl', `${BASE}/settings/storage/migrations`],
  ['settings-audit-3lvl', `${BASE}/settings/audit`],
  ['settings-providers-3lvl', `${BASE}/settings/providers`],
  ['settings-extensions-3lvl', `${BASE}/settings/extensions`],
]
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, '--no-first-run', '--disable-gpu', '--disable-extensions', '--remote-allow-origins=*', '--window-size=1600,900', 'about:blank'], { stdio: 'ignore' })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
async function getWsUrl() {
  for (let i = 0; i < 60; i++) {
    try { const r = await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: 'PUT' }); const j = await r.json(); if (j.webSocketDebuggerUrl) return j.webSocketDebuggerUrl } catch {}
    await sleep(200)
  }
  throw new Error('no cdp')
}
const ws = new WebSocket(await getWsUrl())
let seq = 0
const pending = new Map()
const events = []
ws.onmessage = (e) => { let m; try { m = JSON.parse(typeof e.data === 'string' ? e.data : e.data.toString()) } catch { return }; if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) } else if (m.method) events.push(m) }
await new Promise((r) => { ws.onopen = r })
const send = (method, params = {}) => new Promise((r) => { const i = ++seq; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })) })
await send('Runtime.enable')
await send('Page.enable')
await send('Page.navigate', { url: 'http://127.0.0.1:18000/auth/login' })
await sleep(4000)
await send('Runtime.evaluate', { expression: `(() => { const s = sessionStorage; s.setItem('learngraph.access_token', '${TOKEN}'); s.setItem('learngraph.workspace_id', 'demo-workspace'); s.setItem('learngraph.user_id', 'demo-user'); s.setItem('learngraph.username', 'demo'); s.setItem('learngraph.display_name', 'Demo User'); return 'ok' })()`, returnByValue: true })

for (const [label, url] of pages) {
  const before = events.length
  await send('Page.navigate', { url })
  await sleep(5500)
  const slice = events.slice(before)
  const exceptions = slice.filter((e) => e.method === 'Runtime.exceptionThrown')
  const errors = slice.filter((e) => e.method === 'Runtime.consoleAPICalled' && e.params.type === 'error')
  const failed = slice.filter((e) => e.method === 'Network.loadingFailed' && !e.params.canceled)
  const r = await send('Runtime.evaluate', {
    expression: `(() => {
      const root = document.getElementById('root');
      const rootVisible = [];
      const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      let n; while (n = w.nextNode()) { const t = n.textContent.trim(); if (t) { const el = n.parentElement; const cs = el && getComputedStyle(el); if (cs && cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0') rootVisible.push(t.slice(0, 40)); } }
      const modal = document.querySelector('[data-settings-modal-body]');
      return JSON.stringify({ url: location.pathname, rootLen: root ? root.innerHTML.length : -1, visible: rootVisible.length, sample: rootVisible.slice(0, 8), modal: !!modal });
    })()`,
    returnByValue: true,
  })
  let v
  try { v = JSON.parse(r.result.result.value) } catch { v = { parseError: true } }
  const flag = v.visible > 0 && v.rootLen > 1000 ? 'OK ' : 'WHITE'
  console.log(`[${flag}] ${label} len=${v.rootLen} vis=${v.visible} modal=${v.modal} exc=${exceptions.length} err=${errors.length} netfail=${failed.length}`)
  if (exceptions.length) {
    const d = exceptions[0].params.exceptionDetails
    console.log('   EXC:', d.exception?.className, d.text, (d.exception?.description || '').split('\n').slice(0, 2).join(' | '))
  }
  if (errors.length) console.log('   ERR:', errors[0].params.args.map((a) => a.value ?? a.description ?? '').join(' ').slice(0, 200))
  if (failed.length) console.log('   NET:', failed.slice(0, 3).map((e) => `${e.params.type} ${e.params.errorText}`).join(' ; '))
  if (flag === 'WHITE') {
    await send('Page.captureScreenshot', { format: 'png' }).then(async (s) => {
      if (s.result?.data) { const { writeFileSync } = await import('node:fs'); writeFileSync(`C:/temp/lg-deep-${label}.png`, Buffer.from(s.result.data, 'base64')) }
    })
  }
}
ws.close()
chrome.kill()
console.log('done')
