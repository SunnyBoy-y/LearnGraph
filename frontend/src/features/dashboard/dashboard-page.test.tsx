import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";

import {
  createAction,
  getDashboard,
  listActions,
  listSessionMessages,
  listSessions,
  renderSessionActivitySummary,
  updateAction,
} from "@/api";
import {
  AuthContext,
  type AuthContextValue,
} from "@/features/auth/auth-context-value";
import { createTestQueryClient } from "@/test/render";
import { deferred } from "@/test/async";
import type { DashboardResponse } from "@/types/dashboard";
import type { ActionItem } from "@/types/workflow";

import { DashboardPage } from "./dashboard-page";

vi.mock("@/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api")>()),
  getDashboard: vi.fn(),
  listSessions: vi.fn(),
  listSessionMessages: vi.fn(),
  listActions: vi.fn(),
  renderSessionActivitySummary: vi.fn(),
  createAction: vi.fn(),
  updateAction: vi.fn(),
}));

const WORKSPACE_ID = "ws-1";

const emptyDashboard: DashboardResponse = {
  workspace_id: WORKSPACE_ID,
  metrics: [],
  next_actions: [],
  system_status: {},
};

// Both createAction and updateAction resolve an ActionItem; the dashboard tests
// only exercise the mutation lifecycle, so one minimal valid item satisfies the
// shared type without asserting on its shape.
const minimalAction: ActionItem = {
  id: "action-1",
  title: "t",
  description: "",
  status: "pending",
  source: "user",
  action_type: "todo",
  project_id: null,
  goal_id: null,
  graph_id: null,
  node_id: null,
  roadmap_id: null,
  day_index: 1,
  duration_minutes: 30,
  due_at: null,
  priority: 50,
  position: 0,
  completed_at: null,
  metadata_json: {},
};

const authValue: AuthContextValue = {
  authenticated: true,
  username: "tester",
  avatarUrl: null,
  updateProfile: vi.fn(),
  workspaceId: WORKSPACE_ID,
  workspaceName: "测试工作区",
  mustChangePassword: false,
  setWorkspaceId: vi.fn(async () => undefined),
  login: vi.fn(async () => ({ workspaceId: WORKSPACE_ID, mustChangePassword: false })),
  demoLogin: vi.fn(async () => ({ workspaceId: WORKSPACE_ID, mustChangePassword: false })),
  register: vi.fn(async () => ({ workspaceId: WORKSPACE_ID, mustChangePassword: false })),
  changePassword: vi.fn(async () => undefined),
  deleteAccount: vi.fn(async () => undefined),
  logout: vi.fn(async () => undefined),
};

function renderDashboard(): ReturnType<typeof render> {
  const queryClient = createTestQueryClient();
  const element = (
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider value={authValue}>
        <MemoryRouter initialEntries={[`/w/${WORKSPACE_ID}/dashboard`]}>
          <Routes>
            <Route
              path="/w/:workspaceId/dashboard"
              element={<DashboardPage /> as ReactElement}
            />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>
  );
  return render(element);
}

describe("DashboardPage query states", () => {
  beforeEach(() => {
    vi.mocked(getDashboard).mockResolvedValue(emptyDashboard);
    vi.mocked(listSessions).mockResolvedValue([]);
    vi.mocked(listSessionMessages).mockResolvedValue([]);
    vi.mocked(listActions).mockResolvedValue([]);
    vi.mocked(renderSessionActivitySummary).mockResolvedValue({
      id: "unused-session",
      workspace_id: WORKSPACE_ID,
      title: "unused",
      goal_id: null,
      graph_id: null,
      project_id: null,
      parent_session_id: null,
      source_message_id: null,
      memory_enabled: false,
      pinned: false,
      model_snapshot: {},
      status: "active",
      closed_at: null,
      archived_at: null,
      session_kind: "main",
      writeback_policy: "normal",
      context_capsule: {},
      activity_summary: "unused",
      created_at: "2026-08-02T00:00:00.000Z",
      updated_at: "2026-08-02T00:00:00.000Z",
    });
    vi.mocked(createAction).mockResolvedValue(minimalAction);
    // updateAction resolves an ActionItem; the test only drives the mutation,
    // so a minimal valid item satisfies the type without changing behavior.
    vi.mocked(updateAction).mockResolvedValue(minimalAction);
  });

  it("shows a loading state while the dashboard query is pending, then the page", async () => {
    const gate = deferred<DashboardResponse>();
    vi.mocked(getDashboard).mockReturnValue(gate.promise);

    renderDashboard();

    // Distinct loading UI is user-visible as a status live region.
    expect(screen.getByText("正在准备工作区首页…")).toBeInTheDocument();
    expect(screen.getByRole("status")).toBeInTheDocument();

    gate.resolve(emptyDashboard);

    await waitFor(() =>
      expect(screen.getByText("继续你的学习计划")).toBeInTheDocument(),
    );
  });

  it("shows an error state with the failure message and recovers on retry", async () => {
    vi.mocked(getDashboard)
      .mockRejectedValueOnce(new Error("backend exploded"))
      .mockResolvedValue(emptyDashboard);

    const user = userEvent.setup();
    renderDashboard();

    // Distinct error UI renders the query error message and a retry action.
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("backend exploded"),
    );
    expect(screen.getByText("暂时无法加载")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重试" }));

    // Refetch succeeded: the page body replaces the error state.
    await waitFor(() =>
      expect(screen.getByText("继续你的学习计划")).toBeInTheDocument(),
    );
  });

  it("renders empty-state copy when the workspace has no actions or sessions", async () => {
    renderDashboard();

    expect(await screen.findByText("继续你的学习计划")).toBeInTheDocument();

    // Empty action list → no primary action card.
    expect(screen.getByText("当前没有待处理行动")).toBeInTheDocument();
    // Empty today list.
    expect(screen.getByText("今天暂无待办安排或新会话。")).toBeInTheDocument();
    // No recent session → fallback copy.
    expect(screen.getByText("还没有学习会话")).toBeInTheDocument();
    expect(
      screen.getByText("创建会话后，最近的学习摘要会显示在这里。"),
    ).toBeInTheDocument();
  });
});
