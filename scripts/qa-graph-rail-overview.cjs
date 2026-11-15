const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const packageBin = process.env.PATH.split(path.delimiter).find(dir => fs.existsSync(path.resolve(dir, '../playwright/package.json')));
if (!packageBin) throw new Error('Run with npx --package=playwright -c "node scripts/qa-graph-rail-overview.cjs"');
const { chromium } = require(path.resolve(packageBin, '../playwright'));
const baseURL = process.env.GRAPH_QA_URL || 'http://127.0.0.1:5201';

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    for (const count of [100, 12]) {
      const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      page.setDefaultTimeout(30000);
      // Vary only the isolated fixture; production data and source stay untouched.
      if (count !== 100) await page.route('**/src/dev/graph-sidebar-preview.tsx*', async route => {
        const response = await route.fetch();
        const body = (await response.text()).replace(/length: 100/g, `length: ${count}`);
        await route.fulfill({ response, body });
      });
      const canvas = page.locator('.bound-graph-rail .graph-canvas');
      const nodes = canvas.locator('.react-flow__node');
      const transform = () => canvas.locator('.react-flow__viewport').evaluate(element => element.style.transform);
      const checkOverview = async () => {
        await page.waitForFunction(expected => {
          const canvas = document.querySelector('.bound-graph-rail .graph-canvas');
          if (!canvas) return false;
          const bounds = canvas.getBoundingClientRect();
          const nodes = [...canvas.querySelectorAll('.react-flow__node')];
          return nodes.length === expected && nodes.every(node => {
            const r = node.getBoundingClientRect();
            return r.width > 0 && r.left >= bounds.left - 1 && r.right <= bounds.right + 1 && r.top >= bounds.top - 1 && r.bottom <= bounds.bottom + 1;
          });
        }, count + 1);
      };
      const audit = async state => {
        const report = await page.evaluate(async () => {
          const { default: clipAudit } = await import('/src/dev/desktop-clip-audit.js');
          return clipAudit({ root: '.bound-graph-rail', viewport: true });
        });
        assert.equal(report.cut, 0, `${state}: ${JSON.stringify(report.issues)}`);
        return { state, cut: report.cut, hidden: report.hidden };
      };
      await page.goto(`${baseURL}/graph-sidebar-qa.html`);
      await page.getByRole('button', { name: '继续学习', exact: true }).waitFor();
      await checkOverview();
      const audits = [await audit('default')];
      const initial = await transform();
      await page.getByRole('button', { name: '适应完整图谱' }).hover();
      audits.push(await audit('hover'));
      await page.setViewportSize({ width: 1280, height: 820 });
      await page.waitForFunction(previous => document.querySelector('.bound-graph-rail .react-flow__viewport')?.style.transform !== previous, initial);
      await checkOverview();
      const resized = await transform();
      const rect = await canvas.boundingBox();
      assert.ok(rect && rect.width >= 220);
      // Node selection previews details, not a cropped neighbourhood or new session.
      const card = canvas.locator('.react-flow__node[data-id="g1-n2"]');
      await card.click({ force: true });
      await page.getByRole('status', { name: '会话节点绑定' }).filter({ hasText: '事务隔离' }).waitFor();
      assert.equal(await transform(), resized);
      assert.equal(await nodes.count(), count + 1);
      assert.equal(await page.evaluate(() => window.__graphSidebarQA.sessions[0].current_node_id), 'g1-n1');
      assert.deepEqual(await page.evaluate(() => window.__graphSidebarQA.writes), []);
      await page.getByRole('button', { name: '放大图谱' }).click();
      await page.waitForFunction(previous => document.querySelector('.bound-graph-rail .react-flow__viewport')?.style.transform !== previous, resized);
      await page.getByRole('button', { name: '适应完整图谱' }).click();
      await checkOverview();
      audits.push(await audit('refitted'));
      await page.getByRole('button', { name: '更多节点操作' }).click();
      await page.getByRole('menuitem', { name: '编辑节点' }).waitFor();
      audits.push(await audit('expanded'));
      await page.keyboard.press('Escape');
      if (count === 12) await page.screenshot({ path: 'docs/graph-rail-full-overview-qa.png' });
      assert.deepEqual(errors, []);
      console.log('GRAPH_RAIL_OVERVIEW', JSON.stringify({ pass: true, nodes: count + 1, canvas: rect, allNodesInside: true, previewPreservesCamera: true, bindingUnchanged: true, audits }));
      await page.close();
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
