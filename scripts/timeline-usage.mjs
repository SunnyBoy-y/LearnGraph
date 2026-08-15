import { spawn } from 'node:child_process'
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const PORT = 9230
const profile = `C:/temp/lg-chrome-${Date.now()}`
const TOKEN = '0o5AYDpompQEMsCjHTvC7kDcv4a8hYYSv1AJfCj_bMofvD_JbJR4aNaHArhLJu2V'
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
ws.onmessage = (e) => { let m; try { m = JSON.parse(typeof e.data === 'string' ? e.data : e.data.toString()) } catch { return }; if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) } }
await new Promise((r) => { ws.onopen = r })
const send = (method, params = {}) => new Promise((r) => { const i = ++seq; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })) })
await send('Runtime.enable')
await send('Page.enable')
await send('Page.navigate', { url: 'http://127.0.0.1:18000/auth/login' })
await sleep(4000)
await send('Runtime.evaluate', { expression: `(() => { const s = sessionStorage; s.setItem('learngraph.access_token', '${TOKEN}'); s.setItem('learngraph.workspace_id', 'demo-workspace'); s.setItem('learngraph.user_id', 'demo-user'); s.setItem('learngraph.username', 'demo'); s.setItem('learngraph.display_name', 'Demo User'); return 'ok' })()`, returnByValue: true })
await send('Page.navigate', { url: 'http://127.0.0.1:18000/w/demo-workspace/settings/usage' })

async function snapshot(label) {
  const r = await send('Runtime.evaluate', {
    expression: `(() => {
      const root = document.getElementById('root');
      const modal = document.querySelector('[data-settings-modal-body]');
      const rootVisible = [];
      const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      let n; while (n = w.nextNode()) { const t = n.textContent.trim(); if (t) { const el = n.parentElement; const cs = el && getComputedStyle(el); if (cs && cs.display !== 'none' && cs.visibility !== 'hidden') rootVisible.push(t.slice(0, 30)); } }
      return JSON.stringify({ rootLen: root ? root.innerHTML.length : -1, rootInnerText: (root ? root.innerText : '').slice(0, 100), modal: !!modal, modalLen: modal ? modal.innerHTML.length : 0, rootVisibleCount: rootVisible.length, firstRootVisible: rootVisible.slice(0, 5) });
    })()`,
    returnByValue: true,
  })
  let v = r.result?.result?.value ?? JSON.stringify(r).slice(0, 200)
  try { v = JSON.parse(v) } catch {}
  console.log(`[${label}]`, JSON.stringify(v))
  await send('Page.captureScreenshot', { format: 'png' }).then(async (s) => {
    const { writeFileSync } = await import('node:fs')
    if (s.result?.data) writeFileSync(`C:/temp/lg-usage-${label.replace(/[^0-9]+/g, '')}.png`, Buffer.from(s.result.data, 'base64'))
  })
}
await snapshot('t3s'); await sleep(3000)
await snapshot('t6s'); await sleep(2000)
await snapshot('t8s'); await sleep(2000)
await snapshot('t10s'); await sleep(2000)
await snapshot('t12s')
ws.close()
chrome.kill()
console.log('done')
