// Run against Vite: npx --package=playwright -c "node scripts/qa-message-copy.cjs"
// Fixtures and clipboard interception are isolated from the user's session.
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const bin = process.env.PATH.split(path.delimiter).find(dir => fs.existsSync(path.resolve(dir, '../playwright/package.json')));
if (!bin) throw new Error('Run with npx --package=playwright');
const { chromium } = require(path.resolve(bin, '../playwright'));
const base = process.env.CHAT_QA_URL || 'http://127.0.0.1:15173';
const ws = 'qa-message-copy';
const timestamp = '2026-11-15T09:00:00Z';
const session = { id: 'qa-rich', workspace_id: ws, title: '全文复制验证', status: 'active', session_kind: 'main', memory_enabled: false, model_snapshot: {}, context_capsule: {}, parent_session_id: null, project_id: null, graph_id: null, goal_id: null, pinned: false, has_children: false, created_at: timestamp, updated_at: timestamp };
const ui = (id, component, props, data = {}) => ({ id, type: 'ui', status: 'completed', data: { protocol: 'suip/0.1', block_id: id, component, state: 'completed', revision: 1, props, ...data } });
const doc = ui('document', 'document', { title: '完整文档' }, { blocks: [
  { id: 'heading', type: 'heading', level: 2, text: '六个阶段' },
  { id: 'steps', type: 'step_list' },
  { id: 'first', parent_id: 'steps', type: 'rail_item', label: '第一阶段', text: '阶段内容', icon: 'fluent:books' },
  { id: 'tabs', type: 'tabs' },
  { id: 'one', parent_id: 'tabs', type: 'paragraph', label: '概览', text: '当前标签内容' },
  { id: 'two', parent_id: 'tabs', type: 'paragraph', label: '详情', text: '隐藏标签内容' },
  { id: 'accordion', type: 'accordion' },
  { id: 'collapsed', parent_id: 'accordion', type: 'paragraph', label: '折叠标题', text: '折叠正文内容' },
  { id: 'code', type: 'code', language: 'js', text: 'const answer = 42;' },
  { id: 'math', type: 'math', text: 'x^2', inline: false },
  { id: 'quote', type: 'quote', text: '引用内容' },
] });
const table = ui('table', 'table', { title: '数据表', columns: [{ key: 'v', label: '人数' }], source_note: '用户提供' }, { rows: Array.from({ length: 12 }, (_, i) => ({ id: `r${i}`, label: `第${i + 1}项`, values: { v: i + 10 } })) });
const graph = ui('graph', 'graph', { title: '关系图' }, { nodes: [{ id: 'a', label: '起点', description: '节点说明' }, { id: 'b', label: '终点' }], edges: [{ id: 'ab', source: 'a', target: 'b', label: '关系说明' }] });
const timeline = ui('timeline', 'timeline', { title: '时间线' }, { items: [{ id: 'event', label: '关键事件', date: '2026-11-15', description: '事件说明' }] });
const rich = { id: 'rich-answer', workspace_id: ws, session_id: session.id, parent_message_id: null, role: 'assistant', version: 1, status: 'completed', content: '过期的纯文本摘要', provider_trace: {}, created_at: timestamp, parts: [
  { id: 'intro', type: 'text', status: 'completed', content: '回答开头\n[[artifact:block_id:document]]\n文档后说明' }, doc, table, graph, timeline,
  { id: 'end', type: 'text', status: 'completed', content: '回答结尾' },
] };
const plain = { ...rich, id: 'plain-answer', content: '普通回答保持原样', parts: [{ id: 'plain', type: 'text', status: 'completed', content: '普通回答保持原样' }] };
const expected = ['回答开头', '# 完整文档', '## 六个阶段', '**📚 第一阶段**', '阶段内容', '概览', '当前标签内容', '详情', '隐藏标签内容', '折叠标题', '折叠正文内容', '```js\nconst answer = 42;\n```', '$$\nx^2\n$$', '> 引用内容', '文档后说明', '| 第12项 | 21 |', '用户提供', '起点：节点说明', '起点 → 终点：关系说明', '2026-11-15', '事件说明', '回答结尾'];
const report = { checks: [], errors: [] };
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    for (const mobile of [false, true]) {
      const label = mobile ? 'mobile' : 'desktop';
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1600, height: 1000 }, reducedMotion: 'reduce', serviceWorkers: 'block' });
      await context.addInitScript(({ ws }) => {
        localStorage.setItem('learngraph.access_token', 'qa-isolated');
        localStorage.setItem('learngraph.workspace_id', ws);
        localStorage.setItem('learngraph.username', 'qa');
        localStorage.setItem('learngraph.must_change_password', 'false');
        Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async value => { window.__copiedText = value; } } });
      }, { ws });
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin !== new URL(base).origin) return route.abort();
        if (!url.pathname.startsWith('/api/')) return route.continue();
        const endpoint = url.pathname.replace('/api/v1', '');
        if (endpoint.endsWith('/suggested-prompts')) return route.fulfill({ json: { id: 'qa-prompts', session_id: endpoint.split('/')[2], anchor_message_id: endpoint.includes('/qa-rich/') ? rich.id : plain.id, anchor_message_version_id: 'qa-v1', prompts: [{ id: 'next', content: '继续学习' }], provider_trace: {}, memory_used: false, generated_at: timestamp, cached: true } });
        if (!['GET', 'HEAD'].includes(route.request().method())) return route.fulfill({ status: 409, json: { detail: 'QA blocked write' } });
        let data = [];
        if (endpoint === '/auth/me') data = { id: 'qa-user', username: 'qa', display_name: 'QA', is_system_admin: false, must_change_password: false };
        else if (endpoint === '/workspaces') data = [{ id: ws, name: 'LearnGraph', effective_permissions: [] }];
        else if (endpoint === '/settings') data = [{ key: 'ui.preferences', value: { theme: 'light', colorTheme: 'mono' } }];
        else if (endpoint === '/sessions/page') data = { items: [session, { ...session, id: 'qa-plain', title: '普通回答' }], has_more: false, next_cursor: null, has_previous: false, previous_cursor: null };
        else if (endpoint === '/sessions/summary') data = { total: 2, active: 2, archived: 0, memory_enabled: 0 };
        else if (/^\/sessions\/qa-(rich|plain)$/.test(endpoint)) data = { ...session, id: endpoint.split('/').at(-1) };
        else if (endpoint.endsWith('/versions')) data = [{ id: 'qa-v1', message_id: endpoint.includes('/qa-rich/') ? rich.id : plain.id, version: 1, status: 'completed', created_at: timestamp, updated_at: timestamp }];
        else if (endpoint.endsWith('/messages')) data = { items: endpoint.includes('/qa-rich/') ? [rich] : [{ ...plain, session_id: 'qa-plain' }], total_count: 1, has_more_before: false };
        else if (endpoint.endsWith('/context-usage')) data = null;
        else if (endpoint.endsWith('/stream-ticket')) data = { ticket: 'qa-ticket' };
        else if (endpoint === '/deployment/profile') data = { mode: 'self_hosted' };
        return route.fulfill({ json: data });
      });
      const page = await context.newPage();
      page.setDefaultTimeout(20000);
      page.on('pageerror', error => report.errors.push(`${label}: ${error.message}`));
      async function checkCopy(container, suffix) {
        const copy = container.getByRole('button', { name: '复制全文', exact: true });
        await copy.click();
        await container.getByRole('button', { name: '已复制', exact: true }).waitFor();
        const result = await page.evaluate(() => window.__copiedText);
        for (const value of expected) assert(result.includes(value), `${label}/${suffix}: missing ${value}`);
        assert.equal(result.match(/# 完整文档/g).length, 1);
        assert(!/artifact:|suip\/0.1|过期的纯文本摘要/.test(result));
        assert(result.indexOf('回答开头') < result.indexOf('# 完整文档'));
        assert(result.indexOf('# 完整文档') < result.indexOf('文档后说明'));
        assert.equal(await container.getByRole('button', { name: /复制文档|导出 Markdown/ }).count(), 0);
        report.checks.push(`${label}/${suffix}: complete clipboard, order, copy feedback and no document toolbar`);
        return result;
      }
      await page.goto(`${base}/w/${ws}/chat/qa-rich`);
      const main = page.locator('.workspace-main');
      await main.getByRole('heading', { name: '六个阶段', exact: true }).waitFor();
      assert.equal(await main.getByText('隐藏标签内容', { exact: true }).isVisible(), false);
      assert.equal(await main.getByText('折叠正文内容', { exact: true }).isVisible(), false);
      const original = await checkCopy(main, 'main');
      await main.getByRole('tab', { name: '详情', exact: true }).click();
      await main.getByText('折叠标题', { exact: true }).click();
      await main.getByText('隐藏标签内容', { exact: true }).waitFor();
      await main.getByText('折叠正文内容', { exact: true }).waitFor();
      await page.waitForFunction(() => ![...document.querySelectorAll('.workspace-main button')].some(el => el.getAttribute('aria-label') === '已复制'));
      assert.equal(await checkCopy(main, 'expanded'), original);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      if (mobile) await page.getByRole('button', { name: '展开图谱面板', exact: true }).click();
      const tab = page.getByRole('tab', { name: '侧边对话', exact: true });
      if (!(await tab.isVisible())) await page.getByRole('button', { name: '展开图谱面板', exact: true }).click();
      await tab.click();
      const rail = page.locator('.context-rail');
      await rail.getByRole('heading', { name: '六个阶段', exact: true }).waitFor();
      await checkCopy(rail, 'side');
      await page.goto(`${base}/w/${ws}/chat/qa-plain`);
      await main.getByText('普通回答保持原样', { exact: true }).waitFor();
      await main.getByRole('button', { name: '复制全文', exact: true }).click();
      await main.getByRole('button', { name: '已复制', exact: true }).waitFor();
      assert.equal(await page.evaluate(() => window.__copiedText), '普通回答保持原样');
      report.checks.push(`${label}: plain-text copy unchanged`);
      await context.close();
    }
    assert.deepEqual(report.errors, []);
    console.log(JSON.stringify(report, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
