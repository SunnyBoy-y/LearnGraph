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
    if (path === '/sessions/page') return { items: [], has_more: false, next_cursor: null, has_previous: false, previous_cursor: null };
    if (/^\/sessions\/[^/]+$/.test(path)) return (await (delayedSessions ?? Promise.resolve(records))).find(record => record.id === decodeURIComponent(path.split('/').at(-1)!));
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
  it.each([
    { agent: false, external: true }, { agent: true, external: true },
    { agent: false, external: false }, { agent: true, external: false },
  ])("sends Codex hosted search with agent=$agent and AnySearch=$external", async ({ agent, external }) => {
    const id = `codex-search-${agent}-${external}`;
    setSessionComposerPrefs(id, { providerId: "provider-1", modelId: "model-1",
      responseMode: agent ? "agentic" : "fast", thinkingMode: agent ? "medium" : "off", searchRoute: "auto" });
    const view = setup([session(id, staleSnapshot)]);
    await waitFor(() => expect(apiClient.get).toHaveBeenCalledWith("/providers/provider-1/models"));
    await act(async () => {
      view.queryClient.setQueryData(workspaceQueryKey("test-workspace", "providers"), [{
        id: "provider-1", provider_type: "codex_chatgpt", display_name: "官方登录",
        enabled: true, remote_capability: true,
        capabilities: { default_model: "model-1", hosted_web_search: true,
          reasoning_efforts: ["low", "medium", "high", "xhigh"] },
      }, ...(external ? [{ id: "search-1", provider_type: "anysearch", display_name: "AnySearch",
        enabled: true, remote_capability: true, capabilities: {} }] : [])]);
      view.queryClient.setQueryData(workspaceQueryKey("test-workspace", "provider-models", "provider-1"), {
        provider_id: "provider-1", status: "ok", models: [{ id: "model-1", roles: ["llm"],
          enabled: true, remote: true, streaming: true, capabilities: { hosted_web_search: true,
            reasoning_efforts: ["low", "medium", "high", "xhigh"] } }],
      });
    });
    await waitFor(() => expect(getSessionComposerPrefs(id)?.searchRoute).toBe("model_native"));
    const input = view.container.querySelector("textarea")!;
    fireEvent.change(input, { target: { value: "请检索官方最新消息" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(apiClient.postSse).toHaveBeenCalledTimes(1));
    expect(vi.mocked(apiClient.postSse).mock.calls[0][1]).toMatchObject({
      provider_id: "provider-1", search_route: "model_native", web_search: true, agent_mode: agent,
    });
    await waitFor(() => expect(getSessionStream(id)).toBeUndefined());
  });

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
    const modeRow = await screen.findByRole("menuitem", { name: /^响应模式/ });
    fireEvent.click(modeRow);
    const fastOption = await screen.findByRole("menuitemradio", { name: "极速" });
    fireEvent.click(fastOption);
    fireEvent.keyDown(fastOption, { key: "Escape" });
    await waitFor(() => expect(modeTrigger()).toHaveTextContent("极速"));
    await act(async () => { view.queryClient.setQueryData(sessionsKey, records.map(record => ({ ...record, title: "刷新" }))); });
    await send("继续刚才的西瓜，画个简单示意图");
    await waitFor(() => expect(apiClient.postSse).toHaveBeenCalledTimes(2));
    expect(vi.mocked(apiClient.postSse).mock.calls[1][1]).toMatchObject({ agent_mode: false, thinking_mode: "off" });
    expect(getSessionComposerPrefs("active")).toMatchObject({ responseMode: "fast", thinkingMode: "off" });
  });

  it("refreshes the send gate for the same model without disabling its thinking", async () => {
    const records = [session("cap-refresh", { ...staleSnapshot, agent_mode: true, thinking_mode: "high" })];
    const view = setup(records);
    await waitFor(() => expect(modeTrigger()).toHaveTextContent("高"));
    await waitFor(() => expect(apiClient.get).toHaveBeenCalledWith("/providers/provider-1/models"));
    const send = async (text: string, count: number) => {
      const input = view.container.querySelector("textarea")!;
      fireEvent.change(input, { target: { value: text } });
      fireEvent.submit(input.closest("form")!);
      await waitFor(() => expect(apiClient.postSse).toHaveBeenCalledTimes(count));
      await waitFor(() => expect(getSessionStream("cap-refresh")).toBeUndefined());
    };
    await send("第一次", 1);
    expect(vi.mocked(apiClient.postSse).mock.calls[0][1]).toMatchObject({ agent_mode: true, thinking_mode: "high" });
    await act(async () => {
      view.queryClient.setQueryData(workspaceQueryKey("test-workspace", "provider-models", "provider-1"), {
        provider_id: "provider-1", status: "ok", models: [{ id: "model-1", roles: ["llm"],
          enabled: true, remote: true, streaming: true, capabilities: { supports_agent_tools: false,
            reasoning_efforts: ["low", "medium", "high"], thinking_required: true } }],
      });
    });
    vi.mocked(apiClient.get).mockClear();
    await send("只思考，不使用工具", 2);
    expect(vi.mocked(apiClient.postSse).mock.calls[1][1]).toMatchObject({ agent_mode: false, goal_mode: false, thinking_mode: "high" });
    expect(vi.mocked(apiClient.get).mock.calls.map(([path]) => path)).not.toContain("/sandbox/agent/readiness");
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
    const modeRow = await screen.findByRole("menuitem", { name: /^响应模式/ });
    fireEvent.click(modeRow);
    const agentOption = await screen.findByRole("menuitemradio", { name: "智能体" });
    fireEvent.click(agentOption);
    fireEvent.keyDown(agentOption, { key: "Escape" });
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
    await waitFor(() => expect(apiClient.get).toHaveBeenCalledWith('/sessions/fresh', expect.objectContaining({ signal: expect.any(AbortSignal) })));
    expect(window.localStorage.getItem("learngraph:session-composer-prefs")).toBeNull();
    await act(async () => { pending.resolve(records); });
    await waitFor(() => expect(modeTrigger()).toHaveTextContent("高"));
    expect(getSessionComposerPrefs("fresh")).toMatchObject({ responseMode: "agentic", thinkingMode: "high" });
  });
});
