// Isolated browser QA. Run with npm exec --package=playwright@1.56.1 -- node scripts/check-schedules.mjs
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import clipAudit from './notification-clip-audit.mjs';
const bin = process.env.PATH.split(path.delimiter).find(entry => fs.existsSync(path.resolve(entry, '../playwright/package.json')));
const require = bin ? createRequire(path.resolve(bin, '../playwright/package.json')) : createRequire(import.meta.url);
const { chromium, expect } = require('playwright/test');
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 }, reducedMotion: 'reduce' });
const workspaceId = 'qa-schedules';
const errors = [];
const writes = [];
const report = { cases: [], screenshots: [], errors };
const now = new Date().toISOString();
const baseTask = { id: 'task-1', created_by: 'qa-user', name: '每周研究速递', description: '', prompt: '筛选 Data Agent 本体与语义层的新研究，提炼值得深入阅读和验证的成果。', task_type: 'prompt', trigger_type: 'time', schedule_mode: 'weekly', schedule_summary: '每周五 09:00', time_of_day: '09:00', weekdays: [4], cron_expression: '0 9 * * 5', timezone: 'Asia/Shanghai', enabled: true, created_via: 'ai', remaining_executions: null, execution_count: 2, skill_id: null, mcp_server_id: null, tool_name: null, payload_json: {}, timeout_seconds: 300, next_run_at: new Date(Date.now() + 3600000).toISOString(), last_run_at: null, last_status: null, created_at: now, updated_at: now };
const session = { id: 'schedule-chat', workspace_id: workspaceId, title: '安排定时任务', status: 'active', memory_enabled: true, model_snapshot: {}, context_capsule: {}, project_id: null, graph_id: null, goal_id: null, parent_session_id: null, created_at: now, updated_at: now };
let tasks = [];
let runs = [];
let sessions = [];
let messages = [];
let failSessionCreate = false;
page.on('pageerror', error => errors.push(error.stack || error.message));
await page.addInitScript(workspaceId => {
  localStorage.setItem('learngraph.access_token', 'isolated-schedules-qa');
  localStorage.setItem('learngraph.workspace_id', workspaceId);
  localStorage.setItem('learngraph.username', 'qa-render');
  localStorage.setItem('learngraph.must_change_password', 'false');
}, workspaceId);
await page.route('**/api/v1/**', route => {
  const request = route.request();
  const method = request.method();
  const endpoint = new URL(request.url()).pathname.replace('/api/v1', '');
  if (!['GET', 'HEAD'].includes(method)) writes.push({ method, endpoint, body: request.postDataJSON() });
  const json = data => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
  if (endpoint === '/scheduled-tasks/suggestions' && method === 'POST') return json({ suggestions: [{ name: '新闻动态', prompt: '每天早上 9 点给我发送一份新闻汇总，涵盖我关注的话题。', icon: 'newspaper' }, { name: '语义层论文跟踪', prompt: '每周五筛选语义层研究论文并整理验证方案。', icon: 'microscope' }], metadata: { memory_used: true } });
  if (endpoint === '/scheduled-tasks' && method === 'POST') {
    const task = { ...baseTask, ...request.postDataJSON(), id: 'manual-task', created_via: 'ui' };
    tasks.push(task);
    return json(task);
  }
  const item = endpoint.match(/^\/scheduled-tasks\/([^/]+)(?:\/(run|cancel|runs))?$/);
  if (item) {
    const task = tasks.find(task => task.id === item[1]);
    if (method === 'PATCH') {
      Object.assign(task, request.postDataJSON());
      return json(task);
    }
    if (method === 'DELETE') {
      tasks = tasks.filter(task => task.id !== item[1]);
      return route.fulfill({ status: 204 });
    }
    if (item[2] === 'runs') return json(runs);
    if (method === 'POST' && ['run', 'cancel'].includes(item[2])) {
      const running = item[2] === 'run';
      task.last_status = running ? 'running' : 'failed';
      const run = { id: 'manual-run', task_id: task.id, status: task.last_status, trigger: 'manual', started_at: now, scheduled_for: now, finished_at: running ? null : now, result_json: {}, error_code: running ? null : 'cancelled', error_message: running ? null : '用户取消' };
      runs = [run];
      return json(run);
    }
  }
  if (endpoint === '/sessions' && method === 'POST') {
    if (failSessionCreate) return route.fulfill({ status: 503, json: { error: { code: 'service_unavailable', message: '连接暂不可用' } } });
    sessions = [session];
    return json(session);
  }
  if (endpoint === '/sessions/schedule-chat/messages/stream') {
    const content = request.postDataJSON().content;
    const user = { id: 'qa-user-message', workspace_id: workspaceId, session_id: session.id, parent_message_id: null, role: 'user', version: 1, status: 'completed', content, parts: [], provider_trace: {}, created_at: now };
    const answer = { ...user, id: 'qa-answer', parent_message_id: user.id, role: 'assistant', content: '请确认每天早上 9 点执行这项任务。', parts: [{ id: 'qa-text', type: 'text', status: 'completed', content: '请确认每天早上 9 点执行这项任务。' }] };
    messages = [user, answer];
    const events = [
      { type: 'message.accepted', message_id: answer.id, payload: { user_message_id: user.id } },
      { type: 'part.delta', part: { id: 'qa-text', type: 'text', status: 'streaming', sequence: 1, content_delta: answer.content } },
      { type: 'answer.started', payload: { final_part_id: 'qa-text', boundary_sequence: 1 } },
      { type: 'message.completed', message_id: answer.id },
    ];
    return route.fulfill({ contentType: 'text/event-stream', body: events.map(event => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join('') });
  }
  let data = [];
  if (endpoint === '/auth/me') data = { id: 'qa-user', username: 'qa-render', display_name: 'QA', is_system_admin: false, must_change_password: false };
  else if (endpoint === '/workspaces') data = [{ id: workspaceId, name: 'LearnGraph', effective_permissions: [] }];
  else if (endpoint === '/deployment/profile') data = { mode: 'self_hosted' };
  else if (endpoint === '/providers') data = [{ id: 'qa-provider', provider_type: 'openai_compatible_chat', display_name: 'QA provider', enabled: true, remote_capability: true, capabilities: { default_model: 'qa-model' } }];
  else if (endpoint === '/providers/qa-provider/models') data = { provider_id: 'qa-provider', status: 'ok', models: [{ id: 'qa-model', roles: ['llm'], enabled: true, remote: true, streaming: true, supports_tools: true }] };
  else if (endpoint === '/settings') data = [{ key: 'chat.default_response_mode', value: { response_mode: 'fast' } }];
  else if (endpoint === '/sessions') data = sessions;
  else if (endpoint === '/sessions/page') data = { items: sessions, next_cursor: null, previous_cursor: null, has_more: false, has_previous: false };
  else if (endpoint === '/sessions/summary') data = { total: sessions.length, active: sessions.length, archived: 0, memory_enabled: sessions.length };
  else if (endpoint === '/sessions/schedule-chat') data = session;
  else if (endpoint === '/sessions/schedule-chat/messages') data = { items: messages, total_count: messages.length, has_more_before: false };
  else if (/^\/sessions\/schedule-chat\/messages\/qa-(user-message|answer)$/.test(endpoint)) data = messages.find(message => endpoint.endsWith(message.id));
  else if (endpoint.endsWith('/readiness')) data = { available: true };
  else if (endpoint.includes('/browser')) data = { items: [], has_more: false, total_count: 0, next_cursor: null };
  else if (endpoint === '/scheduled-tasks') data = tasks;
  else if (endpoint === '/scheduled-tasks/trigger-catalog') data = { event_types: ['file.upload'], condition_paths: [], operators: [], modes: [] };
  return json(data);
});
const output = path.resolve(import.meta.dirname, '../../docs');
const screenshot = async name => {
  const file = path.join(output, name);
  await page.screenshot({ path: file, fullPage: true });
  report.screenshots.push(file);
};
const audit = async name => {
  await page.evaluate(async () => { await Promise.all(document.getAnimations().filter(animation => animation.effect?.getTiming().iterations !== Infinity).map(animation => animation.finished.catch(() => {}))); });
  const clip = await page.evaluate(clipAudit, { root: name.endsWith('-open') ? 'body' : '.schedules-page' });
  assert.equal(clip.cut, 0, JSON.stringify(clip));
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  assert.equal(overflow, false, `${name}: horizontal overflow`);
  report.cases.push({ name, clip, horizontalOverflow: overflow });
};
const schedulesUrl = `${process.env.SCHEDULE_QA_URL || 'http://127.0.0.1:5205'}/w/${workspaceId}/schedules`;
try {
  await page.goto(schedulesUrl);
  await page.getByRole('heading', { name: '定时任务', exact: true }).waitFor();
  await page.getByRole('button', { name: '安排新闻动态', exact: true }).waitFor();
  await audit('empty-light');
  await screenshot('schedules-light.png');
  await page.getByRole('combobox', { name: '任务状态' }).click();
  await expect(page.getByRole('option', { name: '已开启', exact: true })).toHaveAttribute('aria-selected', 'true');
  await audit('filter-menu-open');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '安排新闻动态', exact: true }).hover();
  await audit('recommendation-hover');
  await page.getByRole('button', { name: '安排新闻动态', exact: true }).click();
  assert.match(await page.getByLabel('安排任务', { exact: true }).inputValue(), /每天早上 9 点/);
  assert.equal(await page.getByRole('button', { name: '发送任务' }).isEnabled(), true);
  assert.ok(writes.length > 0 && writes.every(write => write.endpoint === '/scheduled-tasks/suggestions'));
  await page.getByRole('button', { name: '推荐', exact: true }).click();
  assert.equal(await page.getByRole('button', { name: '安排新闻动态', exact: true }).count(), 0);
  await page.getByRole('button', { name: '推荐', exact: true }).click();
  await page.getByRole('button', { name: '新建任务', exact: true }).click();
  await page.getByRole('heading', { name: '新建定时任务' }).waitFor();
  await audit('editor-open');
  await page.getByLabel('名称', { exact: true }).fill('学习计划');
  await page.getByLabel('触发后执行的 Prompt').fill('整理今天要复习的内容');
  await page.getByLabel('重复次数').selectOption('limited');
  await page.getByLabel('剩余执行次数').fill('5');
  await page.getByRole('button', { name: '保存任务', exact: true }).click();
  await page.getByRole('button', { name: '学习计划', exact: true }).waitFor();
  assert.equal(tasks[0].remaining_executions, 5);
  assert.equal(tasks[0].task_type, 'prompt');
  await page.getByRole('button', { name: '学习计划', exact: true }).click();
  await page.getByLabel('名称', { exact: true }).fill('晚间学习计划');
  await page.getByLabel('触发时间').fill('20:30');
  await page.getByRole('button', { name: '保存任务', exact: true }).click();
  await page.getByRole('button', { name: '晚间学习计划', exact: true }).waitFor();
  assert.equal(tasks[0].time_of_day, '20:30');
  await page.getByRole('button', { name: '暂停 晚间学习计划', exact: true }).click();
  await expect(page.getByRole('button', { name: '晚间学习计划', exact: true })).toHaveCount(0);
  await expect(page.getByText('没有已开启的任务', { exact: true })).toBeVisible();
  await page.getByRole('combobox', { name: '任务状态' }).click();
  await page.getByRole('option', { name: '已暂停', exact: true }).click();
  await page.getByRole('button', { name: '启用 晚间学习计划', exact: true }).click();
  await expect(page.getByText('没有已暂停的任务', { exact: true })).toBeVisible();
  await page.getByRole('combobox', { name: '任务状态' }).click();
  await page.getByRole('option', { name: '全部任务', exact: true }).click();
  await page.getByRole('button', { name: '立即执行 晚间学习计划', exact: true }).click();
  await page.getByRole('button', { name: '停止 晚间学习计划', exact: true }).waitFor();
  assert.equal(tasks[0].remaining_executions, 5, 'Manual runs do not consume schedule attempts');
  await page.getByRole('button', { name: '停止 晚间学习计划', exact: true }).click();
  await expect(page.getByRole('button', { name: '停止 晚间学习计划', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '记录 晚间学习计划', exact: true }).click();
  await page.locator('summary').click();
  await expect(page.getByRole('alert')).toHaveText('cancelled：用户取消');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '更多操作 晚间学习计划', exact: true }).click();
  await page.getByRole('menuitem', { name: '删除任务', exact: true }).click();
  await expect(page.getByRole('alertdialog')).toBeVisible();
  await page.getByRole('button', { name: '取消', exact: true }).click();
  assert.equal(tasks.length, 1, 'Cancelling deletion preserves the task');
  await page.getByRole('button', { name: '更多操作 晚间学习计划', exact: true }).click();
  await page.getByRole('menuitem', { name: '删除任务', exact: true }).click();
  await page.getByRole('button', { name: '删除任务', exact: true }).click();
  await expect(page.getByRole('button', { name: '晚间学习计划', exact: true })).toHaveCount(0);
  assert.equal(tasks.length, 0);
  report.cases.push({ name: 'create-edit-pause-resume-run-cancel-history-delete', passed: true });

  tasks = [baseTask, { ...baseTask, id: 'task-paused', name: '晚间学习回顾', enabled: false }];
  await page.reload();
  await page.getByRole('button', { name: baseTask.name, exact: true }).waitFor();
  await page.getByRole('combobox', { name: '任务状态' }).click();
  await page.getByRole('option', { name: '已暂停', exact: true }).click();
  await page.getByRole('button', { name: '晚间学习回顾', exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: baseTask.name, exact: true }).count(), 0);
  await page.getByRole('combobox', { name: '任务状态' }).click();
  await page.getByRole('option', { name: '全部任务', exact: true }).click();
  await page.getByRole('button', { name: baseTask.name, exact: true }).waitFor();
  await audit('populated-list');
  await page.getByRole('button', { name: `更多操作 ${baseTask.name}`, exact: true }).click();
  await page.getByRole('menuitem', { name: '编辑任务' }).waitFor();
  await audit('actions-menu-open');
  await page.keyboard.press('Escape');
  await page.evaluate(() => document.documentElement.classList.add('dark'));
  await audit('populated-dark');
  await screenshot('schedules-dark.png');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByLabel('安排任务', { exact: true }).fill('每天早上九点，帮我整理学习计划。\n'.repeat(20));
  await audit('narrow-long-draft');
  assert.ok(await page.getByLabel('安排任务', { exact: true }).evaluate(node => node.offsetHeight <= 192));
  await screenshot('schedules-mobile.png');
  await page.setViewportSize({ width: 320, height: 640 });
  await audit('small-mobile');
  await page.getByRole('button', { name: '新建任务', exact: true }).click();
  await page.getByRole('heading', { name: '新建定时任务' }).waitFor();
  await audit('small-editor-open');
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.evaluate(() => document.documentElement.classList.remove('dark'));

  const draft = '每天早上 9 点提醒我阅读';
  failSessionCreate = true;
  await page.getByLabel('安排任务', { exact: true }).fill(draft);
  await page.getByRole('button', { name: '发送任务' }).click();
  await expect(page.getByRole('alert')).toHaveText('连接暂不可用');
  await expect(page.getByLabel('安排任务', { exact: true })).toHaveValue(draft);
  await expect(page.getByRole('button', { name: '发送任务' })).toBeEnabled();
  failSessionCreate = false;
  await page.getByRole('button', { name: '发送任务' }).click();
  await page.waitForURL(`**/w/${workspaceId}/chat/schedule-chat`);
  await expect(page.getByText('请确认每天早上 9 点执行这项任务。', { exact: true })).toBeVisible({ timeout: 20000 });
  const streams = writes.filter(write => write.endpoint.endsWith('/messages/stream'));
  assert.equal(streams.length, 1);
  assert.match(streams[0].body.content, /每天早上 9 点提醒我阅读/);
  assert.equal(streams[0].body.agent_mode, true, 'Scheduling uses the existing agent tools even when workspace default is fast');
  await page.reload();
  await expect(page.getByText('请确认每天早上 9 点执行这项任务。', { exact: true })).toBeVisible();
  assert.equal(writes.filter(write => write.endpoint.endsWith('/messages/stream')).length, 1, 'Reload must not resubmit the task prompt');
  await page.goto(schedulesUrl);
  await page.getByRole('button', { name: baseTask.name, exact: true }).waitFor();
  runs = [{ id: 'result-run', task_id: baseTask.id, status: 'succeeded', trigger: 'scheduled', started_at: now, scheduled_for: now, finished_at: now, result_json: { chat_session_id: session.id }, error_code: null, error_message: null }];
  await page.getByRole('button', { name: `记录 ${baseTask.name}`, exact: true }).click();
  await page.locator('summary').click();
  await page.getByRole('link', { name: '打开执行会话', exact: true }).click();
  await expect(page.getByText('请确认每天早上 9 点执行这项任务。', { exact: true })).toBeVisible();
  report.cases.push({ name: 'failed-draft-retry-agent-handoff-reload-result-session', passed: true });
  assert.deepEqual(errors, []);
  report.writes = writes.map(({ method, endpoint }) => `${method} ${endpoint}`);
  report.passed = true;
} catch (error) {
  report.passed = false;
  report.failure = String(error.stack || error);
  report.pageText = (await page.locator('body').innerText()).slice(0, 3000);
  process.exitCode = 1;
} finally {
  fs.writeFileSync(path.join(output, 'schedules-qa.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  await browser.close();
}
