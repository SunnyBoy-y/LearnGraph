const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const packageBin = process.env.PATH.split(path.delimiter).find(dir => fs.existsSync(path.resolve(dir, '../playwright/package.json')));
if (!packageBin) throw new Error('Run with npx --package=playwright');
const { chromium } = require(path.resolve(packageBin, '../playwright'));
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const setActivity = async (kind, id, viewing = false) => page.evaluate(async ({ kind, id, viewing }) => {
    const activity = await import('/src/lib/session-activity.ts');
    if (kind === 'start') activity.markSessionRunning(id, true);
    if (kind === 'finish') activity.markSessionGenerationFinished(id, { viewing });
    if (kind === 'stop') activity.markSessionRunning(id, false);
  }, { kind, id, viewing });
  const desktop = () => page.locator('.sidebar-nav').filter({ has: page.locator('[data-sidebar-toggle]') }).first();
  const row = (sidebar, id) => sidebar.locator(`[data-virtual-key="session:${id}"] .sidebar-project__session-title`);
  const checkRotation = async target => {
    await target.locator('.sidebar-session__running').waitFor();
    const before = await target.locator('.sidebar-session__running').evaluate(el => {
      const style = getComputedStyle(el);
      return { transform: style.transform, iterations: style.animationIterationCount, duration: style.animationDuration, state: el.getAnimations()[0]?.playState };
    });
    assert.equal(before.iterations, 'infinite');
    assert.equal(before.state, 'running');
    await page.waitForFunction(({ element, transform }) => getComputedStyle(element).transform !== transform,
      { element: await target.locator('.sidebar-session__running').elementHandle(), transform: before.transform });
  };
  const checkDot = async target => {
    await target.locator('.sidebar-session__unread-dot').waitFor();
    assert.equal(await target.locator('.sidebar-session__running').count(), 0);
    const status = await target.evaluate(el => {
      const title = el.querySelector('span.flex-1');
      const slot = el.querySelector('.sidebar-session__activity');
      const dot = el.querySelector('.sidebar-session__unread-dot');
      return { trailing: el.lastElementChild === slot, afterTitle: slot.getBoundingClientRect().left >= title.getBoundingClientRect().right, color: getComputedStyle(dot).backgroundColor, slotWidth: slot.getBoundingClientRect().width };
    });
    assert.equal(status.trailing, true);
    assert.equal(status.afterTitle, true);
    assert.equal(status.slotWidth, 12);
    assert.ok(['rgb(59, 130, 246)', 'rgb(96, 165, 250)'].includes(status.color), JSON.stringify(status));
  };
  try {
    await page.goto('http://127.0.0.1:5201/sidebar-navigation-qa.html');
    let sidebar = desktop();
    await row(sidebar, 'tcp').waitFor();
    for (const id of ['tcp', 'recent-0']) {
      const target = row(sidebar, id);
      await setActivity('start', id);
      await checkRotation(target);
      const spinnerX = await target.locator('.sidebar-session__activity').evaluate(el => el.getBoundingClientRect().x);
      await setActivity('finish', id);
      await checkDot(target);
      assert.equal(await target.locator('.sidebar-session__activity').evaluate(el => el.getBoundingClientRect().x), spinnerX);
      await target.click();
      await target.locator('.sidebar-session__activity').waitFor({ state: 'detached' });
    }
    await setActivity('finish', 'recent-0', true);
    assert.equal(await row(sidebar, 'recent-0').locator('.sidebar-session__activity').count(), 0);
    await setActivity('start', 'recent-0');
    await setActivity('stop', 'recent-0');
    assert.equal(await row(sidebar, 'recent-0').locator('.sidebar-session__activity').count(), 0);
    await sidebar.getByRole('button', { name: '新建对话', exact: true }).click();
    await setActivity('finish', 'recent-0');
    for (const label of ['图谱', '资料', '产物', '定时任务']) {
      await sidebar.getByRole('link', { name: label, exact: true }).click();
      await checkDot(row(sidebar, 'recent-0'));
    }
    for (const label of ['练习', '记忆']) {
      await sidebar.getByRole('button', { name: '更多', exact: true }).click();
      await page.getByRole('menuitem', { name: label, exact: true }).click();
      await checkDot(row(sidebar, 'recent-0'));
    }
    await page.evaluate(() => document.documentElement.classList.add('dark'));
    await checkDot(row(sidebar, 'recent-0'));
    await setActivity('start', 'recent-0');
    await checkRotation(row(sidebar, 'recent-0'));
    assert.equal(await row(sidebar, 'recent-0').locator('.sidebar-session__unread-dot').count(), 0);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await checkRotation(row(sidebar, 'recent-0'));
    await setActivity('finish', 'recent-0');
    await checkDot(row(sidebar, 'recent-0'));
    await page.evaluate(() => document.documentElement.classList.remove('dark'));
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: '打开导航', exact: true }).click();
    sidebar = page.locator('.sidebar-nav.is-mobile');
    await checkDot(row(sidebar, 'recent-0'));
    await row(sidebar, 'recent-0').click();
    await page.getByRole('button', { name: '打开导航', exact: true }).click();
    await row(sidebar, 'recent-0').waitFor();
    assert.equal(await row(sidebar, 'recent-0').locator('.sidebar-session__activity').count(), 0);
    await setActivity('start', 'recent-0');
    await checkRotation(row(sidebar, 'recent-0'));
    await setActivity('finish', 'recent-0');
    await checkDot(row(sidebar, 'recent-0'));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ pass: true, checks: ['project and recent session status transitions', 'continuous rotation including reduced motion', 'same trailing position for spinner and blue dot', 'click clears completion', 'viewed completion and stopped generation stay clear', 'status persists on all shared navigation routes', 'dark theme blue dot', 'mobile drawer status and clearing', 'no horizontal overflow or page errors'] }, null, 2));
  } catch (error) {
    console.error('Browser state', await page.evaluate(async () => ({ route: window.__sidebarNavigationQA?.route, activity: (await import('/src/lib/session-activity.ts')).getSessionActivity('recent-0'), rows: Array.from(document.querySelectorAll('[data-virtual-key]')).map(el => el.getAttribute('data-virtual-key')), scroll: Array.from(document.querySelectorAll('.sidebar-nav__scroll')).map(el => el.scrollTop), text: document.body.innerText.slice(0, 2500) })));
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
