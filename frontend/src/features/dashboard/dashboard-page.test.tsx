import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createAction, getDashboard, listActions, listGraphs, listPracticeSessions, updateAction } from "@/api";
import { getSessionActivityDays, listSessionPage, type SessionSummary } from "@/api/session-browser";
import { AuthContext, type AuthContextValue } from "@/features/auth/auth-context-value";
import { createTestQueryClient } from "@/test/render";
import type { GraphSummary } from "@/types/graphs";
import type { ActionItem } from "@/types/workflow";
import { DashboardPage } from "./dashboard-page";
import { LearningPlanPage, LearningReportPage } from "./learning-workspace-pages";

vi.mock("@/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api")>()),
  getDashboard: vi.fn(), listActions: vi.fn(), listGraphs: vi.fn(), listPracticeSessions: vi.fn(),
  renderSessionActivitySummary: vi.fn(), createAction: vi.fn(), updateAction: vi.fn(),
}));
vi.mock("@/api/session-browser", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/session-browser")>()), listSessionPage: vi.fn(), getSessionActivityDays: vi.fn(),
}));

const workspaceId = "ws-1";
const base = `/w/${workspaceId}`;
const graph: GraphSummary = { id: "graph", goal_id: "goal", workspace_id: workspaceId, title: "操作系统原理", status: "published", revision: 1, published_at: null };
const iso = (daysAgo = 0) => new Date(Date.now() - daysAgo * 86_400_000).toISOString();
function action(id: string, title: string, due_at: string | null = null): ActionItem {
  return { id, title, due_at, description: "", status: "pending", source: "user", action_type: "todo", project_id: null, goal_id: null,
    graph_id: null, node_id: null, roadmap_id: null, day_index: 1, duration_minutes: 30, priority: 50, position: 0, completed_at: null, metadata_json: {} };
}
function session(id: string, graph_id: string | null = null): SessionSummary {
  return { id, workspace_id: workspaceId, title: `会话 ${id}`, goal_id: null, graph_id, project_id: null, parent_session_id: null, source_message_id: null,
    memory_enabled: false, pinned: false, status: "active", closed_at: null, archived_at: null, session_kind: "main", writeback_policy: "normal",
    activity_summary: "这段会话正文不应出现", created_at: iso(1), updated_at: iso(1), has_children: false };
}
const page = (items: SessionSummary[] = []) => ({ items, next_cursor: null, previous_cursor: null, has_more: false, has_previous: false });

function renderPage(start = "home") {
  return render(<QueryClientProvider client={createTestQueryClient()}>
    <AuthContext.Provider value={{ workspaceId } as AuthContextValue}>
      <MemoryRouter initialEntries={[`${base}/${start}`]}><Routes>
        <Route path="/w/:workspaceId/home" element={<DashboardPage />} />
        <Route path="/w/:workspaceId/learning-report" element={<LearningReportPage />} />
        <Route path="/w/:workspaceId/learning-plan" element={<LearningPlanPage />} />
      </Routes></MemoryRouter>
    </AuthContext.Provider>
  </QueryClientProvider>);
}

describe("学习空间", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getDashboard).mockResolvedValue({ workspace_id: workspaceId, metrics: [], next_actions: [], system_status: {} });
    vi.mocked(listActions).mockResolvedValue([]);
    vi.mocked(listGraphs).mockResolvedValue([]);
    vi.mocked(listPracticeSessions).mockResolvedValue([]);
    vi.mocked(listSessionPage).mockResolvedValue(page());
    vi.mocked(getSessionActivityDays).mockResolvedValue({});
  });

  it("shows a first-goal CTA and does not fetch a day until selected", async () => {
    const user = userEvent.setup();
    renderPage();
    expect(screen.getByRole("heading", { name: "我的学习空间" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "开启你的第一个学习目标" })).toBeInTheDocument();
    expect(screen.getByText("没有安排中的学习任务。")).toBeInTheDocument();
    expect(screen.getByText(/还没有会话。/)).toBeInTheDocument();
    expect(screen.queryByText("今日活动")).not.toBeInTheDocument();
    expect(screen.queryByText(/活动构成/)).not.toBeInTheDocument();
    expect(listSessionPage).toHaveBeenCalledTimes(1);
    const day = screen.getAllByRole("button", { name: /次活动/ }).at(-1)!;
    await user.click(day);
    expect(await screen.findByText("当天没有记录的学习活动。")).toBeInTheDocument();
    expect(listSessionPage).toHaveBeenLastCalledWith(expect.objectContaining({ created_after: expect.any(String) }), {}, expect.anything());
    await user.click(day);
    expect(screen.queryByText("当天没有记录的学习活动。")).not.toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: "学习报告" }));
    expect(await screen.findByText(/活动构成/)).toBeInTheDocument();
    expect(screen.getByText(/月度活动/)).toBeInTheDocument();
  });

  it("resumes a graph session, shows three compact sessions, and keeps overdue work off home", async () => {
    const user = userEvent.setup();
    const current = { ...action("now", "理解进程与线程"), graph_id: graph.id };
    const old = { ...action("old", "过期任务", iso(12)), graph_id: graph.id, priority: 100 };
    vi.mocked(listActions).mockResolvedValue([old, current]);
    vi.mocked(listGraphs).mockResolvedValue([graph]);
    vi.mocked(listSessionPage).mockResolvedValue(page([session("ordinary"), session("graph", graph.id), session("more"), session("hidden")]));
    vi.mocked(updateAction).mockImplementation(async () => {
      const updated = { ...current, status: "completed", completed_at: iso() };
      vi.mocked(listActions).mockResolvedValue([old, updated]);
      return updated;
    });
    renderPage();
    expect(await screen.findByRole("link", { name: "继续学习" })).toHaveAttribute("href", `${base}/chat/graph`);
    expect(within(screen.getByRole("region", { name: "最近学习" })).getAllByRole("listitem")).toHaveLength(3);
    expect(screen.queryByText("这段会话正文不应出现")).not.toBeInTheDocument();
    expect(screen.queryByText(old.title)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: `完成任务：${current.title}` }));
    await waitFor(() => expect(updateAction).toHaveBeenCalledWith(current.id, { status: "completed" }));
    expect(await screen.findByText("没有安排中的学习任务。")).toBeInTheDocument();
  });

  it("resumes a recent unfinished practice with actual progress", async () => {
    vi.mocked(listPracticeSessions).mockResolvedValue([{ id: "practice", title: "系统知识检验", status: "active", mode: "review", created_at: iso(1),
      planned_question_count: 10, completed_question_count: 4, duration_seconds: 5, total_attempt_count: 4, first_try_correct_count: 3,
      final_correct_count: 3, node_count: 1, node_labels: ["进程与线程"] }]);
    renderPage();
    expect(await screen.findByText(/已答 4 \/ 10 题/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "继续练习" })).toHaveAttribute("href", `${base}/practice/session/practice`);
  });

  it("archives historical tasks from the plan page", async () => {
    const user = userEvent.setup();
    const old = action("old", "历史学习任务", iso(8));
    vi.mocked(listActions).mockResolvedValue([old]);
    vi.mocked(updateAction).mockResolvedValue({ ...old, status: "archived" });
    renderPage("learning-plan");
    await user.click(await screen.findByText("历史任务（1）"));
    expect(screen.getByRole("button", { name: "重新安排" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "归档" }));
    await waitFor(() => expect(updateAction).toHaveBeenCalledWith(old.id, { status: "archived" }));
  });
});
