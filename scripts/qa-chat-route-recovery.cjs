const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const packageBin = process.env.PATH.split(path.delimiter).find(dir => fs.existsSync(path.resolve(dir, '../playwright/package.json')));
if (!packageBin) throw new Error('Run with npx --package=playwright -c "node scripts/qa-chat-route-recovery.cjs"');
const { chromium } = require(path.resolve(packageBin, '../playwright'));

const workspaceId = 'qa-chat-render';
const markdown = '#### 应当保留的格式化草稿';
const scenarios = [
  { name: 'empty canvas' },
  { name: 'restored markdown', fields: { text: markdown, files: [], stickers: [], pendingFiles: [], queue: [] }, rich: true },
  { name: 'malformed attachment fields', fields: { text: '应当保留的草稿', files: null, stickers: {}, pendingFiles: null, queue: null } },
  { name: 'markdown typed into a fresh canvas', typeMarkdown: true, rich: true },
];

async function checkScenario(browser, scenario) {
  // Every scenario uses its own isolated context and mocked APIs, never real credentials or writes.
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const errors = [];
  const writes = [];
  page.on('pageerror', error => { errors.push(error.stack || error.message); console.error('PAGE_ERROR', error.stack || error.message); });
  page.on('console', message => { if (message.type() === 'error') { errors.push(message.text()); console.error('CONSOLE_ERROR', message.text()); } });
  await page.addInitScript(({ workspaceId, fields }) => {
    localStorage.setItem('learngraph.access_token', 'isolated-qa-token');
    localStorage.setItem('learngraph.workspace_id', workspaceId);
    localStorage.setItem('learngraph.username', 'qa-render');
    localStorage.setItem('learngraph.must_change_password', 'false');
    localStorage.setItem('qa:unrelated-draft', '保留其他会话草稿');
    const key = `lg:composer-state:v2:${workspaceId}:new:root`;
    if (fields && !localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify({ version: 2, updatedAt: Date.now(), fields }));
  }, { workspaceId, fields: scenario.fields });
  await page.route('**/api/v1/**', route => {
    if (!['GET', 'HEAD'].includes(route.request().method())) writes.push(route.request().method() + ' ' + route.request().url());
    const url = new URL(route.request().url());
    const endpoint = url.pathname.replace('/api/v1', '');
    let data = [];
    if (endpoint === '/auth/me') data = { id: 'qa-user', username: 'qa-render', display_name: 'QA', is_system_admin: false, must_change_password: false };
    else if (endpoint === '/workspaces') data = [{ id: workspaceId, name: 'QA workspace', effective_permissions: [] }];
    else if (endpoint === '/providers') data = [{ id: 'qa-provider', provider_type: 'openai_compatible_chat', display_name: 'QA provider', enabled: true, remote_capability: true, capabilities: { default_model: 'qa-model' } }];
    else if (endpoint === '/providers/qa-provider/models') data = { provider_id: 'qa-provider', status: 'ok', models: [{ id: 'qa-model', roles: ['llm'], enabled: true, remote: true, streaming: true }] };
    else if (endpoint === '/settings') data = [{ key: 'chat.default_response_mode', value: { response_mode: 'fast' } }];
    else if (endpoint === '/sessions/page') data = { items: Array.from({ length: 10 }, (_, index) => ({ id: `qa-session-${index}`, workspace_id: workspaceId, title: `QA session ${index}`, status: 'active', memory_enabled: true, has_children: false, parent_session_id: null, project_id: null, graph_id: null, created_at: '2026-11-14T00:00:00Z', updated_at: '2026-11-14T00:00:00Z' })), next_cursor: null, previous_cursor: null, has_more: false, has_previous: false };
    else if (endpoint === '/sessions/summary') data = { total: 10, active: 10, archived: 0, memory_enabled: 10 };
    else if (endpoint === '/projects') data = [{ id: 'qa-project', workspace_id: workspaceId, title: 'QA project', status: 'active', primary_graph_id: 'qa-graph', position: 0 }];
    else if (endpoint === '/graphs') data = [{ id: 'qa-graph', workspace_id: workspaceId, title: 'QA graph', status: 'published', revision: 1 }];
    else if (endpoint === '/deployment/profile') data = { mode: 'self_hosted' };
    else if (endpoint.endsWith('/messages')) data = { items: [], total_count: 0, has_more_before: false };
    else if (endpoint.includes('/browser')) data = { items: [], has_more: false, total_count: 0, next_cursor: null };
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
  });
  async function assertHealthy() {
    await page.waitForFunction(() => document.body.innerText.includes('页面暂时无法显示') || document.body.innerText.includes('从一个问题开始'), null, { timeout: 45000 });
    await page.waitForLoadState('networkidle');
    if (scenario.rich) await page.waitForFunction(() => document.querySelector('[data-composer-rich-input]') || document.body.innerText.includes('页面暂时无法显示'));
    const text = await page.locator('body').innerText();
    assert.equal(text.includes('页面暂时无法显示'), false, `${scenario.name}: new-chat route must not trigger the render boundary`);
    assert.ok(text.includes('从一个问题开始'));
    if (scenario.rich) {
      assert.equal(await page.locator('[data-composer-rich-input] h4').innerText(), '应当保留的格式化草稿');
      assert.equal(await page.locator('textarea[name="message"]').isVisible(), false);
    }
    const expected = scenario.typeMarkdown ? markdown : scenario.fields?.text;
    if (expected) assert.equal(await page.locator('textarea[name="message"]').inputValue(), expected);
    assert.equal(await page.evaluate(() => localStorage.getItem('qa:unrelated-draft')), '保留其他会话草稿');
    assert.deepEqual(errors, []);
    assert.deepEqual(writes, [], 'Opening or editing a draft must not write a server session');
  }
  try {
    await page.goto(`${process.env.CHAT_QA_URL || 'http://127.0.0.1:18000'}/w/${workspaceId}/chat/new`);
    if (scenario.typeMarkdown) {
      const input = page.getByRole('combobox', { name: '输入消息', exact: true });
      await input.waitFor();
      await input.fill(markdown);
    }
    await assertHealthy();
    await page.reload();
    await assertHealthy();
    if (process.env.CHAT_QA_SCREENSHOT && scenario.name === 'restored markdown') await page.screenshot({ path: process.env.CHAT_QA_SCREENSHOT, fullPage: true });
    console.log(`PASS: ${scenario.name}, reload, draft preservation, zero JS errors and zero server writes`);
  } catch (error) {
    console.error('FAILED_SCENARIO', scenario.name, (await page.locator('body').innerText()).slice(0, 1800));
    throw error;
  } finally { await page.close(); }
}

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try { for (const scenario of scenarios) await checkScenario(browser, scenario); }
  finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
