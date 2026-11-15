const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const packageBin = process.env.PATH.split(path.delimiter).find(dir => fs.existsSync(path.resolve(dir, '../playwright/package.json')));
if (!packageBin) throw new Error('Run with npx --package=playwright -c "node scripts/qa-bookshelf.cjs"');
const { chromium } = require(path.resolve(packageBin, '../playwright'));
const baseURL = process.env.GRAPH_QA_URL || 'http://127.0.0.1:5201';

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.setDefaultTimeout(30000);
  try {
    // Extend the fixture module before its API/query caches initialize.
    await page.route('**/src/dev/graph-sidebar-preview.tsx*', async route => {
      const response = await route.fetch();
      const source = await response.text();
      const titles = ['美食大战老鼠：知识图谱（示意）', '操作系统原理（面试导向）', '辣椒炒肉（家常湘菜小炒）', 'Java 微服务（Spring Cloud Alibaba）与分布式系统实践', 'SQL 查询入门与实战', '羽毛球基础与多拍对打'];
      assert.ok(source.includes('const projects ='), 'fixture insertion point');
      const body = source.replace('const projects =', `graphs.push(...${JSON.stringify(titles)}.map((title, index) => ({ ...graphs[0], id: 'extra-' + index, title, status: index === 3 ? 'candidate' : 'published' })));\nconst projects =`);
      await route.fulfill({ response, body });
    });
    await page.goto(`${baseURL}/graph-sidebar-qa.html`);
    await page.getByRole('button', { name: '切换图谱', exact: true }).click();
    const shelf = page.getByRole('region', { name: '图谱书架', exact: true });
    await shelf.waitFor();
    const rows = shelf.locator('.project-bookshelf__list > button');
    assert.equal(await rows.count(), 6);
    const search = shelf.getByRole('textbox', { name: '搜索图谱', exact: true });
    await search.fill('Java');
    assert.equal(await rows.count(), 1);
    await shelf.getByRole('button', { name: '清除图谱搜索' }).click();
    assert.equal(await rows.count(), 6);
    await search.fill('不存在的图谱');
    await shelf.getByRole('status').filter({ hasText: '没有匹配的图谱' }).waitFor();
    await shelf.getByRole('button', { name: '清除图谱搜索' }).click();
    await shelf.getByRole('button', { name: '查看全部 9 个图谱' }).click();
    assert.equal(await rows.count(), 9);
    assert.equal(await shelf.getByRole('button', { name: '收起列表' }).getAttribute('aria-expanded'), 'true');
    const themes = [];
    for (const mode of ['light', 'dark']) {
      for (const colorTheme of ['mono', 'forest', 'ocean', 'violet', 'amber', 'rose']) {
        await page.evaluate(async ({ mode, colorTheme }) => {
          const { applyAppearance } = await import('/src/lib/appearance.ts');
          applyAppearance({ theme: mode, colorTheme }, false);
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          await Promise.allSettled(document.getAnimations().filter(animation => animation instanceof CSSTransition).map(animation => animation.finished));
        }, { mode, colorTheme });
        const contrast = await page.evaluate(() => {
          const pixel = document.createElement('canvas').getContext('2d');
          const rgb = color => {
            pixel.clearRect(0, 0, 1, 1); pixel.fillStyle = color; pixel.fillRect(0, 0, 1, 1);
            return [...pixel.getImageData(0, 0, 1, 1).data].slice(0, 3);
          };
          const luminance = color => rgb(color).map(channel => {
            const v = channel / 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;
          }).reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
          const ratio = (a, b) => (Math.max(luminance(a), luminance(b)) + .05) / (Math.min(luminance(a), luminance(b)) + .05);
          const icon = getComputedStyle(document.querySelector('.project-bookshelf__icon'));
          const status = getComputedStyle(document.querySelector('.project-bookshelf__content small'));
          const shelf = document.querySelector('.project-bookshelf');
          let surface = shelf;
          while (surface && getComputedStyle(surface).backgroundColor === 'rgba(0, 0, 0, 0)') surface = surface.parentElement;
          const background = getComputedStyle(surface || document.body).backgroundColor;
          return { icon: ratio(icon.color, icon.backgroundColor), status: ratio(status.color, background), foreground: icon.color, background: icon.backgroundColor };
        });
        assert.ok(contrast.icon >= 3, `${mode}/${colorTheme} icon ${JSON.stringify(contrast)}`);
        assert.ok(contrast.status >= 4.5, `${mode}/${colorTheme} status ${JSON.stringify(contrast)}`);
        themes.push({ mode, colorTheme, ...contrast });
      }
    }
    await page.evaluate(async () => {
      const { applyAppearance } = await import('/src/lib/appearance.ts');
      applyAppearance({ theme: 'light', colorTheme: 'mono' }, false);
    });
    const audits = [];
    const audit = async state => {
      const result = await page.evaluate(async () => {
        const { default: clipAudit } = await import('/src/dev/desktop-clip-audit.js');
        return clipAudit({ root: '.project-bookshelf', viewport: true });
      });
      assert.equal(result.cut, 0, `${state}: ${JSON.stringify(result.issues)}`);
      audits.push({ state, cut: result.cut, hidden: result.hidden });
    };
    await audit('expanded');
    await rows.first().hover();
    await audit('hover');
    await rows.first().focus();
    assert.equal(await rows.first().evaluate(el => getComputedStyle(el).outlineStyle), 'solid');
    await audit('keyboard-focus');
    await search.focus();
    await audit('search-focus');
    await search.blur();
    const widths = [];
    for (const width of [280, 320, 360, 480]) {
      await page.evaluate(width => document.querySelector('.workspace-shell').style.setProperty('--rail-width', `${width}px`), width);
      const bounds = await page.evaluate(() => {
        const rail = document.querySelector('.context-rail').getBoundingClientRect();
        const switcher = document.querySelector('.chat-graph-rail__switch');
        const tools = [...switcher.querySelectorAll(':scope > button')].map(el => el.getBoundingClientRect());
        const list = document.querySelector('.project-bookshelf__list');
        return { width: rail.width, noHorizontalOverflow: list.scrollWidth <= list.clientWidth + 1, toolsInside: tools.every(r => r.left >= rail.left && r.right <= rail.right), rowWidth: list.clientWidth };
      });
      assert.ok(bounds.noHorizontalOverflow && bounds.toolsInside, `${width}: ${JSON.stringify(bounds)}`);
      widths.push(bounds);
    }
    await page.evaluate(() => document.querySelector('.workspace-shell').style.setProperty('--rail-width', '360px'));
    await page.screenshot({ path: 'docs/bookshelf-light-qa.png' });
    await page.evaluate(async () => {
      const { applyAppearance } = await import('/src/lib/appearance.ts');
      applyAppearance({ theme: 'dark', colorTheme: 'ocean' }, false);
    });
    await page.screenshot({ path: 'docs/bookshelf-dark-qa.png' });
    await shelf.getByRole('button', { name: '收起列表' }).click();
    assert.equal(await rows.count(), 6);
    await audit('collapsed');
    assert.deepEqual(await page.evaluate(() => window.__graphSidebarQA.writes), []);
    assert.deepEqual(errors, []);
    console.log('BOOKSHELF_QA', JSON.stringify({ pass: true, themes, widths, audits, search: true, clear: true, empty: true, expandCollapse: true, bindingWrites: 0 }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
