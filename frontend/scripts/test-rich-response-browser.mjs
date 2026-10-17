import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { join } from "node:path";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
const require = createRequire(import.meta.url);
let pw;
try { pw = require("playwright"); } catch { pw = require(join(process.env.APPDATA, "npm/node_modules/@playwright/cli/node_modules/playwright")); }
const browser = await pw.chromium.launch({headless:true, executablePath: join(process.env.LOCALAPPDATA, "ms-playwright/chromium-1243/chrome-win64/chrome.exe")});
const evidence = { checks: [], errors: [] };
try {
  const page = await browser.newPage({viewport:{width:1440,height:1160}, reducedMotion:"reduce"});
  page.on("pageerror", error => evidence.errors.push(error.message));
  await page.goto(process.env.RICH_RESPONSE_URL || "http://127.0.0.1:5173/rich-response-preview.html");
  const after = page.locator('[data-preview="after"]');
  await after.getByRole("slider", {name:"追赶速度"}).waitFor();
  await after.getByText("结论：", {exact:false}).waitFor();
  await page.screenshot({path:"../docs/rich-response-light.png",fullPage:true});
  evidence.checks.push("actual message renderer and nested simulation render");
  await page.getByRole("button", {name:"回放流式生成"}).click();
  await after.getByText("正在组织内容…").waitFor();
  const slider = after.getByRole("slider", {name:"追赶速度"});
  await slider.waitFor();
  await slider.focus(); await slider.press("End");
  assert.equal(await slider.inputValue(), "5");
  await page.getByRole("button", {name:"完整内容", exact:true}).click();
  await after.getByText("每一步的时间逐渐变短").waitFor();
  assert.equal(await slider.inputValue(), "5");
  evidence.checks.push("local slider state persists while new blocks arrive");
  await after.getByRole("tab", {name:"计算",exact:true}).click();
  assert.equal(await after.getByRole("tab", {name:"计算",exact:true}).getAttribute("aria-selected"),"true");
  assert(await after.getByRole("tabpanel").innerText(), "selected tab has content");
  evidence.checks.push("native accessible tabs work");
  await page.getByRole("button", {name:"深色",exact:true}).click();
  await page.screenshot({path:"../docs/rich-response-dark.png",fullPage:true});
  await page.setViewportSize({width:390,height:844});
  const overflow = await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
  assert(overflow.scroll <= overflow.width, JSON.stringify(overflow));
  const columns = await after.locator(".rich-doc__columns").evaluate(element=>getComputedStyle(element).gridTemplateColumns);
  assert(!columns.includes(" "), `mobile columns must collapse: ${columns}`);
  await page.screenshot({path:"../docs/rich-response-mobile.png",fullPage:true});
  evidence.checks.push("390px dark layout has no horizontal overflow and columns collapse");
  let realReport;
  try { realReport = JSON.parse(await readFile("../backend/test-results/rich-response/real-model.json", "utf8")); } catch { /* optional real-provider evidence */ }
  if (realReport) {
    await page.setViewportSize({width:1080,height:1200});
    await page.emulateMedia({colorScheme:"light"});
    await page.evaluate(() => document.documentElement.classList.remove("dark"));
    const sample = realReport.cases.find(item=>item.name==="fast-rich");
    await page.evaluate(async sample=> {
      const {renderVerifiedResponse} = await import("/src/dev/rich-response-preview.tsx");
      renderVerifiedResponse(sample.message.parts, "DeepSeek · 芝诺悖论 · 真实生成结果");
    }, sample);
    await page.getByRole("slider",{name:"追赶速度"}).waitFor();
    await page.screenshot({path:"../docs/rich-response-real.png",fullPage:true});
    evidence.checks.push("actual real-model saved document renders with a local simulation");
    await page.setViewportSize({width:390,height:844});
    const realOverflow = await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
    assert(realOverflow.scroll <= realOverflow.width, `real model document overflow: ${JSON.stringify(realOverflow)}`);
    evidence.checks.push("real model document including long formulas fits 390px");
  }
  if (process.env.RICH_RESPONSE_PRODUCTION_URL) {
    const asset = (await readdir("dist/assets")).find(name=>/^rich-document-.*\.js$/.test(name));
    assert(asset, "production document chunk exists");
    const production = await browser.newPage();
    production.on("pageerror",error=>evidence.errors.push(error.message));
    await production.goto(`${process.env.RICH_RESPONSE_PRODUCTION_URL}/api/v1/health`);
    const exports = await production.evaluate(async asset=>Object.keys(await import(`/assets/${asset}`)), asset);
    assert(exports.length, "production module imports successfully");
    evidence.checks.push("production rich-document chunk and dependencies initialize without module errors");
    await production.close();
  }
  assert.deepEqual(evidence.errors,[]);
  console.log(JSON.stringify(evidence,null,2));
  await mkdir("../backend/test-results/rich-response",{recursive:true});
  await writeFile("../backend/test-results/rich-response/browser.json",JSON.stringify(evidence,null,2));
} finally { await browser.close(); }
