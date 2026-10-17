import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { WorkspaceSearchDialog } from "./workspace-search-dialog";
import { searchWorkspace, type WorkspaceSearchHit } from "@/api/workspace-search";

vi.mock("@/api/workspace-search", () => ({ searchWorkspace: vi.fn() }));
const hit = (kind: WorkspaceSearchHit["kind"], id: string): WorkspaceSearchHit => ({
  kind, id, title: "操作系统复习", snippet: "时间片轮转", session_id: "session-1",
  updated_at: "2026-10-15T08:00:00Z", match_field: "content",
});
function Location() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}:{JSON.stringify(location.state)}</output>;
}
function mount(open = true) {
  const close = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter>
    <WorkspaceSearchDialog open={open} workspaceId="test-ws" onOpenChange={close} /><Location />
  </MemoryRouter></QueryClientProvider>);
  return close;
}
describe("工作区搜索", () => {
  it("关闭时不发送搜索请求", () => {
    vi.mocked(searchWorkspace).mockResolvedValue({ items: [hit("session", "session-1")], has_more: false, next_offset: 1 });
    mount(false);
    expect(searchWorkspace).not.toHaveBeenCalled();
  });
  it("搜索正文后用回车跳转到目标消息，并高亮关键词", async () => {
    vi.mocked(searchWorkspace).mockImplementation(async (q) => ({
      items: q ? [hit("message", "message-1")] : [hit("session", "session-1")], has_more: false, next_offset: 1,
    }));
    const close = mount();
    expect(await screen.findByText("最近会话")).toBeInTheDocument();
    await screen.findByText("操作系统复习");
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "时间片" } });
    await waitFor(() => expect(searchWorkspace).toHaveBeenCalledWith("时间片", "all", 0, expect.any(AbortSignal)));
    expect(await screen.findByText("时间片", { selector: "mark" })).toBeInTheDocument();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(close).toHaveBeenCalledWith(false);
    expect(screen.getByTestId("location")).toHaveTextContent('/w/test-ws/chat/session-1:{"targetMessageId":"message-1"}');
  });
  it("文件筛选与点击跳转，支持继续加载", async () => {
    vi.mocked(searchWorkspace).mockImplementation(async (_q, kind, offset) => ({
      items: kind === "file" ? [hit("file", `file-${offset}`)] : [], has_more: offset === 0 && kind === "file", next_offset: offset + 20,
    }));
    mount();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "调度" } });
    fireEvent.click(screen.getByRole("button", { name: "文件", exact: true }));
    await screen.findByText("操作系统复习");
    fireEvent.click(screen.getByRole("button", { name: "加载更多" }));
    await waitFor(() => expect(screen.getAllByText("操作系统复习")).toHaveLength(2));
    fireEvent.click(screen.getAllByRole("button").find((button) => button.hasAttribute("data-search-result"))!);
    expect(screen.getByTestId("location")).toHaveTextContent("/w/test-ws/documents/file-0");
  });
  it("不显示旧查询结果，空结果有提示且可关闭", async () => {
    vi.mocked(searchWorkspace).mockImplementation(async (q) => ({ items: q ? [] : [hit("session", "session-1")], has_more: false, next_offset: 0 }));
    const close = mount();
    await screen.findByText("操作系统复习");
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "不存在" } });
    expect(screen.queryByText("操作系统复习")).not.toBeInTheDocument();
    await screen.findByText("没有找到相关结果");
    fireEvent.click(screen.getByRole("button", { name: "关闭搜索" }));
    expect(close).toHaveBeenCalledWith(false);
  });
  it("方向键选择结果并通过回车打开", async () => {
    vi.mocked(searchWorkspace).mockResolvedValue({ items: [hit("session", "session-1"), hit("message", "message-2")], has_more: false, next_offset: 2 });
    mount();
    await screen.findAllByText("操作系统复习");
    const input = screen.getByRole("textbox");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByTestId("location")).toHaveTextContent('"targetMessageId":"message-2"');
  });
});
