import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ManagedAgentControls, ManagedChildSessionControls } from "./managed-agent-controls";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/api/client", () => ({ apiClient: api }));
vi.mock("@/api/auth-store", () => ({ authStore: { getWorkspaceId: () => "ws" } }));

describe("managed child session controls", () => {
  it("opens the normal child conversation and waits for a safe takeover", async () => {
    api.get.mockResolvedValue({ status: "RUNNING", spec_json: { agent_session_id: "child" } });
    api.post.mockResolvedValue({ status: "running", agent_session_id: "child", controller: "agent" });
    render(<ManagedAgentControls taskId="task" active />);
    const link = await screen.findByRole("link", { name: "查看执行过程" });
    expect(link).toHaveAttribute("href", "/w/ws/chat/child");
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "接管子会话" })));
    expect(api.post).toHaveBeenCalledWith("/sandbox/agent-tasks/task/execution/takeover", {});
    expect(screen.getByRole("button", { name: "正在保存检查点…" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("暂停后");
  });
  it("hands a paused ordinary child conversation back to the Agent", async () => {
    api.get.mockResolvedValue({ tasks: [{ task_id: "task", status: "PAUSED", spec_json: { agent_session_id: "child" } }] });
    api.post.mockResolvedValue({ status: "queued", agent_session_id: "child", controller: "agent" });
    render(<ManagedChildSessionControls sessionId="child" parentId="parent" />);
    const resume = await screen.findByRole("button", { name: "交回 Agent 继续" });
    await act(async () => fireEvent.click(resume));
    expect(api.post).toHaveBeenCalledWith("/sandbox/agent-tasks/task/execution/resume", {});
    expect(screen.getByRole("status")).toHaveTextContent("继续原任务");
  });
});
