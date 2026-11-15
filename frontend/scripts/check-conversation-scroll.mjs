// Repeatable headless QA against real Vite-served React components and production CSS.
// npm exec --yes --package=playwright@1.56.1 -- node scripts/check-conversation-scroll.mjs
// No backend data, application source files, or browser profiles are modified.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const bin = process.env.PATH.split(path.delimiter).find(entry => fs.existsSync(path.resolve(entry, '../playwright/package.json')));
const require = bin ? createRequire(path.resolve(bin, '../playwright/package.json')) : createRequire(import.meta.url);
const { chromium, expect } = require('playwright/test');
const baseUrl = process.env.SCROLL_QA_URL || 'http://127.0.0.1:5206';
// Guard against the production route drifting away from the tested composition.
const chatSource = fs.readFileSync(path.resolve(import.meta.dirname, '../src/features/chat/chat-pages.tsx'), 'utf8');
assert.match(chatSource, /<ConversationScrollButton\s+portalTarget=\{composerDockElement\}/);
assert.match(chatSource, /ref=\{setComposerDockElement\}/);
// Reuse Vite's exact dependency URLs, including optimizer hashes, to avoid loading
// a second React instance into the component tree.
const main = await (await fetch(`${baseUrl}/src/main.tsx`)).text();
const reactUrl = main.match(/from "([^"\n]*\/react\.js[^"\n]*)"/)?.[1];
const domUrl = main.match(/from "([^"\n]*\/react-dom_client\.js[^"\n]*)"/)?.[1];
assert.ok(reactUrl && domUrl, 'Vite must be running and expose React dependency URLs');

const html = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body><div id="root"></div><script type="module">
import RefreshRuntime from '/@react-refresh';
RefreshRuntime.injectIntoGlobalHook(window);
window.$RefreshReg$ = () => {};
window.$RefreshSig$ = () => type => type;
window.__vite_plugin_react_preamble_installed__ = true;
const React = (await import(${JSON.stringify(reactUrl)})).default;
const { createRoot } = (await import(${JSON.stringify(domUrl)})).default;
const { useState, useRef, useEffect, useLayoutEffect } = React;
const { Conversation, ConversationContent, ConversationScrollButton } = await import('/src/components/ai-elements/conversation.tsx');
const { useConversationScrollController } = await import('/src/features/chat/use-conversation-scroll-controller.ts');
await import('/src/index.css');
const h = React.createElement;
const surface = new URL(location.href).searchParams.get('surface') || 'main';
// Only the outer QA frame is synthetic. Conversation, content, button, scroll
// controller, textarea sizing, CSS and browser layout are real (not mocked).
function Harness() {
  const controller = useConversationScrollController();
  const [dock, setDock] = useState(null);
  const [draft, setDraft] = useState('');
  const [chunks, setChunks] = useState(0);
  const [thinking, setThinking] = useState(false);
  const input = useRef(null);
  useLayoutEffect(() => {
    const node = input.current;
    if (!node) return;
    node.style.height = 'auto';
    node.style.height = Math.min(220, Math.max(56, node.scrollHeight)) + 'px';
  }, [draft]);
  useEffect(() => {
    window.scrollQa = {
      controller,
      startThinking() { setThinking(true); setDraft(''); },
      growThinking() { setChunks(value => value + 8); },
      setReserve(height) { document.documentElement.style.setProperty('--composer-height', height + 'px'); },
    };
  });
  useEffect(() => { controller.notifyLayoutChanged(); }, [chunks, controller.notifyLayoutChanged]);
  const rows = Array.from({ length: 36 + chunks }, (_, index) =>
    h('div', { key: index, 'data-message-mount-id': 'history-' + index, style: { minHeight: 88, flexShrink: 0 } },
      h('p', null, '历史消息 ' + (index + 1)), h('p', null, '用于验证真实聊天滚动、定位和返回最新消息。')));
  if (thinking) rows.push(h('div', { key: 'thinking', 'data-testid': 'thinking', style: { minHeight: 80 } }, 'AI 思考中 · ' + chunks));
  const main = surface === 'main';
  return h('main', {
    className: main ? 'chat-canvas-page relative flex min-h-0 flex-col bg-background' : 'relative flex min-h-0 flex-col bg-background',
    'data-testid': 'qa-surface',
    style: { height: '100dvh', width: '100%', maxWidth: main ? '1100px' : '720px', margin: '0 auto' },
  },
    h(Conversation, { controller, className: surface === 'rail' ? 'right-rail-conversation__messages-shell min-h-0 flex-1' : 'min-h-0 flex-1' },
      h(ConversationContent, { className: main ? 'chat-messages-content' : surface === 'rail' ? 'right-rail-conversation__messages' : 'flex flex-col', style: { gap: 16 } }, rows),
      main ? h(ConversationScrollButton, { portalTarget: dock }) : h(ConversationScrollButton)),
    h('div', {
      className: main ? 'chat-composer-dock' : 'qa-local-composer', ref: setDock, 'data-testid': 'composer',
      style: main ? {} : { flexShrink: 0, padding: 12, borderTop: '1px solid #aaa' },
    },
      h('div', { style: { width: '100%', minHeight: 24 } }, surface + ' · QA composer'),
      h('textarea', {
        ref: input, 'aria-label': 'QA 输入消息', value: draft,
        onChange: event => setDraft(event.target.value),
        style: { width: '100%', height: 56, resize: 'none', boxSizing: 'border-box', lineHeight: '22px', padding: '8px', border: '1px solid #999' },
      })));
}
document.documentElement.style.setProperty('--composer-height', '480px');
createRoot(document.getElementById('root')).render(h(Harness));
</script></body></html>`;

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const report = { url: baseUrl, harness: 'real React Conversation + real scroll controller + production index.css; isolated route, no API mocks or writes', cases: [], errors: [], networkErrors: [] };
let activePage;
try {
  for (const viewport of [
    { name: 'desktop', width: 1440, height: 1000, isMobile: false, hasTouch: false },
    { name: 'mobile', width: 390, height: 844, isMobile: true, hasTouch: true },
  ]) {
    const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height }, isMobile: viewport.isMobile, hasTouch: viewport.hasTouch, reducedMotion: 'reduce' });
    for (const surface of ['main', 'rail', 'document']) {
      const page = await context.newPage();
      activePage = page;
      page.setDefaultTimeout(10000);
      page.on('pageerror', error => report.errors.push({ surface, viewport: viewport.name, message: error.stack || error.message }));
      page.on('requestfailed', request => report.networkErrors.push({ url: request.url(), failure: request.failure()?.errorText }));
      page.on('response', response => { if (response.status() >= 400) report.networkErrors.push({ url: response.url(), status: response.status() }); });
      await page.route('**/api/**', route => route.abort('blockedbyclient'));
      await page.route('**/__qa__/conversation-scroll?*', route => route.fulfill({ contentType: 'text/html', body: html }));
      await page.goto(`${baseUrl}/__qa__/conversation-scroll?surface=${surface}`, { waitUntil: 'commit', timeout: 45000 });
      await page.waitForFunction(() => Boolean(window.scrollQa?.controller), null, { timeout: 45000 });
      const scroller = page.locator('[data-conversation-scroll]');
      const button = page.locator('.chat-scroll-to-bottom');
      const composer = page.getByTestId('composer');
      const textarea = page.getByRole('textbox', { name: 'QA 输入消息' });
      const sample = async () => page.evaluate(() => {
        const rect = element => { const r = element.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height, top: r.top, bottom: r.bottom }; };
        const button = document.querySelector('.chat-scroll-to-bottom');
        const composer = document.querySelector('[data-testid="composer"]');
        const scroller = document.querySelector('[data-conversation-scroll]');
        const log = document.querySelector('[role="log"]');
        return {
          button: button ? rect(button) : null, composer: rect(composer), log: rect(log),
          scrollTop: scroller.scrollTop, distance: scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight,
          mode: log.dataset.scrollMode, label: button?.getAttribute('aria-label'),
          inComposer: button?.parentElement === composer, inLog: log.contains(button),
          reserve: getComputedStyle(document.documentElement).getPropertyValue('--composer-height').trim(),
          buttonPosition: button && getComputedStyle(button).position,
          hit: button ? (() => { const r = button.getBoundingClientRect(); return button.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)); })() : false,
        };
      });
      const assertPosition = async phase => {
        await expect(button).toBeVisible();
        const state = await sample();
        const gap = surface === 'main' ? state.composer.top - state.button.bottom : state.log.bottom - state.button.bottom;
        assert.ok(Math.abs(gap - (surface === 'main' ? 8 : 16)) <= 1.5, `${viewport.name}/${surface}/${phase}: gap=${gap}; ${JSON.stringify(state)}`);
        assert.ok(state.button.top >= 0 && state.button.bottom <= viewport.height, 'Button must stay inside visual viewport');
        assert.equal(state.buttonPosition, 'absolute');
        assert.equal(state.hit, true, 'Button must be hit-testable above composer');
        assert.equal(state.inComposer, surface === 'main');
        assert.equal(state.inLog, surface !== 'main');
        return { phase, gap, ...state };
      };
      // Build a tall draft, then send/clear it while retaining the large global
      // scroll reservation -- the precise thinking-state regression condition.
      await textarea.fill('输入框高度变化与发送后收缩。\n'.repeat(12));
      const tallDraft = await composer.boundingBox();
      await page.evaluate(() => window.scrollQa.startThinking());
      await expect(page.getByTestId('thinking')).toBeVisible();
      await expect.poll(async () => (await composer.boundingBox()).height).toBeLessThan(tallDraft.height - 40);
      // Genuine browser wheel input, not a mocked controller state.
      await scroller.hover();
      await page.mouse.wheel(0, -650);
      await expect(page.getByRole('log')).toHaveAttribute('data-scroll-mode', 'MANUAL_READING');
      await page.evaluate(() => window.scrollQa.growThinking());
      await expect(button).toHaveAttribute('aria-label', '还有新内容');
      const samples = [await assertPosition('thinking-after-draft-shrink')];
      const readingTop = samples[0].scrollTop;
      for (let index = 0; index < 3; index++) {
        await page.evaluate(() => window.scrollQa.growThinking());
        samples.push(await assertPosition('thinking-growth-' + index));
      }
      assert.ok(Math.abs((await sample()).scrollTop - readingTop) <= 2, 'Thinking growth must not steal manual reading');
      await textarea.fill('附件与工具栏变化等效的多行输入。\n'.repeat(10));
      await expect.poll(async () => (await composer.boundingBox()).height).toBeGreaterThan(samples[0].composer.height + 40);
      samples.push(await assertPosition('composer-expanded'));
      await textarea.fill('');
      await expect.poll(async () => (await composer.boundingBox()).height).toBeLessThan(tallDraft.height - 40);
      samples.push(await assertPosition('composer-shrunk'));
      if (surface !== 'main') {
        const before = await sample();
        await page.evaluate(() => window.scrollQa.setReserve(760));
        const after = await assertPosition('unrelated-main-composer-height-760');
        assert.ok(Math.abs(after.button.top - before.button.top) <= 1, 'Shared button must ignore the main composer height');
        samples.push(after);
      }
      await button.click();
      await expect(page.getByRole('log')).toHaveAttribute('data-scroll-mode', 'FOLLOW_ACTIVITY');
      await expect.poll(async () => (await sample()).distance).toBeLessThanOrEqual(2);
      await expect(button).toHaveCount(0);
      await page.evaluate(() => window.scrollQa.growThinking());
      await expect.poll(async () => (await sample()).distance).toBeLessThanOrEqual(2);
      report.cases.push({ viewport: viewport.name, surface, passed: true, tallDraftHeight: tallDraft.height, samples, returnedToLatest: await sample() });
      console.log('PASS ' + viewport.name + '/' + surface + ': ' + samples.length + ' geometry samples; return-to-latest and continued following verified');
      await page.close();
    }
    await context.close();
  }
  assert.deepEqual(report.errors, []);
  report.passed = true;
} catch (error) {
  report.passed = false;
  report.failure = error.stack || String(error);
  if (activePage && !activePage.isClosed()) {
    report.lastDom = await activePage.evaluate(() => ({
      url: location.href,
      textarea: document.querySelector('textarea')?.outerHTML,
      text: document.body.innerText.slice(-1200),
    })).catch(() => null);
  }
  process.exitCode = 1;
} finally {
  await browser.close();
  console.log(JSON.stringify(report, null, 2));
}
