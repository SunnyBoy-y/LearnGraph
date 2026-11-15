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
// display:contents wrapper, completed <details>, and in-flight stats sibling.
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
<p>正文保持当前阅读宽度，用于检查思考状态、生成详情、消息操作与后续问题的边缘。</p></div></div></div>
<details class="message-stream-details"><summary>生成详情</summary><div class="message-stream-stats"><span>总耗时 50.9s</span><span>43 tok/s</span></div></details>
<div class="flex items-center gap-1" id="answer-actions"><button>复制</button><button>重试</button><button>更多</button></div></div>
<section class="chat-follow-up-prompts"><div class="chat-follow-up-prompts__label">接下来可以问</div>
<div class="chat-suggestions"><div class="chat-empty-state__prompts"><button class="chat-empty-state__prompt">能不能帮我设计一套具体的测试题，并说明每道题的区别？</button></div></div></section>
<div class="is-assistant group flex flex-col gap-2 max-w-none" data-streaming="true" id="streaming-message">
<div class="w-full min-w-0 max-w-full flex flex-col gap-1 overflow-hidden text-sm" data-message-content><div class="contents">
<div class="space-y-2"><div class="thinking-chain"><button class="thinking-chain__trigger">正在思考</button></div></div>
<div class="message-answer-segment"><p>流式回答正文</p></div></div></div>
<div class="message-stream-stats message-stream-stats--loading"><span>正在生成</span></div></div>
</div></section></main><div class="workspace-resizer"></div><aside class="context-rail"></aside></div></body></html>`
const selectors = ['.thinking-chain', '.message-stream-details', '#answer-actions', '.chat-follow-up-prompts', '#streaming-message > .message-stream-stats']
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
        await page.locator('.message-stream-details > summary').click()
        const expandedStats = await page.locator('.message-stream-details .message-stream-stats').boundingBox()
        assert(expandedStats && Math.abs(expandedStats.x - answer.x) < 1 && Math.abs(expandedStats.width - answer.width) < 1, 'Expanded stats must not receive a second inset')
        await page.locator('.message-stream-details > summary').click()
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
  console.log(`PASS: ${checks} browser layout cases; all answer chrome aligned within 1px, prose unchanged.`)
} finally {
  await browser.close()
}
