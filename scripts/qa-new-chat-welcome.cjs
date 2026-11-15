// Run serially: npx --package=playwright -c "node scripts/qa-new-chat-welcome.cjs"
// Isolated Edge contexts; every API response is synthetic; external HTTP is blocked.
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const packageBin = process.env.PATH.split(path.delimiter).find(dir => fs.existsSync(path.resolve(dir, '../playwright/package.json')));
if (!packageBin) throw new Error('Run with npx --package=playwright -c "node scripts/qa-new-chat-welcome.cjs"');
const { chromium } = require(path.resolve(packageBin, '../playwright'));
const base = (process.env.CHAT_QA_URL || 'http://127.0.0.1:15173').replace(/\/$/, '');
const workspaceId = 'qa-new-chat-welcome';
const outputDir = process.env.CHAT_QA_OUTPUT || 'artifacts/new-chat-welcome';
const auditSource = process.env.CLIP_AUDIT_PATH ? fs.readFileSync(process.env.CLIP_AUDIT_PATH, 'utf8') : null;
const report = { pass: false, base, checks: [], layouts: [], colors: [], requests: [], screenshots: [], scenarios: [] };
fs.mkdirSync(outputDir, { recursive: true });
const timestamp = '2026-11-15T08:00:00Z';
const sessionBase = { workspace_id: workspaceId, status: 'active', memory_enabled: true, model_snapshot: {}, context_capsule: {}, project_id: null, graph_id: null, goal_id: null, parent_session_id: null, pinned: false, has_children: false, created_at: timestamp, updated_at: timestamp };
const session = { ...sessionBase, id: 'qa-started', title: '示例对话' };
const emptySession = { ...sessionBase, id: 'qa-empty', title: '空白会话（QA）' };
const project = { id: 'qa-project', workspace_id: workspaceId, title: '计算机网络（QA）', description: '', status: 'active', position: 0, created_at: timestamp, updated_at: timestamp };
const sidebarSessions = [
  { ...sessionBase, id: 'qa-tcp', project_id: project.id, title: 'TCP 三次握手（QA）' },
  { ...sessionBase, id: 'qa-recent', title: '学习计划（QA）' },
];
const promptSets = [
  ['结合上次的网络笔记，TCP 为什么需要三次握手？', '用一个生活例子解释拥塞控制。', '帮我找出网络协议复习中的薄弱环节。'],
  ['从上次的学习进度继续，先复习哪个知识点？', '如何区分流量控制与拥塞控制？', '给我一道循序渐进的网络协议练习题。'],
  ['把最近复习的协议串成一张知识图谱。', '用自己的话解释重传机制应该怎么说？', '围绕上次的薄弱点安排一次小测验。'],
];
function check(label) { report.checks.push(label); console.log('PASS', label); }
const postCount = (h, endpoint) => h.state.writes.filter(item => item.method === 'POST' && item.endpoint === endpoint).length;
const editor = page => page.getByRole('combobox', { name: '输入消息', exact: true });
const prompts = page => page.locator('.new-chat-suggestions__prompt');
const refresh = page => page.getByRole('button', { name: '换一组推荐问题', exact: true });
const stats = page => page.getByRole('button', { name: '上下文用量与会话统计', exact: true });

async function harness(browser, options = {}) {
  const state = { appearance: { theme: 'light', colorTheme: 'mono' }, mode: 'success', providers: true, messages: [], opening: [], writes: [], errors: [], consoleErrors: [], external: [], unknownReads: new Set(), ...options };
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce', serviceWorkers: 'block' });
  await context.addInitScript(({ workspaceId }) => {
    localStorage.setItem('learngraph.access_token', 'isolated-welcome-qa');
    localStorage.setItem('learngraph.workspace_id', workspaceId);
    localStorage.setItem('learngraph.username', 'qa-welcome');
    localStorage.setItem('learngraph.must_change_password', 'false');
  }, { workspaceId });
  await context.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== new URL(base).origin) {
      state.external.push(request.method() + ' ' + request.url());
      return route.abort('blockedbyclient');
    }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    if (!url.pathname.startsWith('/api/v1/')) return route.fulfill({ status: 404, json: { detail: 'QA blocked unknown API namespace' } });
    const endpoint = url.pathname.replace('/api/v1', '');
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    if (!['GET', 'HEAD'].includes(method)) state.writes.push({ method, endpoint, body });
    if (endpoint === '/sessions/opening-prompts' && method === 'POST') {
      const call = { index: state.opening.length + 1, body, mode: state.mode };
      state.opening.push(call);
      if (state.mode === 'failure-once' && state.opening.length === 1) {
        call.status = 503;
        return route.fulfill({ status: 503, json: { detail: 'QA 模拟推荐生成暂不可用' } });
      }
      call.status = 200;
      const scope = JSON.stringify([body.project_id, body.provider_id, body.model_id]);
      state.pools ??= new Map();
      let cached = state.pools.get(scope);
      if (!cached || (body.exhausted_pool_id && body.exhausted_pool_id === cached.metadata.request_id)) {
        const pool = state.mode === 'empty' ? [] : promptSets.flat().map((content, i) => ({ id: `qa-opening-${call.index}-${i}`, content }));
        cached = { prompts: pool.slice(0, 3), pool, metadata: { request_id: `qa-pool-${call.index}`, refresh_after_seconds: 600 } };
        state.pools.set(scope, cached);
      }
      call.prompts = cached.prompts;
      return route.fulfill({ json: cached });
    }
    if (endpoint === '/sessions' && method === 'POST') return route.fulfill({ json: session });
    if (endpoint === '/sessions/qa-started/messages/stream' && method === 'POST') {
      const user = { id: 'qa-user-message', workspace_id: workspaceId, session_id: session.id, parent_message_id: null, role: 'user', version: 1, status: 'completed', content: body.content, parts: [], provider_trace: {}, created_at: timestamp };
      const answer = { ...user, id: 'qa-answer', parent_message_id: user.id, role: 'assistant', content: '我们从一个有趣的问题开始学习。', parts: [{ id: 'qa-text', type: 'text', status: 'completed', content: '我们从一个有趣的问题开始学习。' }] };
      state.messages = [user, answer];
      const events = [
        { type: 'message.accepted', message_id: answer.id, payload: { user_message_id: user.id } },
        { type: 'part.delta', part: { id: 'qa-text', type: 'text', status: 'streaming', sequence: 1, content_delta: answer.content } },
        { type: 'answer.started', payload: { final_part_id: 'qa-text', boundary_sequence: 1 } },
        { type: 'message.completed', message_id: answer.id },
      ];
      return route.fulfill({ contentType: 'text/event-stream', body: events.map(event => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join('') });
    }
    // Delete-impact is a read-only preview. Actual delete/other writes are never allowed.
    if (endpoint.endsWith('delete-impact')) return route.fulfill({ json: { resource_type: 'mixed_batch', resource_id: 'qa-selection', title: 'QA 选择项', impacts: [{ resource_type: 'project', action: 'delete', count: 1 }, { resource_type: 'chat_session', action: 'delete', count: 2 }], confirmation_text: 'DELETE' } });
    if (!['GET', 'HEAD'].includes(method)) return route.fulfill({ status: 409, json: { detail: 'QA blocked unexpected mutation: ' + method + ' ' + endpoint } });
    let data;
    if (endpoint === '/auth/me') data = { id: 'qa-user', username: 'qa-welcome', display_name: 'QA', is_system_admin: false, must_change_password: false };
    else if (endpoint === '/workspaces') data = [{ id: workspaceId, name: 'LearnGraph', effective_permissions: [] }];
    else if (endpoint === '/providers') data = state.providers ? [{ id: 'qa-provider', provider_type: 'openai_compatible_chat', display_name: 'QA provider', enabled: true, remote_capability: true, capabilities: { default_model: 'qa-model' } }] : [];
    else if (endpoint === '/providers/qa-provider/models') data = { provider_id: 'qa-provider', status: 'ok', models: [{ id: 'qa-model', roles: ['llm'], enabled: true, remote: true, streaming: true }] };
    else if (endpoint === '/settings') data = [{ key: 'chat.default_response_mode', value: { response_mode: 'fast' } }, { key: 'ui.preferences', value: state.appearance }];
    else if (endpoint === '/projects') data = [project];
    else if (endpoint === '/projects/qa-project') data = project;
    else if (endpoint === '/sessions/page') {
      let items = sidebarSessions.filter(item => url.searchParams.get('project_id') ? item.project_id === url.searchParams.get('project_id') : !item.project_id);
      if (url.searchParams.has('parent_session_id')) items = [];
      data = { items, next_cursor: null, previous_cursor: null, has_more: false, has_previous: false, total_count: items.length };
    } else if (endpoint === '/sessions/summary') data = { total: 2, active: 2, archived: 0, memory_enabled: 2 };
    else if (endpoint === '/deployment/profile') data = { mode: 'self_hosted' };
    else if (endpoint.endsWith('/context-usage')) data = { session_id: endpoint.split('/')[2], estimated_tokens: 1200, input_budget_tokens: 32000, compaction_threshold_tokens: 24000, remaining_tokens: 22800, used_ratio: 0.05, context_window_tokens: 32000, compaction_ratio: 0.75, message_count: endpoint.includes('/qa-empty/') ? 0 : state.messages.length, compacted_message_count: 0 };
    else if (endpoint.endsWith('/messages')) data = { items: endpoint.includes('/qa-started/') ? state.messages : [], total_count: endpoint.includes('/qa-started/') ? state.messages.length : 0, has_more_before: false };
    else if (/^\/sessions\/qa-started\/messages\/qa-(user-message|answer)$/.test(endpoint)) data = state.messages.find(message => endpoint.endsWith(message.id));
    else if (endpoint === '/sessions/qa-started') data = session;
    else if (endpoint === '/sessions/qa-empty') data = emptySession;
    else if (/^\/sessions\/qa-(tcp|recent)$/.test(endpoint)) data = sidebarSessions.find(item => endpoint.endsWith(item.id));
    else if (endpoint.includes('/browser')) data = { items: [], has_more: false, total_count: 0, next_cursor: null };
    else { state.unknownReads.add(endpoint); data = []; }
    return route.fulfill({ status: 200, json: data ?? null });
  });
  // Permit only same-origin Vite HMR; no API or third-party websocket can connect.
  await context.routeWebSocket('**/*', socket => {
    const url = new URL(socket.url());
    if (url.host === new URL(base).host && !url.pathname.startsWith('/api/')) {
      const server = socket.connectToServer();
      server.onMessage(message => {
        // Keep hot updates from interrupting a scenario; explicit navigation
        // still loads the current source normally.
        let data;
        try { data = JSON.parse(String(message)); } catch { /* Not a Vite JSON message. */ }
        if (data?.type !== 'update' && data?.type !== 'full-reload') socket.send(message);
      });
    } else { state.external.push('WS ' + socket.url()); socket.close(); }
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.setDefaultNavigationTimeout(30000);
  page.on('pageerror', error => { state.errors.push(error.message); console.error('PAGE_ERROR', error.message); });
  page.on('console', message => { if (message.type() === 'error') state.consoleErrors.push(message.text()); });
  return { state, context, page };
}
async function openWelcome(h, suffix = 'new') {
  await h.page.goto(`${base}/w/${workspaceId}/chat/${suffix}`, { waitUntil: 'domcontentloaded' });
  await h.page.getByRole('heading', { name: '从一个问题开始', exact: true }).waitFor();
  await editor(h.page).waitFor();
}
async function waitPrompts(h) {
  await prompts(h.page).first().waitFor();
  await h.page.locator('.new-chat-suggestions[aria-busy="false"]').waitFor();
  assert.equal(await prompts(h.page).count(), 3);
}
async function screenshot(page, name) {
  const target = `${outputDir}/${name}.png`;
  await page.screenshot({ path: target, animations: 'disabled' });
  report.screenshots.push(target);
}
async function bounds(page, label) {
  const result = await page.evaluate(() => {
    const root = document.querySelector('.chat-canvas-page');
    const r = root.getBoundingClientRect();
    const elements = [...document.querySelectorAll('.new-chat-heading, .new-chat-suggestions, .new-chat-suggestions__prompt, .chat-composer-anchor')].map(el => {
      const rect = el.getBoundingClientRect();
      return { element: el.className, x: rect.x, y: rect.y, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height };
    });
    return { width: innerWidth, height: innerHeight, overflow: root.scrollWidth - root.clientWidth, pageOverflow: document.documentElement.scrollWidth - innerWidth, scrollBottom: r.y + root.scrollHeight, elements };
  });
  assert.ok(result.overflow <= 1 && result.pageOverflow <= 1, label + ': no horizontal overflow ' + JSON.stringify(result));
  for (const el of result.elements) {
    assert.ok(el.x >= -1 && el.right <= result.width + 1, label + ': horizontal bounds ' + JSON.stringify(el));
    assert.ok(el.y >= -1 && el.bottom <= Math.max(result.height, result.scrollBottom) + 1, label + ': reachable vertical bounds ' + JSON.stringify(el));
  }
  if (auditSource) {
    const audit = await page.evaluate(`(${auditSource})({ root: '.chat-composer-dock', viewport: true })`);
    assert.equal(audit.cut, 0, label + ': composer shadows must not be clipped');
    result.audit = audit;
  }
  report.layouts.push({ label, ...result });
  check(label + ' layout');
}
async function colors(sidebar, label) {
  const result = await sidebar.evaluate(root => {
    const entries = [...root.querySelectorAll('[data-nav-label]')].map(el => ({ label: el.getAttribute('data-nav-label'), text: getComputedStyle(el).color, icon: el.querySelector('svg') ? getComputedStyle(el.querySelector('svg')).color : null }));
    const brand = root.querySelector('.sidebar-brand');
    const mark = root.querySelector('.sidebar-brand__mark');
    const more = root.querySelector('.sidebar-nav__more > button');
    const projectHeading = root.querySelector('.sidebar-project__heading');
    const projectHeader = root.querySelector('.sidebar-project__header');
    const folder = root.querySelector('.sidebar-project__folder-toggle');
    const sessionRow = root.querySelector('.sidebar-project__session');
    const style = el => el ? { color: getComputedStyle(el).color, background: getComputedStyle(el).backgroundColor, shadow: getComputedStyle(el).boxShadow } : null;
    return { dark: document.documentElement.classList.contains('dark'), colorMode: document.documentElement.dataset.colorMode, entries, brand: style(brand), mark: style(mark), more: style(more), moreIcon: style(more?.querySelector('svg')), project: style(projectHeading), projectHeader: style(projectHeader), folder: style(folder), session: style(sessionRow) };
  });
  assert.equal(new Set(result.entries.map(entry => entry.icon)).size, 1, `${label}: neutral navigation icons`);
  assert.equal(result.mark.background, 'rgb(25, 25, 25)', label + ': black brand background');
  assert.equal(result.mark.color, 'rgb(255, 255, 255)', label + ': white brand glyph');
  assert.ok(result.entries.some(entry => entry.label === '产物') && result.entries.some(entry => entry.label === '定时任务'));
  assert.ok(result.more && result.moreIcon, label + ': More retains inherited palette');
  if (result.project) {
    assert.equal(result.project.background, 'rgba(0, 0, 0, 0)', label + ': transparent project surface retained');
    assert.equal(result.project.shadow, 'none');
    assert.equal(result.folder.color, result.project.color, label + ': folder inherits historical project text');
  }
  report.colors.push({ label, ...result });
  check(label + ' computed neutral sidebar palette');
}
async function batchChecks(h, sidebar, label) {
  const { page, state } = h;
  const writesBefore = state.writes.length;
  const manager = sidebar.getByRole('button', { name: '管理项目分组', exact: true });
  await sidebar.getByRole('button', { name: '选择项目 计算机网络（QA）', exact: true }).waitFor();
  await manager.focus();
  await manager.press('Enter');
  await page.getByRole('menuitem', { name: '批量管理', exact: true }).click();
  await sidebar.getByRole('checkbox', { name: '选择项目 计算机网络（QA）', exact: true }).waitFor();
  await sidebar.getByRole('button', { name: '全选已加载', exact: true }).click();
  await sidebar.getByRole('button', { name: '取消全选', exact: true }).waitFor();
  const selected = await sidebar.locator('[role="checkbox"][data-state="checked"]').count();
  assert.ok(selected >= 2, label + ': project and recent session selected');
  await sidebar.getByRole('button', { name: '批量删除', exact: true }).click();
  const dialog = page.getByRole('alertdialog');
  await dialog.waitFor();
  const confirm = dialog.getByRole('button', { name: /^(删除选中项|删除 \d+ 个会话|删除会话|确认删除)$/ });
  await confirm.waitFor({ state: 'visible' });
  await dialog.locator('div[aria-busy="false"]').waitFor();
  assert.equal(await confirm.isEnabled(), true, label + ': delete-impact preview has finished');
  await dialog.getByRole('button', { name: '取消，保留', exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
  assert.equal(await sidebar.locator('[role="checkbox"][data-state="checked"]').count(), selected, label + ': cancelling preserves selection');
  await sidebar.getByRole('button', { name: '取消全选', exact: true }).click();
  assert.equal(await sidebar.locator('[role="checkbox"][data-state="checked"]').count(), 0);
  await manager.focus();
  await manager.press('Enter');
  await page.getByRole('menuitem', { name: '退出批量管理', exact: true }).click();
  assert.equal(await sidebar.getByRole('checkbox').count(), 0);
  assert.ok(state.writes.slice(writesBefore).every(item => item.endpoint.endsWith('delete-impact')), label + ': batch preview/cancel has no mutations');
  check(label + ' batch menu / select all / preview / cancel / deselect / exit');
}
function verifyIsolation(h, name) {
  assert.deepEqual(h.state.errors, [], name + ': zero uncaught browser errors');
  assert.deepEqual(h.state.external, [], name + ': zero external requests');
  assert.ok(h.state.writes.every(item => item.endpoint === '/sessions/opening-prompts' || item.endpoint === '/sessions' || item.endpoint === '/sessions/qa-started/messages/stream' || item.endpoint.endsWith('delete-impact')), name + ': no unexpected writes');
  assert.ok(!h.state.writes.some(item => item.method === 'DELETE' || /\/delete$/.test(item.endpoint)), name + ': no delete submitted');
  const unexpectedConsole = h.state.consoleErrors.filter(text => !text.includes('503 (Service Unavailable)'));
  assert.deepEqual(unexpectedConsole, [], name + ': zero unexpected console errors');
  report.scenarios.push({ name, pageErrors: h.state.errors, consoleErrors: h.state.consoleErrors, externalRequests: h.state.external, unknownMockedReads: [...h.state.unknownReads] });
  report.requests.push({ scenario: name, opening: h.state.opening.map(({ index, body, status }) => ({ index, body, status })), writes: h.state.writes });
}

async function mobileWelcomeChecks(h) {
  const { page, state } = h;
  const geometry = async label => {
    await bounds(page, label);
    const layout = await page.evaluate(() => {
      const heading = document.querySelector('.new-chat-heading');
      const suggestions = document.querySelector('.new-chat-suggestions');
      const composer = document.querySelector('.chat-composer-anchor');
      const box = el => { const r = el.getBoundingClientRect(); return { top: r.top, bottom: r.bottom }; };
      return { heading: box(heading), suggestions: box(suggestions), composer: box(composer), position: getComputedStyle(suggestions).position, mobile: matchMedia('(max-width: 1024px)').matches };
    });
    assert.ok(layout.heading.bottom <= Math.min(layout.suggestions.top, layout.composer.top) + 1, label + ': heading stays above content');
    if (layout.mobile) {
      assert.equal(layout.position, 'static', label + ': suggestions participate in flex layout');
      assert.ok(layout.suggestions.bottom + 20 <= layout.composer.top, label + ': all suggestions stay above the composer');
    } else {
      assert.equal(layout.position, 'absolute', label + ': desktop positioning is unchanged');
      assert.ok(layout.composer.bottom <= layout.suggestions.top, label + ': desktop suggestions stay below the composer');
    }
  };
  await openWelcome(h);
  await waitPrompts(h);
  for (const [width, height] of [[320, 640], [390, 844], [768, 1024], [1024, 768], [1025, 900], [1440, 1000]]) {
    await page.setViewportSize({ width, height });
    await page.locator('.chat-canvas-page').evaluate(el => { el.scrollTop = 0; });
    await geometry(`${width}x${height}`);
  }
  await screenshot(page, 'mobile-layout-desktop');
  await page.setViewportSize({ width: 390, height: 844 });
  await editor(page).fill('保留手机端草稿');
  const before = await editor(page).boundingBox();
  await editor(page).evaluate(el => { window.__qaComposer = el; });
  const firstGroup = await prompts(page).allTextContents();
  const openingCount = state.opening.length;
  await refresh(page).click();
  await waitPrompts(h);
  assert.notDeepEqual(await prompts(page).allTextContents(), firstGroup);
  assert.equal(state.opening.length, openingCount, 'Mobile refresh reuses the generated pool');
  assert.equal(await editor(page).inputValue(), '保留手机端草稿');
  assert.equal(await editor(page).evaluate(el => el === window.__qaComposer), true, 'Refreshing does not remount the input');
  const after = await editor(page).boundingBox();
  assert.ok(Math.abs(after.y - before.y) <= 1, 'Refresh leaves the input in place');
  await geometry('mobile refreshed draft');
  await editor(page).fill(Array.from({ length: 10 }, (_, i) => `第 ${i + 1} 行手机端草稿`).join('\n'));
  await geometry('mobile multiline draft');
  await page.getByRole('button', { name: '展开输入框', exact: true }).click();
  assert.equal(await page.locator('.new-chat-suggestions').isVisible(), false);
  await editor(page).press('Escape');
  await geometry('mobile collapsed draft');
  await editor(page).fill('');
  await page.locator('.new-chat-suggestions__prompt').last().scrollIntoViewIfNeeded();
  await screenshot(page, 'mobile-layout-phone');
  for (const suffix of ['new?project=qa-project', 'qa-empty']) {
    await openWelcome(h, suffix);
    await waitPrompts(h);
    await geometry('mobile ' + suffix);
  }
  state.appearance.theme = 'dark';
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForSelector('html.dark .is-new-chat-welcome');
  await waitPrompts(h);
  await geometry('mobile dark');
  await page.goto(`${base}/w/${workspaceId}/chat/new?mode=goal`, { waitUntil: 'domcontentloaded' });
  await editor(page).waitFor();
  assert.equal(await page.locator('.new-chat-suggestions').count(), 0, 'Goal setup keeps its own layout');
  await page.getByRole('button', { name: '目标中', exact: true }).click();
  await waitPrompts(h);
  const selected = await prompts(page).first().innerText();
  await prompts(page).first().click();
  await page.waitForURL('**/chat/qa-started');
  await page.getByText('我们从一个有趣的问题开始学习。', { exact: true }).waitFor();
  await page.locator('.new-chat-heading').waitFor({ state: 'detached' });
  assert.equal(await page.locator('.new-chat-suggestions').count(), 0);
  assert.equal(postCount(h, '/sessions'), 1);
  assert.equal(postCount(h, '/sessions/qa-started/messages/stream'), 1);
  assert.equal(state.writes.find(item => item.endpoint.endsWith('/messages/stream')).body.content, selected);
  await bounds(page, 'mobile started conversation');
  await page.setViewportSize({ width: 1440, height: 1000 });
  await bounds(page, 'desktop started conversation');
  await stats(page).waitFor();
  check('mobile ordering / breakpoint boundaries / refresh / stable input / multiline draft / expand / project and empty routes / dark theme / goal mode / send');
}

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  let active;
  try {
    if (process.env.CHAT_QA_MOBILE_LAYOUT === '1') {
      active = await harness(browser);
      await mobileWelcomeChecks(active);
      verifyIsolation(active, 'mobile welcome layout');
      report.pass = true;
      console.log('PASS_ALL', JSON.stringify({ checks: report.checks, screenshots: report.screenshots }));
      return;
    }
    active = await harness(browser);
    const h = active;
    const page = h.page;
    await openWelcome(h);
    await waitPrompts(h);
    assert.equal(h.state.opening.length, 1, 'Initial recommendation uses one mocked POST');
    assert.equal(h.state.opening[0].body.count, 3);
    assert.equal(h.state.opening[0].body.project_id, null);
    assert.equal(await stats(page).count(), 0, 'New homepage hides usage even with a valid usage mock');
    check('initial server-generated recommendations / POST body / no new-home stats');
    const firstGroup = await prompts(page).allTextContents();
    await editor(page).fill('保留这个还没发出的想法');
    const callsBeforeRefresh = h.state.opening.length;
    await refresh(page).click();
    await waitPrompts(h);
    assert.equal(h.state.opening.length, callsBeforeRefresh, 'Refresh rotates the pre-generated pool without a request');
    assert.notDeepEqual(await prompts(page).allTextContents(), firstGroup);
    assert.equal(await editor(page).inputValue(), '保留这个还没发出的想法');
    await refresh(page).click();
    await waitPrompts(h);
    assert.notDeepEqual(await prompts(page).allTextContents(), firstGroup);
    await refresh(page).click();
    await waitPrompts(h);
    assert.deepEqual(await prompts(page).allTextContents(), firstGroup);
    assert.equal(h.state.opening.length, callsBeforeRefresh, 'Cycling the pool during cooldown must not regenerate');
    assert.equal(postCount(h, '/sessions'), 0, 'Generating or refreshing recommendations must not create a session');
    await page.reload({ waitUntil: 'domcontentloaded' });
    await waitPrompts(h);
    assert.equal(await editor(page).inputValue(), '保留这个还没发出的想法');
    await editor(page).fill('');
    check('local pool rotation / cooldown reuse / preserved draft / reload / no session creation');
    const desktop = page.locator('.workspace-sidebar .sidebar-nav');
    const expandSidebar = desktop.getByRole('button', { name: '展开侧边栏', exact: true });
    if (await expandSidebar.isVisible()) await expandSidebar.click();
    await page.locator('.workspace-sidebar .sidebar-nav:not(.is-collapsed)').waitFor();
    await bounds(page, 'desktop light');
    await colors(desktop, 'desktop light mono');
    const recentCreate = desktop.getByRole('button', { name: '新建最近对话', exact: true });
    await page.mouse.move(1000, 200);
    assert.equal(await recentCreate.evaluate(el => getComputedStyle(el).opacity), '0');
    await desktop.locator('.sidebar-section--recent .sidebar-section__heading').hover();
    assert.equal(await recentCreate.evaluate(el => getComputedStyle(el).opacity), '1');
    for (const heading of await desktop.locator('.sidebar-section__toggle').all()) {
      assert.equal(await heading.evaluate(el => getComputedStyle(el).fontWeight), '600');
    }
    check('recent create appears on hover / section titles are semibold');
    await screenshot(page, 'desktop');
    await batchChecks(h, desktop, 'desktop');
    h.state.appearance.theme = 'dark';
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForSelector('html.dark .is-new-chat-welcome');
    await waitPrompts(h);
    await bounds(page, 'desktop dark');
    await colors(desktop, 'desktop dark mono');
    await screenshot(page, 'desktop-dark');
    h.state.appearance.theme = 'light';
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForSelector('html:not(.dark) .is-new-chat-welcome');
    await waitPrompts(h);
    await editor(page).fill(Array.from({ length: 12 }, (_, i) => `第 ${i + 1} 行学习想法`).join('\n'));
    await bounds(page, 'long draft');
    await page.getByRole('button', { name: '展开输入框', exact: true }).click();
    await page.getByRole('button', { name: '收起输入框', exact: true }).waitFor();
    assert.ok((await editor(page).inputValue()).includes('第 12 行学习想法'));
    await editor(page).press('Escape');
    await bounds(page, 'collapsed long draft');
    await editor(page).fill('');
    for (const [width, height, name] of [[390, 844, 'mobile'], [320, 640, 'small-mobile']]) {
      await page.setViewportSize({ width, height });
      await page.getByRole('heading', { name: '从一个问题开始', exact: true }).click();
      await bounds(page, name);
      assert.equal(await stats(page).count(), 0);
      await screenshot(page, name);
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: '打开导航', exact: true }).click();
    const mobile = page.locator('.sidebar-nav.is-mobile');
    await mobile.getByRole('button', { name: '管理项目分组', exact: true }).waitFor();
    await colors(mobile, 'mobile light mono');
    const mobileRecent = mobile.getByRole('button', { name: '新建最近对话', exact: true });
    assert.equal(await mobileRecent.evaluate(el => getComputedStyle(el).opacity), '1');
    const mobileRecentBox = await mobileRecent.boundingBox();
    assert.ok(mobileRecentBox.width >= 44 && mobileRecentBox.height >= 44);
    await mobileRecent.click();
    await page.locator('.mobile-nav-drawer.is-open').waitFor({ state: 'detached' });
    await page.getByRole('button', { name: '打开导航', exact: true }).click();
    await batchChecks(h, mobile, 'mobile');
    await screenshot(page, 'sidebar-mobile');
    await page.keyboard.press('Escape');
    await page.setViewportSize({ width: 1440, height: 1000 });
    await openWelcome(h, 'new?project=qa-project');
    await waitPrompts(h);
    assert.equal(h.state.opening.at(-1).body.project_id, 'qa-project');
    assert.equal(postCount(h, '/sessions'), 0);
    await bounds(page, 'project draft');
    await desktop.getByRole('button', { name: '新建最近对话', exact: true }).focus();
    await desktop.getByRole('button', { name: '新建最近对话', exact: true }).press('Enter');
    await page.waitForURL(`**/w/${workspaceId}/chat/new`);
    assert.equal(new URL(page.url()).searchParams.has('project'), false);
    assert.equal(postCount(h, '/sessions'), 0);
    check('recent create clears project context and opens an ungrouped draft');
    await openWelcome(h, 'qa-empty');
    await waitPrompts(h);
    assert.equal(await stats(page).count(), 0, 'Existing empty session also hides usage despite non-null mock');
    await bounds(page, 'existing empty session');
    await page.goto(`${base}/w/${workspaceId}/chat/new?mode=goal`, { waitUntil: 'domcontentloaded' });
    await editor(page).waitFor();
    assert.equal(await page.locator('.is-new-chat-welcome').count(), 0, 'Goal setup keeps its own layout');
    await page.getByRole('button', { name: '目标中', exact: true }).click();
    await page.getByRole('heading', { name: '从一个问题开始', exact: true }).waitFor();
    await waitPrompts(h);
    const selectedPrompt = await prompts(page).first().innerText();
    assert.equal(postCount(h, '/sessions'), 0);
    await prompts(page).first().click();
    await page.waitForURL('**/chat/qa-started');
    await page.getByText('我们从一个有趣的问题开始学习。', { exact: true }).waitFor();
    await page.locator('.new-chat-heading').waitFor({ state: 'detached' });
    await stats(page).waitFor();
    assert.equal(await prompts(page).count(), 0);
    assert.equal(postCount(h, '/sessions'), 1);
    assert.equal(postCount(h, '/sessions/qa-started/messages/stream'), 1);
    assert.equal(h.state.writes.find(item => item.endpoint.endsWith('/messages/stream')).body.content, selectedPrompt);
    await stats(page).click();
    await page.getByRole('heading', { name: '会话规模', exact: true }).waitFor();
    await page.keyboard.press('Escape');
    await editor(page).fill('接着聊聊这个问题');
    await page.reload({ waitUntil: 'domcontentloaded' });
    await editor(page).waitFor();
    assert.equal(await editor(page).inputValue(), '接着聊聊这个问题');
    await stats(page).waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await bounds(page, 'started conversation mobile');
    await stats(page).waitFor();
    check('project payload / empty-session stats hidden / goal layout retained / one click = one create + one send / normal-conversation stats retained');
    verifyIsolation(h, 'main');
    await h.context.close();

    active = await harness(browser, { mode: 'failure-once' });
    await openWelcome(active);
    await active.page.getByText('推荐问题生成失败', { exact: true }).waitFor();
    assert.equal(active.state.opening.length, 1);
    assert.equal(await prompts(active.page).count(), 0);
    await active.page.getByRole('button', { name: '重试', exact: true }).click();
    await waitPrompts(active);
    assert.equal(active.state.opening.length, 2, 'Toast retry must make a fresh request');
    assert.equal(postCount(active, '/sessions'), 0);
    check('generation failure / toast retry succeeds / no session creation');
    verifyIsolation(active, 'failure and retry');
    await active.context.close();

    active = await harness(browser, { mode: 'empty' });
    const emptyResponse = active.page.waitForResponse(response => response.url().endsWith('/sessions/opening-prompts') && response.request().method() === 'POST');
    await openWelcome(active);
    await emptyResponse;
    await active.page.waitForLoadState('networkidle');
    await active.page.locator('[aria-label="正在生成推荐问题"]').waitFor({ state: 'detached' });
    assert.equal(active.state.opening.length, 1);
    assert.equal(await active.page.locator('.new-chat-suggestions').count(), 0, 'Empty memory returns no recommendation section or template');
    assert.equal(postCount(active, '/sessions'), 0);
    check('empty generation result shows no static template');
    verifyIsolation(active, 'no memories');
    await active.context.close();

    active = await harness(browser, { providers: false });
    await openWelcome(active);
    await active.page.waitForLoadState('networkidle');
    assert.equal(active.state.opening.length, 0, 'No provider must not attempt recommendation generation');
    assert.equal(await active.page.locator('.new-chat-suggestions').count(), 0, 'No provider shows no template');
    assert.equal(await stats(active.page).count(), 0);
    assert.equal(postCount(active, '/sessions'), 0);
    check('no provider = no generation request and no static template');
    verifyIsolation(active, 'no provider');
    report.pass = true;
    console.log('PASS_ALL', JSON.stringify({ checks: report.checks, screenshots: report.screenshots }));
  } catch (error) {
    report.failure = { message: error.message, stack: error.stack };
    if (active && !active.page.isClosed()) {
      console.error('FAILED_UI', (await active.page.locator('body').innerText()).slice(0, 14000));
      console.error('FAILED_REQUESTS', JSON.stringify(active.state.writes));
      report.failure.body = (await active.page.locator('body').innerText()).slice(0, 14000);
      report.failure.writes = active.state.writes;
      report.failure.pageErrors = active.state.errors;
      report.failure.consoleErrors = active.state.consoleErrors;
      await screenshot(active.page, 'failure');
    }
    throw error;
  } finally {
    fs.writeFileSync(`${outputDir}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
