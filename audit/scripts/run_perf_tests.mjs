// LearnGraph audit - performance runtime (dev 5174; prod build 5175 broken - P0 noted separately)
// Evidence: audit/evidence/perf/perf-report.json + graph-300.png
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFileSync } from 'node:child_process'

const require = createRequire(import.meta.url)
const __dirname = path.dirname(fileURLToPath(import.meta.url))
const EVID = path.resolve(__dirname, '..', 'evidence', 'perf')
fs.mkdirSync(EVID, { recursive: true })
const candidates = [
  path.join('C:', 'Users', '13600', 'AppData', 'Roaming', 'npm', 'node_modules', 'playwright'),
  path.join('C:', 'Users', '13600', 'AppData', 'Local', 'npm-cache', '_npx', '423231821c231c73', 'node_modules', 'playwright'),
  path.join('C:', 'Users', '13600', 'AppData', 'Local', 'npm-cache', '_npx', 'e41f203b7505f1fb', 'node_modules', 'playwright'),
]
let chromium = null
for (const c of candidates) { try { chromium = require(c).chromium; break } catch {} }
if (!chromium) { console.error('playwright not found'); process.exit(1) }

const BASE = process.env.BASE || 'http://127.0.0.1:5174'
const API = 'http://127.0.0.1:8002/api/v1'
const EXE = path.join('C:', 'Users', '13600', 'AppData', 'Local', 'ms-playwright', 'chromium-1234', 'chrome-win64', 'chrome.exe')
const out = { pages: {}, graph: {}, memory: {} }

async function measurePage(browser, url, label) {
  const results = []
  for (let i = 0; i < 3; i++) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
    const page = await ctx.newPage()
    const errors = []
    page.on('pageerror', (e) => errors.push(String(e).slice(0, 150)))
    try {
      const t0 = Date.now()
      await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 })
      const loadMs = Date.now() - t0
      const nt = await page.evaluate(() => {
        const p = performance.getEntriesByType('navigation')[0]
        const js = performance.getEntriesByType('resource').filter(r => r.initiatorType === 'script' || r.name.endsWith('.js'))
        return {
          ttfb: p.responseStart - p.requestStart,
          domContentLoaded: Math.round(p.domContentLoadedEventEnd),
          jsCount: js.length,
          jsBytes: js.reduce((a, r) => a + (r.transferSize || 0), 0),
        }
      })
      results.push({ loadMs, errors, ...nt })
      await ctx.close()
    } catch (e) { results.push({ error: String(e).slice(0, 150) }); await ctx.close() }
  }
  out.pages[label] = results
  const ok = results.filter(r => r.loadMs)
  if (ok.length) {
    const med = ok.map(r => r.loadMs).sort((a, b) => a - b)[Math.floor(ok.length / 2)]
    console.log(`[perf] ${label}: median ${med}ms runs=${JSON.stringify(ok.map(r => r.loadMs))} js=${ok[0].jsCount}files ${Math.round(ok[0].jsBytes / 1024)}KB ttfb=${ok[0].ttfb}ms dcl=${ok[0].domContentLoaded}ms`)
  }
}

async function main() {
  const browser = await chromium.launch({ headless: true, executablePath: EXE })
  await measurePage(browser, BASE + '/auth/login', 'login')

  // register user, copy provider, create REAL goal via API, insert 300-node graph via SQL
  const uname = 'perf' + Date.now().toString(36)
  const pw = 'Perf@Pass2026!x'
  const reg = await fetch(API + '/auth/register', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: uname, display_name: 'Perf User', password: pw }) }).then(r => r.json())
  const tok = reg.access_token
  const ws = (await fetch(API + '/workspaces', { headers: { Authorization: 'Bearer ' + tok } }).then(r => r.json()))[0].id
  console.log('[perf] user', uname, 'ws', ws)
  const setupPy = `
import json, uuid, sqlite3, datetime, time
from urllib.request import Request, urlopen
import urllib.error
BASE = "http://127.0.0.1:8002/api/v1"
def req(m, p, b=None, t=None, w=None, timeout=90):
    d = json.dumps(b).encode() if b is not None else None
    h = {"Content-Type": "application/json"}
    if t: h["Authorization"] = f"Bearer {t}"
    if w: h["X-Workspace-ID"] = w
    r = Request(f"{BASE}{p}", data=d, headers=h, method=m)
    try:
        with urlopen(r, timeout=timeout) as resp: return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e: return e.code, e.read().decode()[:200]
tok = "${tok}"; ws = "${ws}"
con = sqlite3.connect(r"${path.join(__dirname, '..', '..', 'backend', 'data', 'audit_test.db')}")
src = "abd8294b-6cf4-4f31-ba5b-eaa80ca9cf87"
newid = str(uuid.uuid4())
cols = [d[0] for d in con.execute("SELECT * FROM provider_configs LIMIT 0").description]
row = con.execute("SELECT * FROM provider_configs WHERE id=?", (src,)).fetchone()
con.execute(f"INSERT INTO provider_configs ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
    [newid if c=='id' else (ws if c=='workspace_id' else row[cols.index(c)]) for c in cols])
scols = [d[0] for d in con.execute("SELECT * FROM provider_secrets LIMIT 0").description]
srow = con.execute("SELECT * FROM provider_secrets WHERE provider_id=?", (src,)).fetchone()
con.execute(f"INSERT INTO provider_secrets ({','.join(scols)}) VALUES ({','.join('?'*len(scols))})",
    [newid if c=='provider_id' else (ws if c=='workspace_id' else srow[scols.index(c)]) for c in scols])
con.commit(); con.close()
st, g = req("POST", "/goals/clarify", {"prompt": "审计性能测试目标"}, t=tok, w=ws)
gid = g["goal"]["id"] if isinstance(g, dict) and "goal" in g else (g.get("goal_id") or g.get("id"))
now = datetime.datetime.now(datetime.UTC).isoformat()
ggraph = str(uuid.uuid4()); P = "pg" + str(int(time.time()))
con = sqlite3.connect(r"${path.join(__dirname, '..', '..', 'backend', 'data', 'audit_test.db')}")
con.execute("INSERT INTO graphs (id,goal_id,title,status,revision,created_at,updated_at,workspace_id) VALUES (?,?,?, 'active',1,?,?,?)", (ggraph, gid, "perf-graph-300", now, now, ws))
for i in range(300):
    con.execute("INSERT INTO graph_nodes (id,graph_id,label,description,node_type,target_weight,mastery_stars,retrieval_state,evidence_state,attention_state,teaching_strategy,created_at,updated_at,workspace_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"{P}-{i}", ggraph, f"节点{i}", "", "knowledge", 1.0, i%5, "fresh", "robust", "focused", "", now, now, ws))
for i in range(299):
    con.execute("INSERT INTO graph_edges (id,graph_id,source_node_id,target_node_id,relation,created_at,updated_at,workspace_id) VALUES (?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), ggraph, f"{P}-{i}", f"{P}-{i+1}", "relates", now, now, ws))
con.commit(); con.close()
print(ggraph)
`
  const gid = execFileSync('python', ['-c', setupPy], { encoding: 'utf-8' }).trim()
  console.log('[perf] graph', gid, 'created (300 nodes)')

  // UI login
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await ctx.newPage()
  await page.goto(BASE + '/auth/login', { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('#username', { timeout: 15000 })
  await page.locator('#username').fill(uname)
  await page.locator('#password').fill(pw)
  await page.getByRole('button', { name: /登录/ }).click()
  await page.waitForURL(/\/w\//, { timeout: 20000 }).catch(() => {})
  console.log('[perf] logged in:', page.url())

  // graph page load
  const t0 = Date.now()
  await page.goto(`${BASE}/w/${ws}/graphs/${gid}`, { waitUntil: 'networkidle', timeout: 45000 }).catch(() => {})
  const loadMs = Date.now() - t0
  await page.waitForTimeout(3000)
  const heap = await page.evaluate(() => performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null)
  const nodes = await page.evaluate(() => document.querySelectorAll('.react-flow__node').length).catch(() => -1)
  out.graph = { graphId: gid, pageLoadMs: loadMs, jsHeapMB: heap, domNodes: nodes }
  await page.screenshot({ path: path.join(EVID, 'graph-300.png') })
  console.log('[perf] graph page load', loadMs, 'ms, heap', heap, 'MB, dom nodes', nodes)

  // pan/zoom + select timing
  const t1 = Date.now()
  await page.mouse.move(720, 450)
  await page.mouse.wheel(0, -800)
  await page.waitForTimeout(500)
  await page.mouse.wheel(0, 800)
  await page.waitForTimeout(500)
  const firstNode = page.locator('.react-flow__node').first()
  await firstNode.click().catch(() => {})
  const t2 = Date.now()
  out.graph.interactMs = t2 - t1
  console.log('[perf] pan/zoom+select', t2 - t1, 'ms')
  await ctx.close()

  // memory loop: 10x graph<->home toggle
  const mem = []
  const ctx2 = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page2 = await ctx2.newPage()
  await page2.goto(BASE + '/auth/login', { waitUntil: 'domcontentloaded' })
  await page2.waitForSelector('#username', { timeout: 15000 })
  await page2.locator('#username').fill(uname); await page2.locator('#password').fill(pw)
  await page2.getByRole('button', { name: /登录/ }).click()
  await page2.waitForURL(/\/w\//, { timeout: 20000 }).catch(() => {})
  for (let i = 0; i < 10; i++) {
    await page2.goto(`${BASE}/w/${ws}/graphs/${gid}`, { waitUntil: 'domcontentloaded' })
    await page2.waitForTimeout(800)
    await page2.goto(`${BASE}/w/${ws}/home`, { waitUntil: 'domcontentloaded' })
    await page2.waitForTimeout(800)
    const h = await page2.evaluate(() => performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null)
    mem.push(h)
  }
  out.memory = { loopMB: mem }
  console.log('[perf] memory loop MB:', JSON.stringify(mem))
  await ctx2.close()

  fs.writeFileSync(path.join(EVID, 'perf-report.json'), JSON.stringify(out, null, 2), 'utf-8')
  console.log('[perf] done')
  await browser.close()
}

main().catch((e) => { console.error('[perf] FAILED', e); process.exit(1) })
