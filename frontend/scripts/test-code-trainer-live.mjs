import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { join } from "node:path";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
const require = createRequire(import.meta.url);
let pw;
try { pw = require("playwright"); } catch { pw = require(join(process.env.APPDATA, "npm/node_modules/@playwright/cli/node_modules/playwright")); }
const origin = process.env.CODE_TRAINER_LIVE_URL || "http://127.0.0.1:5207";
const evidence = fileURLToPath(new URL("../../backend/test-results/code-trainer-live/", import.meta.url));
await mkdir(evidence, { recursive: true });
const browser = await pw.chromium.launch({ headless: true, executablePath: process.env.CODE_TRAINER_CHROMIUM || join(process.env.LOCALAPPDATA, "ms-playwright/chromium-1243/chrome-win64/chrome.exe") });
const report = { verified_at: new Date().toISOString(), origin, checks: [], requests: [], errors: [] };
let activePage;
try {
  const context = await browser.newContext({ viewport: { width: 1500, height: 1120 }, permissions: ['clipboard-read','clipboard-write'] });
  const loginResponse = await context.request.post(`${origin}/api/v1/auth/demo-login`, { data: {} });
  assert.equal(loginResponse.status(), 200, "configured demo login must succeed");
  const login = await loginResponse.json();
  let workspace = login.default_workspace_id;
  const headers = { Authorization: `Bearer ${login.access_token}`, "X-Workspace-Id": workspace };
  const api = async (path, method = "get", data) => {
    const response = await context.request[method](`${origin}/api/v1${path}`, { headers, data });
    assert.ok(response.ok(), `${method} ${path} returned ${response.status()}`);
    return response.json();
  };
  let providers = await api("/providers");
  const pickProvider = items => items.find(item => item.display_name === "DeepSeek1") ?? items.find(item => item.enabled && /deepseek/i.test(item.display_name) && ["deepseek_chat", "openai_compatible_chat"].includes(item.provider_type));
  let provider = pickProvider(providers);
  if (!provider) {
    for (const item of await api("/workspaces")) {
      headers["X-Workspace-Id"] = item.id;
      providers = await api("/providers");
      provider = pickProvider(providers);
      if (provider) { workspace = item.id; break; }
    }
  }
  console.log("AVAILABLE MODEL PROVIDERS", JSON.stringify(providers.filter(item => /deepseek|openai|anthropic/.test(item.provider_type)).map(item => ({name:item.display_name,type:item.provider_type}))));
  assert.ok(provider, "an existing real provider must be configured");
  report.provider = { id: provider.id, name: provider.display_name, provider_type: provider.provider_type };
  console.log("REAL PROVIDER", JSON.stringify(report.provider));
  const session = process.env.CODE_TRAINER_SESSION ? {id: process.env.CODE_TRAINER_SESSION} : await api("/sessions", "post", { title: "编程训练器 · 真实模型完整验收", memory_enabled: false });
  report.session_id = session.id;
  report.workspace_id = workspace;
  const modelResponse = await api(`/providers/${provider.id}/models`);
  console.log("MODEL CATALOG", JSON.stringify(modelResponse.models.map(item => item.id)));
  const model = "deepseek-flash";
  report.model_id = model;
  await context.addInitScript(({ login, workspace, sessionId, providerId, model }) => {
    if (window.top !== window) return;
    const values = { "learngraph.access_token": login.access_token, "learngraph.workspace_id": workspace, "learngraph.user_id": login.user_id, "learngraph.username": login.username, "learngraph.display_name": login.display_name, "learngraph.session_id": login.session_id, "learngraph.must_change_password": String(login.must_change_password) };
    for (const [key, value] of Object.entries(values)) { localStorage.setItem(key, value); sessionStorage.setItem(key, value); }
    localStorage.setItem("learngraph:session-composer-prefs", JSON.stringify({ [sessionId]: { responseMode: "agentic", thinkingMode: "off", searchRoute: "disabled", generationMode: "text", providerId, modelId: model } }));
  }, { login, workspace, sessionId: session.id, providerId: provider.id, model });
  const page = await context.newPage();
  activePage = page;
  await page.addInitScript(() => {
    if (window.top !== window) return;
    const realFetch = window.fetch.bind(window);
    window.__trainerStreams = [];
    window.fetch = async (...args) => {
      const response = await realFetch(...args);
      if (String(args[0]).includes('/messages/stream')) void response.clone().text().then(text => window.__trainerStreams.push(text));
      return response;
    };
  });
  page.on("pageerror", error => { report.errors.push(error.message); console.log("PAGE ERROR", error.message); });
  page.on("console", message => { if (message.type() === 'error') console.log('BROWSER CONSOLE', message.text().slice(0,1200)); });
  page.on("request", request => {
    if (request.method() === "POST" && /\/messages(?:\/stream)?$/.test(new URL(request.url()).pathname)) {
      try { const data = request.postDataJSON(); report.requests.push({ url: new URL(request.url()).pathname, ...data }); console.log("CHAT REQUEST", JSON.stringify({ agent_mode: data.agent_mode, model: data.model_id, content_length: data.content?.length })); } catch {}
    }
  });
  await page.goto(`${origin}/w/${workspace}/chat/${session.id}`);
  await page.getByLabel("输入消息", { exact: true }).waitFor({ timeout: 30000 }).catch(async error => {
    await writeFile(join(evidence, "initial-failure.txt"), `${page.url()}\n${await page.locator("body").innerText()}`);
    await page.screenshot({path:join(evidence,"initial-failure.png"),fullPage:true}); throw error;
  });
  await writeFile(join(evidence, "initial-page.txt"), await page.locator("body").innerText());
  await page.screenshot({ path: join(evidence, "initial-page.png"), fullPage: true });
  console.log("CHAT READY", page.url());
  const prompt = "请为我生成编程判题训练器。请调用 canvas_emit_trusted_component，component_type=code_trainer，schema_version=1.0，allowed_events=['submit']。连续生成四个控件，标题分别为 Python 输入求和、JavaScript 输入求和、TypeScript 输入求和、Java 输入求和。language 分别 python/javascript/typescript/java。每题都是读取标准输入中的所有空白分隔整数，输出总和，不带数字个数前缀。请给每个控件完整正确可运行的 starter_code；Python 用 sys.stdin.read()，JS/TS 仅使用全局 stdin 和 console.log，不使用 Node 模块。Java 用 Scanner 读至 EOF。每题包含三个公开测试：正数 '2 3' 输出 '5'，负数 '-5 0 2' 输出 '-3'，单数 '42' 输出 '42'，默认 stdin='2 3'，time_limit_ms=2000。description 简洁说明题目。不要使用终端、沙箱执行或联网，不要创建知识图谱；仅发布这四个原生可信控件，不要用 HTML 或 Markdown 代替。发布后简短说明 Java 仅由 AI 推演、公开测试不代表正式成绩。";
  if (process.env.CODE_TRAINER_SESSION) {
    const existing = await api(`/sessions/${session.id}/messages?compact=false`);
    console.log("EXISTING MESSAGES", JSON.stringify(existing.items.map(item => ({role:item.role,status:item.status,trainer_count:item.parts?.filter(part=>part.data?.component_type==='code_trainer').length}))));
  }
  if (!process.env.CODE_TRAINER_CONTINUE) {
  await page.getByLabel("输入消息", { exact: true }).fill(prompt);
  const responsePromise = page.waitForResponse(response => /\/messages\/stream$/.test(new URL(response.url()).pathname) && response.request().method() === "POST", { timeout: 30000 });
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  const response = await responsePromise;
  console.log("GENERATION STREAM", response.status());
  report.generation_stream_status = response.status();
  assert.equal(response.status(), 200);
  }
  const cards = page.locator("section.code-trainer");
  await cards.nth(3).waitFor({ timeout: 120000 });
  async function completedMessages() {
    for (let attempt=0; attempt<120; attempt++) {
      const data = await api(`/sessions/${session.id}/messages?compact=false`);
      const last = data.items.filter(item=>item.role==='assistant').at(-1);
      if (last && last.status === 'completed') return data;
      if (last && ['failed','cancelled'].includes(last.status)) throw new Error(`Assistant message ${last.status}: ${last.content?.slice(-300)}`);
      await page.waitForTimeout(1000);
    }
    throw new Error('Real model response did not complete within 120 seconds');
  }
  const messages = await completedMessages();
  await writeFile(join(evidence, "messages.json"), JSON.stringify(messages,null,2));
  await writeFile(join(evidence, "generation.sse"), await page.evaluate(() => window.__trainerStreams.join('\n')));
  console.log("GENERATED TRAINERS", await cards.count());
  report.checks.push({ name: "real model generated four trainer components", passed: true, count: await cards.count() });
  await writeFile(join(evidence, "generation-page.txt"), await page.locator("body").innerText());
  await page.screenshot({ path: join(evidence, "generated.png"), fullPage: true });
  report.browser_url = page.url();
  async function modelText(card) {
    const uri = await card.locator('.code-ide').getAttribute('data-model-uri');
    return page.evaluate(async uri=>{const resource=performance.getEntriesByType('resource').map(item=>item.name).find(url=>/\/assets\/code-trainer-monaco-[^/]+\.js/.test(url));const {monaco}=await import(resource??'/src/lib/code-trainer-monaco.ts');return monaco.editor.getModels().find(model=>model.uri.toString()===uri)?.getValue()},uri);
  }
  async function replaceCode(card, code) {
    const editor=card.getByRole('textbox',{name:'代码编辑器',exact:true});
    await editor.focus(); await page.keyboard.press('Control+a');
    if(code) { await page.evaluate(text=>navigator.clipboard.writeText(text),code); await page.keyboard.press('Control+v'); } else await page.keyboard.press('Backspace');
    await page.keyboard.press('Escape');
    for(let i=0;i<20&&await modelText(card)!==code;i++) await page.waitForTimeout(100);
    assert.equal(await modelText(card),code,'real editor model reflects keyboard edits');
  }
  if (!process.env.CODE_TRAINER_AI_ONLY) {
  for (let index=0;index<3;index++) {
    const card=cards.nth(index);
    await card.getByRole('textbox',{name:'代码编辑器',exact:true}).waitFor({timeout:60000});
    const title=await card.locator('header strong').innerText();
    await card.getByRole('textbox',{name:'标准输入',exact:true}).fill('8 3');
    await card.getByRole('button',{name:'运行',exact:true}).click();
    await card.locator('.code-trainer__results > div > strong').waitFor({timeout:50000});
    assert.match(await card.locator('.code-trainer__results').innerText(),/运行完成/,`${title}: ${await card.locator('.code-trainer__results').innerText()}`);
    assert.equal((await card.getByLabel('标准输出',{exact:true}).innerText()).trim(),'11',title);
    await card.getByRole('button',{name:'测试公开样例',exact:true}).click();
    await card.getByText('已通过 3 / 3 个公开样例',{exact:true}).waitFor({timeout:60000});
    await page.waitForFunction(index=>!document.querySelectorAll('section.code-trainer')[index].querySelector('.code-trainer__actions button').disabled,index);
    report.checks.push({name:`${title}: actual browser run and three public tests`,passed:true,stdout:'11',tests_passed:3});
    console.log('PASS REAL CHAT RUN',title);
  }
  const python=cards.nth(0), original=await modelText(python);
  await python.getByRole('checkbox',{name:'智能补全'}).uncheck();
  await replaceCode(python,'');
  await python.getByRole('textbox',{name:'代码编辑器',exact:true}).focus();
  await page.keyboard.type('imp',{delay:120});
  await python.getByRole('listitem',{name:'import, Keyword',exact:true}).waitFor({timeout:10000});
  await page.screenshot({path:join(evidence,'keyword-completion.png'),fullPage:true});
  await page.keyboard.press('Tab');assert.equal(await modelText(python),'import');
  report.checks.push({name:'Keyword completion remains active with smart completion off; Tab accepts import',passed:true});
  await replaceCode(python,'\n');await page.keyboard.press('Tab');assert.match(await modelText(python),/^\n {2,4}$/);
  await replaceCode(python,original);
  await python.getByRole('checkbox',{name:'智能补全'}).check();
  await python.getByRole('button',{name:'左右分屏',exact:true}).click();
  await page.waitForTimeout(300);
  assert.equal(await python.locator('.monaco-editor').count(),1);
  const problem=python.getByRole('complementary',{name:'题目与公开样例'});
  await problem.waitFor();assert.match(await problem.innerText(),/Python 输入求和/);
  assert.equal(await problem.locator('section').count(),3);
  const problemBox=await problem.boundingBox(), editorBox=await python.locator('.code-ide__pane').boundingBox();
  assert.ok(problemBox.x+problemBox.width<=editorBox.x+1,'problem is on left and code editor on right');
  assert.equal(await modelText(python),original,'split preserves existing code');
  await replaceCode(python,`${original}\n# editing in split`);
  await python.getByRole('textbox',{name:'代码编辑器',exact:true}).focus();await page.keyboard.press('Control+z');
  assert.notEqual(await modelText(python),`${original}\n# editing in split`,'editor undo works in split');
  await replaceCode(python,original);
  await python.getByRole('button',{name:'全屏',exact:true}).click();
  assert.ok(await python.locator('.code-ide').evaluate(el=>el.getBoundingClientRect().width>innerWidth-32));
  await page.screenshot({path:join(evidence,'editor-fullscreen-split.png'),fullPage:true});
  await python.getByRole('button',{name:'退出全屏',exact:true}).click();
  const colors=await python.locator('.view-lines span').evaluateAll(elements=>[...new Set(elements.map(el=>getComputedStyle(el).color))]);
  assert.ok(colors.length>=2,'syntax tokens use multiple colors');
  assert.ok(await problem.isVisible(),'fullscreen retains problem panel');
  report.checks.push({name:'Tab indentation, left problem and public samples, right single editor, undo, fullscreen, colored syntax',passed:true,token_colors:colors});
  await python.getByRole('button',{name:'关闭分屏',exact:true}).click();
  }
  const java=cards.nth(3);
  assert.equal(await java.getByRole('button',{name:'运行',exact:true}).count(),0);
  await java.getByRole('button',{name:'交给 AI 分析',exact:true}).click();
  await page.waitForTimeout(1000);
  const javaMessages=await completedMessages();
  const javaAnswer=javaMessages.items.filter(item=>item.role==='assistant').at(-1);
  assert.equal(javaAnswer.provider_trace.remote_capability,true);
  assert.equal(javaAnswer.provider_trace.usage_is_estimate,false);
  assert.ok(javaAnswer.provider_trace.output_tokens>0);
  assert.ok(!javaAnswer.parts.some(part=>part.type==='tool_call'),'analysis response must not invoke tools');
  const analysisRequest=report.requests.at(-1);
  assert.equal(analysisRequest.agent_mode,false);assert.equal(analysisRequest.goal_mode,false);assert.equal(analysisRequest.web_search,false);
  assert.ok(analysisRequest.content.includes('"language":"java"'));
  assert.ok(/推演|没有.*执行|未.*运行|无法.*执行/.test(javaAnswer.content),'Java response must acknowledge AI reasoning');
  report.checks.push({name:'Java source submitted through real chat as analysis-only prompt',passed:true,assistant_message_id:javaAnswer.id,answer:javaAnswer.content});
  await writeFile(join(evidence,'messages.json'),JSON.stringify(javaMessages,null,2));
  await writeFile(join(evidence,'streams.json'),JSON.stringify(await page.evaluate(()=>window.__trainerStreams),null,2));
  await page.screenshot({path:join(evidence,'ai-analysis.png'),fullPage:true});
  assert.deepEqual(report.errors,[],'actual conversation must have no uncaught page errors');
  console.log('REAL MODEL ACCEPTANCE CHECKS',report.checks.length);
} finally {
  if(activePage && !activePage.isClosed()) {
    await writeFile(join(evidence,'last-state.txt'),await activePage.locator('body').innerText());
    await activePage.screenshot({path:join(evidence,'last-state.png'),fullPage:true});
  }
  await writeFile(join(evidence, "verification.json"), JSON.stringify(report, null, 2));
  await browser.close();
}
