import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";
import { SchedulesPage, parseSchedulePayload, scheduleKey } from "./schedules-page";
import { useLocation } from "react-router-dom";
import { createSession } from "@/api/sessions";
import { getSessionComposerPrefs } from "@/lib/session-composer-prefs";
import { deferred } from "@/test/async";
import type { Session } from "@/types/sessions";
import * as api from "@/api/schedules";
import type { ScheduledTask } from "@/types/schedules";
const auth = vi.hoisted(() => ({ workspaceId: "ws-one" }));
vi.mock("@/features/auth/auth-context-value", () => ({ useAuth: () => auth }));
// This composer does not accept attachments; keep document preview out of these interaction tests.
vi.mock("@/components/resources/file-preview-dialog", () => ({ FilePreviewDialog: () => null }));
vi.mock("@/api/sessions", () => ({ createSession: vi.fn(), listSessions: vi.fn(async () => []) }));
vi.mock("@/api/extensions", () => ({ listSkills: vi.fn(async () => []), listMcpServers: vi.fn(async () => []) }));
vi.mock("@/api/schedules", () => ({ generateScheduleSuggestions:vi.fn(),listScheduledTasks:vi.fn(),createScheduledTask:vi.fn(),updateScheduledTask:vi.fn(),deleteScheduledTask:vi.fn(),runScheduledTask:vi.fn(),cancelScheduledTask:vi.fn(),listScheduledTaskRuns:vi.fn(),getTriggerCatalog:vi.fn(async()=>({event_types:["file.upload"],condition_paths:[],operators:[],modes:[]})),rotateScheduledWebhookToken:vi.fn() }));
const task: ScheduledTask = {id:"task",created_by:"owner",name:"Daily report",description:"",task_type:"skill",skill_id:"skill",mcp_server_id:null,tool_name:null,cron_expression:"0 9 * * *",timezone:"Asia/Shanghai",enabled:true,payload_json:{},timeout_seconds:60,next_run_at:"2026-11-12T01:00:00Z",last_run_at:null,last_status:null,created_at:"2026-11-11T00:00:00Z",updated_at:"2026-11-11T00:00:00Z"};
const recommendation = { name: "语义层论文跟踪", prompt: "每周五上午 9 点筛选本周语义层新论文，整理研究差异和可验证实验。", icon: "microscope" };
beforeEach(() => { vi.clearAllMocks();auth.workspaceId="ws-one";vi.mocked(api.generateScheduleSuggestions).mockResolvedValue({ suggestions: [recommendation], metadata: {} });vi.mocked(api.listScheduledTasks).mockResolvedValue([]);vi.mocked(api.listScheduledTaskRuns).mockResolvedValue([]); });
afterEach(cleanup);
describe("SchedulesPage", () => {
  it("shows an empty state and creates an editor", async () => {
    renderWithProviders(<SchedulesPage/>);
    expect(await screen.findByRole("region", { name: "推荐任务" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "发送任务" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    expect(screen.getByLabelText("执行模式")).toHaveValue("daily");
    expect(screen.getByLabelText("触发后执行的 Prompt")).toBeInTheDocument();
    expect(screen.getByLabelText("执行时区")).toBeInTheDocument();
  });
  it("shows server state without claiming a queued request succeeded", async () => {
    vi.mocked(api.listScheduledTasks).mockResolvedValue([task]);
    vi.mocked(api.runScheduledTask).mockResolvedValue({id:"run",status:"running"} as never);
    renderWithProviders(<SchedulesPage/>);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "更多操作 Daily report" }));
    await user.click(await screen.findByRole("menuitem", { name: "立即运行" }));
    await waitFor(() => expect(api.runScheduledTask).toHaveBeenCalledWith("task"));
    expect(screen.queryByText("成功")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("switch", { name: "启用计划：Daily report" })).toBeEnabled());
    await user.click(screen.getByRole("switch", { name: "启用计划：Daily report" }));
    await waitFor(() => expect(api.updateScheduledTask).toHaveBeenCalledWith("task",{enabled:false}));
  });
  it("shows real execution history and errors", async () => {
    vi.mocked(api.listScheduledTasks).mockResolvedValue([task]);
    vi.mocked(api.listScheduledTaskRuns).mockResolvedValue([{id:"run",task_id:"task",status:"failed",trigger:"scheduled",started_at:task.created_at,scheduled_for:task.created_at,finished_at:task.created_at,result_json:{},error_code:"permission_denied",error_message:"授权已失效"}]);
    renderWithProviders(<SchedulesPage/>);
    const user = userEvent.setup();
    const menu = await screen.findByRole("button", { name: "更多操作 Daily report" });
    await user.click(menu);
    await user.click(await screen.findByRole("menuitem", { name: "执行记录" }));
    expect(await screen.findByText("授权已失效")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(menu).toHaveFocus());
  });
  it("reports loading errors", async () => {
    vi.mocked(api.listScheduledTasks).mockRejectedValue(new Error("服务暂不可用"));
    renderWithProviders(<SchedulesPage/>);
    expect(await screen.findByText("服务暂不可用")).toBeInTheDocument();
  });
  it("partitions caches and clears open editors when workspace changes", async () => {
    expect(scheduleKey("one")).not.toEqual(scheduleKey("two"));
    const view = renderWithProviders(<SchedulesPage/>);
    await screen.findByRole("region", { name: "推荐任务" });
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    fireEvent.change(screen.getByLabelText("名称"),{target:{value:"Private draft"}});
    auth.workspaceId="ws-two";view.rerender(<SchedulesPage/>);
    expect(screen.queryByDisplayValue("Private draft")).not.toBeInTheDocument();
  });
  it("creates finite prompt schedules with visible modes", async()=>{
    vi.mocked(api.createScheduledTask).mockResolvedValue({...task,task_type:"prompt"});
    renderWithProviders(<SchedulesPage/>);
    await screen.findByRole("region", { name: "推荐任务" });fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    fireEvent.change(screen.getByLabelText("名称"),{target:{value:"AI daily"}});
    fireEvent.change(screen.getByLabelText("触发后执行的 Prompt"),{target:{value:"每天总结三条学习建议"}});
    fireEvent.change(screen.getByLabelText("重复次数"),{target:{value:"limited"}});
    fireEvent.change(screen.getByLabelText("剩余执行次数"),{target:{value:"5"}});
    fireEvent.click(screen.getByRole("button",{name:"保存任务"}));
    await waitFor(()=>expect(api.createScheduledTask).toHaveBeenCalledWith(expect.objectContaining({task_type:"prompt",schedule_mode:"daily",prompt:"每天总结三条学习建议",remaining_executions:5})));
  });
  it("supports one-time delays and event conditions in the editor",async()=>{
    renderWithProviders(<SchedulesPage/>);
    await screen.findByRole("region", { name: "推荐任务" });fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    fireEvent.change(screen.getByLabelText("执行模式"),{target:{value:"once"}});
    expect(screen.getByLabelText("延迟（秒）")).toHaveValue(600);
    fireEvent.change(screen.getByLabelText("触发条件"),{target:{value:"condition"}});
    expect(screen.getByLabelText("事件名称")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "添加条件" }));
    expect(screen.getByLabelText("条件 1 的字段")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("条件 1 的字段"), { target: { value: "payload.score" } });
    fireEvent.change(screen.getByLabelText("比较方式"), { target: { value: "gte" } });
    fireEvent.change(screen.getByLabelText("值类型"), { target: { value: "number" } });
    fireEvent.change(screen.getByLabelText("条件值"), { target: { value: "80" } });
    expect(screen.getByLabelText("条件值")).toHaveValue(80);
  });
  it("keeps enabled status separate from execution failure and hides internal fields", async () => {
    vi.mocked(api.listScheduledTasks).mockResolvedValue([{...task, task_type:"prompt", prompt:"Internal prompt", description:"Daily summary", last_status:"failed", created_via:"ai", remaining_executions:1 }]);
    renderWithProviders(<SchedulesPage/>);
    expect(await screen.findByText("上次执行失败")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "启用计划：Daily report" })).toBeChecked();
    expect(screen.queryByText("Daily summary")).not.toBeInTheDocument();
    expect(screen.queryByText("下一次执行")).not.toBeInTheDocument();
    expect(screen.queryByText("Internal prompt")).not.toBeInTheDocument();
    expect(screen.queryByText("AI 创建")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看原因" }));
    expect(screen.getByRole("dialog", { name: "Daily report" })).toBeInTheDocument();
    expect(screen.getByText("执行记录")).toBeVisible();
  });
  it("opens a separate reading drawer, with forms and logs available on demand", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listScheduledTasks).mockResolvedValue([{ ...task, task_type: "prompt", prompt: "完整任务指令", schedule_summary: "每天 09:00（Asia/Shanghai），持续运行", last_status: "succeeded" }]);
    vi.mocked(api.listScheduledTaskRuns).mockResolvedValue(["recent-run", "older-run"].map((id, index) => ({ id, task_id: task.id, status: index === 0 ? "succeeded" : "failed", trigger: "scheduled", started_at: task.created_at, scheduled_for: task.created_at, finished_at: task.created_at, result_json: { chat_session_id: id }, error_code: null, error_message: index === 0 ? null : "旧记录的错误" })));
    renderWithProviders(<SchedulesPage />);
    const opener = await screen.findByRole("button", { name: "Daily report" });
    expect(screen.queryByText("上次执行成功")).not.toBeInTheDocument();
    expect(screen.getByText("每天 09:00")).toBeVisible();
    await user.click(opener);
    expect(screen.getByRole("dialog", { name: "Daily report" }).closest(".schedules-page")).toBeNull();
    expect(screen.getByText("完整任务指令")).toBeVisible();
    expect(screen.getByText("11月12日 09:00")).toBeVisible();
    expect(await screen.findByRole("link", { name: "查看执行会话" })).toHaveAttribute("href", "/w/ws-one/chat/recent-run");
    expect(screen.queryByText("旧记录的错误")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("名称")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "全部记录" }));
    expect(await screen.findByText("旧记录的错误")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "返回任务详情" }));
    await user.click(screen.getByRole("button", { name: "编辑任务" }));
    expect(screen.getByLabelText("名称")).toHaveValue("Daily report");
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.getByText("完整任务指令")).toBeVisible();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(opener).toHaveFocus());
  });
  it("filters real enabled and paused tasks without hiding recommendations", async () => {
    vi.mocked(api.listScheduledTasks).mockResolvedValue([task, { ...task, id: "paused", name: "Paused report", enabled: false }]);
    renderWithProviders(<SchedulesPage />);
    expect(await screen.findByRole("button", { name: "Daily report" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Paused report" })).toBeInTheDocument();
    const user = userEvent.setup();
    screen.getByRole("combobox", { name: "任务状态" }).focus();
    await user.keyboard("[Enter]");
    await user.click(await screen.findByRole("option", { name: "已开启" }));
    expect(screen.queryByRole("button", { name: "Paused report" })).not.toBeInTheDocument();
    screen.getByRole("combobox", { name: "任务状态" }).focus();
    await user.keyboard("[Enter]");
    await user.click(await screen.findByRole("option", { name: "已暂停" }));
    expect(screen.getByRole("button", { name: "Paused report" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Daily report" })).not.toBeInTheDocument();
    screen.getByRole("combobox", { name: "任务状态" }).focus();
    await user.keyboard("[Enter]");
    await user.click(await screen.findByRole("option", { name: "全部任务" }));
    expect(screen.getByRole("button", { name: "Daily report" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Paused report" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "推荐任务" })).toBeInTheDocument();
  });
  it("keeps recommendation descriptions and prompts off the preview", async () => {
    vi.mocked(api.generateScheduleSuggestions).mockResolvedValue({ suggestions: [{ ...recommendation, description: "梳理论文的研究差异", schedule: "每周五 09:00", icon: "constructor", theme: "sage" }], metadata: {} });
    renderWithProviders(<SchedulesPage />);
    const card = await screen.findByRole("button", { name: `安排${recommendation.name}` });
    expect(card.closest("li")).not.toHaveAttribute("data-theme");
    expect(card.querySelector("svg")).toBeInTheDocument();
    expect(screen.queryByText("梳理论文的研究差异")).not.toBeInTheDocument();
    expect(screen.getByText("每周五 09:00")).toBeVisible();
    expect(screen.queryByText(recommendation.prompt)).not.toBeInTheDocument();
    expect(api.generateScheduleSuggestions).toHaveBeenCalledTimes(1);
  });
  it("fills a recommendation as an editable draft without creating a task", async () => {
    renderWithProviders(<SchedulesPage />);
    fireEvent.click(await screen.findByRole("button", { name: `安排${recommendation.name}` }));
    expect(screen.getByLabelText("安排任务")).toHaveValue(recommendation.prompt);
    expect(screen.getByLabelText("安排任务")).toHaveFocus();
    expect(createSession).not.toHaveBeenCalled();
    expect(api.createScheduledTask).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "试试这些任务" }));
    expect(screen.queryByRole("button", { name: `安排${recommendation.name}` })).not.toBeInTheDocument();
    expect(screen.getByLabelText("安排任务")).not.toHaveValue("");
  });
  it("refreshes AI recommendations without replacing an edited draft", async () => {
    renderWithProviders(<SchedulesPage />);
    await screen.findByRole("button", { name: `安排${recommendation.name}` });
    fireEvent.change(screen.getByLabelText("安排任务"), { target: { value: "保留我的草稿" } });
    vi.mocked(api.generateScheduleSuggestions).mockResolvedValue({ suggestions: [{ name: "数据库实验回顾", prompt: "每周日总结数据库实验记录并提出下周验证项。", icon: "database" }], metadata: {} });
    fireEvent.click(screen.getByRole("button", { name: "换一组" }));
    expect(await screen.findByRole("button", { name: "安排数据库实验回顾" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: `安排${recommendation.name}` })).not.toBeInTheDocument();
    expect(screen.getByLabelText("安排任务")).toHaveValue("保留我的草稿");
    expect(api.generateScheduleSuggestions).toHaveBeenCalledWith(expect.objectContaining({ headers: { "X-Workspace-ID": "ws-one" } }));
  });
  it("uses no fixed templates when authorized context is empty or generation fails", async () => {
    vi.mocked(api.generateScheduleSuggestions).mockResolvedValue({ suggestions: [], metadata: { reason: "no_authorized_context" } });
    renderWithProviders(<SchedulesPage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "换一组" })).toBeEnabled());
    expect(screen.queryByRole("button", { name: /^安排/ })).not.toBeInTheDocument();
    vi.mocked(api.generateScheduleSuggestions).mockRejectedValue(new Error("Provider unavailable"));
    fireEvent.click(screen.getByRole("button", { name: "换一组" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "换一组" })).toBeEnabled());
    expect(screen.queryByRole("button", { name: /^安排/ })).not.toBeInTheDocument();
    expect(screen.queryByText("电子邮箱监控")).not.toBeInTheDocument();
    expect(screen.getByLabelText("安排任务")).toBeEnabled();
  });
  it("hands off one natural-language request to the existing agent conversation", async () => {
    const result = deferred<Session>();
    vi.mocked(createSession).mockReturnValue(result.promise);
    function LocationProbe() {
      const location = useLocation();
      return <output data-testid="location">{JSON.stringify({ path: location.pathname, state: location.state })}</output>;
    }
    renderWithProviders(<><SchedulesPage /><LocationProbe /></>);
    const input = screen.getByLabelText("安排任务");
    fireEvent.change(input, { target: { value: "每天 10 点提醒我复习" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(createSession).not.toHaveBeenCalled();
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });
    fireEvent.compositionEnd(input);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(createSession).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(createSession).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "发送任务" })).toBeDisabled();
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(createSession).toHaveBeenCalledTimes(1));
    result.resolve({ id: "schedule-chat" } as Session);
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/w/ws-one/chat/schedule-chat"));
    const location = JSON.parse(screen.getByTestId("location").textContent!);
    expect(location.state.pendingPrompt).toContain("每天 10 点提醒我复习");
    expect(location.state.pendingPrompt).toContain(Intl.DateTimeFormat().resolvedOptions().timeZone);
    expect(getSessionComposerPrefs("schedule-chat")).toMatchObject({ responseMode: "agentic", generationMode: "text" });
    expect(api.createScheduledTask).not.toHaveBeenCalled();
  });
  it("keeps a failed scheduling draft available for retry", async () => {
    vi.mocked(createSession).mockRejectedValue(new Error("连接暂不可用"));
    renderWithProviders(<SchedulesPage />);
    fireEvent.change(screen.getByLabelText("安排任务"), { target: { value: "每天提醒我阅读" } });
    fireEvent.click(screen.getByRole("button", { name: "发送任务" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("连接暂不可用");
    expect(screen.getByLabelText("安排任务")).toHaveValue("每天提醒我阅读");
    expect(screen.getByRole("button", { name: "发送任务" })).toBeEnabled();
  });
  it("aborts a pending handoff and clears its draft on workspace changes", async () => {
    const result = deferred<Session>();
    vi.mocked(createSession).mockReturnValue(result.promise);
    const view = renderWithProviders(<SchedulesPage />);
    fireEvent.change(screen.getByLabelText("安排任务"), { target: { value: "工作区私有任务" } });
    fireEvent.click(screen.getByRole("button", { name: "发送任务" }));
    await waitFor(() => expect(createSession).toHaveBeenCalledTimes(1));
    const options = vi.mocked(createSession).mock.calls[0][1]!;
    expect(options.headers).toEqual({ "X-Workspace-ID": "ws-one" });
    auth.workspaceId = "ws-two";
    view.rerender(<SchedulesPage />);
    expect(options.signal?.aborted).toBe(true);
    expect(screen.getByLabelText("安排任务")).toHaveValue("");
    result.resolve({ id: "old-workspace-session" } as Session);
    await waitFor(() => expect(screen.getByRole("button", { name: "发送任务" })).toBeDisabled());
  });
  it("accepts only JSON objects as payloads", () => {
    expect(parseSchedulePayload('{"argv_extra":[]}')).toEqual({argv_extra:[]});
    for (const text of ["[]","null","1",'"text"']) expect(() => parseSchedulePayload(text)).toThrow("JSON 对象");
  });
  it("treats naive backend ISO timestamps as UTC when formatting in the task timezone", async () => {
    vi.mocked(api.listScheduledTasks).mockResolvedValue([{
      ...task,
      name: "早报",
      timezone: "Asia/Shanghai",
      next_run_at: "2026-11-28T00:30:00",
      last_status: "failed",
    }]);
    vi.mocked(api.listScheduledTaskRuns).mockResolvedValue([{
      id: "run-utc-naive",
      task_id: task.id,
      status: "failed",
      trigger: "scheduled",
      started_at: "2026-11-27T00:30:05",
      scheduled_for: "2026-11-27T00:30:00",
      finished_at: "2026-11-27T00:31:05",
      result_json: { chat_session_id: "sess-1", status: "failed" },
      error_code: "scheduled_prompt_timeout",
      error_message: "Scheduled prompt exceeded its execution timeout",
    }]);
    renderWithProviders(<SchedulesPage />);
    fireEvent.click(await screen.findByText("查看原因"));
    expect(await screen.findByText(/08:30:05/)).toBeInTheDocument();
    expect(screen.queryByText(/00:30:05/)).not.toBeInTheDocument();
  });
});
