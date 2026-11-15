// Run with npx --package=playwright -c "node scripts/qa-session-files.cjs".
// API fixtures run in isolated browser contexts; no user data is modified.
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const bin = process.env.PATH.split(path.delimiter).find(dir => fs.existsSync(path.resolve(dir, '../playwright/package.json')));
if (!bin) throw new Error('Run with npx --package=playwright');
const { chromium } = require(path.resolve(bin, '../playwright'));
const base = process.env.CHAT_QA_URL || 'http://127.0.0.1:15173';
const output = 'artifacts/session-files';
const ws = 'qa-session-files';
const timestamp = '2026-11-15T08:00:00Z';
const session = { id: 'qa-files', workspace_id: ws, title: '文件目录测试', status: 'active', session_kind: 'main', memory_enabled: false, model_snapshot: {}, context_capsule: {}, parent_session_id: null, project_id: null, graph_id: null, goal_id: null, pinned: false, has_children: false, created_at: timestamp, updated_at: timestamp };
function file(id, filename, filePath = null) { return { file_id: id, filename, path: filePath, mime_type: 'text/plain', size_bytes: 50, origin: 'session_workspace', relation: null, source: null, message_id: null, is_image: false, storage_status: 'stored', prompt_summary: null, created_at: timestamp }; }
const files = [file('root', 'package.json'), file('readme', 'README.md', 'src/README.md'), file('nested', 'notes.txt', 'src/docs/notes.txt'), file('same', 'README.md', 'dist/README.md'), file(null, 'shot.mjs', 'tools/shot.mjs'), file('long', 'a-very-long-file-name-that-must-not-push-controls-outside-the-panel.txt', 'src/a-very-long-file-name-that-must-not-push-controls-outside-the-panel.txt')];
const report = { checks: [], errors: [], screenshots: [] };
fs.mkdirSync(output, { recursive: true });
async function run() {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    for (const mobile of [false, true]) {
      for (const dark of [false, true]) {
        const label = `${mobile ? 'mobile' : 'desktop'}-${dark ? 'dark' : 'light'}`;
        const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1600, height: 1000 }, reducedMotion: 'reduce', serviceWorkers: 'block', acceptDownloads: true });
        let mode = 'normal';
        await context.addInitScript(({ ws }) => {
          localStorage.setItem('learngraph.access_token', 'qa-isolated');
          localStorage.setItem('learngraph.workspace_id', ws);
          localStorage.setItem('learngraph.username', 'qa');
          localStorage.setItem('learngraph.must_change_password', 'false');
          localStorage.setItem('lg-rail-collapsed', 'false');
          localStorage.setItem('learngraph:right-rail-view', 'files');
        }, { ws });
        await context.route('**/*', async route => {
          const url = new URL(route.request().url());
          if (url.origin !== new URL(base).origin) return route.abort();
          if (!url.pathname.startsWith('/api/')) return route.continue();
          const endpoint = url.pathname.replace('/api/v1', '');
          if (!['GET', 'HEAD'].includes(route.request().method())) return route.fulfill({ status: 409, json: { detail: 'QA blocked write' } });
          if (endpoint.endsWith('/files') && endpoint.startsWith('/sessions/')) {
            if (mode === 'error') return route.fulfill({ status: 503, json: { detail: 'QA temporary failure' } });
            return route.fulfill({ json: mode === 'empty' ? [] : files });
          }
          if (endpoint.endsWith('/content') || endpoint.endsWith('/stream')) return route.fulfill({ contentType: 'text/plain', body: 'Session file preview content: ' + endpoint, headers: { 'Content-Disposition': 'attachment; filename=package.json' } });
          let data = [];
          if (endpoint.endsWith('/stream-ticket')) data = { ticket: 'qa-ticket' };
          else if (endpoint === '/auth/me') data = { id: 'qa-user', username: 'qa', display_name: 'QA', is_system_admin: false, must_change_password: false };
          else if (endpoint === '/workspaces') data = [{ id: ws, name: 'LearnGraph', effective_permissions: [] }];
          else if (endpoint === '/settings') data = [{ key: 'ui.preferences', value: { theme: dark ? 'dark' : 'light', colorTheme: 'mono' } }];
          else if (endpoint === '/sessions/page') data = { items: [session, { ...session, id: 'qa-other', title: '另一个会话' }], has_more: false, next_cursor: null, has_previous: false, previous_cursor: null };
          else if (endpoint === '/sessions/summary') data = { total: 2, active: 2, archived: 0, memory_enabled: 0 };
          else if (/^\/sessions\/qa-(files|other)$/.test(endpoint)) data = { ...session, id: endpoint.split('/').at(-1) };
          else if (endpoint.endsWith('/messages')) data = { items: [], total_count: 0, has_more_before: false };
          else if (endpoint.endsWith('/context-usage')) data = { session_id: 'qa-files', estimated_tokens: 0, input_budget_tokens: 32000, context_window_tokens: 32000, remaining_tokens: 32000, message_count: 0, used_ratio: 0 };
          else if (endpoint === '/deployment/profile') data = { mode: 'self_hosted' };
          return route.fulfill({ json: data });
        });
        const page = await context.newPage();
        page.setDefaultTimeout(20000);
        page.on('pageerror', error => report.errors.push(`${label}: ${error.message}`));
        async function open(id = 'qa-files') {
          await page.goto(`${base}/w/${ws}/chat/${id}`);
          const tab = page.getByRole('tab', { name: '会话文件', exact: true });
          if (mobile) await page.getByRole('button', { name: '展开图谱面板', exact: true }).click();
          await tab.waitFor({ state: 'attached' }).catch(async error => {
            await page.screenshot({ path: `${output}/${label}-failure.png` });
            console.error((await page.locator('body').innerText()).slice(0, 2500));
            throw error;
          });
          if (!(await tab.isVisible())) await page.getByRole('button', { name: '展开图谱面板', exact: true }).click();
          await tab.click();
        }
        await open();
        const rail = page.getByRole('region', { name: '会话文件预览' });
        await rail.getByRole('navigation', { name: '文件目录' }).waitFor();
        assert.equal(await rail.locator('.right-rail-files__preview').count(), 0);
        await rail.locator('summary').filter({ hasText: /^src$/ }).click();
        await rail.locator('summary').filter({ hasText: /^docs$/ }).click();
        await rail.getByRole('button', { name: '预览 src/docs/notes.txt', exact: true }).click();
        await rail.getByText(/Session file preview content/).first().waitFor();
        assert.equal(await rail.getByLabel('文件列表', { exact: true }).isVisible(), false);
        const height = await rail.boundingBox();
        const preview = await rail.locator('.right-rail-files__preview').boundingBox();
        assert(preview.height / height.height > 0.94, `${label}: preview fills rail`);
        const close = rail.getByRole('button', { name: '关闭预览，返回文件列表' });
        await close.click();
        const nested = rail.getByRole('button', { name: '预览 src/docs/notes.txt', exact: true });
        await nested.waitFor();
        assert(await nested.evaluate(el => el === document.activeElement));
        await rail.getByRole('button', { name: '刷新会话文件' }).click();
        assert.equal(await rail.locator('.right-rail-files__preview').count(), 0);
        const listShot = `${output}/${label}-directory.png`;
        await page.screenshot({ path: listShot }); report.screenshots.push(listShot);
        await rail.getByRole('button', { name: /预览 src\/a-very-long/ }).click();
        await rail.getByText(/Session file preview content/).first().waitFor();
        const controls = await close.boundingBox();
        assert(controls.x >= 0 && controls.x + controls.width <= (mobile ? 390 : 1600), `${label}: close is onscreen`);
        await page.keyboard.press('Escape');
        await rail.getByRole('navigation', { name: '文件目录' }).waitFor();
        await rail.locator('summary').filter({ hasText: /^tools$/ }).click();
        await rail.getByRole('button', { name: '预览 tools/shot.mjs' }).click();
        assert(await rail.getByRole('button', { name: '下载文件' }).isDisabled());
        await close.click();
        await rail.getByRole('button', { name: '预览 package.json' }).click();
        await rail.getByText(/Session file preview content/).first().waitFor();
        const download = page.waitForEvent('download');
        await rail.getByRole('button', { name: '下载文件' }).click();
        await download;
        const shot = `${output}/${label}-preview.png`;
        await page.screenshot({ path: shot }); report.screenshots.push(shot);
        await open('qa-other');
        await rail.getByRole('navigation', { name: '文件目录' }).waitFor();
        assert.equal(await rail.locator('.right-rail-files__preview').count(), 0);
        await page.getByRole('tab', { name: '当前学习', exact: true }).click();
        await page.getByRole('tab', { name: '会话文件', exact: true }).click();
        await rail.getByRole('navigation', { name: '文件目录' }).waitFor();
        mode = 'empty';
        await rail.getByRole('button', { name: '刷新会话文件' }).click();
        await rail.getByText('还没有可预览的文件', { exact: true }).waitFor();
        mode = 'normal';
        await rail.getByRole('button', { name: '刷新会话文件' }).click();
        await rail.getByRole('navigation', { name: '文件目录' }).waitFor();
        report.checks.push(`${label}: folders, nested preview, full height, close, focus restoration, refresh, long names, Escape, workspace-only files, download, session switch, neighboring tab, empty and recovery`);
        console.log('PASS', label);
        await context.close();
      }
    }
    assert.deepEqual(report.errors, []);
  } finally { await browser.close(); fs.writeFileSync(`${output}/report.json`, JSON.stringify(report, null, 2)); }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
