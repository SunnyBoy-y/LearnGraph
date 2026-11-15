const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const packageBin = process.env.PATH.split(path.delimiter).find(dir => fs.existsSync(path.resolve(dir, '../playwright/package.json')));
if (!packageBin) throw new Error('Run with npx --package=playwright -c "node scripts/qa-conversation-links.cjs"');
const { chromium } = require(path.resolve(packageBin, '../playwright'));
const origin = process.env.CHAT_QA_URL || 'http://127.0.0.1:5174';
const ws = 'qa-message-links';
const stamp = '2026-11-15T00:00:00Z';
const session = id => ({ id, workspace_id: ws, title: id === 'source' ? '寻找数据库讨论' : '数据库与本体层', status: 'active', session_kind: 'main', memory_enabled: true, has_children: false, parent_session_id: null, project_id: null, graph_id: null, goal_id: null, current_node_id: null, context_capsule: {}, model_snapshot: {}, pinned: false, created_at: stamp, updated_at: stamp });
const message = (sid, id, role, content, parent = null) => ({ id, session_id: sid, workspace_id: ws, role, parent_message_id: parent, content, parts: role === 'assistant' ? [{ id: `${id}-text`, type: 'text', status: 'completed', content }] : [], status: 'completed', version: 1, provider_trace: {}, created_at: stamp });
const href = `/w/${ws}/chat/destination?message=old-user`;
const source = [message('source', 'source-user', 'user', '帮我找之前关于数据库的会话'), message('source', 'source-answer', 'assistant', `找到了：[查看数据库与本体层讨论](${href})`, 'source-user')];
const older = [message('destination', 'old-user', 'user', '数据库的 schema 之外还能提取什么？'), message('destination', 'old-answer', 'assistant', '还可以提取注释、统计信息、约束和查询使用痕迹。', 'old-user')];
const recent = [message('destination', 'recent-user', 'user', '后续问题'), message('destination', 'recent-answer', 'assistant', '后续回答', 'recent-user')];
async function run(browser, viewport) {
  const page = await browser.newPage({ viewport, reducedMotion: 'reduce' });
  const errors = [], writes = [], olderRequests = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(({ ws }) => {
    localStorage.setItem('learngraph.access_token', 'isolated-qa-token');
    localStorage.setItem('learngraph.workspace_id', ws);
    localStorage.setItem('learngraph.username', 'qa-links');
    localStorage.setItem('learngraph.must_change_password', 'false');
  }, { ws });
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url()), endpoint = url.pathname.replace('/api/v1', '');
    if (!['GET', 'HEAD'].includes(request.method())) writes.push(request.method() + ' ' + endpoint);
    let data = [];
    if (endpoint === '/auth/me') data = { id: 'qa-user', username: 'qa-links', display_name: 'QA', must_change_password: false };
    else if (endpoint === '/workspaces') data = [{ id: ws, name: 'QA workspace', effective_permissions: [] }];
    else if (endpoint === '/providers') data = [{ id: 'qa-provider', provider_type: 'openai_compatible_chat', display_name: 'QA provider', enabled: true, remote_capability: true, capabilities: { default_model: 'qa-model' } }];
    else if (endpoint === '/providers/qa-provider/models') data = { provider_id: 'qa-provider', status: 'ok', models: [{ id: 'qa-model', roles: ['llm'], enabled: true, remote: true, streaming: true }] };
    else if (endpoint === '/settings') data = [{ key: 'chat.default_response_mode', value: { response_mode: 'fast' } }, { key: 'chat.suggested_prompts', value: { enabled: false } }];
    else if (endpoint === '/sessions/page') data = { items: [session('source'), session('destination')], has_more: false, has_previous: false, next_cursor: null, previous_cursor: null };
    else if (endpoint === '/sessions/summary') data = { total: 2, active: 2, archived: 0, memory_enabled: 2 };
    else if (endpoint === '/sessions') data = [session('source'), session('destination')];
    else if (endpoint === '/deployment/profile') data = { mode: 'self_hosted' };
    else if (endpoint.match(/^\/sessions\/(source|destination)$/)) data = session(endpoint.split('/')[2]);
    else if (endpoint.endsWith('/messages')) {
      const sid = endpoint.split('/')[2], before = url.searchParams.get('before_id');
      if (before) olderRequests.push(before);
      data = sid === 'source' ? { items: source, total_count: 2, has_more_before: false } : { items: before ? older : recent, total_count: 4, has_more_before: !before, oldest_id: before ? 'old-user' : 'recent-user' };
    } else if (endpoint.includes('/browser')) data = { items: [], total_count: 0, has_more: false, next_cursor: null };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
  });
  try {
    await page.goto(`${origin}/w/${ws}/chat/source`);
    const link = page.getByRole('link', { name: '查看数据库与本体层讨论', exact: true });
    await link.waitFor({ timeout: 60000 });
    assert.equal(new URL(page.url()).pathname, `/w/${ws}/chat/source`, 'Showing a link must not navigate');
    assert.equal(await link.getAttribute('href'), href);
    await link.click();
    const target = page.locator('[data-message-mount-id="old-user"]');
    await target.waitFor();
    await page.waitForFunction(() => !new URL(location.href).searchParams.has('message'));
    assert.equal(new URL(page.url()).pathname, `/w/${ws}/chat/destination`);
    assert.ok(olderRequests.length >= 1, 'Must load old history');
    await target.scrollIntoViewIfNeeded();
    await page.waitForTimeout(200);
    assert.ok(await target.isVisible());
    await page.goBack();
    await link.waitFor();
    await link.click();
    await target.waitFor();
    await page.waitForFunction(() => !new URL(location.href).searchParams.has('message'));
    await page.goto(`${origin}/w/${ws}/chat/destination?keep=1&message=old-answer#evidence`);
    const answer = page.locator('[data-message-mount-id="old-answer"]');
    await answer.waitFor();
    await page.waitForFunction(() => !new URL(location.href).searchParams.has('message'));
    assert.equal(new URL(page.url()).searchParams.get('keep'), '1');
    assert.equal(new URL(page.url()).hash, '#evidence');
    await page.goto(`${origin}/w/${ws}/chat/destination?message=missing-message`);
    await page.getByText('未能定位到该消息，请重新搜索', { exact: true }).waitFor({ timeout: 30000 });
    await page.waitForFunction(() => !new URL(location.href).searchParams.has('message'));
    await page.goto(`${origin}/w/${ws}/chat/new`);
    await page.getByText('从一个问题开始', { exact: true }).waitFor();
    assert.deepEqual(errors, [], 'No JavaScript runtime errors');
    assert.deepEqual(writes, [], 'Navigation must not write sessions or start generation');
    console.log(`PASS ${viewport.width}x${viewport.height}: click, old history, back/repeat, direct assistant link, query/hash, missing target, new chat, zero writes/errors`);
  } catch (error) {
    console.error('QA_FAILURE', page.url(), 'older requests:', olderRequests, 'errors:', errors, (await page.locator('body').innerText()).slice(0, 2400));
    throw error;
  } finally { await page.close(); }
}
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    await run(browser, { width: 1440, height: 1000 });
    await run(browser, { width: 390, height: 844 });
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
