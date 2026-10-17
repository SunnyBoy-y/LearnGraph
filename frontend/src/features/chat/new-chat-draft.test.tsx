import { QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Profiler } from "react";
import { createMemoryRouter, Route, RouterProvider, Routes, useLocation, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiClient } from "@/api/client";
import { TooltipProvider } from "@/components/ui/tooltip";
import { WorkspaceShell } from "@/components/layout/workspace-shell";
import { createTestQueryClient, renderWithProviders } from "@/test/render";
import { deferred } from "@/test/async";
import { abortSessionStream, getSessionStream } from "@/lib/session-streams";
import type { Message, Session } from "@/types/sessions";
import { ChatCanvasPage } from "./chat-pages";

vi.mock("@/features/auth/auth-context-value", () => ({
  useAuth: () => ({ workspaceId: "test-workspace", username: "tester", workspaceName: "Test workspace", effectivePermissions: [] }),
}));

describe("unused chat drafts", () => {
  beforeEach(() => {
    vi.spyOn(apiClient, "get").mockResolvedValue([]);
    vi.spyOn(apiClient, "post").mockRejectedValue(new Error("Unexpected write"));
    vi.spyOn(apiClient, "patch").mockRejectedValue(new Error("Unexpected write"));
  });

  it.each(["", "?project=folder-a"])(
    "opening, revisiting and leaving a blank canvas%s never writes a session",
    async (search) => {
      const open = () => renderWithProviders(
        <TooltipProvider>
          <Routes>
            <Route path="/w/:workspaceId/chat/:sessionId" element={<ChatCanvasPage />} />
          </Routes>
        </TooltipProvider>,
        { initialEntries: [`/w/test-workspace/chat/new${search}`] },
      );
      const first = open();
      expect(await screen.findByRole("heading", { name: "从一个问题开始" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "我现在最应该先学什么？" })).toBeEnabled();
      await act(async () => {
        fireEvent(window, new Event("blur"));
        fireEvent(document, new Event("visibilitychange"));
        fireEvent(window, new Event("focus"));
      });
      first.unmount();
      const revisited = open();
      expect(await screen.findByRole("heading", { name: "从一个问题开始" })).toBeInTheDocument();
      revisited.unmount();

      expect(apiClient.post).not.toHaveBeenCalled();
      expect(apiClient.patch).not.toHaveBeenCalled();
    },
  );
});

describe("first message draft promotion", () => {
  it("isolates a promoted fourth conversation while three background answers keep streaming", async () => {
    const labels = ["A", "B", "C", "D"];
    const advance = deferred<void>();
    const finish = deferred<void>();
    const created = deferred<Session>();
    const sessions = labels.map((label) => ({
      id: `concurrent-${label}`, workspace_id: "test-workspace", title: label,
      status: "active", memory_enabled: true, model_snapshot: {}, context_capsule: {},
    } as Session));
    const message = (label: string, role: "user" | "assistant"): Message => ({
      id: `${label}-${role}`, workspace_id: "test-workspace", session_id: `concurrent-${label}`,
      parent_message_id: role === "assistant" ? `${label}-user` : null,
      role, version: 1, status: "completed", content: role === "user" ? `问题${label}` : `回复${label}继续${label}`,
      parts: [], provider_trace: {}, created_at: new Date().toISOString(),
    });
    vi.spyOn(apiClient, "get").mockImplementation(async (path) => {
      if (path === "/providers") return [{
        id: "provider-1", provider_type: "openai_compatible_chat", display_name: "测试模型",
        enabled: true, remote_capability: true, capabilities: { default_model: "model-1" },
      }];
      if (path === "/providers/provider-1/models") return {
        provider_id: "provider-1", status: "ok",
        models: [{ id: "model-1", roles: ["llm"], enabled: true, remote: true, streaming: true }],
      };
      if (path === "/settings") return [{ key: "chat.default_response_mode", value: { response_mode: "fast" } }];
      if (path === "/sessions") return sessions;
      if (/\/messages$/.test(path)) return { items: [], total_count: 0, has_more_before: false };
      const match = path.match(/\/messages\/([A-D])-(user|assistant)$/);
      if (match) return message(match[1], match[2] as "user" | "assistant");
      return [];
    });
    vi.spyOn(apiClient, "post").mockImplementation((path) => {
      if (path === "/sessions") return created.promise;
      return Promise.reject(new Error(`Unexpected write: ${path}`));
    });
    vi.spyOn(apiClient, "postSse").mockImplementation((path) => (async function* () {
      const label = path.match(/concurrent-([A-D])/)![1];
      yield { event: "message.accepted", rawData: "", data: {
        type: "message.accepted", message_id: `${label}-assistant`, payload: { user_message_id: `${label}-user` },
      } };
      yield { event: "part.delta", rawData: "", data: {
        type: "part.delta", part: { id: `answer-${label}`, type: "text", status: "streaming", sequence: 1, content_delta: `回复${label}` },
      } };
      await advance.promise;
      yield { event: "part.delta", rawData: "", data: {
        type: "part.delta", part: { id: `answer-${label}`, type: "text", status: "streaming", sequence: 1, content_delta: `继续${label}` },
      } };
      await finish.promise;
      yield { event: "message.completed", rawData: "", data: { type: "message.completed", message_id: `${label}-assistant` } };
    })());
    const leakedCommits: string[] = [];
    function Page() {
      const navigate = useNavigate();
      return <>
        {labels.map((label) => <button key={label} onClick={() => navigate(`/w/test-workspace/chat/${label === "D" ? "new" : `concurrent-${label}`}`)}>窗口{label}</button>)}
        <Profiler id="isolation" onRender={() => {
          const canvas = document.querySelector('.chat-canvas-page[data-session-id="concurrent-D"]');
          if (canvas && /问题[A-C]|回复[A-C]|继续[A-C]/.test(canvas.textContent ?? "")) {
            leakedCommits.push(canvas.textContent ?? "");
          }
        }}><ChatCanvasPage /></Profiler>
      </>;
    }
    const view = renderWithProviders(<TooltipProvider><Routes>
      <Route path="/w/:workspaceId/chat/:sessionId" element={<Page />} />
    </Routes></TooltipProvider>, { initialEntries: ["/w/test-workspace/chat/concurrent-A"] });
    try {
      await screen.findByText("极速");
      await waitFor(() => expect(apiClient.get).toHaveBeenCalledWith("/providers/provider-1/models"));
      for (const label of labels) {
        if (label !== "A") fireEvent.click(screen.getByRole("button", { name: `窗口${label}` }));
        await waitFor(() => expect(view.container.querySelector(".chat-canvas-page")).toHaveAttribute("data-session-id", label === "D" ? "new" : `concurrent-${label}`));
        const composer = view.container.querySelector("textarea")!;
        fireEvent.change(composer, { target: { value: `问题${label}` } });
        fireEvent.submit(composer.closest("form")!);
        if (label === "D") {
          await waitFor(() => expect(apiClient.post).toHaveBeenCalledWith("/sessions", expect.anything(), expect.anything()));
          await act(async () => { created.resolve(sessions[3]); });
        }
        await waitFor(() => expect(screen.getByText(`回复${label}`)).toBeInTheDocument());
      }
      expect(apiClient.postSse).toHaveBeenCalledTimes(4);
      const backgroundControllers = sessions.slice(0, 3).map((session) => getSessionStream(session.id)!.controller);
      await act(async () => { advance.resolve(); });
      await waitFor(() => expect(screen.getByText("回复D继续D")).toBeInTheDocument());
      expect(leakedCommits).toEqual([]);
      for (const label of labels.slice(0, 3)) {
        expect(screen.queryByText(`问题${label}`)).not.toBeInTheDocument();
        expect(screen.queryByText(`回复${label}继续${label}`)).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: `窗口${label}` }));
        await waitFor(() => expect(screen.getByText(`回复${label}继续${label}`)).toBeInTheDocument());
        expect(screen.queryByText("回复D继续D")).not.toBeInTheDocument();
      }
      expect(backgroundControllers.every((controller) => !controller.signal.aborted)).toBe(true);
      await act(async () => { finish.resolve(); });
      await waitFor(() => expect(sessions.every((session) => !getSessionStream(session.id))).toBe(true));
    } finally {
      view.unmount();
      await act(async () => { created.resolve(sessions[3]); advance.resolve(); finish.resolve(); });
      sessions.forEach((session) => abortSessionStream(session.id));
    }
  }, 20_000);

  it.each(["suggestion", "composer", "page return", "slow history"])("keeps the first turn and composer mounted when sending via %s", async (entry) => {
    const created = deferred<Session>();
    const firstChunk = deferred<void>();
    const secondChunk = deferred<void>();
    const returnedChunk = deferred<void>();
    const finalText = "第一段流式回复，第二段继续出现" + (entry === "page return" ? "，返回后继续输出" : "");
    const streamFinished = deferred<void>();
    const delayedHistory = deferred<{ items: Message[]; total_count: number; has_more_before: boolean }>();
    const queryClient = createTestQueryClient();
    let holdHistory = false;
    let heldHistoryStarted = false;
    const session = {
      id: "first-session", workspace_id: "test-workspace", title: "新会话",
      status: "active", memory_enabled: true, model_snapshot: {},
      context_capsule: {}, project_id: null, graph_id: null, goal_id: null,
    } as Session;
    const persistedUser: Message = {
      id: "durable-user", workspace_id: "test-workspace", session_id: session.id,
      parent_message_id: null, role: "user", version: 1, status: "completed",
      content: "我现在最应该先学什么？", parts: [], provider_trace: {}, created_at: new Date().toISOString(),
    };
    const persistedAssistant: Message = {
      ...persistedUser, id: "durable-assistant", parent_message_id: persistedUser.id, role: "assistant",
      content: finalText,
      parts: [{ id: "answer-text", type: "text", status: "completed", content: finalText }],
    };
    vi.spyOn(apiClient, "get").mockImplementation(async (path) => {
      if (path === "/providers") return [{
        id: "provider-1", provider_type: "openai_compatible_chat",
        display_name: "测试模型", enabled: true, remote_capability: true,
        capabilities: { default_model: "model-1" },
      }];
      if (path === "/providers/provider-1/models") return {
        provider_id: "provider-1", status: "ok",
        models: [{ id: "model-1", roles: ["llm"], enabled: true, remote: true, streaming: true }],
      };
      if (path === "/settings") return [{
        key: "chat.default_response_mode", value: { response_mode: "fast" },
      }];
      if (path === "/sessions/first-session/messages") {
        if (holdHistory) { heldHistoryStarted = true; return delayedHistory.promise; }
        return { items: [], total_count: 0, has_more_before: false };
      }
      if (path === "/sessions/first-session/messages/durable-user") return persistedUser;
      if (path === "/sessions/first-session/messages/durable-assistant") return persistedAssistant;
      return [];
    });
    vi.spyOn(apiClient, "post").mockImplementation((path) => {
      if (path === "/sessions") return created.promise;
      return Promise.reject(new Error(`Unexpected write: ${path}`));
    });
    vi.spyOn(apiClient, "postSse").mockImplementation(() => (async function* () {
      yield { event: "message.accepted", rawData: "", data: {
        type: "message.accepted", message_id: persistedAssistant.id, payload: { user_message_id: persistedUser.id },
      } };
      await firstChunk.promise;
      yield { event: "part.delta", rawData: "", data: {
        type: "part.delta", part: { id: "answer-text", type: "text", status: "streaming", sequence: 1, content_delta: "第一段流式回复" },
      } };
      await secondChunk.promise;
      yield { event: "part.delta", rawData: "", data: {
        type: "part.delta", part: { id: "answer-text", type: "text", status: "streaming", sequence: 1, content_delta: "，第二段继续出现" },
      } };
      if (entry === "page return") {
        await returnedChunk.promise;
        yield { event: "part.delta", rawData: "", data: {
          type: "part.delta", part: { id: "answer-text", type: "text", status: "streaming", sequence: 1, content_delta: "，返回后继续输出" },
        } };
      }
      await streamFinished.promise;
      // The live agent endpoint may classify its text only after the last
      // token. Partial text must already be visible before this boundary.
      yield { event: "answer.started", rawData: "", data: {
        type: "answer.started", payload: { final_part_id: "answer-text", boundary_sequence: 1 },
      } };
      yield { event: "message.completed", rawData: "", data: {
        type: "message.completed", message_id: persistedAssistant.id,
      } };
    })());

    const commits: {
      pathname: string;
      empty: boolean;
      hasQuestion: boolean;
      userCount: number;
      composer: HTMLTextAreaElement | null;
    }[] = [];
    const question = "我现在最应该先学什么？";
    function Page() {
      const location = useLocation();
      const navigate = useNavigate();
      return <>
        <button onClick={() => navigate("/w/test-workspace/chat/new")}>新建测试会话</button>
        <button onClick={() => navigate("/w/test-workspace/chat/other-session")}>切换测试会话</button>
        <button onClick={() => navigate("/w/test-workspace/chat/first-session")}>返回测试会话</button>
        <button onClick={() => navigate("/w/test-workspace/home")}>离开聊天页面</button>
        <Profiler id="chat" onRender={() => commits.push({
          pathname: location.pathname,
          empty: Boolean(screen.queryByRole("heading", { name: "从一个问题开始" })),
          hasQuestion: Boolean(document.querySelector("[data-message-id^='temp-user-']")?.textContent?.includes(question)),
          userCount: document.querySelectorAll("[data-message-id^='temp-user-']").length,
          composer: document.querySelector("textarea"),
        })}>
          <ChatCanvasPage />
        </Profiler>
      </>;
    }
    function Home() {
      const navigate = useNavigate();
      return <button onClick={() => navigate("/w/test-workspace/chat/first-session")}>从首页返回聊天</button>;
    }
    const router = createMemoryRouter([{ path: "*", element: <Routes>
        <Route path="/w/:workspaceId" element={<WorkspaceShell />}>
          <Route path="chat/:sessionId" element={<Page />} />
          <Route path="home" element={<Home />} />
        </Route>
      </Routes> }], { initialEntries: ["/w/test-workspace/chat/new"] });
    const view = render(
      <QueryClientProvider client={queryClient}>
        <TooltipProvider><RouterProvider router={router} /></TooltipProvider>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: question })).toBeEnabled());
    await screen.findByText("极速");
    await waitFor(() => expect(apiClient.get).toHaveBeenCalledWith("/providers/provider-1/models"));
    const composer = view.container.querySelector("textarea");
    expect(composer).not.toBeNull();
    if (entry === "suggestion") {
      fireEvent.click(screen.getByRole("button", { name: question }));
    } else {
      fireEvent.change(composer!, { target: { value: question } });
      fireEvent.submit(composer!.closest("form")!);
    }
    await waitFor(() => expect(apiClient.post).toHaveBeenCalledWith("/sessions", expect.anything(), expect.anything()));
    expect(screen.queryByRole("heading", { name: "从一个问题开始" })).not.toBeInTheDocument();
    fireEvent.change(composer!, { target: { value: "下一个问题" } });
    commits.length = 0;
    await act(async () => { created.resolve(session); });
    await waitFor(() => expect(apiClient.postSse).toHaveBeenCalled());
    await waitFor(() => expect(commits.some((commit) => commit.pathname.endsWith("/first-session"))).toBe(true));

    try {
      // Cross the actual route exit/enter window, then deliver separate SSE
      // chunks while the response is still open. An immediate assertion can
      // pass against AnimatePresence's outgoing canvas before it unmounts.
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 600)); });
      expect(view.container.querySelector(".chat-canvas-page")).toHaveAttribute("data-session-id", session.id);
      await act(async () => { firstChunk.resolve(); });
      await waitFor(() => expect(screen.getByText("第一段流式回复")).toBeInTheDocument());
      await act(async () => { secondChunk.resolve(); });
      await waitFor(() => expect(screen.getByText("第一段流式回复，第二段继续出现")).toBeInTheDocument());
      expect(commits.every((commit) => !commit.empty)).toBe(true);
      expect(commits.every((commit) => commit.hasQuestion && commit.userCount === 1)).toBe(true);
      expect(commits.every((commit) => commit.composer === composer)).toBe(true);
      expect(view.container.querySelector("textarea")).toBe(composer);
      expect(composer).toHaveValue("下一个问题");
      expect(within(view.container.querySelector(".chat-canvas-page") as HTMLElement).getByText(question)).toBeInTheDocument();
      if (entry === "page return") {
        fireEvent.click(screen.getByRole("button", { name: "离开聊天页面" }));
        await screen.findByRole("button", { name: "从首页返回聊天" });
        expect(view.container.querySelector(".chat-canvas-page")).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: "从首页返回聊天" }));
        await waitFor(() => expect(view.container.querySelector(".chat-canvas-page")).toHaveAttribute("data-session-id", session.id));
        await waitFor(() => expect(screen.getByText("第一段流式回复，第二段继续出现")).toBeInTheDocument());
        await act(async () => { returnedChunk.resolve(); });
        await waitFor(() => expect(screen.getByText(finalText)).toBeInTheDocument());
        fireEvent.change(view.container.querySelector('textarea')!, { target: { value: '' } });
        await waitFor(() => expect(screen.getByRole("button", { name: "中断回答" })).toBeEnabled());
      }
      fireEvent.click(screen.getByRole("button", { name: "切换测试会话" }));
      expect(await screen.findByRole("heading", { name: "从一个问题开始" })).toBeInTheDocument();
      expect(screen.queryByText(finalText)).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "返回测试会话" }));
      await waitFor(() => expect(screen.getByText(finalText)).toBeInTheDocument());
      expect(apiClient.postSse).toHaveBeenCalledTimes(1);
      let historyRead: Promise<void> | undefined;
      if (entry === "slow history") {
        holdHistory = true;
        await act(async () => {
          historyRead = queryClient.invalidateQueries({ queryKey: ['workspace', 'test-workspace', 'messages', session.id], exact: true });
        });
        await waitFor(() => expect(heldHistoryStarted).toBe(true));
      }
      commits.length = 0;
      await act(async () => { streamFinished.resolve(); });
      await waitFor(() => expect(view.container.querySelector('[data-message-id="durable-assistant"]')).toBeInTheDocument());
      if (entry === "slow history") {
        await act(async () => {
          delayedHistory.resolve({ items: [], total_count: 0, has_more_before: false });
          await historyRead;
        });
        expect(screen.getByText(finalText)).toBeInTheDocument();
      }
      expect(commits.every((commit) => !commit.empty)).toBe(true);
      expect(screen.queryByRole("button", { name: "中断回答" })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "新建测试会话" }));
      expect(await screen.findByRole("heading", { name: "从一个问题开始" })).toBeInTheDocument();
      expect(view.container.querySelector("textarea")).not.toBe(composer);
      expect(view.container.querySelector("textarea")).toHaveValue("");
    } finally {
      abortSessionStream(session.id);
      await act(async () => { firstChunk.resolve(); secondChunk.resolve(); returnedChunk.resolve(); delayedHistory.resolve({ items: [], total_count: 0, has_more_before: false }); streamFinished.resolve(); });
      view.unmount();
    }
  }, 15_000);
});
