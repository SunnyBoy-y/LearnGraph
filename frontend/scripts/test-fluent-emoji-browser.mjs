import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import path from 'node:path';
import { mkdir, writeFile } from 'node:fs/promises';

const require = createRequire(import.meta.url);
let playwright;
try { playwright = require('playwright'); }
catch { playwright = require(path.join(process.env.APPDATA, 'npm/node_modules/@playwright/cli/node_modules/playwright')); }
const url = process.env.FLUENT_EMOJI_URL || 'http://127.0.0.1:5191/fluent-emoji-preview.html';
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || path.join(process.env.LOCALAPPDATA, 'ms-playwright/chromium-1243/chrome-win64/chrome.exe');
const browser = await playwright.chromium.launch({ headless: true, executablePath });
const output = path.resolve('../artifacts/fluent-emoji');
await mkdir(output, { recursive: true });
const report = { url, checks: [], errors: [], images: 0, screenshots: [] };
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  page.on('pageerror', error => report.errors.push(error.message));
  const externalImages = [];
  page.on('request', request => { if (request.resourceType() === 'image' && new URL(request.url()).origin !== new URL(url).origin) externalImages.push(request.url()); });
  await page.goto(url);
  await page.getByRole('img', { name: '开始探索', exact: true }).waitFor();
  const prose = page.locator('[data-case="markdown"]');
  const document = page.locator('[data-case="document"]');
  assert.equal(await prose.locator('img.answer-icon').count(), 6);
  assert.equal(await prose.locator('code img, pre img').count(), 0);
  assert.match(await prose.locator('code').first().innerText(), /🚀 代码保持原样/);
  assert(await prose.locator('.katex').count() > 0);
  assert.equal(await document.getByRole('heading', { name: '建立知识联系' }).locator('img').count(), 1);
  assert.equal(await document.getByText('等待数据填充…').count(), 0);
  for (const name of ['metric', 'timeline']) assert.equal(await page.locator(`[data-case="${name}"] img.answer-icon`).count(), 3);
  report.checks.push('actual message renderer: Markdown, Stream UI document, headings, standalone, rails, metric and timeline');
  const images = page.locator('img.answer-icon:visible');
  report.images = await images.count();
  for (let index = 0; index < report.images; index++) {
    await images.nth(index).scrollIntoViewIfNeeded();
    await images.nth(index).evaluate(image => image.decode());
  }
  assert.deepEqual(externalImages, []);
  assert(await document.locator('.lucide-lightbulb').count() > 0);
  report.checks.push('all visible local WebP assets decode, no external image requests, legacy Lucide still renders');
  const taggedFlag = await page.request.get(new URL('fluent-emoji/1.1.0/1f3f4-e0067_e0062_e0065_e006e_e0067.webp', url).href);
  assert.equal(taggedFlag.status(), 200);
  assert.match(taggedFlag.headers()['content-type'], /^image\/webp/);
  const license = await page.request.get(new URL('fluent-emoji/1.1.0/LICENSE.txt', url).href);
  assert.equal(license.status(), 200);
  assert.match(await license.text(), /Microsoft Corporation/);
  report.checks.push('unusual upstream filenames and attribution files are served in the deployment');
  await document.getByRole('tab', { name: '练习', exact: true }).click();
  const slider = document.getByRole('slider', { name: '系数 a', exact: true });
  await slider.focus();
  await slider.press('End');
  assert.equal(await slider.inputValue(), '3');
  await page.getByRole('button', { name: '回放', exact: true }).click();
  await document.getByRole('tab', { name: '练习', exact: true }).waitFor();
  await page.getByRole('img', { name: '开始探索', exact: true }).waitFor();
  await page.getByRole('button', { name: '完整内容', exact: true }).click();
  assert.equal(await document.getByRole('tab', { name: '练习', exact: true }).getAttribute('aria-selected'), 'true');
  assert.equal(await slider.inputValue(), '3');
  report.checks.push('streaming replay preserves tab selection and simulation input state');
  for (const dark of [false, true]) {
    if (dark) await page.getByRole('button', { name: '深色', exact: true }).click();
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(viewport);
      await page.evaluate(() => window.scrollTo(0, 0));
      const dimensions = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth,
        icons: [...document.querySelectorAll('img.answer-icon')].filter(image => image.getBoundingClientRect().width > 0).map(image => ({ width: image.getBoundingClientRect().width, height: image.getBoundingClientRect().height, filter: getComputedStyle(image).filter })) }));
      assert(dimensions.scroll <= dimensions.width, `horizontal overflow: ${JSON.stringify(dimensions)}`);
      assert(dimensions.icons.every(image => image.width > 0 && image.width <= 48 && Math.abs(image.width - image.height) < 1));
      const filename = `${new URL(url).port}-${dark ? 'dark' : 'light'}-${viewport.width}.png`;
      await page.screenshot({ path: path.join(output, filename), fullPage: true });
      report.screenshots.push(filename);
    }
  }
  report.checks.push('desktop/mobile (1440px/390px), light/dark: no overflow and bounded square icons');
  const failed = await browser.newPage();
  failed.on('pageerror', error => report.errors.push(error.message));
  await failed.route('**/fluent-emoji/1.1.0/1f680.webp', route => route.fulfill({ status: 503, body: '' }));
  await failed.goto(url);
  await failed.locator('.rich-doc__standalone-icon').scrollIntoViewIfNeeded();
  await failed.waitForFunction(() => document.querySelector('[data-case="document"] [aria-label="开始探索"]')?.tagName === 'SPAN');
  assert.equal(await failed.getByRole('img', { name: '开始探索', exact: true }).innerText(), '🚀');
  report.checks.push('failed image requests fall back to readable Unicode without broken images');
  await failed.close();
  if (!process.env.FLUENT_EMOJI_SKIP_SHARED_PREVIEWS) {
    for (const route of ['rich-response-preview.html', 'editorial-preview.html', 'genui-preview.html', 'timeline-preview.html', 'ui-input-preview.html']) {
      await page.goto(new URL(route, url).href);
      await page.locator('main').waitFor();
      assert.equal(await page.locator('vite-error-overlay').count(), 0, route);
      assert(await page.locator('main').innerText(), route);
    }
    report.checks.push('shared rich-response/editorial/genui/timeline/input preview routes render without runtime errors');
  }
  assert.deepEqual(report.errors, []);
  await writeFile(path.join(output, `browser-${new URL(url).port}.json`), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report, null, 2));
} finally { await browser.close(); }
