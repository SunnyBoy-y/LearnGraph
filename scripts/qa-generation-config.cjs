// Run with Vite on port 5177: npx --offline --package=playwright -c "node scripts/qa-generation-config.cjs"
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const bin = process.env.PATH.split(path.delimiter).find(dir => fs.existsSync(path.resolve(dir, '../playwright/package.json')));
const { chromium } = require(path.resolve(bin, '../playwright'));
const base = process.env.GENERATION_QA_URL || 'http://127.0.0.1:5177';
const workspace = 'generation-qa';
const stamp = '2026-11-26T00:00:00Z';
const session = { id: 'chat', workspace_id: workspace, title: '生成配置验证', status: 'active', session_kind: 'main', memory_enabled: false, model_snapshot: {}, context_capsule: {}, created_at: stamp, updated_at: stamp };
const model = (id, efforts, image = false) => ({ id, roles: ['llm'], enabled: true, remote: true, streaming: true, capabilities: { reasoning_efforts: efforts, supports_image_input: image, hosted_web_search: true } });
const lists = { first: [model('session-model', ['low', 'high'], true), ...Array.from({ length: 36 }, (_, i) => model(`extra-model-${i}`, ['low']))], second: [model('historical-model', ['low', 'high', 'max']), model('custom-model', ['high'])] };
const providers = Object.entries(lists).map(([id, models]) => ({ id, provider_type: 'openai_compatible_chat', display_name: id === 'first' ? '会话服务商' : '历史服务商', enabled: true, remote_capability: true, roles: ['llm'], capabilities: { default_model: models[0].id, hosted_web_search: true } }));
const user = { id: 'question', workspace_id: workspace, session_id: session.id, role: 'user', version: 1, status: 'completed', content: '请解释这里的配置。', parts: [], created_at: stamp };
const answer = { ...user, id: 'answer', parent_message_id: user.id, role: 'assistant', content: '这是一条历史回答。', provider_trace: { provider_id: 'second', model_id: 'historical-model', agent_mode: false, thinking_mode: 'max', web_search: true, search_route: 'model_native', allowed_domains: ['example.org'] } };
answer.parts = [{ id: 'answer-text', type: 'text', status: 'completed', content: answer.content }];

async function run(browser, viewport, phone) {
  const context = await browser.newContext({ viewport, hasTouch: phone, isMobile: phone });
  try {
    await context.addInitScript(({ workspace }) => {
      localStorage.setItem('learngraph.access_token', 'isolated-generation-qa');
      localStorage.setItem('learngraph.workspace_id', workspace);
      localStorage.setItem('learngraph.username', 'qa');
      localStorage.setItem('learngraph.must_change_password', 'false');
      localStorage.setItem('learngraph:session-composer-prefs', JSON.stringify({ chat: { providerId: 'first', modelId: 'session-model', responseMode: 'agentic', thinkingMode: 'low', searchRoute: 'disabled', generationMode: 'text', thinkingMappingVersion: 2 } }));
    }, { workspace });
    const payloads = [];
    await context.route('**/api/v1/**', route => {
      const req = route.request();
      const endpoint = new URL(req.url()).pathname.replace('/api/v1', '');
      let data = [];
      if (endpoint === '/auth/me') data = { id: 'qa', username: 'qa', display_name: 'QA', must_change_password: false };
      else if (endpoint === '/workspaces') data = [{ id: workspace, name: 'LearnGraph', effective_permissions: [] }];
      else if (endpoint === '/settings') data = [{ key: 'chat.suggested_prompts', value: { enabled: false }, updated_at: stamp }];
      else if (endpoint === '/providers') data = providers;
      else if (/^\/providers\/[^/]+\/models$/.test(endpoint)) {
        const id = endpoint.split('/')[2];
        data = { provider_id: id, status: 'ok', models: lists[id] };
      } else if (endpoint === '/sessions/page') data = { items: [session], next_cursor: null, has_more: false };
      else if (endpoint === '/sessions/chat') data = session;
      else if (endpoint === '/sessions/chat/messages') data = { items: [user, { ...answer, version: 1 + payloads.length }], total_count: 2, has_more_before: false };
      else if (endpoint === '/sessions/chat/messages/answer') data = { ...answer, version: 1 + payloads.length };
      else if (endpoint.endsWith('/answer/retry')) {
        payloads.push(req.postDataJSON());
        const event = { type: 'message.completed', message_id: answer.id, sequence: 1 };
        return route.fulfill({ contentType: 'text/event-stream', body: `event: message.completed\ndata: ${JSON.stringify(event)}\n\n` });
      } else if (endpoint.endsWith('/suggested-prompts')) return route.fulfill({ status: 204 });
      else if (endpoint.endsWith('/context-usage')) data = null;
      else if (endpoint === '/deployment/profile') data = { mode: 'self_hosted' };
      return route.fulfill({ json: data });
    });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    await page.goto(`${base}/w/${workspace}/chat/chat`, { waitUntil: 'commit', timeout: 45000 });
    const composer = page.getByRole('button', { name: '选择响应模式、思考力度和模型' }).and(page.locator('button:not([disabled])')).filter({ visible: true });
    await composer.waitFor();
    if (phone) await composer.press('Enter');
    else await composer.click();
    if (!phone) await page.getByRole('menuitem', { name: /^模型/ }).hover();
    const search = page.getByRole('textbox', { name: '搜索模型' });
    await search.waitFor();
    const list = page.locator('[data-slot="model-selector-list"]');
    const manage = page.getByRole('menuitem', { name: '管理模型' });
    assert(await manage.isVisible());
    const before = await manage.boundingBox();
    await list.evaluate(el => { el.scrollTop = el.scrollHeight; });
    assert(await list.evaluate(el => el.scrollTop > 0));
    assert(Math.abs((await manage.boundingBox()).y - before.y) < 2);
    for (const menu of await page.locator('.generation-config-menu[data-state="open"]').all()) {
      const box = await menu.boundingBox();
      assert(box.x >= 0 && box.y >= 0 && box.x + box.width <= viewport.width + 1 && box.y + box.height <= viewport.height + 1, JSON.stringify(box));
    }
    if (!phone) await search.click();
    await search.fill('会话服务商');
    assert(await page.getByRole('menuitemradio', { name: 'session-model' }).isVisible());
    assert.equal(await page.getByRole('menuitemradio', { name: 'historical-model' }).count(), 0);
    await page.keyboard.press('Escape');
    await composer.getAttribute('aria-expanded').then(value => assert.equal(value, 'false'));

    const retry = page.getByRole('button', { name: '重试并保留版本', exact: true }).last();
    await retry.press('Enter');
    await page.getByRole('menuitem', { name: /沿用上次回答配置/ }).waitFor();
    await page.getByRole('menuitem', { name: /^模型.*historical-model/ }).click();
    await page.getByRole('textbox', { name: '搜索模型' }).fill('custom-model');
    await page.getByRole('menuitemradio', { name: 'custom-model' }).click();
    await Promise.all([
      page.waitForResponse(response => response.url().endsWith('/answer/retry')),
      page.getByRole('menuitem', { name: /沿用上次回答配置/ }).click(),
    ]);
    assert.equal(payloads.length, 1);
    assert.equal(payloads[0].model_id, 'historical-model');
    assert.equal(payloads[0].thinking_mode, 'max');
    assert.equal(payloads[0].agent_mode, false);
    assert.equal(payloads[0].search_route, 'model_native');
    assert.deepEqual(payloads[0].allowed_domains, ['example.org']);
    await retry.click();
    await page.getByRole('menuitem', { name: /^模型.*historical-model/ }).click();
    await page.getByRole('menuitemradio', { name: 'custom-model' }).click();
    await Promise.all([
      page.waitForResponse(response => response.url().endsWith('/answer/retry')),
      page.getByRole('menuitem', { name: '使用当前配置重新生成' }).click(),
    ]);
    assert.equal(payloads[1].model_id, 'custom-model');
    assert.equal(payloads[1].thinking_mode, 'high');
    const prefs = await page.evaluate(() => JSON.parse(localStorage.getItem('learngraph:session-composer-prefs')).chat);
    assert.equal(prefs.modelId, 'session-model');
    assert.equal(prefs.thinkingMode, 'low');
    assert.equal(prefs.searchRoute, 'disabled');
    if (!phone) {
      const railTab = page.getByRole('tab', { name: '侧边对话' });
      if (!(await railTab.isVisible())) await page.locator('.topbar-graph-toggle').click();
      await railTab.click();
      const rail = page.getByRole('tabpanel', { name: '侧边对话' });
      await rail.getByRole('button', { name: '选择响应模式、思考力度和模型' }).click();
      await page.getByRole('slider', { name: '思考强度' }).waitFor();
      await page.getByRole('textbox', { name: '搜索模型' }).fill('custom-model');
      await page.getByRole('menuitemradio', { name: 'custom-model' }).click();
      await page.waitForFunction(() => {
        const trigger = document.querySelector('[role="tabpanel"][data-state="active"] .chat-mode-chip');
        return trigger?.getAttribute('aria-expanded') === 'false' && trigger.textContent.trim() === '高';
      });
    }
    await page.goto(`${base}/document-model-preview.html`, { waitUntil: 'commit', timeout: 30000 });
    const documentControl = page.getByRole('button', { name: '选择响应模式、思考力度和模型' });
    await documentControl.click();
    await page.getByRole('textbox', { name: '搜索模型' }).fill('demo-model-b');
    await page.getByRole('menuitemradio', { name: 'demo-model-b' }).click();
    await page.getByRole('button', { name: '选择响应模式、思考力度和模型', expanded: false }).waitFor();
    await documentControl.press('Enter');
    await page.getByRole('slider', { name: '思考强度' }).waitFor();
    await page.getByRole('button', { name: '高', exact: true }).click();
    await page.keyboard.press('Escape');
    await page.getByRole('textbox', { name: '询问当前文档' }).fill('测试文档配置');
    await page.getByRole('textbox', { name: '询问当前文档' }).press('Enter');
    const sent = page.getByText('发送参数（本地演示）');
    await sent.waitFor();
    assert.match(await sent.locator('..').innerText(), /"thinkingMode": "high"/);
    console.log(`${viewport.width}×${viewport.height}: model scrolling, keyboard close, direct/custom retry, session isolation and document selection passed${phone ? '' : ' (side rail also passed)'}`);
  } finally { await context.close(); }
}

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    await run(browser, { width: 1280, height: 640 }, false);
    await run(browser, { width: 390, height: 720 }, true);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
