import "@/test/pdfjs-polyfills";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listSessionMessages, listSessions } from "@/api";
import { listProviders, discoverProviderModels } from "@/api/providers";
import { renderWithProviders } from "@/test/render";
import type { Session } from "@/types/sessions";
import { RailConversationComposer } from "./rail-conversation";
import { SideConversationRail } from "./right-rail-panels";

vi.mock("@/api", () => ({
  listSessions: vi.fn(), listSessionMessages: vi.fn(), createSession: vi.fn(),
  cancelSessionMessage: vi.fn(), streamSessionMessage: vi.fn(),
  downloadFile: vi.fn(), listSessionFiles: vi.fn(),
}));

vi.mock("@/api/providers", () => ({ listProviders: vi.fn(), discoverProviderModels: vi.fn() }));

function session(id: string, title: string): Session {
  return { id, title, workspace_id: "ws-1", goal_id: null, graph_id: null, project_id: null,
    parent_session_id: null, source_message_id: null, memory_enabled: false, pinned: false,
    model_snapshot: {}, status: "active", closed_at: null, archived_at: null,
    session_kind: "main", writeback_policy: "normal", context_capsule: {}, activity_summary: null,
    created_at: "2026-10-15T00:00:00Z", updated_at: "2026-10-15T00:00:00Z" };
}

beforeEach(() => {
  vi.mocked(listProviders).mockResolvedValue([]);
  vi.mocked(discoverProviderModels).mockResolvedValue({ provider_id: "none", status: "ok", models: [] });
  vi.mocked(listSessions).mockResolvedValue([session("main-1", "算法学习"), session("side-1", "二分查找")]);
  vi.mocked(listSessionMessages).mockResolvedValue([]);
});

describe("side conversation picker", () => {
  it("combines current-session information with searchable session switching", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SideConversationRail currentSessionId="main-1" workspaceId="ws-1" />);
    const picker = await screen.findByRole("button", { name: "选择分屏会话" });
    await waitFor(() => expect(picker).toHaveTextContent("算法学习"));
    expect(picker).toHaveTextContent("主会话 · 当前主区");
    expect(screen.queryByLabelText("搜索会话")).not.toBeInTheDocument();
    await user.click(picker);
    await user.type(screen.getByLabelText("搜索会话"), "二分");
    await user.click(screen.getByRole("button", { name: /二分查找.*主会话/ }));
    await waitFor(() => expect(picker).toHaveTextContent("二分查找"));
    expect(screen.queryByLabelText("搜索会话")).not.toBeInTheDocument();
    await waitFor(() => expect(listSessionMessages).toHaveBeenCalledWith("side-1", { compact: false }));
    expect(screen.queryByText("侧边问答 · 支持完整消息卡片与文件预览")).not.toBeInTheDocument();
  });

  it("honors focus on a newly selected side session", async () => {
    renderWithProviders(<SideConversationRail currentSessionId="main-1" focusSessionId="side-1" workspaceId="ws-1" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "选择分屏会话" })).toHaveTextContent("二分查找"));
  });
});

describe("shared rail composer", () => {
  function renderComposer(busy = false, disabled = false) {
    const onSubmit = vi.fn();
    const onStop = vi.fn();
    renderWithProviders(<RailConversationComposer value="追问" onChange={vi.fn()} onSubmit={onSubmit}
      onStop={onStop} busy={busy} disabled={disabled} label="追问输入" placeholder="输入消息" sendLabel="发送追问" />);
    return { input: screen.getByLabelText("追问输入"), onSubmit, onStop };
  }

  it("sends on Enter while preserving Shift+Enter and IME composition", () => {
    const { input, onSubmit } = renderComposer();
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("stops an active response without sending another message", async () => {
    const { input, onSubmit, onStop } = renderComposer(true);
    fireEvent.keyDown(input, { key: "Enter" });
    await userEvent.click(screen.getByRole("button", { name: "停止生成" }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("disables submission while the conversation is unavailable", () => {
    const { input, onSubmit } = renderComposer(false, true);
    expect(input).toBeDisabled();
    expect(screen.getByRole("button", { name: "发送追问" })).toBeDisabled();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
