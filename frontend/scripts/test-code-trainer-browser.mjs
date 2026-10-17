import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { join } from "node:path";
import { mkdir } from "node:fs/promises";
const require = createRequire(import.meta.url);
let pw;
try { pw = require("playwright"); } catch { pw = require(join(process.env.APPDATA, "npm/node_modules/@playwright/cli/node_modules/playwright")); }
const browser = await pw.chromium.launch({ headless: true, executablePath: process.env.CODE_TRAINER_CHROMIUM || join(process.env.LOCALAPPDATA, "ms-playwright/chromium-1243/chrome-win64/chrome.exe") });
let passed = 0;
try {
  const page = await browser.newPage({ viewport: { width: 1180, height: 1100 } });
  const failures = [];
  let currentCheck = "initial render";
  page.on("pageerror", error => { failures.push({ check: currentCheck, message: error.message, stack: error.stack }); console.error("PAGE ERROR", currentCheck, error); });
  await page.goto(process.env.CODE_TRAINER_URL || "http://127.0.0.1:5203/code-trainer.html");
  await page.getByRole("textbox", { name: "代码编辑器" }).waitFor();
  async function check(name, language, code, stdin, status, stdout, cancelAfter) {
    currentCheck = name;
    const result = await page.evaluate(async ({ language, code, stdin, cancelAfter }) => {
      const { runCode } = await import("/src/lib/code-trainer-runner.ts");
      const abort = new AbortController();
      if (cancelAfter) setTimeout(() => abort.abort(), cancelAfter);
      const result = await runCode({ language, code, stdin, timeLimitMs: 2000 }, abort.signal);
      return { ...result, framesLeft: document.querySelectorAll('iframe[title="隔离的编程执行环境"]').length };
    }, { language, code, stdin, cancelAfter });
    assert.equal(result.status, status, `${name}: ${JSON.stringify(result)}`);
    if (stdout !== undefined) assert.equal(result.stdout, stdout, name);
    assert.equal(result.framesLeft, 0, "execution frame must be destroyed");
    console.log(`PASS ${++passed} ${name}`);
  }
  await check("JavaScript stdin + stdout", "javascript", "console.log(stdin.trim().split(/\\s+/).map(Number).reduce((a,b)=>a+b,0))", "2 3", "completed", "5\n");
  await check("JavaScript denies host capabilities", "javascript", "console.log(typeof fetch, typeof document, typeof localStorage, typeof Worker, typeof process, typeof require)", "", "completed", "undefined undefined undefined undefined undefined undefined\n");
  await check("JavaScript syntax error", "javascript", "const = ;", "", "compile_error");
  await check("JavaScript runtime error", "javascript", "throw Error('test error')", "", "runtime_error");
  await check("JavaScript infinite loop", "javascript", "while(true){}", "", "time_limit");
  await check("JavaScript memory cap", "javascript", "console.log('x'.repeat(128 * 1024 * 1024))", "", "memory_limit");
  await check("JavaScript output cap", "javascript", "while(true) console.log('x'.repeat(1000))", "", "output_limit");
  await check("JavaScript promises drain", "javascript", "Promise.resolve(7).then(console.log)", "", "completed", "7\n");
  await check("TypeScript transpile + execute", "typescript", "const n: number = Number(readline()); console.log(n * 2)", "21", "completed", "42\n");
  await check("TypeScript transform error", "typescript", "const x: = 1", "", "compile_error");
  await check("Python sys.stdin and exact partial stdout", "python", "import sys\na,b=map(int,sys.stdin.read().split())\nsys.stdout.write(str(a+b))", "2 3", "completed", "5");
  await check("Python input EOF", "python", "print(input()); print(input())", "你好\n世界", "completed", "你好\n世界\n");
  await check("Python exception", "python", "raise ValueError('test error')", "", "runtime_error");
  await check("Python infinite loop", "python", "while True: pass", "", "time_limit");
  await check("Explicit cancel", "python", "while True: pass", "", "cancelled", undefined, 100);
  await check("Fresh Python state", "python", "print('secret' in globals())", "", "completed", "False\n");
  await check("Python cannot access app globals", "python", "from js import document", "", "runtime_error");
  await check("HTML strings stay inert", "javascript", "console.log('</script><script>parent.__trainerEscaped=true</script>')", "", "completed", "</script><script>parent.__trainerEscaped=true</script>\n");
  assert.equal(await page.evaluate(() => window.__trainerEscaped), undefined);
  // Verify CSP at the browser boundary, including attempts through Python's JS FFI.
  const requests = [];
  // Chromium emits request events even for CSP-blocked fetches. Route interception
  // is downstream of CSP and would run only if the request reached the network layer.
  await page.route("**/*trainer-beacon*", async route => { requests.push(route.request().url()); await route.abort(); });
  await check("Python FFI cannot evaluate host scripts", "python", "from pyodide.code import run_js\nrun_js('globalThis.fetch(\"https://example.com/trainer-beacon\")')", "", "runtime_error");
  currentCheck = "opaque worker CSP probe";
  const boundary = await page.evaluate(async () => {
    const { runnerDocument } = await import("/src/lib/code-trainer-runner.ts");
    const frame = document.createElement("iframe");
    frame.setAttribute("sandbox", "allow-scripts");
    const channel = crypto.randomUUID();
    return await new Promise((resolve, reject) => {
      const timer = setTimeout(() => { frame.remove(); reject(new Error("CSP probe timed out")); }, 5000);
      const receive = event => {
        if (event.source !== frame.contentWindow || event.data?.channel !== channel) return;
        if (event.data.ready) frame.contentWindow.postMessage({ channel, request: {}, source: `onmessage=async()=>{let xhr='escaped',net='escaped',storage='escaped';try{let r=new XMLHttpRequest();r.open('GET','https://example.com/trainer-beacon',false);r.send()}catch(e){xhr='blocked'}try{await fetch('https://example.com/trainer-beacon')}catch(e){net='blocked'}try{indexedDB.open('trainer-test')}catch(e){storage='blocked'}postMessage({xhr,net,storage});}` }, "*");
        else { clearTimeout(timer); window.removeEventListener("message", receive); frame.remove(); resolve(event.data.result); }
      };
      window.addEventListener("message", receive);
      frame.srcdoc = runnerDocument(channel); document.body.append(frame);
    });
  });
  assert.deepEqual(boundary, { xhr: "blocked", net: "blocked", storage: "blocked" });
  console.log(`PASS ${++passed} opaque worker CSP denies XHR, fetch and storage`);
  assert.deepEqual(requests, []);
  currentCheck = "UI public tests";
  await page.getByRole("button", { name: "测试公开样例" }).click();
  await page.getByText("已通过 3 / 3 个公开样例").waitFor({ timeout: 45000 });
  await page.waitForFunction(() => !document.querySelector('.code-trainer__actions button')?.hasAttribute('disabled'));
  console.log(`PASS ${++passed} UI runs all public tests`);
  const output = new URL("../../docs/", import.meta.url);
  await mkdir(output, { recursive: true });
  await page.screenshot({ path: new URL("code-trainer-light.png", output).pathname.replace(/^\/(.:\/)/, "$1"), fullPage: true });
  await page.getByRole("button", { name: "切换主题" }).click();
  await page.waitForTimeout(400); // Let the application's theme color transitions settle for visual QA.
  await page.screenshot({ path: new URL("code-trainer-dark.png", output).pathname.replace(/^\/(.:\/)/, "$1"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 900 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= 390), "mobile page should not overflow horizontally");
  console.log(`PASS ${++passed} mobile layout has no horizontal overflow`);
  await page.getByRole("combobox", { name: "编程语言" }).click();
  await page.getByRole("option", { name: "Java · AI", exact: true }).click();
  assert.equal(await page.getByRole("button", { name: "运行", exact: true }).count(), 0);
  await page.getByRole("textbox", { name: "代码编辑器", exact: true }).focus();
  await page.keyboard.press("Control+a");
  await page.keyboard.insertText("class Main { public static void main(String[] args) { System.out.println(5); } }");
  await page.getByRole("button", { name: "交给 AI 分析" }).click();
  await page.getByText("待发送给对话模型的 Prompt", { exact: false }).waitFor();
  assert.match(await page.locator("main>section").last().innerText(), /此语言没有真实执行/);
  console.log(`PASS ${++passed} Java falls back to code prompt only`);
  assert.deepEqual(failures, [], "no uncaught page errors");
  console.log(`${passed} real Chromium checks passed`);
} finally { await browser.close(); }
