// Browser geometry regression for the answer-column CSS (no login or API data).
// Start Vite first, then run: node scripts/check-chat-answer-alignment.mjs
// Playwright may be installed locally or through the global @playwright/cli.
import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'

const localRequire = createRequire(import.meta.url)
let chromium
try {
  ;({ chromium } = localRequire('playwright'))
} catch {
  assert(process.env.APPDATA, 'Install Playwright locally or set APPDATA for the global CLI')
  const globalRequire = createRequire(path.join(process.env.APPDATA, 'npm/node_modules/'))
  ;({ chromium } = globalRequire('@playwright/cli/node_modules/playwright'))
}

const origin = process.env.LEARNGRAPH_WEB_ORIGIN ?? 'http://127.0.0.1:5173'
const entry = await readFile(new URL('../src/main.tsx', import.meta.url), 'utf8')
assert(entry.includes("import '@/styles/chat-answer-alignment.css'"), 'Load the alignment stylesheet in the app entry')
const css = await Promise.all(['index.css', 'styles/chat-answer-alignment.css'].map(async (file) => {
  const response = await fetch(`${origin}/src/${file}?direct`, { signal: AbortSignal.timeout(30_000) })
  assert(response.ok, `${file}: HTTP ${response.status}`)
  return response.text()
}))
const newerChromium = process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, 'ms-playwright/chromium-1243/chrome-win64/chrome.exe')
const executablePath = process.env.LEARNGRAPH_CHROMIUM ?? (existsSync(chromium.executablePath()) ? chromium.executablePath() : newerChromium)
const browser = await chromium.launch({ headless: true, executablePath })

// Mirror the Message/MessageContent and AssistantMessage DOM, including the
// display:contents wrapper, consolidated action bar, and in-flight stats sibling.
const html = `<!doctype html><html><head><meta charset="utf-8">
<style>${css[0]}</style><style id="answer-alignment">${css[1]}</style>
</head><body><div class="workspace-shell" style="--sidebar-width:268px;--rail-width:360px">
<aside class="workspace-sidebar"></aside><main class="workspace-main workspace-main--chat">
<section class="chat-canvas-page"><div class="chat-messages-content" style="padding-top:72px;display:flex;flex-direction:column;gap:20px">
<div class="is-assistant group flex flex-col gap-2 max-w-none" id="completed-message">
<div class="w-full min-w-0 max-w-full flex flex-col gap-1 overflow-hidden text-sm" data-message-content>
<div class="contents"><div class="space-y-2"><div class="thinking-chain">
<button class="thinking-chain__trigger">思考了 50s <span class="thinking-chain__chip">3 次工具调用</span></button>
<div class="thinking-chain__content" hidden>已完成推理与来源核对。</div></div></div>
<div class="message-answer-segment"><p>检索结果显示：现有平台主要提供能力评测，并非后台型号取证。</p>
<p>正文保持当前阅读宽度，用于检查思考状态、消息操作与后续问题的边缘。</p></div></div></div>
<div class="flex items-center gap-1" id="answer-actions"><button>复制</button><button>重试</button><button>生成详情</button><button>更多</button></div></div>
<section class="chat-follow-up-prompts"><div class="chat-follow-up-prompts__label">接下来可以问</div>
<div class="chat-suggestions"><div class="chat-empty-state__prompts"><button class="chat-empty-state__prompt">能不能帮我设计一套具体的测试题，并说明每道题的区别？</button></div></div></section>
<div class="is-assistant group flex flex-col gap-2 max-w-none" data-streaming="true" id="streaming-message">
<div class="w-full min-w-0 max-w-full flex flex-col gap-1 overflow-hidden text-sm" data-message-content><div class="contents">
<div class="space-y-2"><div class="thinking-chain"><button class="thinking-chain__trigger">正在思考</button></div></div>
<div class="message-answer-segment"><p>流式回答正文</p></div></div></div>
<div class="message-stream-stats message-stream-stats--loading"><span>正在生成</span></div></div>
</div></section></main><div class="workspace-resizer"></div><aside class="context-rail"></aside></div></body></html>`
const selectors = ['.thinking-chain', '#answer-actions', '.chat-follow-up-prompts', '#streaming-message > .message-stream-stats']
let checks = 0
try {
  const page = await browser.newPage()
  await page.route(`${origin}/__answer-alignment-check__`, (route) => route.fulfill({ contentType: 'text/html', body: html }))
  await page.goto(`${origin}/__answer-alignment-check__`, { waitUntil: 'networkidle' })
  for (const width of [2560, 1920, 1440, 1280, 1100, 1024, 800, 390]) {
    await page.setViewportSize({ width, height: 1000 })
    for (const collapsed of [false, true]) {
      for (const dark of [false, true]) {
        await page.evaluate(({ collapsed, dark }) => {
          document.querySelector('.workspace-shell').classList.toggle('workspace-shell--rail-collapsed', collapsed)
          document.documentElement.classList.toggle('dark', dark)
          document.documentElement.classList.remove('is-native-app')
          document.querySelector('#answer-alignment').sheet.disabled = true
        }, { collapsed, dark })
        await page.evaluate(async () => { await Promise.allSettled(document.getAnimations().filter((animation) => Number.isFinite(animation.effect?.getComputedTiming().endTime)).map((animation) => animation.finished)) })
        const before = await page.locator('.message-answer-segment').first().boundingBox()
        await page.evaluate(() => { document.querySelector('#answer-alignment').sheet.disabled = false })
        // Finish any existing shell transitions without adding test-only layout rules.
        await page.evaluate(async () => { await Promise.allSettled(document.getAnimations().filter((animation) => Number.isFinite(animation.effect?.getComputedTiming().endTime)).map((animation) => animation.finished)) })
        const answer = await page.locator('.message-answer-segment').first().boundingBox()
        assert(before && answer)
        assert(Math.abs(before.x - answer.x) < 1 && Math.abs(before.width - answer.width) < 1, `Answer moved at ${width}/${collapsed}/${dark}`)
        for (const selector of selectors) {
          for (const item of await page.locator(selector).all()) {
            const bounds = await item.boundingBox()
            assert(bounds, `${selector} has no layout box`)
            assert(Math.abs(bounds.x - answer.x) < 1, `${selector} left ${bounds.x} != answer ${answer.x} at ${width}/${collapsed}/${dark}`)
            assert(Math.abs(bounds.width - answer.width) < 1, `${selector} width ${bounds.width} != answer ${answer.width} at ${width}/${collapsed}/${dark}`)
          }
        }
        checks++
      }
    }
    if (width <= 1024) {
      await page.evaluate(() => document.documentElement.classList.add('is-native-app'))
      const answer = await page.locator('.message-answer-segment').first().boundingBox()
      for (const selector of selectors) {
        const bounds = await page.locator(selector).first().boundingBox()
        assert(bounds && Math.abs(bounds.x - answer.x) < 1 && Math.abs(bounds.width - answer.width) < 1, `Native ${selector} at ${width}`)
      }
      checks++
    }
  }
  const readingCss = await (await fetch(`${origin}/src/components/chat/message-reading.css?direct`, { signal: AbortSignal.timeout(30_000) })).text()
  const streamUiCss = await (await fetch(`${origin}/src/components/chat/stream-ui.css?direct`, { signal: AbortSignal.timeout(30_000) })).text()
  const artifactCardCss = await (await fetch(`${origin}/src/components/chat/unified-artifact-card.css?direct`, { signal: AbortSignal.timeout(30_000) })).text()
  const richDocCss = await (await fetch(`${origin}/src/components/chat/rich-document.css?direct`, { signal: AbortSignal.timeout(30_000) })).text()
  const wideColumns = Array.from({ length: 10 }, (_, idx) => `<th data-streamdown="table-header-cell" class="whitespace-nowrap">超长列标题_${idx + 1}_字段说明</th>`).join('')
  const wideCells = Array.from({ length: 10 }, (_, idx) => `<td data-streamdown="table-cell">宽表格单元格内容_${idx + 1}_用于验证内部横向滚动不会撑开正文列</td>`).join('')
  const longCodeLine = 'const veryLongIdentifierNameForHorizontalScrollVerification = ' + '"abcdefghij".repeat(24);'
  const wideSvg = `data:image/svg+xml;utf8,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="800"><rect width="1600" height="800" fill="#dbeafe"/></svg>')}`

  for (const scenario of [
    { width: 1920, sidebarWidth: 268, railWidth: 360, collapsed: false },
    { width: 1440, sidebarWidth: 268, railWidth: 360, collapsed: false },
    { width: 1440, sidebarWidth: 64, railWidth: 360, collapsed: false },
    { width: 1440, sidebarWidth: 268, railWidth: 620, collapsed: false },
    { width: 1440, sidebarWidth: 268, railWidth: 360, collapsed: true },
    { width: 1280, sidebarWidth: 268, railWidth: 360, collapsed: false },
    { width: 1024, sidebarWidth: 268, railWidth: 360, collapsed: false },
    { width: 390, sidebarWidth: 0, railWidth: 0, collapsed: true },
  ]) {
    await page.setViewportSize({ width: scenario.width, height: 820 })
    await page.mouse.move(0, 0)
    await page.setContent(`<style>${css.join('\n')}\n${readingCss}\n${streamUiCss}\n${artifactCardCss}\n${richDocCss}</style>
      <div class="workspace-shell ${scenario.collapsed ? 'workspace-shell--rail-collapsed' : ''}" style="--sidebar-width:${scenario.sidebarWidth}px;--rail-width:${scenario.railWidth}px">
        <aside class="workspace-sidebar"></aside>
        <main class="workspace-main workspace-main--chat">
          <div class="chat-canvas-page" style="height:760px;display:flex;flex-direction:column">
            <div id="viewport" data-conversation-scroll style="flex:1 1 0;min-height:0;overflow:auto;scrollbar-gutter:stable both-edges">
              <div class="chat-messages-content chat-outline-lane">
                <div class="chat-message-mount">
                  <div class="is-user group flex flex-col gap-2">
                    <div id="user-bubble" class="flex w-fit max-w-full flex-col px-4 py-3" data-message-content>为什么不能用流式 SVG？</div>
                  </div>
                </div>
                <div class="chat-message-mount">
                <div class="is-assistant">
                  <div class="chat-message-version-row">版本：v1 v2</div>
                  <div data-message-content class="message-reading-surface">
                    <div class="message-reading">
                      <div class="message-reading__process">
                        <div class="thinking-chain"><button class="thinking-chain__trigger">思考了 8s</button></div>
                      </div>
                      <div class="message-reading__body">
                        <div class="message-answer-segment answer-flow">
                          <div class="message-text-block"><div data-message-selectable-text><div class="answer-markdown-flow">
                            <p id="prose-anchor">核心区别一句话：纯文本是在「说话」，表格、代码块、Stream UI 与图片都必须服从同一个 Assistant Content Rail 边界。</p>
                            <div data-streamdown="table-wrapper" id="md-table">
                              <div><button type="button">复制</button><button type="button">下载</button><button type="button">全屏</button></div>
                              <div id="md-table-scroll"><table data-streamdown="table"><thead><tr>${wideColumns}</tr></thead><tbody><tr>${wideCells}</tr></tbody></table></div>
                            </div>
                            <div data-streamdown="code-block" data-language="ts" id="md-code">
                              <div data-streamdown="code-block-header">ts</div>
                              <div data-streamdown="code-block-body" id="md-code-scroll"><pre><code>${longCodeLine}</code></pre></div>
                            </div>
                            <pre class="incremental-markdown-fence" id="streaming-code"><code>${longCodeLine}</code></pre>
                            <img id="md-img" src="${wideSvg}" width="1600" height="800" alt="宽图测试" />
                          </div></div></div>
                          <section class="unified-artifact-card unified-artifact-card--inline" data-display-mode="inline" data-artifact-type="svg" id="inline-svg">
                            <div class="unified-artifact-card__stage">
                              <div class="unified-artifact-card__floating-actions" role="toolbar"><button type="button">源码</button><button type="button">全屏</button><button type="button">下载</button></div>
                              <svg class="unified-artifact-card__native-svg" viewBox="0 0 640 320" style="aspect-ratio: 640 / 320" role="img" aria-label="内嵌架构图"><rect x="4" y="4" width="632" height="312" rx="12" fill="none" stroke="#cbd5e1"/></svg>
                            </div>
                          </section>
                          <section class="message-component stream-ui stream-ui--document" data-ui-component="document" id="composable-stream-ui">
                            <div class="rich-doc rich-doc__flow">
                              <div class="rich-doc__node"><h2>修改前后的布局对比</h2></div>
                              <div class="rich-doc__node">
                                <section class="rich-doc__layout rich-doc__columns" style="--rich-columns: 2">
                                  <div class="rich-doc__node" id="compare-col-left">
                                    <section class="rich-doc__layout rich-doc__card">
                                      <h3 class="rich-doc__section-title">修改前</h3>
                                      <div class="rich-doc__node">
                                        <figure class="rich-doc__illustration rich-svg-figure" data-display-mode="inline">
                                          <div class="rich-doc__illustration-stage">
                                            <div class="rich-svg-figure__floating-actions" role="toolbar"><button type="button">源码</button><button type="button">全屏</button><button type="button">下载</button></div>
                                            <svg id="doc-svg-left" viewBox="0 0 320 180" style="aspect-ratio: 320 / 180" role="img" aria-label="修改前"><rect x="4" y="4" width="312" height="172" rx="8" fill="#ffffff" stroke="#d1d5db"/></svg>
                                          </div>
                                        </figure>
                                      </div>
                                      <div class="rich-doc__node"><p class="rich-doc__caption">卡片过窄，两侧空间浪费。</p></div>
                                    </section>
                                  </div>
                                  <div class="rich-doc__node" id="compare-col-right">
                                    <section class="rich-doc__layout rich-doc__card">
                                      <h3 class="rich-doc__section-title">修改后</h3>
                                      <div class="rich-doc__node">
                                        <figure class="rich-doc__illustration rich-svg-figure" data-display-mode="inline">
                                          <div class="rich-doc__illustration-stage">
                                            <div class="rich-svg-figure__floating-actions" role="toolbar"><button type="button">源码</button><button type="button">全屏</button><button type="button">下载</button></div>
                                            <svg id="doc-svg-right" viewBox="0 0 320 180" style="aspect-ratio: 320 / 180" role="img" aria-label="修改后"><rect x="4" y="4" width="312" height="172" rx="8" fill="#ffffff" stroke="#d1d5db"/></svg>
                                          </div>
                                        </figure>
                                      </div>
                                      <div class="rich-doc__node"><p class="rich-doc__caption">充分利用宽度，统一表单边界。</p></div>
                                    </section>
                                  </div>
                                </section>
                              </div>
                            </div>
                          </section>
                          <section class="message-component stream-ui" data-presentation="canvas" id="stream-ui-card">
                            <header class="stream-ui__heading"><div class="stream-ui__heading-copy"><h3 class="stream-ui__title">Stream UI 卡片</h3></div></header>
                            <div class="stream-ui__table-scroll" id="stream-ui-table-scroll"><table><thead><tr>${wideColumns}</tr></thead><tbody><tr>${wideCells}</tr></tbody></table></div>
                          </section>
                          <section class="unified-artifact-card unified-artifact-card--artifact unified-artifact-card--wide" data-display-mode="artifact" id="wide-artifact" style="height:120px">
                            <header class="unified-artifact-card__header"><strong class="unified-artifact-card__title">deliverable.svg</strong></header>
                            <div class="unified-artifact-card__stage">独立产物卡</div>
                          </section>
                        </div>
                      </div>
                    </div>
                  </div>
                  <div data-retry-details-block="true" id="retry-details">自动重试详情</div>
                  <div data-turn-error-kind="timeout" id="turn-error">错误提示</div>
                </div>
                </div>
                <section id="suggestions" class="chat-follow-up-prompts">推荐问题</section>
              </div>
            </div>
            <div class="chat-composer-dock"><div class="chat-scroll-button-slot"><button class="chat-scroll-to-bottom">↓</button></div>
              <div id="composer" class="chat-composer" style="height:120px;width:100%">输入框</div></div>
          </div>
        </main>
        <div class="workspace-resizer"></div>
        <aside class="context-rail"></aside>
      </div>`)
    // Mirror ConversationContent's scrollbar-gutter sync onto .chat-canvas-page.
    await page.evaluate(() => {
      const scroller = document.querySelector('#viewport')
      const canvas = document.querySelector('.chat-canvas-page')
      const gutter = Math.max(0, (scroller.offsetWidth - scroller.clientWidth) / 2)
      canvas.style.setProperty('--chat-scroll-gutter', `${gutter}px`)
    })
    await page.locator('#inline-svg').scrollIntoViewIfNeeded()
    const inlineSvgBeforeHover = await page.locator('#inline-svg').boundingBox()
    const floatingOpacityBefore = await page.locator('#inline-svg .unified-artifact-card__floating-actions').evaluate((el) => getComputedStyle(el).opacity)
    await page.locator('#inline-svg').hover()
    const inlineSvgDuringHover = await page.locator('#inline-svg').boundingBox()
    assert(inlineSvgBeforeHover && inlineSvgDuringHover)
    assert.equal(floatingOpacityBefore, '0', `Floating toolbar should be hidden before hover at ${JSON.stringify(scenario)}`)
    assert(
      Math.abs(inlineSvgBeforeHover.x - inlineSvgDuringHover.x) < 0.5 &&
      Math.abs(inlineSvgBeforeHover.y - inlineSvgDuringHover.y) < 0.5 &&
      Math.abs(inlineSvgBeforeHover.width - inlineSvgDuringHover.width) < 0.5 &&
      Math.abs(inlineSvgBeforeHover.height - inlineSvgDuringHover.height) < 0.5,
      `Hovering inline SVG caused layout shift (${JSON.stringify({ inlineSvgBeforeHover, inlineSvgDuringHover })}) at ${JSON.stringify(scenario)}`,
    )
    const geometry = await page.evaluate(() => {
      const viewport = document.querySelector('#viewport')
      viewport.scrollTop = viewport.scrollHeight
      const box = (s) => document.querySelector(s).getBoundingClientRect()
      const dock = document.querySelector('.chat-composer-dock')
      const prose = box('#prose-anchor')
      const userBubble = box('#user-bubble')
      const inlineSvgEl = document.querySelector('#inline-svg')
      const inlineSvgStyle = getComputedStyle(inlineSvgEl)
      const docSvgLeftStyle = getComputedStyle(document.querySelector('#doc-svg-left'))
      const blocks = {
        rail: box('.is-assistant [data-message-content]'),
        versionRow: box('.chat-message-version-row'),
        thinking: box('.thinking-chain'),
        answer: box('.message-answer-segment'),
        table: box('#md-table'),
        code: box('#md-code'),
        streamingCode: box('#streaming-code'),
        inlineSvg: box('#inline-svg'),
        composableStreamUi: box('#composable-stream-ui'),
        streamUi: box('#stream-ui-card'),
        artifact: box('#wide-artifact'),
        retryDetails: box('#retry-details'),
        turnError: box('#turn-error'),
        suggestions: box('#suggestions'),
        composerDock: box('.chat-composer-dock'),
        composer: box('#composer'),
      }
      const compareCols = {
        left: box('#compare-col-left'),
        right: box('#compare-col-right'),
        docWidth: box('#composable-stream-ui .rich-doc').width,
      }
      const inlineSvgMeta = {
        bg: inlineSvgStyle.backgroundColor,
        borderTopWidth: inlineSvgStyle.borderTopWidth,
        docSvgBg: docSvgLeftStyle.backgroundColor,
        svgBox: box('#inline-svg svg'),
      }
      const image = box('#md-img')
      const tableScroll = document.querySelector('#md-table-scroll')
      const codeScroll = document.querySelector('#md-code-scroll')
      const streamUiScroll = document.querySelector('#stream-ui-table-scroll')
      const codePre = codeScroll.querySelector('pre')
      const result = {
        position: getComputedStyle(dock).position,
        dock: box('.chat-composer-dock').top, bottom: box('#viewport').bottom,
        suggestionsBottom: blocks.suggestions.bottom, button: box('.chat-scroll-to-bottom').top,
        slotBottom: box('.chat-scroll-button-slot').bottom, composer: box('#composer').top,
        prose, userBubble, blocks, compareCols, inlineSvgMeta, image,
        tableScroll: { scrollWidth: tableScroll.scrollWidth, clientWidth: tableScroll.clientWidth, overflowX: getComputedStyle(tableScroll).overflowX },
        codeScroll: {
          scrollWidth: Math.max(codeScroll.scrollWidth, codePre?.scrollWidth ?? 0),
          clientWidth: Math.min(codeScroll.clientWidth, codePre?.clientWidth ?? codeScroll.clientWidth),
        },
        streamUiScroll: { scrollWidth: streamUiScroll.scrollWidth, clientWidth: streamUiScroll.clientWidth },
        overflow: viewport.scrollWidth - viewport.clientWidth,
      }
      dock.classList.add('is-enlarged')
      result.enlargedPosition = getComputedStyle(dock).position
      result.enlargedDock = box('.chat-composer-dock')
      dock.classList.remove('is-enlarged')
      // Simulate stream completion: mounting MessageActions after [data-message-content]
      // must not shift the rail or any child block horizontally.
      const actions = document.createElement('div')
      actions.className = 'flex items-center gap-1'
      actions.id = 'mounted-actions'
      actions.innerHTML = '<button type="button">复制</button>'
      document.querySelector('.is-assistant').appendChild(actions)
      result.afterComplete = {
        rail: box('.is-assistant [data-message-content]'),
        prose: box('#prose-anchor'),
        table: box('#md-table'),
        actions: box('#mounted-actions'),
        composer: box('#composer'),
      }
      return result
    })
    assert.equal(geometry.position, 'relative')
    assert.equal(geometry.enlargedPosition, 'absolute')
    assert(
      Math.abs(geometry.enlargedDock.left - geometry.prose.left) < 1 &&
      Math.abs(geometry.enlargedDock.right - geometry.prose.right) < 1,
      `Enlarged composer [${geometry.enlargedDock.left.toFixed(1)}, ${geometry.enlargedDock.right.toFixed(1)}] != prose [${geometry.prose.left.toFixed(1)}, ${geometry.prose.right.toFixed(1)}] at ${JSON.stringify(scenario)}`,
    )
    assert(
      Math.abs(geometry.userBubble.right - geometry.prose.right) < 1 &&
      geometry.userBubble.left >= geometry.prose.left - 1 &&
      geometry.userBubble.width < geometry.prose.width,
      `User bubble [${geometry.userBubble.left.toFixed(1)}, ${geometry.userBubble.right.toFixed(1)}] should be content-sized and right-aligned to prose [${geometry.prose.left.toFixed(1)}, ${geometry.prose.right.toFixed(1)}] at ${JSON.stringify(scenario)}`,
    )
    assert(geometry.bottom <= geometry.dock + 1 && geometry.suggestionsBottom <= geometry.button + 1 && geometry.slotBottom <= geometry.composer + 1)
    assert(geometry.overflow <= 1, `Viewport overflowed by ${geometry.overflow}px at ${JSON.stringify(scenario)}`)
    assert(geometry.tableScroll.scrollWidth > geometry.tableScroll.clientWidth, `Wide markdown table should scroll internally (${JSON.stringify(geometry.tableScroll)}) at ${JSON.stringify(scenario)}`)
    assert(geometry.codeScroll.scrollWidth > geometry.codeScroll.clientWidth, `Wide code block should scroll internally (${JSON.stringify(geometry.codeScroll)}) at ${JSON.stringify(scenario)}`)
    assert(geometry.streamUiScroll.scrollWidth > geometry.streamUiScroll.clientWidth, `Wide Stream UI table should scroll internally (${JSON.stringify(geometry.streamUiScroll)}) at ${JSON.stringify(scenario)}`)
    for (const [name, bounds] of Object.entries(geometry.blocks)) {
      assert(
        Math.abs(bounds.left - geometry.prose.left) < 1 && Math.abs(bounds.right - geometry.prose.right) < 1,
        `${name} [${bounds.left.toFixed(1)}, ${bounds.right.toFixed(1)}] != prose [${geometry.prose.left.toFixed(1)}, ${geometry.prose.right.toFixed(1)}] at ${JSON.stringify(scenario)}`,
      )
    }
    for (const [name, bounds] of Object.entries(geometry.afterComplete)) {
      assert(
        Math.abs(bounds.left - geometry.prose.left) < 1 && Math.abs(bounds.right - geometry.prose.right) < 1,
        `Post-stream ${name} shifted [${bounds.left.toFixed(1)}, ${bounds.right.toFixed(1)}] != [${geometry.prose.left.toFixed(1)}, ${geometry.prose.right.toFixed(1)}] at ${JSON.stringify(scenario)}`,
      )
    }
    assert(
      geometry.image.left >= geometry.prose.left - 1 && geometry.image.right <= geometry.prose.right + 1,
      `Image [${geometry.image.left}, ${geometry.image.right}] escaped prose [${geometry.prose.left}, ${geometry.prose.right}] at ${JSON.stringify(scenario)}`,
    )
    assert(Math.abs(geometry.image.width / geometry.image.height - 2) < 0.05, `Image aspect ratio distorted at ${JSON.stringify(scenario)}`)
    assert.equal(geometry.inlineSvgMeta.bg, 'rgba(0, 0, 0, 0)', `Inline SVG outer background must be transparent at ${JSON.stringify(scenario)}`)
    assert.equal(geometry.inlineSvgMeta.borderTopWidth, '0px', `Inline SVG must have no outer border at ${JSON.stringify(scenario)}`)
    assert.equal(geometry.inlineSvgMeta.docSvgBg, 'rgba(0, 0, 0, 0)', `Stream UI inline SVG must be transparent at ${JSON.stringify(scenario)}`)
    assert(Math.abs(geometry.inlineSvgMeta.svgBox.width / geometry.inlineSvgMeta.svgBox.height - 2) < 0.05, `Inline SVG viewBox aspect ratio distorted at ${JSON.stringify(scenario)}`)
    if (geometry.compareCols.docWidth > 560) {
      assert(geometry.compareCols.right.left >= geometry.compareCols.left.right - 1, `Two-column Stream UI should sit side-by-side at width ${geometry.compareCols.docWidth} (${JSON.stringify(scenario)})`)
    } else {
      assert(geometry.compareCols.right.top >= geometry.compareCols.left.bottom - 1, `Two-column Stream UI should degrade to single column at narrow width ${geometry.compareCols.docWidth} (${JSON.stringify(scenario)})`)
    }
    checks++
  }
  const reply = (id, withOutline) => `<article class="is-assistant" id="${id}"><div data-message-content class="message-reading-surface">
    <div class="message-reading">
      ${withOutline ? `<div class="message-outline-track"><nav class="message-outline"><button class="message-outline__trigger">目录 <span>⌄</span></button></nav></div>` : ''}
      <div class="message-reading__process"><div class="thinking-chain"><button class="thinking-chain__trigger">思考了 28s</button></div></div>
      <div class="message-reading__body"><div class="message-answer-segment answer-flow">
        <div class="message-text-block"><div data-message-selectable-text><div class="answer-markdown-flow"><p>正文第一段。</p><h2>章节</h2><p>正文第二段。</p></div></div></div>
        <div style="height:680px">长回复末段</div>
      </div></div>
    </div></div></article>`
  for (const width of [1440, 720, 390]) {
    await page.setViewportSize({ width, height: 660 })
    await page.setContent(`<style>${css.join('\n')}\n${readingCss}</style>
      <main class="workspace-main--chat"><div class="chat-canvas-page" style="height:610px;display:flex;flex-direction:column">
        <div data-conversation-scroll style="flex:1 1 0;min-height:0;overflow:auto;scrollbar-gutter:stable both-edges">
          <div class="chat-messages-content chat-outline-lane" style="display:flex;flex-direction:column;gap:24px;padding-block:60px">
            ${reply('first', true)}<div style="height:64px">短回复</div>${reply('second', true)}
          </div>
        </div>
      </div></main>`)
    const reading = await page.evaluate(() => {
      const scroller = document.querySelector('[data-conversation-scroll]')
      const first = document.querySelector('#first .message-reading')
      const track = first.querySelector('.message-outline-track')
      const trigger = track.querySelector('button')
      const second = document.querySelector('#second .message-outline__trigger')
      const firstBlock = first.querySelector('.message-text-block')
      const paragraph = firstBlock.querySelector('p')
      const heading = firstBlock.querySelector('h2')
      const box = (element) => element.getBoundingClientRect()
      const before = box(firstBlock).top
      const initialTrigger = box(trigger)
      const thinking = box(first.querySelector('.thinking-chain__trigger'))
      const sectionGap = box(heading).top - box(paragraph).bottom
      const bodyGap = before - box(first.querySelector('.message-reading__process')).bottom
      track.hidden = true
      const after = box(firstBlock).top
      track.hidden = false
      const viewport = box(scroller)
      scroller.scrollTop = 250
      const sticky = box(trigger)
      const answerRight = box(first.querySelector('.message-answer-segment')).right
      scroller.scrollTop += box(first).bottom - viewport.top + 16
      const exited = box(trigger).bottom <= viewport.top
      const nextUnstuck = box(second).top > viewport.top + 12
      scroller.scrollTop += box(second).top - viewport.top + 40
      const nextSticky = box(second).top - viewport.top
      return { before, after, sticky: sticky.top - viewport.top, noOverlap: sticky.left >= answerRight, inPane: sticky.right <= viewport.right,
        inline: Math.abs((initialTrigger.top + initialTrigger.bottom - thinking.top - thinking.bottom) / 2),
        exited, nextUnstuck, nextSticky, sectionGap, bodyGap, overflow: scroller.scrollWidth - scroller.clientWidth }
    })
    assert.equal(reading.before, reading.after, `Outline shifts the body at ${width}px`)
    assert(reading.inline < 8 && reading.noOverlap && reading.inPane && reading.overflow <= 1, JSON.stringify({ width, reading }))
    assert.equal(reading.sticky, 12)
    assert(reading.exited && reading.nextUnstuck, JSON.stringify({ width, reading }))
    assert.equal(reading.nextSticky, 12)
    assert.equal(reading.bodyGap, 12)
    assert.equal(reading.sectionGap, 24)
    checks++
  }
  console.log(`PASS: ${checks} browser layout cases; unified assistant content rail, tables, code blocks, Stream UI, images, dock spacing and message outlines verified.`)
} finally {
  await browser.close()
}
