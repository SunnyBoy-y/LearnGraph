import { spawn, spawnSync } from 'node:child_process';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const backend = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const root = process.argv[2];
if (!root?.replaceAll('\\', '/').endsWith('/native-billing-implementation')) throw new Error('An isolated verification directory is required');
const apiOrigin = 'http://127.0.0.1:24139', origin = 'http://127.0.0.1:24140';
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const result = { scope: 'Actual LearnGraph frontend and backend; isolated fixture database', checks: [], screenshots: [], consoleErrors: [], status: 'pending' };
const check = (name, value, details) => { result.checks.push({ name, ok: Boolean(value), details }); if (!value) throw new Error(name + ': ' + JSON.stringify(details)); };
for (let i = 0; i < 60; i++) { try { if ((await fetch(apiOrigin + '/api/v1/health')).ok) break; } catch {} await sleep(500); }
async function api(url, identity, workspace, method = 'GET', body) {
  const response = await fetch(apiOrigin + '/api/v1' + url, { method, headers: { 'Content-Type': 'application/json',
    ...(identity ? { Authorization: 'Bearer ' + identity.access_token } : {}), ...(workspace ? { 'X-Workspace-ID': workspace } : {}) }, body: body === undefined ? undefined : JSON.stringify(body) });
  const value = response.status === 204 ? null : await response.json();
  if (!response.ok) throw new Error(url + ' ' + response.status + ' ' + JSON.stringify(value));
  return value;
}
function seed(payload) {
  const child = spawnSync(path.join(backend, '.venv/Scripts/python.exe'), [path.join(backend, 'scripts/seed_native_billing_verification.py')],
    { cwd: backend, env: { ...process.env, LEARNGRAPH_DATABASE_URL: 'sqlite:///' + path.join(root, 'live-test.db').replaceAll('\\', '/'),
      LEARNGRAPH_ENV: 'test', LEARNGRAPH_STORAGE_ROOT: path.join(root, 'storage'), LEARNGRAPH_ENABLE_DEMO_SEED: 'false',
      LEARNGRAPH_MEMORY_EVENT_MASTER_KEY: 'native-billing-verification-only', LEARNGRAPH_SECRET_PROVIDER: 'environment' },
      input: JSON.stringify(payload), encoding: 'utf8', windowsHide: true });
  if (child.status) throw new Error('Fixture seeding failed: ' + child.stderr.slice(-1500));
}
const password = 'NativeTest@Pass2026!x';
const register = label => api('/auth/register', null, null, 'POST', { username: 'native_' + label + '_' + Date.now(), display_name: label, password });
const owner = await register('owner'), member = await register('member'), viewer = await register('viewer');
seed({ phase: 'identities', owner: owner.user_id, members: [member.user_id, viewer.user_id] });
for (const identity of [member, viewer]) Object.assign(identity, await api('/auth/login', null, null, 'POST', { username: identity.username, password }));
const org = await api('/organizations', owner, null, 'POST', { name: 'Native verification ' + Date.now(), workspace_name: 'Native shared workspace' });
const workspaces = await api('/workspaces', owner);
const shared = workspaces.find(workspace => workspace.organization_id === org.id).id;
const personal = owner.default_workspace_id;
const roles = await api('/organizations/' + org.id + '/roles', owner);
for (const [identity, name] of [[member, 'Member'], [viewer, 'Viewer']]) await api('/organizations/' + org.id + '/memberships', owner, null, 'POST', { user_id: identity.user_id, role_id: roles.find(role => role.name === name).id });
seed({ phase: 'ledger', owner: owner.user_id, workspaces: [[shared, 'shared'], [personal, 'personal']] });

const profile = await fs.mkdtemp(path.join(root, 'chrome-verification-'));
const browser = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new', '--no-first-run', '--no-default-browser-check',
  '--disable-background-networking', '--disable-extensions', '--remote-debugging-pipe', '--user-data-dir=' + profile, 'about:blank'],
  { windowsHide: true, stdio: ['ignore', 'ignore', 'pipe', 'pipe', 'pipe'] });
let buffer = Buffer.alloc(0), next = 0, sessionId;
const pending = new Map();
browser.stdio[4].on('data', chunk => { buffer = Buffer.concat([buffer, chunk]); for (;;) { const index = buffer.indexOf(0); if (index < 0) break;
  const packet = JSON.parse(buffer.subarray(0, index).toString()); buffer = buffer.subarray(index + 1);
  if (packet.method === 'Runtime.exceptionThrown') result.consoleErrors.push(packet.params.exceptionDetails.text + ' ' + (packet.params.exceptionDetails.exception?.description ?? ''));
  if (packet.method?.startsWith('Browser.download')) (result.downloads ??= []).push({ method: packet.method, ...packet.params });
  if (packet.id && pending.has(packet.id)) { const item = pending.get(packet.id); pending.delete(packet.id); clearTimeout(item.timer); packet.error ? item.reject(new Error(JSON.stringify(packet.error))) : item.resolve(packet.result); }
} });
function call(method, params = {}, session = sessionId) { return new Promise((resolve, reject) => { const id = ++next;
  const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout ' + method)); }, 20000);
  pending.set(id, { resolve, reject, timer }); browser.stdio[3].write(JSON.stringify({ id, method, params, ...(session ? { sessionId: session } : {}) }) + '\0'); }); }
async function evaluate(expression) { const value = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (value.exceptionDetails) throw new Error(JSON.stringify(value.exceptionDetails)); return value.result.value; }
async function waitFor(expression) { for (let i = 0; i < 100; i++) { if (await evaluate('Boolean((' + expression + '))')) return; await sleep(100); } throw new Error('UI timeout: ' + expression + ' ' + (await evaluate('document.body.innerText')).slice(-2000)); }
async function click(text) {
  const direct = await evaluate(`(()=>{const button=[...document.querySelectorAll('button')].find(el=>el.textContent.trim()===${JSON.stringify(text)}); if(button && button.getAttribute('role')!=='tab'){button.click();return true;} return false;})()`);
  if (direct) return;
  await evaluate(`(()=>{const button=[...document.querySelectorAll('button')].find(el=>el.textContent.trim()===${JSON.stringify(text)}); if(!button) throw new Error('Missing button '+${JSON.stringify(text)}); button.scrollIntoView({block:'center',behavior:'instant'});button.focus();return true;})()`);
  await sleep(100);
  const point = await evaluate(`(()=>{const button=[...document.querySelectorAll('button')].find(el=>el.textContent.trim()===${JSON.stringify(text)}); const r=button.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
  await call('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', clickCount: 1, ...point });
  await call('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', clickCount: 1, ...point });
}
async function tab(identity, workspace, currency = 'USD') {
  const target = await call('Target.createTarget', { url: 'about:blank' }, null);
  ({ sessionId } = await call('Target.attachToTarget', { targetId: target.targetId, flatten: true }, null));
  await call('Page.enable'); await call('Runtime.enable');
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  const values = { access_token: identity.access_token, workspace_id: workspace, user_id: identity.user_id, username: identity.username,
    display_name: identity.display_name, session_id: identity.session_id, must_change_password: 'false' };
  await call('Page.addScriptToEvaluateOnNewDocument', { source: '(()=>{if(location.origin===' + JSON.stringify(origin) + ') { for (const [key,value] of Object.entries(' + JSON.stringify(values) + ')) sessionStorage.setItem("learngraph."+key,value); }})()' });
  await call('Page.navigate', { url: origin + '/w/' + workspace + '/settings/usage?currency=' + currency });
  await waitFor(`document.body.innerText.includes('已记录费用') && document.body.innerText.includes(${JSON.stringify(currency === 'USD' ? '1.2300000000003' : '3.0000000000002')})`);
  return target.targetId;
}
async function screenshot(name) { const shot = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false }); await fs.writeFile(path.join(root, name), Buffer.from(shot.data, 'base64')); result.screenshots.push(name); }
async function fill(name, value) {
  await evaluate(`(()=>{const el=[...document.querySelectorAll('[name='+${JSON.stringify(name)}+']')].at(-1);if(!el)throw new Error('Missing input '+${JSON.stringify(name)});Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el),'value').set.call(el,${JSON.stringify(value)});el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));})()`);
}
try {
  await call('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: path.resolve(root), eventsEnabled: true }, null);
  await tab(owner, shared);
  const metric = () => evaluate(`(()=>{ const label=[...document.querySelectorAll('p')].find(el=>el.textContent==='已记录费用'); return label?.parentElement.innerText; })()`);
  check('USD exact native overview', (await metric()).includes('USD') && !(await metric()).includes('CNY'), await metric());
  await click('人民币 CNY'); await waitFor("document.body.innerText.includes('3.0000000000002')");
  check('CNY switch has independent exact money', (await metric()).includes('CNY') && !(await metric()).includes('USD'), await metric());
  const previousDocument = await evaluate('performance.timeOrigin');
  await call('Page.reload'); await waitFor(`performance.timeOrigin!==${previousDocument} && document.body.innerText.includes('3.0000000000002')`);
  check('CNY view persists in URL after reload', await evaluate("new URL(location.href).searchParams.get('currency')==='CNY'"));
  await click('预算与告警'); await waitFor("document.body.innerText.includes('CNY fixture cap')");
  check('CNY budget excludes USD policy', !(await evaluate('document.body.innerText')).includes('USD fixture cap'));
  check('Manager can create budgets', await evaluate("[...document.querySelectorAll('button')].some(el=>el.textContent.includes('新建预算'))"));
  await click('新建预算'); await waitFor("document.querySelector('[name=hard_limit]')");
  await fill('name', 'Browser CNY cap'); await fill('model_id', 'browser-only'); await fill('hard_limit', '1.234567890123456789'); await click('保存预算');
  await waitFor("!document.querySelector('[name=hard_limit]') && document.body.innerText.includes('Browser CNY cap')");
  const newBudget = (await api('/usage/budget-policies?currency=CNY', owner, shared)).find(item => item.name === 'Browser CNY cap');
  check('Manager form saves exact CNY budget only', newBudget?.hard_limit === '1.234567890123456789' && !(await api('/usage/budget-policies?currency=USD', owner, shared)).some(item => item.id === newBudget.id));
  await screenshot('native-billing-cny-desktop.png');
  await click('价格目录'); await waitFor("document.body.innerText.includes('CNY 手动价格')");
  check('System administrator global refresh visible', await evaluate("[...document.querySelectorAll('button')].some(el=>el.textContent==='刷新 models.dev')"));
  await click('设置为 CNY'); await waitFor("document.body.innerText.includes('Fixture provider shared · CNY')");
  const nativeProvider = (await api('/providers', owner, shared)).find(item => item.display_name === 'Fixture provider shared');
  check('Provider binding preserves existing capabilities', nativeProvider.capabilities.billing_currency === 'CNY' && nativeProvider.capabilities.default_model === 'native-fixture');
  await click('添加手动价格'); await waitFor("document.querySelector('[name=input]')");
  await fill('provider_id', nativeProvider.id); await fill('model_id', 'browser-native-price'); await fill('input', '0.00000000000000000001');
  await fill('cached', '0'); await fill('write', '0.123456789'); await fill('output', '0.5'); await fill('rates_by_unit', '{"requests":"0.02"}'); await click('保存此价格');
  await waitFor("!document.querySelector('[name=input]') && document.body.innerText.includes('browser-native-price')");
  const newPrice = (await api('/usage/manual-prices?currency=CNY', owner, shared)).find(item => item.model_id === 'browser-native-price');
  check('Manager form saves native price and explicit zero cache', newPrice?.input_per_million === '0.00000000000000000001' && newPrice.cached_input_per_million === '0' && newPrice.cache_write_per_million === '0.123456789' && newPrice.rates_by_unit.requests === '0.02' && !(await api('/usage/manual-prices?currency=USD', owner, shared)).some(item => item.id === newPrice.id));
  await click('用量明细'); await waitFor("document.querySelectorAll('tbody tr').length===3");
  check('CNY details exclude USD', await evaluate("[...document.querySelectorAll('tbody tr')].every(el=>el.innerText.includes('CNY'))"));
  await evaluate("window.__nativeDownloads=[]; const original=HTMLAnchorElement.prototype.click; HTMLAnchorElement.prototype.click=function(){window.__nativeDownloads.push({name:this.download});return original.call(this);}");
  await click('导出全部筛选明细');
  for (let i = 0; i < 50 && !(await fs.readdir(root)).some(name => name.endsWith('.csv') && name.includes(shared)); i++) await sleep(100);
  const csvFiles = (await fs.readdir(root)).filter(name => name.endsWith('.csv') && name.includes(shared));
  const csv = csvFiles.length ? await fs.readFile(path.join(root, csvFiles.at(-1)), 'utf8') : '';
  check('CSV native currency and exact decimal', csv.includes('CNY') && csv.includes('1.5000000000001') && !csv.includes('cost_usd') && !csv.includes('exchange_rate'), { files: csvFiles,
    downloadAttempts: await evaluate('window.__nativeDownloads'), ui: await evaluate('document.body.innerText.slice(-300)'), activeWorkspace: await evaluate('sessionStorage.getItem("learngraph.workspace_id")'), expectedWorkspace: shared });
  await evaluate('window.confirm=()=>true'); await click('归档当前范围'); await waitFor("document.querySelectorAll('tbody tr').length===0");
  check('Archive retains CNY costs and attempts', (await metric()).includes('3.0000000000002'), await metric());
  const summary = await api('/usage/summary?currency=CNY', owner, shared); check('Server archive retains budget history', summary.cost_amount === '3.0000000000002' && summary.attempts === 3, { amount: summary.cost_amount, attempts: summary.attempts });
  for (const width of [1440, 390]) for (const theme of ['light', 'dark']) {
    await call('Emulation.setDeviceMetricsOverride', { width, height: width === 390 ? 844 : 1000, deviceScaleFactor: 1, mobile: false });
    await evaluate(`document.documentElement.classList.toggle('dark',${theme === 'dark'});document.querySelectorAll('*').forEach(el=>{if(el.scrollTop)el.scrollTop=0;});window.scrollTo(0,0)`);
    await sleep(200);
    const geometry = await evaluate('({innerWidth, scrollWidth:document.documentElement.scrollWidth})');
    check('Layout ' + width + ' ' + theme, geometry.scrollWidth <= width + 1, geometry);
    await screenshot('native-billing-' + width + '-' + theme + '.png');
  }
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await evaluate(`history.pushState({},'',${JSON.stringify('/w/' + personal + '/settings/usage?currency=USD')});window.dispatchEvent(new PopStateEvent('popstate'));`);
  await waitFor("document.body.innerText.includes('6.6600000000003')");
  check('SPA workspace switch uses personal ledger without shared cache', (await metric()).includes('6.6600000000003') && !(await metric()).includes('1.2300000000003'));
  await evaluate(`history.pushState({},'',${JSON.stringify('/w/' + shared + '/settings/usage?currency=USD')});window.dispatchEvent(new PopStateEvent('popstate'));`);
  await waitFor("document.body.innerText.includes('1.2300000000003')");
  check('SPA switch back restores shared USD ledger', (await metric()).includes('1.2300000000003') && !(await metric()).includes('6.6600000000003'));
  for (const [identity, role] of [[member, 'member'], [viewer, 'viewer']]) {
    await tab(identity, shared); await click('预算与告警'); await waitFor("document.body.innerText.includes('USD fixture cap')");
    check(role + ' sees native budget but no manager actions', await evaluate("![...document.querySelectorAll('button')].some(el=>/新建预算|编辑|停用|邮件配置/.test(el.textContent))"));
    await click('价格目录'); await waitFor("document.body.innerText.includes('USD 手动价格')");
    check(role + ' cannot change prices or refresh global catalog', await evaluate("![...document.querySelectorAll('button')].some(el=>/添加手动价格|设置为|刷新 models.dev/.test(el.textContent))"));
    await click('用量明细'); await waitFor("document.querySelectorAll('tbody tr').length===4");
    check(role + ' cannot archive', await evaluate("![...document.querySelectorAll('button')].some(el=>el.textContent==='归档当前范围')"));
  }
  check('No application runtime exceptions', !result.consoleErrors.length, result.consoleErrors);
  result.status = 'pass';
} catch (error) { result.status = 'fail'; result.error = String(error); try { await screenshot('native-billing-failure.png'); } catch {} }
finally { await fs.writeFile(path.join(root, 'browser-verification.json'), JSON.stringify(result, null, 2)); try { await call('Browser.close', {}, null); } catch { browser.kill(); } }
console.log(JSON.stringify({ status: result.status, checks: result.checks.length, failed: result.checks.filter(item => !item.ok), error: result.error, screenshots: result.screenshots }));
if (result.status !== 'pass') process.exitCode = 1;
