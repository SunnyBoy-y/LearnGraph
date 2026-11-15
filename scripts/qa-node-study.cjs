// Isolated real Edge QA. Run: npx --package=playwright -c "node scripts/qa-node-study.cjs"
// All API responses are synthetic. Cross-origin HTTP/WS and unexpected writes are blocked.
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const bin = process.env.PATH.split(path.delimiter).find(dir => fs.existsSync(path.resolve(dir, '../playwright/package.json')));
if (!bin) throw new Error('Run with npx --package=playwright -c "node scripts/qa-node-study.cjs"');
const { chromium } = require(path.resolve(bin, '../playwright'));
const base = 'http://127.0.0.1:15173';
const out = 'artifacts/node-study';
fs.mkdirSync(out, { recursive: true });
const timestamp = '2026-11-15T08:00:00Z';
const wid = 'qa-node-study';
const graph = { id: 'study-graph', workspace_id: wid, title: '学习图谱（隔离验收）', goal_id: 'goal', status: 'published', revision: 1,
  nodes: [{ id: 'root', label: '根节点', node_type: 'root' }, { id: 'node-a', label: '当前知识点', node_type: 'concept' }, { id: 'node-b', label: '待学知识点', node_type: 'concept' }].map(n => ({ ...n, description: '用于隔离浏览器验收的学习说明', mastery_stars: 0, retrieval_state: 'unlearned', evidence_state: 'none', attention_state: 'normal' })),
  edges: ['node-a', 'node-b'].map(id => ({ id: `e-${id}`, source_node_id: 'root', target_node_id: id, relation: 'contains' })) };
const project = { id: 'study-project', workspace_id: wid, title: '学习文件夹', description: '', status: 'active', graph_id: graph.id, primary_goal_id: 'goal', position: 0, created_at: timestamp, updated_at: timestamp };
const source = { id: 'study-source', workspace_id: wid, title: '已有会话', status: 'active', memory_enabled: true, graph_id: graph.id, current_node_id: 'node-a', project_id: project.id, goal_id: 'goal', context_capsule: {}, session_kind: 'main', parent_session_id: null, pinned: false, has_children: false, created_at: timestamp, updated_at: timestamp,
  model_snapshot: { provider_id: 'study-provider', model_id: 'study-model', thinking_mode: 'off', agent_mode: false } };
const destination = { ...source, id: 'study-destination', current_node_id: 'node-b', title: '待学知识点会话' };
const report = { pass: false, browser: 'installed Microsoft Edge / Playwright headless', base, apiIsolation: 'all /api/ fulfilled locally; unexpected mutations blocked; external HTTP/WS blocked', scenarios: [], screenshots: [] };
// Optional filtered recheck keeps earlier independent passes without re-running them.
if (process.env.NODE_STUDY_MERGE && process.env.NODE_STUDY_FILTER && fs.existsSync(`${out}/report.json`)) {
  const previous = JSON.parse(fs.readFileSync(`${out}/report.json`, 'utf8'));
  const selected = new RegExp(process.env.NODE_STUDY_FILTER);
  report.scenarios = previous.scenarios.filter(s => !selected.test(s.name));
  report.screenshots = previous.screenshots.filter(file => !file.includes('-FAILED'));
}
const gate = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const pause = ms => new Promise(r => setTimeout(r, ms));
const selection = page => page.getByRole('status', { name: '会话节点绑定', exact: true });
async function shot(h, name) { const file = `${out}/${name}.png`; await h.page.screenshot({ path: file, animations: 'disabled' }); report.screenshots.push(file); }
async function harness(browser, options) {
  const target = options.sameNode ? source : destination;
  const state = { writes: [], streams: [], reads: [], unexpectedWrites: [], external: [], errors: [], consoleErrors: [], unknownReads: new Set(), records: new Map([[source.id, structuredClone(source)]]), messages: new Map(), creates: 0, folders: 0, readiness: 0, createArrived: gate(), createRelease: gate(), sandboxArrived: gate(), sandboxRelease: gate(), streamArrived: gate(), step: 'launch' };
  if (options.reuse && !options.sameNode) state.records.set(target.id, structuredClone(target));
  const context = await browser.newContext({ viewport: options.mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: !!options.mobile, hasTouch: !!options.mobile, reducedMotion: 'reduce', serviceWorkers: 'block' });
  await context.addInitScript(({ wid, agentic, ids }) => {
    window.__nodeStudyLearningEvents = [];
    window.addEventListener('learngraph:open-learning-project', e => window.__nodeStudyLearningEvents.push(e.detail));
    localStorage.setItem('learngraph.access_token', 'isolated-node-study-qa');
    localStorage.setItem('learngraph.workspace_id', wid);
    localStorage.setItem('learngraph.username', 'qa-node-study');
    localStorage.setItem('learngraph.must_change_password', 'false');
    localStorage.setItem('learngraph:session-composer-prefs', JSON.stringify(Object.fromEntries(ids.map(id => [id, { providerId: 'study-provider', modelId: 'study-model', responseMode: agentic ? 'agentic' : 'fast', thinkingMode: 'off', searchRoute: 'disabled', generationMode: 'text' }]))));
  }, { wid, agentic: !!options.slowSandbox, ids: [source.id, destination.id] });
  await context.route('**/*', async route => {
    const req = route.request(); const url = new URL(req.url());
    if (url.origin !== base) { state.external.push(`${req.method()} ${url}`); return route.abort('blockedbyclient'); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    const endpoint = url.pathname.replace(/^\/api\/v1/, ''); const method = req.method();
    const body = req.postData() ? req.postDataJSON() : null;
    if (!['GET', 'HEAD'].includes(method)) state.writes.push({ method, endpoint, body }); else state.reads.push(endpoint + url.search);
    if (method === 'POST' && endpoint === `/graphs/${graph.id}/learning-folder`) { state.folders++; return route.fulfill({ json: project }); }
    if (method === 'POST' && endpoint === '/sessions') {
      state.creates++; state.createArrived.resolve();
      if (options.createFails && state.creates === 1) return route.fulfill({ status: 503, json: { detail: '创建会话失败，请重试（QA）' } });
      if (options.slowCreate) await state.createRelease.promise;
      const record = { ...structuredClone(destination), ...body, id: destination.id, title: '待学知识点会话' };
      state.records.set(record.id, record); return route.fulfill({ json: record });
    }
    const streamMatch = endpoint.match(/^\/sessions\/([^/]+)\/messages\/stream$/);
    if (method === 'POST' && streamMatch) {
      const id = streamMatch[1]; const attempt = state.streams.length + 1;
      state.streams.push({ sessionId: id, body }); state.streamArrived.resolve();
      if (options.streamFails && attempt === 1) return route.fulfill({ contentType: 'text/event-stream', body: 'event: message.failed\ndata: ' + JSON.stringify({ type: 'message.failed', payload: { error: { code: 'provider_error', message: '模型失败，请重试（QA）' } } }) + '\n\n' });
      const user = { id: `${id}-user-${attempt}`, workspace_id: wid, session_id: id, parent_message_id: null, role: 'user', version: 1, status: 'completed', content: body.content, parts: [], provider_trace: {}, created_at: timestamp };
      const answer = { ...user, id: `${id}-answer-${attempt}`, parent_message_id: user.id, role: 'assistant', content: '节点讲解已生成：这是隔离 Mock 的 AI 回复。', parts: [{ id: `study-text-${attempt}`, type: 'text', status: 'completed', content: '节点讲解已生成：这是隔离 Mock 的 AI 回复。' }] };
      state.messages.set(id, [...(state.messages.get(id) || []), user, answer]);
      const events = [{ type: 'message.accepted', message_id: answer.id, payload: { user_message_id: user.id } }, { type: 'part.delta', part: { id: `study-text-${attempt}`, type: 'text', status: 'streaming', sequence: 1, content_delta: answer.content } }, { type: 'answer.started', payload: { final_part_id: `study-text-${attempt}`, boundary_sequence: 1 } }, { type: 'message.completed', message_id: answer.id }];
      return route.fulfill({ contentType: 'text/event-stream', body: events.map(e => `event: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`).join('') });
    }
    if (method === 'POST' && endpoint.endsWith('/auto-title')) return route.fulfill({ json: state.records.get(endpoint.split('/')[2]) });
    if (method === 'POST' && endpoint === '/sessions/opening-prompts') return route.fulfill({ json: { prompts: ['先复习当前知识点', '比较两个学习节点', '给我一道练习题'].map((content, i) => ({ id: `qa-prompt-${i}`, content })) } });
    if (!['GET', 'HEAD'].includes(method)) { state.unexpectedWrites.push({ method, endpoint, body }); return route.fulfill({ status: 409, json: { detail: 'QA blocked unexpected mutation' } }); }
    let data;
    if (endpoint === '/auth/me') data = { id: 'qa-user', username: 'qa-node-study', display_name: 'QA', is_system_admin: false, must_change_password: false };
    else if (endpoint === '/workspaces') data = [{ id: wid, name: 'LearnGraph QA', effective_permissions: [] }];
    else if (endpoint === '/providers') data = [{ id: 'study-provider', provider_type: 'openai_compatible_chat', display_name: '隔离测试模型', enabled: true, remote_capability: true, capabilities: { default_model: 'study-model' } }];
    else if (endpoint === '/providers/study-provider/models') data = { provider_id: 'study-provider', status: 'ok', models: [{ id: 'study-model', roles: ['llm'], enabled: true, remote: true, streaming: true }] };
    else if (endpoint === '/settings') data = [{ key: 'chat.suggested_prompts', value: { enabled: false } }, { key: 'chat.default_response_mode', value: { response_mode: options.slowSandbox ? 'agentic' : 'fast' } }, { key: 'ui.preferences', value: { theme: 'light', colorTheme: 'mono' } }];
    else if (endpoint === '/projects') data = [project];
    else if (endpoint === `/projects/${project.id}`) data = project;
    else if (endpoint === '/graphs') data = [graph];
    else if (endpoint === `/graphs/${graph.id}`) data = graph;
    else if (endpoint === '/sessions/page') {
      let items = [...state.records.values()].filter(s => (!url.searchParams.has('project_id') || s.project_id === url.searchParams.get('project_id')) && (!url.searchParams.has('graph_id') || s.graph_id === url.searchParams.get('graph_id')) && (!url.searchParams.has('node_id') || s.current_node_id === url.searchParams.get('node_id')));
      if (url.searchParams.has('parent_session_id') || !url.searchParams.has('project_id')) items = [];
      // The real browser API omits current_node_id; exercise detail validation.
      data = { items: items.map(({ current_node_id, ...s }) => s), next_cursor: null, previous_cursor: null, has_more: false, has_previous: false, total_count: items.length };
    }
    else if (endpoint === '/sessions/summary') data = { total: state.records.size, active: state.records.size, archived: 0, memory_enabled: state.records.size };
    else if (endpoint === '/deployment/profile') data = { mode: 'self_hosted' };
    else if (endpoint === '/sandbox/agent/readiness') {
      state.readiness++; state.sandboxArrived.resolve();
      if (options.slowSandbox) await state.sandboxRelease.promise;
      data = { available: true };
    }
    else if (endpoint === '/sandbox/bootstrap/status') data = { image_ready: true, active_job: null };
    else if (endpoint.endsWith('/context-usage')) data = { session_id: endpoint.split('/')[2], estimated_tokens: 1200, input_budget_tokens: 32000, compaction_threshold_tokens: 24000, remaining_tokens: 22800, used_ratio: 0.05, context_window_tokens: 32000, compaction_ratio: 0.75, message_count: (state.messages.get(endpoint.split('/')[2]) || []).length, compacted_message_count: 0 };
    else if (/^\/sessions\/[^/]+\/messages$/.test(endpoint)) { const items = state.messages.get(endpoint.split('/')[2]) || []; data = { items, total_count: items.length, has_more_before: false }; }
    else if (/^\/sessions\/[^/]+\/messages\/[^/]+$/.test(endpoint)) data = (state.messages.get(endpoint.split('/')[2]) || []).find(m => m.id === endpoint.split('/')[4]);
    else if (/^\/sessions\/[^/]+$/.test(endpoint)) { data = state.records.get(endpoint.split('/')[2]); if (!data) return route.fulfill({ status: 404, json: { detail: 'No isolated QA session' } }); }
    else if (endpoint.includes('/browser')) data = { items: [], has_more: false, total_count: 0, next_cursor: null };
    else { state.unknownReads.add(endpoint); data = []; }
    return route.fulfill({ json: data ?? null });
  });
  await context.routeWebSocket('**/*', socket => {
    const url = new URL(socket.url());
    if (url.host === new URL(base).host && !url.pathname.startsWith('/api/')) {
      const server = socket.connectToServer();
      server.onMessage(message => { let payload; try { payload = JSON.parse(String(message)); } catch {} if (!['update', 'full-reload'].includes(payload?.type)) socket.send(message); });
    } else { state.external.push('WS ' + socket.url()); socket.close(); }
  });
  const page = await context.newPage(); page.setDefaultTimeout(12000);
  page.on('pageerror', e => state.errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') state.consoleErrors.push(m.text()); });
  return { context, page, state, options, target };
}
async function ensureRail(h) {
  if (h.options.mobile) await h.page.locator('.topbar-graph-toggle').waitFor();
  if (h.options.mobile && await h.page.getByRole('button', { name: '展开图谱面板', exact: true }).isVisible()) await h.page.getByRole('button', { name: '展开图谱面板', exact: true }).click();
  if (!h.options.mobile && await h.page.getByRole('button', { name: '展开图谱书架', exact: true }).isVisible()) await h.page.getByRole('button', { name: '展开图谱书架', exact: true }).click();
  await selection(h.page).waitFor();
}
async function checkBound(h, label) {
  await ensureRail(h);
  const currentSelection = await selection(h.page).textContent();
  if (!currentSelection.includes(label) || !currentSelection.includes('当前学习')) await h.page.getByRole('button', { name: '回到当前学习节点', exact: true }).click();
  await h.page.waitForFunction(label => { const el = document.querySelector('[aria-label="会话节点绑定"]'); return el?.textContent.includes(label) && el?.textContent.includes('当前学习'); }, label);
  const cls = await h.page.locator(`.bound-graph-rail .react-flow__node[data-id="${label === '当前知识点' ? 'node-a' : 'node-b'}"] .knowledge-node`).getAttribute('class');
  assert.ok(cls.includes('is-current'), 'graph card retains current binding: ' + cls);
}
async function preview(h) {
  const { page, state } = h; state.step = 'preview real graph card';
  await ensureRail(h);
  await page.locator('.bound-graph-rail .react-flow__node[data-id="node-b"] .knowledge-node > strong').click();
  await page.waitForFunction(() => { const el = document.querySelector('[aria-label="会话节点绑定"]'); return el?.textContent.includes('待学知识点') && el?.textContent.includes('预览') && el?.textContent.includes('正在学：当前知识点'); });
  assert.ok(page.url().endsWith(`/chat/${source.id}`));
  assert.equal(state.creates, 0); assert.equal(state.streams.length, 0); assert.equal(state.writes.filter(w => w.endpoint !== '/sessions/opening-prompts').length, 0, 'preview does not mutate anything (initial empty-chat recommendation POST excluded)');
  assert.equal(state.records.get(source.id).current_node_id, 'node-a');
  assert.deepEqual(await page.evaluate(() => window.__nodeStudyLearningEvents), [], 'preview emits no learning navigation intent');
  assert.ok((await page.locator('.bound-graph-rail .react-flow__node[data-id="node-a"] .knowledge-node').getAttribute('class')).includes('is-current'));
}
async function routeConsumed(page) { await page.waitForFunction(() => !window.history.state?.usr?.pendingPrompt); }
async function completed(h, count = 1) {
  await h.page.getByText('节点讲解已生成：这是隔离 Mock 的 AI 回复。', { exact: true }).last().waitFor();
  await routeConsumed(h.page);
  // Refocus deliberately exercises refetch/re-render without replaying consumed intent.
  await h.page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await pause(300);
  assert.equal(h.state.streams.length, count, 'exactly one stream per explicit intent');
  const call = h.state.streams.at(-1);
  assert.equal(call.sessionId, h.target.id);
  assert.equal(call.body.content, `什么是 ${h.options.sameNode ? '当前知识点' : '待学知识点'}？`);
  assert.deepEqual(call.body.node_ids, [h.target.current_node_id]);
  assert.ok(h.page.url().endsWith(`/chat/${h.target.id}`));
  assert.equal(h.state.records.get(source.id).current_node_id, 'node-a');
  await checkBound(h, h.options.sameNode ? '当前知识点' : '待学知识点');
}
async function switchExisting(h) {
  const { page, state } = h; const before = state.streams.length; state.step = 'browser history session switching';
  if (h.options.mobile && await page.locator('.graph-drawer-close').isVisible()) await page.locator('.graph-drawer-close').click();
  await page.goBack(); await page.waitForURL(`**/chat/${source.id}`); await checkBound(h, '当前知识点');
  if (h.options.mobile && await page.locator('.graph-drawer-close').isVisible()) await page.locator('.graph-drawer-close').click();
  await page.goForward(); await page.waitForURL(`**/chat/${h.target.id}`); await checkBound(h, '待学知识点');
  assert.equal(state.streams.length, before, 'switching existing sessions must not re-submit');
  assert.equal(state.records.get(source.id).current_node_id, 'node-a');
}
const scenarios = [
  { name: 'desktop-create-double-click-slow-create', slowCreate: true, capture: true, switch: true },
  { name: 'desktop-reuse-different-route', reuse: true, switch: true },
  { name: 'desktop-reuse-same-route', reuse: true, sameNode: true },
  { name: 'desktop-slow-sandbox-second-click', reuse: true, sameNode: true, slowSandbox: true },
  { name: 'desktop-create-failure-explicit-retry', createFails: true },
  { name: 'desktop-stream-failure-explicit-retry', reuse: true, sameNode: true, streamFails: true },
  { name: 'mobile-create-double-click-slow-create', mobile: true, slowCreate: true, capture: true, switch: true },
  { name: 'mobile-reuse-different-route', mobile: true, reuse: true },
  { name: 'mobile-slow-sandbox-second-click', mobile: true, reuse: true, sameNode: true, slowSandbox: true },
];
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    for (const options of scenarios.filter(s => !process.env.NODE_STUDY_FILTER || new RegExp(process.env.NODE_STUDY_FILTER).test(s.name))) {
      const h = await harness(browser, options); const { page, state } = h;
      const result = { name: options.name, viewport: page.viewportSize(), pass: false, checks: [] }; report.scenarios.push(result);
      try {
        state.step = 'initial source session'; await page.goto(`${base}/w/${wid}/chat/${source.id}`, { waitUntil: 'domcontentloaded', timeout: 30000 }); await ensureRail(h); await checkBound(h, '当前知识点');
        if (!options.sameNode) { await preview(h); result.checks.push('real right-rail graph card click: preview only; no writes, creation, SSE, route or source binding change'); }
        if (options.capture) await shot(h, options.mobile ? 'mobile-preview' : 'desktop-preview');
        const study = page.getByRole('button', { name: options.sameNode ? '继续学习' : '学习此节点', exact: true });
        const creationFailure = options.createFails ? page.waitForResponse(r => new URL(r.url()).pathname === '/api/v1/sessions' && r.status() === 503) : null;
        state.step = 'double click learning CTA'; await study.dblclick({ delay: 70 });
        if (options.createFails) {
          await creationFailure; await pause(200);
          assert.equal(state.creates, 1); assert.equal(state.records.size, 1); assert.equal(state.streams.length, 0); assert.ok(page.url().endsWith(`/chat/${source.id}`)); assert.equal(await study.isEnabled(), true);
          await study.click(); result.checks.push('failed creation response creates no record/sends nothing; releases navigation lock for explicit retry');
        }
        if (options.slowCreate) {
          await Promise.race([state.createArrived.promise, pause(12000).then(() => { throw new Error('create not reached'); })]);
          assert.equal(state.creates, 1, 'double click during slow creation creates once'); assert.equal(state.streams.length, 0);
          await pause(350); assert.equal(state.creates, 1); state.createRelease.resolve(); result.checks.push('slow creation held behind gate: double click creates exactly once');
        }
        if (options.slowSandbox) {
          await Promise.race([state.sandboxArrived.promise, pause(12000).then(() => { throw new Error('sandbox not reached'); })]);
          await routeConsumed(page); assert.equal(state.streams.length, 0);
          const folderResponse = page.waitForResponse(r => r.url().endsWith(`/graphs/${graph.id}/learning-folder`) && r.request().method() === 'POST');
          await study.click(); await folderResponse; await routeConsumed(page); await pause(200); assert.equal(state.streams.length, 0);
          state.sandboxRelease.resolve(); result.checks.push('second explicit click during sandbox preflight: one eventual auto-submitted SSE');
        }
        if (options.streamFails) {
          await page.getByText('模型失败，请重试（QA）', { exact: false }).first().waitFor(); await routeConsumed(page); await pause(250); assert.equal(state.streams.length, 1);
          await page.evaluate(() => window.dispatchEvent(new Event('focus'))); await pause(200); assert.equal(state.streams.length, 1, 'failed intent not automatically replayed');
          await study.click(); await completed(h, 2); result.checks.push('failed SSE stays consumed; explicit retry sends once without creating');
        } else await completed(h);
        assert.equal(state.creates, options.reuse ? 0 : options.createFails ? 2 : 1);
        if (!options.reuse) { const create = state.writes.find(w => w.endpoint === '/sessions').body; assert.equal(create.graph_id, graph.id); assert.equal(create.current_node_id, 'node-b'); assert.equal(create.project_id, project.id); }
        result.checks.push('correct persisted graph/node/project binding; correct destination; prompt and node_ids; completed AI reply visible; route intent consumed; no duplicate on refocus');
        if (options.switch) { await switchExisting(h); result.checks.push('back/forward switches existing sessions, restores node-a/node-b bindings and does not send again'); }
        if (options.capture) {
          await shot(h, options.mobile ? 'mobile-bound-destination' : 'desktop-completed');
          if (options.mobile) { await page.locator('.graph-drawer-close').click(); await page.getByText('节点讲解已生成：这是隔离 Mock 的 AI 回复。', { exact: true }).last().waitFor(); await shot(h, 'mobile-completed-ai-visible'); }
        }
        assert.deepEqual(state.errors, [], 'no uncaught browser errors'); assert.deepEqual(state.external, [], 'no external calls'); assert.deepEqual(state.unexpectedWrites, [], 'no unexpected API mutation');
        assert.equal(state.writes.filter(w => w.method === 'PATCH' || w.method === 'DELETE').length, 0, 'source never rebound');
        const errors = state.consoleErrors.filter(e => !(options.createFails && e.includes('503 (Service Unavailable)'))); assert.deepEqual(errors, [], 'no unexpected console errors');
        result.pass = true; console.log('PASS', options.name, JSON.stringify({ creates: state.creates, streams: state.streams.length, folders: state.folders }));
      } catch (error) {
        result.error = error.stack; result.failedStep = state.step;
        await shot(h, options.name + '-FAILED').catch(() => {});
        console.error('FAIL', options.name, state.step, error.message);
        // Preserve the strict failure and continue independent contexts for the full matrix.
      } finally {
        result.counts = { creates: state.creates, streams: state.streams.length, folders: state.folders, readiness: state.readiness };
        result.requests = state.writes; result.reads = state.reads; result.unknownReads = [...state.unknownReads]; result.pageErrors = state.errors; result.consoleErrors = state.consoleErrors; result.external = state.external; result.unexpectedWrites = state.unexpectedWrites;
        state.createRelease.resolve(); state.sandboxRelease.resolve(); await h.context.close();
        fs.writeFileSync(`${out}/${process.env.NODE_STUDY_REPORT || 'report.json'}`, JSON.stringify(report, null, 2));
      }
    }
    report.pass = report.scenarios.length > 0 && report.scenarios.every(s => s.pass);
    if (!report.pass) process.exitCode = 1;
  } finally { await browser.close(); fs.writeFileSync(`${out}/${process.env.NODE_STUDY_REPORT || 'report.json'}`, JSON.stringify(report, null, 2)); }
})().catch(e => { console.error(e.stack); process.exitCode = 1; });
