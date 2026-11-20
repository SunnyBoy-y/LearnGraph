// Run: npx --package=playwright -c "node scripts/qa-graph-footer.cjs"
// Uses local previews and does not send requests to a model.
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const packageBin = process.env.PATH.split(path.delimiter).find(dir => fs.existsSync(path.resolve(dir, '../playwright/package.json')));
if (!packageBin) throw new Error('Run with npx --package=playwright -c "node scripts/qa-graph-footer.cjs"');
const { chromium } = require(path.resolve(packageBin, '../playwright'));
const base = process.env.GRAPH_QA_URL || 'http://127.0.0.1:15173';

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const context = await browser.newContext({ reducedMotion: 'reduce' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const graphs = page.locator('.stream-ui[data-ui-component="graph"]');
  const checkCopy = async () => {
    assert.equal(await page.getByText(/拖动画布移动/).count(), 0);
    assert.equal(await graphs.locator('.stream-ui__heading p').count(), 0);
    assert.equal(await graphs.locator('.stream-ui__footer > span').count(), 0);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
    assert.ok(overflow <= 1, `Unexpected horizontal overflow: ${overflow}`);
  };
  const drag = async (locator, dx, dy) => {
    await locator.scrollIntoViewIfNeeded();
    const box = await locator.boundingBox();
    assert.ok(box);
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + dx, box.y + box.height / 2 + dy, { steps: 6 });
    await page.mouse.up();
  };
  try {
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(viewport);
      await page.goto(`${base}/genui-preview.html`);
      await page.getByRole('heading', { name: '增长正在加速', exact: true }).waitFor();
      assert.ok(await page.getByText(/虚构示意数据/).count() > 0);
      const scene = page.getByLabel('检查场景');
      await scene.selectOption('flow');
      await graphs.waitFor();
      await page.getByRole('button', { name: '确认数据口径', exact: true }).click();
      await page.getByText('确认活跃用户去重规则与统计时区。', { exact: true }).waitFor();
      assert.equal(await graphs.locator('.stream-ui__footer').count(), 0);
      await checkCopy();
      await scene.selectOption('canvas');
      const canvas = graphs.locator('.stream-ui__graph');
      await canvas.waitFor();
      assert.equal(await canvas.locator('[data-graph-node]').count(), 4);
      await checkCopy();
      await page.getByLabel('查找节点').fill('确认数据');
      assert.equal(await canvas.locator('[data-graph-node="cohort"]').getAttribute('opacity'), '0.2');
      await page.getByLabel('查找节点').fill('');
      const zoom = page.getByLabel('图谱缩放');
      await zoom.focus();
      await zoom.press('End');
      assert.equal(await zoom.inputValue(), '4');
      await page.getByRole('button', { name: '自动布局 / 适应画布' }).click();
      assert.equal(await zoom.inputValue(), '1');
      const svg = canvas.locator('.stream-ui__graph-scroll > svg');
      await page.getByRole('button', { name: '全屏查看', exact: true }).click();
      await page.waitForFunction(() => document.fullscreenElement?.classList.contains('stream-ui__graph'));
      const fullscreenSize = await canvas.boundingBox();
      assert.ok(fullscreenSize.width >= viewport.width - 1 && fullscreenSize.height >= viewport.height - 1);
      await page.getByRole('button', { name: '退出全屏', exact: true }).click();
      await page.waitForFunction(() => !document.fullscreenElement);
      await page.getByRole('button', { name: '全屏查看', exact: true }).waitFor();
      await svg.dblclick({ position: { x: 12, y: 12 } });
      assert.ok(Number(await zoom.inputValue()) > 1);
      await page.getByRole('button', { name: '自动布局 / 适应画布' }).click();
      const camera = canvas.locator('[data-graph-camera]');
      await svg.focus();
      await svg.press('ArrowRight');
      assert.ok((await camera.getAttribute('transform')).includes('-30'));
      await page.getByRole('button', { name: '自动布局 / 适应画布' }).click();
      const node = canvas.locator('[data-graph-node="data"]');
      const beforeDrag = await node.getAttribute('transform');
      await drag(node, 24, 18);
      assert.notEqual(await node.getAttribute('transform'), beforeDrag);
      await node.press('Enter');
      assert.equal(await node.getAttribute('aria-pressed'), 'true');
      await page.getByLabel('画布底色').selectOption('warm');
      assert.equal(await canvas.getAttribute('data-background'), 'warm');
      await page.getByLabel('画布布局').selectOption('mindmap');
      assert.equal(await canvas.getAttribute('data-layout'), 'mindmap');
      await checkCopy();
      await page.getByRole('button', { name: '切换深色', exact: true }).click();
      assert.ok(await page.locator('html.dark').count());
      await checkCopy();
      await page.goto(`${base}/ai-explain-preview.html`);
      const after = page.getByRole('region', { name: '修复后', exact: true });
      const explain = after.getByRole('button', { name: '让 AI 解释', exact: true });
      await explain.click();
      await page.getByRole('status').filter({ hasText: '解释请求已就绪' }).waitFor();
      await after.getByRole('button', { name: 'O(1) 常数级', exact: true }).click();
      await after.getByRole('button', { name: '让 AI 解释此节点', exact: true }).click();
      assert.ok((await page.getByRole('status').innerText()).includes('节点「O(1) 常数级」'));
      assert.equal(await page.getByText('依据大 O 记号的定义给出的增长顺序关系，非实测数据', { exact: true }).count(), 0);
      await checkCopy();
      console.log(`PASS ${viewport.width}x${viewport.height}: report, flow, canvas, search, zoom, reset, keyboard pan, node drag, selection, backgrounds, layout, dark mode, graph and node explanations`);
    }
    assert.deepEqual(errors, []);
    console.log('PASS: graph footer narration and gesture hints absent; other component source notes retained; zero page errors');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
