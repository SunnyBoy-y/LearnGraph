import { spawn } from 'node:child_process'
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const PORT = 9229
const profile = `C:/temp/lg-chrome-${Date.now()}`
const TOKEN = '0o5AYDpompQEMsCjHTvC7kDcv4a8hYYSv1AJfCj_bMofvD_JbJR4aNaHArhLJu2V'
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, '--no-first-run', '--disable-gpu', '--disable-extensions', '--remote-allow-origins=*', 'about:blank'], { stdio: 'ignore' })
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
await sleep(12000)
const dump = await send('Runtime.evaluate', {
  expression: `(() => {
    const texts = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let n;
    while (n = walker.nextNode()) {
      const t = n.textContent.trim();
      if (!t) continue;
      const el = n.parentElement;
      if (!el) continue;
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0' || cs.color === 'rgba(0, 0, 0, 0)' || cs.color === 'transparent') continue;
      texts.push(t.slice(0, 60));
    }
    const modal = document.querySelector('[data-settings-modal-body]');
    const dialogs = document.querySelectorAll('[role=dialog]');
    return JSON.stringify({
      bodyTextLen: document.body.innerText.length,
      visibleTexts: texts.slice(0, 40),
      settingsModal: !!modal,
      modalHtmlLen: modal ? modal.innerHTML.length : 0,
      dialogs: dialogs.length,
      dialogVisible: [...dialogs].map(d => ({ role: d.getAttribute('role'), cls: d.className.slice(0, 80), text: d.innerText.slice(0, 80) })),
    });
  })()`,
  returnByValue: true,
})
console.log(dump.result.result.value)
ws.close()
chrome.kill()
