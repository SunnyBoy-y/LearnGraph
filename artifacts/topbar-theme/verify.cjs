const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const bin = process.env.PATH.split(path.delimiter).find(entry => fs.existsSync(path.resolve(entry, '../playwright/package.json')));
const { chromium, expect } = require(path.resolve(bin, '../playwright/test'));
const output = __dirname;
const workspaceId = 'qa-topbar';
const base = 'http://127.0.0.1:5205/w/' + workspaceId;
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, reducedMotion: 'reduce' });
  const errors = [];
  const report = { cases: [], errors, screenshots: [] };
  let preferences = { theme: 'dark', colorTheme: 'mono', customColor: '#2563eb' };
  page.on('pageerror', error => errors.push({ url: page.url(), error: error.message }));
  await page.addInitScript(({ workspaceId, preferences }) => {
    localStorage.setItem('learngraph.access_token', 'isolated-topbar-qa');
    localStorage.setItem('learngraph.workspace_id', workspaceId);
    localStorage.setItem('learngraph.username', 'qa-topbar');
    localStorage.setItem('learngraph.must_change_password', 'false');
    if (!localStorage.getItem('lg-appearance')) localStorage.setItem('lg-appearance', JSON.stringify(preferences));
  }, { workspaceId, preferences });
  await page.route('**/api/v1/**', route => {
    const endpoint = new URL(route.request().url()).pathname.replace('/api/v1', '');
    let data = [];
    if (endpoint === '/auth/me') data = { id: 'qa-user', username: 'qa-topbar', display_name: 'QA', is_system_admin: false, must_change_password: false };
    else if (endpoint === '/workspaces') data = [{ id: workspaceId, name: 'LearnGraph', effective_permissions: [] }];
    else if (endpoint === '/dashboard') data = { workspace_id: workspaceId, metrics: [], next_actions: [], system_status: {} };
    else if (endpoint === '/deployment/profile') data = { mode: 'self_hosted' };
    else if (endpoint === '/sessions/page') data = { items: [], next_cursor: null, previous_cursor: null, has_more: false, has_previous: false };
    else if (endpoint === '/sessions/summary') data = { total: 0, active: 0, archived: 0, memory_enabled: 0 };
    else if (endpoint === '/practice/overview') data = { generated_at: new Date().toISOString(), stats: { pending_node_count: 0, due_node_count: 0, weak_node_count: 0, attention_node_count: 0, consolidated_node_count: 0, estimated_minutes: 0, answered_7d: 0, answered_7d_previous: 0, first_try_correct_7d: 0 }, today_plan: { mode: 'empty', message: '', items: [], skipped_nodes: [], node_ids: [], question_types: [], question_count: 0, estimated_minutes: 0, node_count: 0 }, focus_nodes: [], recent_session: null, active_session: null, trend: [] };
    else if (endpoint === '/memory/profile') data = { id: null, workspace_id: workspaceId, owner_subject_id: 'qa-user', version: 0, status: 'empty', overview: '', dimensions: [], scope: 'workspace', source_count: 0, markdown: '', structured_sections: [], source_atom_ids: [], source_fingerprint: '', generated_at: null, updated_at: null, stale_reason: '' };
    else if (endpoint === '/memory/enhancement') data = { workspace_id: workspaceId, extraction: {}, embedding: {}, summarization: {}, active_memories: 0, indexed_memories: 0, current_model_key: null, stale_model_keys: [], cache_invalidated: null };
    else if (endpoint === '/settings') data = [{ key: 'ui.preferences', value: preferences }];
    else if (endpoint === '/settings/ui.preferences' && route.request().method() === 'PUT') {
      const body = route.request().postDataJSON();
      preferences = body.value ?? body;
      data = { key: 'ui.preferences', value: preferences };
    }
    else if (endpoint.includes('/browser')) data = { items: [], has_more: false, total_count: 0, next_cursor: null };
    else if (endpoint.endsWith('/messages')) data = { items: [], total_count: 0, has_more_before: false };
    else if (endpoint === '/scheduled-tasks/trigger-catalog') data = { event_types: [], condition_paths: [], operators: [], modes: [] };
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
  });
  const colors = async selector => page.locator(selector).evaluate(element => {
    const style = getComputedStyle(element);
    const context = document.createElement('canvas').getContext('2d');
    const rgb = value => {
      context.clearRect(0, 0, 1, 1);
      context.fillStyle = value;
      context.fillRect(0, 0, 1, 1);
      return [...context.getImageData(0, 0, 1, 1).data];
    };
    return { background: rgb(style.backgroundColor), foreground: rgb(style.color), muted: rgb(style.getPropertyValue('--muted')), accent: rgb(style.getPropertyValue('--accent')), expectedForeground: rgb(style.getPropertyValue('--foreground')), accentForeground: rgb(style.getPropertyValue('--accent-foreground')), width: element.getBoundingClientRect().width, height: element.getBoundingClientRect().height };
  });
  const reset = async () => {
    await page.mouse.move(190, 250);
    await page.evaluate(() => document.activeElement?.blur());
  };
  const assertDark = async label => {
    await reset();
    for (const selector of ['.topbar-menu-trigger', '.topbar-status']) {
      const button = page.locator('.workspace-topbar ' + selector);
      if (!await button.isVisible()) continue;
      const result = await colors('.workspace-topbar ' + selector);
      assert.deepEqual(result.background, result.muted, label + ' ' + selector + ': dark surface');
      assert.deepEqual(result.foreground, result.expectedForeground, label + ': readable icon');
      assert.ok(result.background[0] < 100, label + ': no bright circle');
      if (page.viewportSize().width <= 1024) assert.ok(result.width >= 44 && result.height >= 44, label + ': touch size preserved');
    }
    report.cases.push({ name: label, passed: true });
  };
  try {
    const routes = ['schedules', 'home', 'chat/new', 'graphs', 'capabilities', 'learn/joint', 'sources', 'evidence/review', 'practice', 'memory', 'artifacts'];
    for (const route of routes) {
      await page.goto(base + '/' + route);
      await page.locator('.workspace-topbar').waitFor();
      await page.waitForLoadState('networkidle');
      await assertDark('mobile ' + route);
      if ((await page.locator('body').innerText()).includes('页面暂时无法显示')) {
        await page.getByText('查看错误详情', { exact: true }).click();
        throw new Error(route + ': ' + await page.locator('body').innerText());
      }
    }
    await page.goto(base + '/schedules');
    await page.getByRole('heading', { name: '定时任务', exact: true }).waitFor();
    for (const selector of ['.topbar-menu-trigger', '.topbar-status']) {
      const button = page.locator('.workspace-topbar ' + selector);
      await button.hover();
      let result = await colors('.workspace-topbar ' + selector);
      assert.deepEqual(result.background, result.accent, 'hover follows theme');
      await reset();
      await button.focus();
      result = await colors('.workspace-topbar ' + selector);
      assert.deepEqual(result.background, result.accent, 'focus follows theme');
      assert.equal(await button.evaluate(element => element.matches(':focus-visible')), true);
    }
    await page.getByRole('button', { name: '打开页面工具', exact: true }).click();
    await expect(page.getByRole('menuitem', { name: '学习活动', exact: true })).toBeVisible();
    const open = await colors('.topbar-status');
    assert.deepEqual(open.background, open.accent, 'open menu remains dark');
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: '打开导航', exact: true }).click();
    await expect(page.locator('.sidebar-nav.is-mobile')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('.sidebar-nav.is-mobile')).not.toBeVisible();
    await assertDark('hover-focus-navigation-tools');
    const file = path.join(output, 'mobile-dark.png');
    await page.screenshot({ path: file });
    report.screenshots.push(file);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await expect(page.locator('.topbar-menu-trigger')).not.toBeVisible();
    const desktop = await colors('.topbar-status');
    assert.equal(desktop.background[3], 0, 'Desktop ghost button stays transparent');
    await page.getByRole('button', { name: '打开页面工具', exact: true }).click();
    await expect(page.getByRole('menuitem', { name: '学习活动', exact: true })).toBeVisible();
    await page.keyboard.press('Escape');
    report.cases.push({ name: 'desktop layout and tools unchanged', passed: true });
    await page.evaluate(() => document.documentElement.classList.add('is-native-app'));
    await assertDark('native wide dark');
    await page.setViewportSize({ width: 390, height: 844 });
    await assertDark('native mobile dark');
    await page.evaluate(() => document.documentElement.classList.remove('is-native-app'));
    await page.goto(base + '/settings/general');
    await page.getByRole('radio', { name: '明亮', exact: true }).check();
    await expect(page.locator('html')).not.toHaveClass(/dark/);
    await expect(page.getByRole('status').filter({ hasText: '按钮、选中状态' })).toBeVisible();
    await page.goto(base + '/schedules');
    await page.getByRole('heading', { name: '定时任务', exact: true }).waitFor();
    await reset();
    const light = await colors('.topbar-status');
    assert.ok(light.background[0] >= 235, 'Light appearance is preserved');
    assert.ok(light.foreground[0] < 100);
    await page.goto(base + '/settings/general');
    await page.getByRole('radio', { name: '深色', exact: true }).check();
    await expect(page.locator('html')).toHaveClass(/dark/);
    await expect(page.getByRole('status').filter({ hasText: '按钮、选中状态' })).toBeVisible();
    await page.goto(base + '/schedules');
    await page.getByRole('heading', { name: '定时任务', exact: true }).waitFor();
    await assertDark('light-to-dark switch');
    await page.goto(base + '/settings/general');
    await page.getByRole('radio', { name: '海蓝', exact: true }).check();
    await expect(page.getByRole('status').filter({ hasText: '按钮、选中状态' })).toBeVisible();
    await page.goto(base + '/schedules');
    await page.getByRole('heading', { name: '定时任务', exact: true }).waitFor();
    await assertDark('colored dark theme');
    await page.locator('.topbar-status').hover();
    const colored = await colors('.topbar-status');
    assert.deepEqual(colored.background, colored.accent);
    assert.deepEqual(colored.foreground, colored.accentForeground);
    assert.deepEqual(errors, []);
    report.passed = true;
  } catch (error) {
    report.passed = false;
    report.failure = String(error.stack || error);
    report.url = page.url();
    report.pageText = (await page.locator('body').innerText()).slice(0, 2000);
    process.exitCode = 1;
  } finally {
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
    await browser.close();
  }
})();
