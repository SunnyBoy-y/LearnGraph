import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Route, Routes, useNavigate } from "react-router-dom";
import { apiClient } from "@/api/client";
import { TooltipProvider } from "@/components/ui/tooltip";
import { workspaceQueryKey } from "@/lib/query-keys";
import { getSessionComposerPrefs, setSessionComposerPrefs } from "@/lib/session-composer-prefs";
import { getSessionStream } from "@/lib/session-streams";
import { deferred } from "@/test/async";
import { renderWithProviders } from "@/test/render";
import type { Session } from "@/types/sessions";
import { ChatCanvasPage } from "./chat-pages";

vi.mock("@/features/auth/auth-context-value", () => ({
  useAuth: () => ({ workspaceId: "test-workspace", username: "tester", effectivePermissions: [] }),
}));

const session = (id: string, snapshot: Record<string, unknown>): Session => ({
  id, workspace_id: "test-workspace", title: id, status: "active",
  memory_enabled: true, model_snapshot: snapshot, context_capsule: {},
} as Session);
const staleSnapshot = { provider_id: "provider-1", model_id: "model-1", thinking_mode: "off", agent_mode: false };
const sessionsKey = workspaceQueryKey("test-workspace", "sessions");

function setup(records: Session[], delayedSessions?: Promise<Session[]>, initialSessionId = records[0].id) {
  vi.spyOn(apiClient, "get").mockImplementation(async (path) => {
    if (path === "/sessions") return delayedSessions ?? records;
    if (path === "/settings") return [];
    if (path === "/providers") return [{
      id: "provider-1", provider_type: "openai_compatible_chat", display_name: "测试模型",
      enabled: true, remote_capability: true,
      capabilities: { default_model: "model-1", reasoning_efforts: ["low", "medium", "high", "xhigh"] },
    }, {
      id: "search-1", provider_type: "tavily", display_name: "搜索",
      enabled: true, remote_capability: true, capabilities: {},
    }];
    if (path === "/providers/provider-1/models") return {
      provider_id: "provider-1", status: "ok",
      models: [{ id: "model-1", roles: ["llm"], enabled: true, remote: true, streaming: true }],
    };
    if (path.endsWith("/messages")) return { items: [], total_count: 0, has_more_before: false };
    return [];
  });
  vi.spyOn(apiClient, "post").mockResolvedValue(records[0]);
  vi.spyOn(apiClient, "postSse").mockImplementation(() => (async function* () {
    yield { event: "message.completed", rawData: "", data: { type: "message.completed", message_id: "answer" } };
  })());
  function Page() {
    const navigate = useNavigate();
    return <>
      {records.map((record) => <button key={record.id} onClick={() => navigate(`/w/test-workspace/chat/${record.id}`)}>切换{record.id}</button>)}
      <ChatCanvasPage />
    </>;
  }
  return renderWithProviders(<TooltipProvider><Routes>
    <Route path="/w/:workspaceId/chat/:sessionId" element={<Page />} />
  </Routes></TooltipProvider>, { initialEntries: [`/w/test-workspace/chat/${initialSessionId}`] });
}

const modeTrigger = () => screen.getByRole("button", { name: "选择响应模式、思考力度和模型" });

describe("composer preference restoration", () => {
  it("sends high then fast in the same session without restoring a stale high snapshot", async () => {
    const records = [session("active", { ...staleSnapshot, agent_mode: true, thinking_mode: "high" })];
    const view = setup(records);
    await waitFor(() => expect(modeTrigger()).toHaveTextContent("高"));
    await waitFor(() => expect(apiClient.get).toHaveBeenCalledWith("/providers/provider-1/models"));
    async function send(content: string) {
      const input = view.container.querySelector("textarea")!;
      fireEvent.change(input, { target: { value: content } });
      fireEvent.submit(input.closest("form")!);
      await waitFor(() => expect(getSessionStream("active")).toBeUndefined());
    }
    await send("先详细解释西瓜");
    await waitFor(() => expect(apiClient.postSse).toHaveBeenCalledTimes(1));
    expect(vi.mocked(apiClient.postSse).mock.calls[0][1]).toMatchObject({ agent_mode: true, thinking_mode: "high" });
    fireEvent.pointerDown(modeTrigger(), { button: 0, pointerType: "mouse", ctrlKey: false });
    const slider = await screen.findByRole("slider");
    vi.spyOn(slider, "getBoundingClientRect").mockReturnValue({ left: 0, width: 400 } as DOMRect);
    fireEvent.pointerDown(slider, { clientX: 0, pointerId: 1 });
    fireEvent.pointerUp(slider, { pointerId: 1 });
    fireEvent.keyDown(slider, { key: "Escape" });
    await waitFor(() => expect(modeTrigger()).toHaveTextContent("极速"));
    await act(async () => { view.queryClient.setQueryData(sessionsKey, records.map(record => ({ ...record, title: "刷新" }))); });
    await send("继续刚才的西瓜，画个简单示意图");
    await waitFor(() => expect(apiClient.postSse).toHaveBeenCalledTimes(2));
    expect(vi.mocked(apiClient.postSse).mock.calls[1][1]).toMatchObject({ agent_mode: false, thinking_mode: "off" });
    expect(getSessionComposerPrefs("active")).toMatchObject({ responseMode: "fast", thinkingMode: "off" });
  });

  it("carries medium thinking from a new draft into both the first and next request", async () => {
    const view = setup([session("created", staleSnapshot)], undefined, "new");
    await waitFor(() => expect(apiClient.get).toHaveBeenCalledWith("/providers/provider-1/models"));
    for (let round = 1; round <= 2; round++) {
      const input = view.container.querySelector("textarea")!;
      fireEvent.change(input, { target: { value: `新会话第${round}个问题` } });
      fireEvent.submit(input.closest("form")!);
      await waitFor(() => expect(apiClient.postSse).toHaveBeenCalledTimes(round));
      expect(vi.mocked(apiClient.postSse).mock.calls[round - 1][1]).toMatchObject({
        provider_id: "provider-1", model_id: "model-1", agent_mode: true, thinking_mode: "medium",
      });
      await waitFor(() => expect(view.container.querySelector(".chat-canvas-page")).toHaveAttribute("data-session-id", "created"));
      await waitFor(() => expect(getSessionStream("created")).toBeUndefined());
    }
    expect(getSessionComposerPrefs("created")).toMatchObject({ responseMode: "agentic", thinkingMode: "medium" });
    expect(vi.mocked(apiClient.post).mock.calls.filter(([path]) => path === "/sessions")).toHaveLength(1);
  });

  it("keeps explicitly saved medium thinking even when it matches defaults and the snapshot is fast", async () => {
    setSessionComposerPrefs("saved", { responseMode: "agentic", thinkingMode: "medium", providerId: "provider-1", modelId: "model-1" });
    setup([session("saved", staleSnapshot)]);
    await waitFor(() => expect(apiClient.get).toHaveBeenCalledWith("/providers/provider-1/models"));
    await waitFor(() => expect(modeTrigger()).toHaveTextContent("中"));
    expect(getSessionComposerPrefs("saved")).toMatchObject({ responseMode: "agentic", thinkingMode: "medium" });
  });

  it("keeps the selected mode across session refetches, two sends, switching away and reopening", async () => {
    const records = [session("active", staleSnapshot), session("other", staleSnapshot)];
    setSessionComposerPrefs("active", { responseMode: "fast", thinkingMode: "off" });
    const view = setup(records);
    await waitFor(() => expect(modeTrigger()).toHaveTextContent("极速"));
    await waitFor(() => expect(apiClient.get).toHaveBeenCalledWith("/providers/provider-1/models"));
    fireEvent.pointerDown(modeTrigger(), { button: 0, pointerType: "mouse", ctrlKey: false });
    const slider = await screen.findByRole("slider");
    vi.spyOn(slider, "getBoundingClientRect").mockReturnValue({ left: 0, width: 400 } as DOMRect);
    fireEvent.pointerDown(slider, { clientX: 200, pointerId: 1 });
    fireEvent.pointerUp(slider, { pointerId: 1 });
    fireEvent.keyDown(slider, { key: "Escape" });
    await waitFor(() => expect(getSessionComposerPrefs("active")).toMatchObject({ responseMode: "agentic", thinkingMode: "medium" }));
    await act(async () => {
      view.queryClient.setQueryData(sessionsKey, records.map((record) => ({ ...record, title: "刷新后的标题" })));
    });
    for (let round = 1; round <= 2; round++) {
      const input = view.container.querySelector("textarea")!;
      fireEvent.change(input, { target: { value: `问题${round}` } });
      fireEvent.submit(input.closest("form")!);
      await waitFor(() => expect(apiClient.postSse).toHaveBeenCalledTimes(round));
      expect(vi.mocked(apiClient.postSse).mock.calls[round - 1][1]).toMatchObject({
        provider_id: "provider-1", model_id: "model-1", agent_mode: true, thinking_mode: "medium",
      });
      await waitFor(() => expect(getSessionStream("active")).toBeUndefined());
    }
    fireEvent.click(screen.getByRole("button", { name: "切换other" }));
    await waitFor(() => expect(modeTrigger()).toHaveTextContent("极速"));
    fireEvent.click(screen.getByRole("button", { name: "切换active" }));
    await waitFor(() => expect(modeTrigger()).toHaveTextContent("中"));
    view.unmount();
    setup(records);
    await waitFor(() => expect(modeTrigger()).toHaveTextContent("中"));
  });

  it("waits for a delayed durable snapshot on first visit and restores its thinking level", async () => {
    const pending = deferred<Session[]>();
    const records = [session("fresh", { ...staleSnapshot, agent_mode: true, thinking_mode: "high" })];
    setup(records, pending.promise);
    await waitFor(() => expect(apiClient.get).toHaveBeenCalledWith("/sessions"));
    expect(window.localStorage.getItem("learngraph:session-composer-prefs")).toBeNull();
    await act(async () => { pending.resolve(records); });
    await waitFor(() => expect(modeTrigger()).toHaveTextContent("高"));
    expect(getSessionComposerPrefs("fresh")).toMatchObject({ responseMode: "agentic", thinkingMode: "high" });
  });
});
