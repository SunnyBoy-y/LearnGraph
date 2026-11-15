const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const packageBin = process.env.PATH.split(path.delimiter).find(dir => fs.existsSync(path.resolve(dir, '../playwright/package.json')));
if (!packageBin) throw new Error('Run with npx --package=playwright -c "node scripts/qa-graph-sidebar.cjs"');
const { chromium } = require(path.resolve(packageBin, '../playwright'));
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.setDefaultTimeout(90000);
  try {
    await page.goto('http://127.0.0.1:5201/graph-sidebar-qa.html');
    await page.getByRole('button', { name: '继续学习', exact: true }).waitFor();
    await page.getByRole('combobox', { name: '输入消息' }).fill('保留数据库会话的未发送草稿');
    const source = await page.evaluate(() => JSON.parse(JSON.stringify(window.__graphSidebarQA.sessions[0])));
    await page.locator('.bound-graph-rail .react-flow__node').first().waitFor();
    assert.equal(await page.locator('.bound-graph-rail .react-flow__node').count(), 2);
    await page.waitForFunction(() => {
      const node = document.querySelector('.bound-graph-rail .react-flow__node[data-id="g1-n1"]');
      const canvas = document.querySelector('.bound-graph-rail .graph-canvas');
      if (!node || !canvas) return false;
      const r = node.getBoundingClientRect(), c = canvas.getBoundingClientRect();
      return r.top >= c.top && r.bottom <= c.bottom && r.left >= c.left && r.right <= c.right;
    });
    // Actual geometry: parent above focus, never shrunk into a full-map poster.
    const boxes = await page.locator('.bound-graph-rail .react-flow__node').evaluateAll(nodes => nodes.map(node => ({ id: node.dataset.id, x: node.getBoundingClientRect().x, y: node.getBoundingClientRect().y, w: node.getBoundingClientRect().width })));
    console.log('GEOMETRY', JSON.stringify(boxes));
    const audit = async () => page.evaluate(async () => {
      const { default: clipAudit } = await import('/src/dev/desktop-clip-audit.js');
      return clipAudit({ root: '.context-rail', viewport: true });
    });
    console.log('CLIP_DEFAULT', JSON.stringify(await audit()));
    await page.getByRole('button', { name: '继续学习', exact: true }).hover();
    console.log('CLIP_HOVER', JSON.stringify(await audit()));
    await page.screenshot({ path: 'docs/graph-sidebar-desktop-qa.png' });
    await page.getByRole('textbox', { name: '搜索图谱节点' }).fill('事务隔离');
    await page.getByRole('button', { name: '事务隔离', exact: true }).click();
    assert.match(await page.getByRole('status', { name: '会话节点绑定' }).innerText(), /预览/);
    assert.match(await page.getByRole('button', { name: '管理当前会话图谱上下文' }).innerText(), /索引结构/);
    await page.getByRole('button', { name: '更多节点操作' }).click();
    await page.getByRole('menuitem', { name: '编辑节点' }).waitFor();
    console.log('CLIP_EXPANDED', JSON.stringify(await audit()));
    await page.screenshot({ path: 'docs/graph-sidebar-menu-qa.png' });
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: '回到当前学习节点' }).click();
    await page.getByRole('button', { name: '继续学习', exact: true }).waitFor();
    await page.getByRole('button', { name: '切换图谱' }).click();
    await page.getByRole('button', { name: '打开图谱文件夹 算法与数据结构', exact: true }).click();
    await page.waitForFunction(() => window.__graphSidebarQA.route.includes('chat/new?project=p-g2'));
    assert.equal(await page.evaluate(() => window.__graphSidebarQA.sessions.length), 1);
    assert.equal(await page.evaluate(() => window.__graphSidebarQA.projects.length), 2);
    await page.getByRole('button', { name: '切换图谱' }).click();
    await page.getByRole('button', { name: '打开图谱文件夹 算法与数据结构', exact: true }).click();
    await page.waitForFunction(() => window.__graphSidebarQA.writes.filter(item => item.path === '/graphs/g2/learning-folder').length === 2);
    assert.equal(await page.evaluate(() => window.__graphSidebarQA.sessions.length), 1);
    assert.equal(await page.evaluate(() => window.__graphSidebarQA.projects.length), 2);
    // Same title g3 must not hijack the database folder g1.
    await page.getByRole('button', { name: '切换图谱' }).click();
    const duplicateTitle = page.getByRole('button', { name: '打开图谱文件夹 数据库学习图谱', exact: true });
    assert.equal(await duplicateTitle.count(), 2);
    await duplicateTitle.last().click();
    await page.waitForFunction(() => window.__graphSidebarQA.route.includes('chat/new?project=p-g3'));
    assert.equal(await page.evaluate(() => window.__graphSidebarQA.projects.length), 3);
    // Reopen the first database graph and recover source draft without mutation.
    await page.getByRole('button', { name: '切换图谱' }).click();
    await page.getByRole('button', { name: '打开图谱文件夹 数据库学习图谱', exact: true }).last().click();
    await page.waitForFunction(() => window.__graphSidebarQA.route.endsWith('/chat/source'));
    await page.waitForFunction(() => document.querySelector('[aria-label="输入消息"]')?.value === '保留数据库会话的未发送草稿');
    assert.equal(await page.getByRole('combobox', { name: '输入消息' }).inputValue(), '保留数据库会话的未发送草稿');
    await page.getByRole('textbox', { name: '搜索图谱节点' }).fill('事务隔离');
    await page.getByRole('button', { name: '事务隔离', exact: true }).click();
    await page.getByRole('button', { name: '学习此节点', exact: true }).click();
    await page.waitForFunction(() => window.__graphSidebarQA.route.includes('/chat/created-'));
    const report = await page.evaluate(() => ({ route: window.__graphSidebarQA.route, writes: window.__graphSidebarQA.writes, sessions: window.__graphSidebarQA.sessions, projects: window.__graphSidebarQA.projects }));
    assert.deepEqual(report.sessions[0], source);
    assert.equal(report.sessions.length, 2);
    assert.equal(report.sessions[1].current_node_id, 'g1-n2');
    assert.equal(report.writes.filter(item => item.method === 'PATCH').length, 0);
    console.log('NAVIGATION_QA', JSON.stringify({ pass: true, scenarios: ['shelf_navigation', 'no_empty_session_creation', 'repeat_click', 'same_title', 'draft_restore', 'node_learning_isolation', 'local_graph_search', 'primary_and_more'], route: report.route, projects: report.projects.length, sessions: report.sessions.length, sourceUnchanged: true }));
    assert.deepEqual(errors, []);
    console.log('SCREENSHOTS docs/graph-sidebar-desktop-qa.png docs/graph-sidebar-menu-qa.png');
  } catch (error) {
    console.error('BROWSER_STATE', JSON.stringify(await page.evaluate(() => ({ route: window.__graphSidebarQA?.route, text: document.body.innerText.slice(-4000), writes: window.__graphSidebarQA?.writes }))), errors);
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
