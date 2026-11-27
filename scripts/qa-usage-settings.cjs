// Run against a local LearnGraph frontend: npx --package=playwright -c "node scripts/qa-usage-settings.cjs"
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const bin = process.env.PATH.split(path.delimiter).find(dir => fs.existsSync(path.resolve(dir, '../playwright/package.json')));
const { chromium } = require(path.resolve(bin, '../playwright'));
const origin = process.env.USAGE_QA_URL || 'http://127.0.0.1:5173';
const workspace = 'qa-usage';
const legacy = '36d669c6-c791-41f7-9977-04fc69586eef';
const day = '2026-11-25';
const provider = { id: 'qa-live', workspace_id: workspace, provider_type: 'openai_compatible_chat', display_name: '测试服务连接', enabled: true, remote_capability: true, base_url: 'https://example.test/v1', capabilities: { default_model: 'qa-model' }, status: 'healthy' };
const event = { id: 'qa-event', workspace_id: workspace, provider_id: legacy, provider_display_name: '旧连接', model_id: 'qa-model', feature: 'chat_fast_preface', currency: 'USD', cost_amount: null, reserved_amount: '0.04', input_tokens: 1200, cached_input_tokens: 0, cache_creation_input_tokens: 0, output_tokens: 800, reasoning_tokens: 0, total_tokens: 2000, attempt: 1, cost_status: 'unpriced', amount_source: 'unknown', record_stage: 'settled', price_version_id: null, pricing_snapshot: {}, legacy_billing_snapshot: {}, usage_units: {}, archived_at: null, latency_ms: 500, created_at: `${day}T12:00:00Z` };

(async () => {
  const browser = await chromium.launch({ channel: 'msedge' });
  try {
    for (const [width, theme] of [[1440, 'light'], [390, 'dark']]) {
      const context = await browser.newContext({ viewport: { width, height: 930 }, reducedMotion: 'reduce', colorScheme: theme, serviceWorkers: 'block' });
      const errors = [];
      const requests = [];
      await context.addInitScript(({ workspace }) => {
        for (const [key, value] of Object.entries({ workspace_id: workspace, access_token: 'qa-isolated', username: 'qa', user_id: 'qa-user' })) localStorage.setItem(`learngraph.${key}`, value);
      }, { workspace });
      await context.route('**/*', async route => {
        const req = route.request();
        const url = new URL(req.url());
        if (url.origin !== origin) return route.abort();
        if (!url.pathname.startsWith('/api/')) return route.continue();
        if (req.method() !== 'GET') return route.fulfill({ status: 409, json: { detail: 'QA: mutation blocked' } });
        requests.push(url);
        const currency = url.searchParams.get('currency') === 'CNY' ? 'CNY' : 'USD';
        const cost = currency === 'USD' ? '88.45' : '31.25';
        const stat = label => ({ label, cost_amount: cost, attempts: 9047, total_tokens: 466125325, unknown_events: 1 });
        const data = {
          '/auth/me': { id: 'qa-user', display_name: 'QA', username: 'qa', is_system_admin: true, must_change_password: false },
          '/workspaces': [{ id: workspace, name: 'QA', effective_permissions: ['workspace.read', 'workspace.manage', 'workspace.write'] }],
          '/settings': [{ key: 'ui.preferences', value: { theme, colorTheme: 'mono' } }],
          '/deployment/profile': { mode: 'self_hosted' },
          '/providers': [provider],
          '/providers/catalog': [{ provider_type: provider.provider_type, role: 'model', label: '兼容接口', create_allowed: true }],
          '/usage/summary': { schema_version: 2, workspace_id: workspace, currency, total_tokens: 466125325, attempts: 9047, cost_amount: cost, reserved_amount: '195.12', input_tokens: 0, cached_input_tokens: 0, cache_creation_input_tokens: 0, output_tokens: 0, reasoning_tokens: 0, unpriced_events: 1, unknown_currency_events: 0, remote_usage_recorded: true, provider_names: { [legacy]: '旧连接' }, breakdown: { day: [stat('2026-11-24'), stat(day)], provider_id: [stat(legacy)], model_id: [stat('qa-model')], feature: [stat(event.feature)] } },
          '/usage/events': [{ ...event, currency }],
          '/usage/alert-email': { enabled: false, smtp_host: '', smtp_port: 465, smtp_security: 'ssl', smtp_username: '', has_password: false, from_address: '', to_addresses: [] },
          '/usage/models-dev': { source: 'https://example.test', origin: 'bundled', priced_model_count: 1 },
        };
        return route.fulfill({ json: data[url.pathname.replace('/api/v1', '')] ?? [] });
      });
      const page = await context.newPage();
      page.setDefaultTimeout(15000);
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(`${origin}/w/${workspace}/settings/models?tab=usage`);
      await page.getByText('$88.45', { exact: true }).first().waitFor().catch(async error => {
        console.error(page.url(), errors, requests.map(url => url.pathname), (await page.locator('body').innerText()).slice(0, 1200));
        throw error;
      });
      await page.locator('.recharts-area-curve[d^="M"]').waitFor({ state: 'attached' });
      assert.equal(await page.evaluate(() => document.documentElement.classList.contains('dark')), theme === 'dark');
      assert.ok(await page.locator('.settings-modal__body').evaluate(el => el.scrollWidth <= el.clientWidth + 1));
      await page.getByRole('button', { name: '时间范围' }).click();
      await page.getByRole('button', { name: '近 7 天 (UTC)', exact: true }).click();
      await page.getByText('$88.45', { exact: true }).first().waitFor();
      assert.ok(requests.some(url => url.pathname.endsWith('/usage/summary') && url.searchParams.has('start_at')));
      await page.getByRole('tab', { name: '按供应商' }).click();
      await page.getByText('旧连接', { exact: true }).waitFor();
      assert.equal(await page.getByText(legacy, { exact: true }).count(), 0);
      await page.getByRole('button', { name: '查看异常记录' }).click();
      await page.getByText('快速响应', { exact: true }).waitFor();
      assert.ok(requests.some(url => url.pathname.endsWith('/usage/events') && url.searchParams.get('unpriced_only') === 'true'));
      assert.equal(await page.getByRole('button', { name: '归档当前范围' }).count(), 0);
      await page.getByRole('button', { name: '详情', exact: true }).click();
      await page.getByText(`UUID: ${legacy}`).waitFor();
      await page.keyboard.press('Escape');
      await page.getByRole('tab', { name: '预算管理' }).click();
      await page.getByRole('heading', { name: 'USD 预算管理' }).waitFor();
      await page.getByRole('button', { name: '价格配置', exact: true }).click();
      await page.getByRole('heading', { name: '价格配置 · USD' }).waitFor();
      await page.getByRole('button', { name: '返回概览' }).click();
      await page.getByRole('region', { name: '核心指标' }).waitFor();
      await page.getByRole('combobox', { name: '计费币种' }).selectOption('CNY');
      await page.getByText('¥31.25', { exact: true }).first().waitFor().catch(async error => {
        console.error(page.url(), errors, requests.filter(url => url.pathname.endsWith('/usage/summary')).map(String), (await page.locator('body').innerText()).slice(-1700));
        throw error;
      });
      assert.equal(await page.getByText('$88.45', { exact: true }).count(), 0);
      await page.getByRole('tab', { name: '服务连接', exact: true }).click();
      await page.getByText('测试服务连接', { exact: true }).waitFor();
      await page.getByRole('tab', { name: '用量与预算', exact: true }).click();
      await page.getByText('¥31.25', { exact: true }).first().waitFor();
      assert.deepEqual(errors, []);
      console.log(`PASS ${width}px ${theme}: filters, chart, anomaly details, budget, pricing, currency and settings navigation`);
      await context.close();
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
