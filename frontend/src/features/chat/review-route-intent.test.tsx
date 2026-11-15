import { QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { apiClient } from "@/api/client";
import { TooltipProvider } from "@/components/ui/tooltip";
import { abortSessionStream, getSessionStream } from "@/lib/session-streams";
import { setSessionComposerPrefs } from "@/lib/session-composer-prefs";
import { workspaceQueryKey } from "@/lib/query-keys";
import { createTestQueryClient } from "@/test/render";
import { deferred } from "@/test/async";
import type { Message, Session } from "@/types/sessions";
import { ChatCanvasPage } from "./chat-pages";

vi.mock("@/features/auth/auth-context-value", () => ({
  useAuth: () => ({ workspaceId: "test-workspace", effectivePermissions: [] }),
}));

describe("review navigation intent", () => {
  it.each(["cached", "slow models", "slow session", "slow sandbox", "mismatched node"])(
    "consumes the node and prompt atomically and sends one turn: %s",
    async (entry) => {
      const queryClient = createTestQueryClient();
      const session = {
        id: `review-${entry.replaceAll(" ", "-")}`, workspace_id: "test-workspace",
        title: "新会话", status: "active", memory_enabled: true,
        model_snapshot: {}, context_capsule: {}, graph_id: "graph-review",
        current_node_id: "os-overview", project_id: null, goal_id: null,
      } as Session;
      const prompt = "请围绕学习节点「操作系统概述与运行机制」生成图文并茂的学习内容，并按知识点逐段呈现。";
      const learningNode = { graphId: session.graph_id!, nodeId: "os-overview", nodeLabel: "操作系统概述与运行机制" };
      const provider = {
        id: "review-provider", provider_type: "openai_compatible_chat", display_name: "测试模型",
        enabled: true, remote_capability: true, capabilities: entry === "slow models" ? {} : { default_model: "review-model" },
      };
      const models = {
        provider_id: provider.id, status: "ok",
        models: [{ id: "review-model", roles: ["llm"], enabled: true, remote: true, streaming: true }],
      };
      const modelReady = deferred<typeof models>();
      const sessionReady = deferred<Session>();
      const sandboxReady = deferred<{ available: boolean }>();
      const finish = deferred<void>();
      const user: Message = {
        id: `${session.id}-user`, workspace_id: session.workspace_id, session_id: session.id,
        parent_message_id: null, role: "user", version: 1, status: "completed",
        content: prompt, parts: [], provider_trace: {}, created_at: new Date().toISOString(),
      };
      const assistant: Message = {
        ...user, id: `${session.id}-assistant`, parent_message_id: user.id,
        role: "assistant", content: "复习内容", parts: [{ id: "review-answer", type: "text", status: "completed", content: "复习内容" }],
      };
      const graph = { id: session.graph_id, title: "操作系统原理", nodes: [{ id: learningNode.nodeId, label: learningNode.nodeLabel }], edges: [] };
      const settings = [{ key: "chat.default_response_mode", value: { response_mode: "fast" } }];
      setSessionComposerPrefs(session.id, { providerId: provider.id, modelId: "review-model", responseMode: entry === "slow sandbox" ? "agentic" : "fast", thinkingMode: "off", searchRoute: "disabled", generationMode: "text" });
      queryClient.setQueryData(workspaceQueryKey("test-workspace", "providers"), [provider]);
      queryClient.setQueryData(workspaceQueryKey("test-workspace", "settings"), settings);
      queryClient.setQueryData(workspaceQueryKey("test-workspace", "messages", session.id), []);
      if (entry !== "slow session") queryClient.setQueryData(workspaceQueryKey("test-workspace", "sessions"), [session]);
      if (entry !== "slow models") queryClient.setQueryData(workspaceQueryKey("test-workspace", "provider-models", provider.id), models);
      vi.spyOn(apiClient, "get").mockImplementation(async (path) => {
        if (path === "/providers") return [provider];
        if (path === `/providers/${provider.id}/models`) return entry === "slow models" ? modelReady.promise : models;
        if (path === "/settings") return settings;
        if (path === "/sandbox/agent/readiness") return sandboxReady.promise;
        if (path === `/sessions/${session.id}`) return entry === "slow session" ? sessionReady.promise : session;
        if (path === `/graphs/${session.graph_id}`) return graph;
        if (path === `/sessions/${session.id}/messages/${user.id}`) return user;
        if (path === `/sessions/${session.id}/messages/${assistant.id}`) return assistant;
        if (path.endsWith("/messages")) return { items: [], total_count: 0, has_more_before: false };
        return [];
      });
      vi.spyOn(apiClient, "getConditional").mockImplementation((async (path: string, _etag?: string | null, options?: Parameters<typeof apiClient.getConditional>[2]) => ({
        notModified: false, etag: null, data: await apiClient.get(path, options),
      })) as typeof apiClient.getConditional);
      vi.spyOn(apiClient, "post").mockImplementation(async (path) => {
        if (path === `/sessions/${session.id}/auto-title`) return { ...session, title: "操作系统复习" };
        throw new Error(`Unexpected write: ${path}`);
      });
      vi.spyOn(apiClient, "postSse").mockImplementation(() => (async function* () {
        yield { event: "message.accepted", rawData: "", data: {
          type: "message.accepted", message_id: assistant.id, payload: { user_message_id: user.id },
        } };
        await finish.promise;
        yield { event: "message.completed", rawData: "", data: { type: "message.completed", message_id: assistant.id } };
      })());
      const router = createMemoryRouter([{ path: "/w/:workspaceId/chat/:sessionId", element: <ChatCanvasPage /> }], {
        initialEntries: [{ pathname: `/w/test-workspace/chat/${session.id}`, state: {
          pendingPrompt: prompt,
          learningNode: entry === "mismatched node" ? { ...learningNode, nodeId: "another-node" } : learningNode,
        } }],
      });
      const view = render(<StrictMode><QueryClientProvider client={queryClient}>
        <TooltipProvider><RouterProvider router={router} /></TooltipProvider>
      </QueryClientProvider></StrictMode>);
      try {
        if (entry === "slow sandbox") {
          await waitFor(() => expect(router.state.location.state).toBeNull());
          await waitFor(() => expect(apiClient.get).toHaveBeenCalledWith("/sandbox/agent/readiness"));
          expect(apiClient.postSse).not.toHaveBeenCalled();
          await act(async () => {
            await queryClient.invalidateQueries({ queryKey: workspaceQueryKey("test-workspace", "settings") });
            sandboxReady.resolve({ available: true });
          });
        }
        if (entry === "slow models" || entry === "slow session") {
          const path = entry === "slow models" ? `/providers/${provider.id}/models` : `/sessions/${session.id}`;
          await waitFor(() => expect(apiClient.get).toHaveBeenCalledWith(path, ...(entry === "slow session" ? [expect.anything()] : [])));
          expect(apiClient.postSse).not.toHaveBeenCalled();
          expect(router.state.location.state).toMatchObject({ pendingPrompt: prompt, learningNode });
          await act(async () => { modelReady.resolve(models); sessionReady.resolve(session); });
        }
        if (entry === "mismatched node") {
          await waitFor(() => expect(router.state.location.state).toBeNull());
          await act(async () => { await queryClient.invalidateQueries({ queryKey: workspaceQueryKey("test-workspace", "settings") }); });
          expect(apiClient.postSse).not.toHaveBeenCalled();
          expect(view.container.querySelectorAll("[data-message-id^='temp-user-']")).toHaveLength(0);
          return;
        }
        await waitFor(() => expect(apiClient.postSse).toHaveBeenCalledTimes(1));
        expect(apiClient.postSse).toHaveBeenCalledWith(`/sessions/${session.id}/messages/stream`, expect.objectContaining({ content: prompt, node_ids: [learningNode.nodeId] }), expect.anything());
        await waitFor(() => expect(router.state.location.state).toBeNull());
        expect(view.container.querySelectorAll("[data-message-id^='temp-user-']")).toHaveLength(1);
        expect(view.container.querySelectorAll("[data-message-id^='temp-assistant-']")).toHaveLength(1);
        // Settings/history refetches must not re-arm the consumed navigation.
        await act(async () => { await queryClient.invalidateQueries({ queryKey: workspaceQueryKey("test-workspace", "settings") }); });
        expect(apiClient.postSse).toHaveBeenCalledTimes(1);
        await waitFor(() => expect(apiClient.post).toHaveBeenCalledWith(`/sessions/${session.id}/auto-title`, expect.objectContaining({ source_message_id: user.id })));
        await act(async () => { finish.resolve(); });
        await waitFor(() => expect(getSessionStream(session.id)).toBeUndefined());
        expect(view.container.querySelectorAll(`[data-message-id='${user.id}']`)).toHaveLength(1);
        expect(await screen.findByText("复习内容")).toBeInTheDocument();
        expect(apiClient.postSse).toHaveBeenCalledTimes(1);
      } finally {
        view.unmount();
        abortSessionStream(session.id);
        await act(async () => { modelReady.resolve(models); sessionReady.resolve(session); sandboxReady.resolve({ available: true }); finish.resolve(); });
        queryClient.clear();
        router.dispose();
      }
    }, 15_000,
  );
});
