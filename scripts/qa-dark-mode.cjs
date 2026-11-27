// Run with an existing Vite server: npx --package=playwright -c "node scripts/qa-dark-mode.cjs"
// All API calls and appearance saves use isolated fixtures.
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const packageBin = process.env.PATH.split(path.delimiter).find(dir => fs.existsSync(path.resolve(dir, '../playwright/package.json')));
if (!packageBin) throw new Error('Run with npx --package=playwright -c "node scripts/qa-dark-mode.cjs"');
const { chromium } = require(path.resolve(packageBin, '../playwright'));
const base = process.env.THEME_QA_URL || 'http://127.0.0.1:15174';
const output = process.env.THEME_QA_OUTPUT || 'artifacts/dark-mode';
const workspaceId = 'qa-dark-mode';

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const errors = [];
  let preferences = { theme: 'light', colorTheme: 'mono', customColor: '#2563eb' };
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await context.addInitScript(({ workspaceId }) => {
    localStorage.setItem('learngraph.access_token', 'isolated-dark-mode-qa');
    localStorage.setItem('learngraph.workspace_id', workspaceId);
    localStorage.setItem('learngraph.username', 'qa-dark');
    localStorage.setItem('learngraph.must_change_password', 'false');
  }, { workspaceId });
  const session = { id: 'qa-chat', workspace_id: workspaceId, title: '认识贝叶斯定理', status: 'active', memory_enabled: true, model_snapshot: {}, context_capsule: {}, project_id: null, graph_id: null, parent_session_id: null, created_at: '2026-11-15T00:00:00Z', updated_at: '2026-11-15T00:00:00Z' };
  const message = { id: 'qa-answer', workspace_id: workspaceId, session_id: session.id, role: 'assistant', version: 1, status: 'completed', content: '## 从一个故事开始\n\n贝叶斯定理帮助我们根据新证据更新判断。\n\n> 先验、证据与后验共同构成这个过程。\n\n```python\nposterior = prior * likelihood / evidence\n```', parts: [], provider_trace: {}, created_at: '2026-11-15T00:00:00Z' };
  message.parts = [{ id: 'qa-markdown', type: 'text', status: 'completed', content: message.content }];
  await context.route('**/api/v1/**', route => {
    const request = route.request();
    const endpoint = new URL(request.url()).pathname.replace('/api/v1', '');
    let data = [];
    if (endpoint === '/settings/ui.preferences' && ['PUT', 'PATCH'].includes(request.method())) {
      preferences = request.postDataJSON().value;
      data = { key: 'ui.preferences', value: preferences, updated_at: new Date().toISOString() };
    } else if (endpoint === '/settings') data = [{ key: 'ui.preferences', value: preferences, updated_at: '' }];
    else if (endpoint === '/auth/me') data = { id: 'qa-user', username: 'qa-dark', display_name: 'QA', is_system_admin: false, must_change_password: false };
    else if (endpoint === '/workspaces') data = [{ id: workspaceId, name: 'LearnGraph', effective_permissions: [] }];
    else if (endpoint === '/providers') data = [{ id: 'qa-provider', provider_type: 'openai_compatible_chat', display_name: 'QA provider', enabled: true, remote_capability: true, capabilities: { default_model: 'qa-model' } }];
    else if (endpoint === '/providers/qa-provider/models') data = { provider_id: 'qa-provider', status: 'ok', models: [{ id: 'qa-model', roles: ['llm'], enabled: true, remote: true, streaming: true }] };
    else if (endpoint === '/sessions/page') data = { items: [session], next_cursor: null, has_more: false, total_count: 1 };
    else if (endpoint === '/sessions/summary') data = { total: 1, active: 1, archived: 0, memory_enabled: 1 };
    else if (endpoint === '/sessions/qa-chat') data = session;
    else if (endpoint === '/sessions/qa-chat/messages/qa-answer') data = message;
    else if (endpoint === '/sessions/qa-chat/messages/qa-answer/versions') data = [{ id: 'qa-answer-v1', message_id: message.id, version: 1, status: 'completed', created_at: message.created_at, updated_at: message.created_at }];
    else if (endpoint === '/sessions/qa-chat/suggested-prompts') data = { id: 'qa-prompts', session_id: session.id, anchor_message_id: message.id, anchor_message_version_id: 'qa-answer-v1', prompts: [{ id: 'qa-next', content: '能用一道练习题检验我的理解吗？' }], provider_trace: {}, memory_used: false, generated_at: message.created_at, cached: true };
    else if (endpoint.endsWith('/suggested-prompts')) return route.fulfill({ status: 204 });
    else if (endpoint.endsWith('/messages')) data = { items: endpoint.includes('/qa-chat/') ? [message] : [], total_count: endpoint.includes('/qa-chat/') ? 1 : 0, has_more_before: false };
    else if (endpoint.endsWith('/context-usage')) data = null;
    else if (endpoint.includes('/browser')) data = { items: [], has_more: false, total_count: 0, next_cursor: null };
    else if (endpoint === '/deployment/profile') data = { mode: 'self_hosted' };
    return route.fulfill({ json: data });
  });
  const settle = async () => page.evaluate(async () => {
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    await Promise.allSettled(document.getAnimations().filter(animation => animation instanceof CSSTransition).map(animation => animation.finished));
  });
  const colors = async label => {
    await settle();
    const result = await page.evaluate(() => {
      const selectors = ['html', 'body', '.workspace-shell', '.workspace-sidebar', '.sidebar-nav', '.workspace-main', '.context-rail', '.chat-canvas-page', '.chat-composer [data-slot="input-group"]', '.chat-graph-rail__switch', '.mobile-nav-drawer', '[data-slot="dropdown-menu-content"]', '[data-slot="dialog-content"]'];
      const canvas = document.createElement('canvas').getContext('2d');
      const rgba = color => {
        canvas.clearRect(0, 0, 1, 1);
        canvas.fillStyle = color;
        canvas.fillRect(0, 0, 1, 1);
        return [...canvas.getImageData(0, 0, 1, 1).data];
      };
      const luminance = channels => channels.slice(0, 3).map(channel => {
        const v = channel / 255;
        return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;
      }).reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
      return selectors.map(selector => {
        const el = document.querySelector(selector);
        if (!el) return null;
        const style = getComputedStyle(el);
        const background = rgba(style.backgroundColor);
        const foreground = rgba(style.color);
        const contrast = (Math.max(luminance(background), luminance(foreground)) + .05) / (Math.min(luminance(background), luminance(foreground)) + .05);
        return { selector, background, foreground, contrast };
      }).filter(Boolean);
    });
    console.log('COLORS', label, JSON.stringify(result));
    return result;
  };
  const checkDark = async label => {
    const result = await colors(label);
    for (const item of result) {
      if (!item.background[3]) continue;
      const shell = ['html', 'body', '.workspace-shell', '.workspace-sidebar', '.sidebar-nav', '.workspace-main', '.context-rail', '.chat-canvas-page'].includes(item.selector);
      const ceiling = shell ? 32 : 56;
      assert.ok(item.background.slice(0, 3).every(channel => channel <= ceiling), `${label}: ${JSON.stringify(item)}`);
      assert.ok(item.contrast >= 4.5, `${label}: readable ${item.selector}`);
    }
    assert.equal(await page.locator('body').innerText().then(text => text.includes('页面暂时无法显示')), false);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${label}: no horizontal overflow`);
  };
  const sampleSwitch = () => page.evaluate(() => {
    window.__darkModeFrames = [];
    const sample = () => {
      if (document.documentElement.classList.contains('dark')) {
        window.__darkModeFrames.push(['.workspace-sidebar', '.sidebar-nav', '.workspace-main', '.context-rail'].map(selector => document.querySelector(selector)).filter(Boolean).map(element => getComputedStyle(element).backgroundColor));
      }
      if (window.__darkModeFrames.length < 6) requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
  const checkSwitch = async () => {
    await page.waitForFunction(() => window.__darkModeFrames.length === 6);
    const frames = await page.evaluate(() => window.__darkModeFrames);
    console.log('SWITCH_FRAMES', JSON.stringify(frames));
    const allowedShellDark = new Set(['rgb(14, 14, 16)', 'rgb(20, 20, 23)']);
    assert.ok(frames.flat().every(color => allowedShellDark.has(color)), 'Switching to dark applies all shell surfaces in the same frame');
  };
  try {
    await page.goto(`${base}/w/${workspaceId}/settings/general`);
    await sampleSwitch();
    await page.getByRole('radio', { name: '深色', exact: true }).check();
    await checkSwitch();
    await checkDark('settings dark');
    await page.goto(`${base}/w/${workspaceId}/chat/new`);
    await page.getByRole('heading', { name: '从一个问题开始' }).waitFor();
    await checkDark('new chat dark');
    await page.screenshot({ path: `${output}/desktop-dark.png` });
    await page.getByRole('button', { name: '展开侧边栏', exact: true }).click();
    await checkDark('expanded sidebar');
    await page.screenshot({ path: `${output}/desktop-dark-expanded.png` });
    const input = page.getByRole('combobox', { name: '输入消息', exact: true });
    await input.fill('深色模式下保留的草稿');
    await page.reload();
    await input.waitFor();
    assert.equal(await input.inputValue(), '深色模式下保留的草稿');
    await checkDark('restored dark draft');
    await page.getByRole('button', { name: '打开页面工具', exact: true }).click();
    await page.locator('[data-slot="dropdown-menu-content"]').waitFor();
    await checkDark('page tools menu');
    await page.keyboard.press('Escape');
    await page.goto(`${base}/w/${workspaceId}/chat/qa-chat`);
    await page.getByRole('heading', { name: '从一个故事开始' }).waitFor();
    await page.getByRole('button', { name: '能用一道练习题检验我的理解吗？', exact: true }).waitFor();
    assert.equal(await page.getByText('问题提示生成失败', { exact: true }).count(), 0);
    await checkDark('existing conversation');
    await page.screenshot({ path: `${output}/conversation-dark.png` });
    for (const route of ['graphs', 'sources']) {
      await page.goto(`${base}/w/${workspaceId}/${route}`);
      await page.locator('.workspace-main h1').waitFor();
      await checkDark(route);
    }
    await page.goto(`${base}/w/${workspaceId}/settings/general`);
    await page.getByRole('radio', { name: '明亮', exact: true }).check();
    await settle();
    const light = await colors('restored light');
    assert.ok(light.filter(item => ['body', '.workspace-main', '.workspace-sidebar'].includes(item.selector)).every(item => item.background.slice(0, 3).every(channel => channel >= 240)));
    await page.getByRole('radio', { name: '跟随系统', exact: true }).check();
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.waitForFunction(() => document.documentElement.classList.contains('dark'));
    await checkDark('system dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await page.waitForFunction(() => !document.documentElement.classList.contains('dark'));
    await page.getByRole('radio', { name: '深色', exact: true }).check();
    for (const [name, id] of [['松绿', 'forest'], ['海蓝', 'ocean'], ['鸢紫', 'violet'], ['琥珀', 'amber'], ['蔷薇', 'rose'], ['石墨', 'mono']]) {
      await page.getByRole('radio', { name, exact: true }).check();
      await page.waitForFunction(id => document.documentElement.dataset.colorMode === id, id);
      await checkDark(`palette ${id}`);
    }
    await page.getByRole('radio', { name: '跟随系统', exact: true }).check();
    await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
    await page.waitForFunction(() => !document.documentElement.classList.contains('dark'));
    await page.goto(`${base}/w/${workspaceId}/chat/new`);
    await page.getByRole('heading', { name: '从一个问题开始' }).waitFor();
    await sampleSwitch();
    await page.emulateMedia({ colorScheme: 'dark' });
    await checkSwitch();
    await checkDark('system dark with reduced motion');
    await input.fill('');
    await page.setViewportSize({ width: 390, height: 844 });
    await checkDark('mobile dark');
    await page.screenshot({ path: `${output}/mobile-dark.png` });
    await page.getByRole('button', { name: '打开导航', exact: true }).click();
    await page.getByRole('dialog', { name: 'LearnGraph 导航' }).waitFor();
    await checkDark('mobile navigation drawer');
    await page.screenshot({ path: `${output}/mobile-navigation-dark.png` });
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: '展开图谱面板', exact: true }).click();
    await page.getByRole('button', { name: '收起图谱面板', exact: true }).last().waitFor();
    await checkDark('mobile graph drawer');
    await page.screenshot({ path: `${output}/mobile-graph-dark.png` });
    await page.keyboard.press('Escape');
    await page.setViewportSize({ width: 320, height: 640 });
    await checkDark('small mobile dark');
    assert.deepEqual(errors, []);
    console.log('PASS: actual appearance controls, same-frame dark switch, refresh, drafts, light restore, system theme, all presets, desktop, mobile, navigation drawer, graph drawer, tools menu, existing conversation, graph library and sources');
  } catch (error) {
    console.error('FAILED_UI', await page.locator('body').innerText(), errors);
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
