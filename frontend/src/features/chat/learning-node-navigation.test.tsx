// Keep the real rail, sidebar navigation, route consumer and streaming path.
import "@/components/graph/knowledge-graph";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { apiClient } from "@/api/client";
import { WorkspaceShell } from "@/components/layout/workspace-shell";
import type { KnowledgeNode } from "@/components/graph/knowledge-graph";
import { TooltipProvider } from "@/components/ui/tooltip";
import { workspaceQueryKey } from "@/lib/query-keys";
import { setSessionComposerPrefs } from "@/lib/session-composer-prefs";
import { abortSessionStream, getSessionStream } from "@/lib/session-streams";
import { createTestQueryClient } from "@/test/render";
import { deferred } from "@/test/async";
import type { Graph } from "@/types/graphs";
import type { Message, Session } from "@/types/sessions";
import { ChatCanvasPage } from "./chat-pages";

vi.mock("@/features/auth/auth-context-value", () => ({
  useAuth: () => ({ workspaceId: "test-workspace", username: "tester", effectivePermissions: [] }),
}));
vi.mock("@xyflow/react", () => ({
  ReactFlow: (props: { nodes: KnowledgeNode[]; onNodeClick: (event: unknown, node: KnowledgeNode) => void }) => <div>
    {props.nodes.map(node => <button type="button" key={node.id} aria-label={`预览${node.data.label}`}
      data-current={String(node.data.current)} onClick={() => props.onNodeClick({}, node)}>{node.data.label}</button>)}
  </div>,
  Background: () => null, Controls: () => null, MiniMap: () => null, Handle: () => null, BaseEdge: () => null,
  Position: { Top: "top", Bottom: "bottom", Left: "left", Right: "right" }, BackgroundVariant: { Dots: "dots" },
  applyNodeChanges: (_changes: unknown, nodes: unknown) => nodes,
}));

const graph = {
  id: "study-graph", title: "学习图谱", goal_id: "goal", status: "published", revision: 1,
  nodes: [
    { id: "root", label: "根节点", node_type: "root" },
    { id: "node-a", label: "当前知识点", node_type: "concept" },
    { id: "node-b", label: "待学知识点", node_type: "concept" },
  ].map(node => ({ ...node, description: "学习说明", mastery_stars: 0, retrieval_state: "unlearned", evidence_state: "none", attention_state: "normal" })),
  edges: ["node-a", "node-b"].map(id => ({ id: `e-${id}`, source_node_id: "root", target_node_id: id, relation: "contains" })),
} as Graph;
const project = { id: "study-project", title: "学习文件夹", status: "active", graph_id: graph.id, primary_goal_id: "goal" };
const source = {
  id: "study-source", workspace_id: "test-workspace", title: "已有会话", status: "active", memory_enabled: true,
  graph_id: graph.id, current_node_id: "node-a", project_id: project.id, context_capsule: {},
  created_at: "2026-11-11T00:00:00Z", updated_at: "2026-11-11T00:00:00Z", session_kind: "main",
  model_snapshot: { provider_id: "study-provider", model_id: "study-model", thinking_mode: "off", agent_mode: false },
} as Session;
const destination = { ...source, id: "study-destination", current_node_id: "node-b", title: "新会话" };

function setup(options: { reuse?: boolean; sameNode?: boolean; createFails?: boolean; narrow?: boolean; slowSandbox?: boolean } = {}) {
  const queryClient = createTestQueryClient();
  const target = options.sameNode ? source : destination;
  const records = new Map([[source.id, source], [target.id, target]]);
  const settings = [{ key: "chat.default_response_mode", value: { response_mode: "fast" } }];
  const provider = { id: "study-provider", provider_type: "openai_compatible_chat", display_name: "测试模型", enabled: true,
    remote_capability: true, capabilities: { default_model: "study-model" } };
  const models = { provider_id: provider.id, status: "ok", models: [{ id: "study-model", roles: ["llm"], enabled: true, remote: true, streaming: true }] };
  const finish = deferred<void>();
  const createReady = deferred<Session>();
  const sandboxReady = deferred<{ available: boolean }>();
  const user = { id: `${target.id}-user`, workspace_id: source.workspace_id, session_id: target.id,
    parent_message_id: null, role: "user", version: 1, status: "completed", content: "学习问题", parts: [], provider_trace: {}, created_at: source.created_at } as Message;
  const assistant = { ...user, id: `${target.id}-answer`, parent_message_id: user.id, role: "assistant",
    content: "节点讲解已生成", parts: [{ id: "study-text", type: "text", status: "completed", content: "节点讲解已生成" }] } as Message;
  for (const record of records.values()) setSessionComposerPrefs(record.id, { providerId: provider.id, modelId: "study-model", responseMode: options.slowSandbox ? "agentic" : "fast", thinkingMode: "off" });
  queryClient.setQueryData(workspaceQueryKey("test-workspace", "settings"), settings);
  queryClient.setQueryData(workspaceQueryKey("test-workspace", "providers"), [provider]);
  queryClient.setQueryData(workspaceQueryKey("test-workspace", "provider-models", provider.id), models);
  vi.spyOn(apiClient, "get").mockImplementation(async (path, request) => {
    if (path === "/sessions/page") {
      const nodeId = request?.query?.node_id;
      const items = nodeId && options.reuse ? [target] : [];
      // The production browser API deliberately strips current_node_id.
      return { items: items.map(({ current_node_id: _node, ...item }) => ({ ...item, has_children: false })), has_more: false, has_previous: false, next_cursor: null, previous_cursor: null };
    }
    if (path === "/sessions/summary") return { total: 1, active: 1, archived: 0, memory_enabled: 1 };
    if (path === "/providers") return [provider];
    if (path === `/providers/${provider.id}/models`) return models;
    if (path === "/sandbox/agent/readiness") return sandboxReady.promise;
    if (path === "/settings") return settings;
    if (path === "/projects") return [project];
    if (path === "/graphs") return [graph];
    if (path === `/graphs/${graph.id}`) return graph;
    if (path === `/sessions/${target.id}/messages/${user.id}`) return user;
    if (path === `/sessions/${target.id}/messages/${assistant.id}`) return assistant;
    if (path.endsWith("/messages")) return { items: [], total_count: 0, has_more_before: false };
    if (/^\/sessions\/[^/]+$/.test(path)) return records.get(path.split("/").at(-1)!);
    return [];
  });
  vi.spyOn(apiClient, "getConditional").mockImplementation((async (path: string, _etag?: string | null, request?: Parameters<typeof apiClient.getConditional>[2]) => ({
    notModified: false, etag: null, data: await apiClient.get(path, request),
  })) as typeof apiClient.getConditional);
  let attempts = 0;
  vi.spyOn(apiClient, "post").mockImplementation(async (path) => {
    if (path === "/sessions/opening-prompts") return { prompts: [] };
    if (path === `/graphs/${graph.id}/learning-folder`) return project;
    if (path === "/sessions") {
      if (options.createFails && attempts++ === 0) throw new Error("创建会话失败，请重试");
      return createReady.promise;
    }
    if (path.endsWith("/auto-title")) return target;
    throw new Error(`Unexpected write: ${path}`);
  });
  vi.spyOn(apiClient, "patch").mockResolvedValue(source);
  vi.spyOn(apiClient, "postSse").mockImplementation(() => (async function* () {
    yield { event: "message.accepted", rawData: "", data: { type: "message.accepted", message_id: assistant.id, payload: { user_message_id: user.id } } };
    await finish.promise;
    yield { event: "message.completed", rawData: "", data: { type: "message.completed", message_id: assistant.id } };
  })());
  if (options.narrow) vi.spyOn(window, "matchMedia").mockImplementation(query => ({ matches: query.includes("max-width"), media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: () => true }));
  const router = createMemoryRouter([{ path: "/w/:workspaceId", element: <WorkspaceShell />, children: [
    { path: "chat/:sessionId", element: <ChatCanvasPage /> },
  ] }], { initialEntries: [`/w/test-workspace/chat/${source.id}`] });
  const view = render(<StrictMode><QueryClientProvider client={queryClient}><TooltipProvider><RouterProvider router={router} /></TooltipProvider></QueryClientProvider></StrictMode>);
  return { ...view, queryClient, router, target, finish, createReady, sandboxReady,
    async dispose() {
      view.unmount();
      abortSessionStream(target.id);
      await act(async () => { finish.resolve(); createReady.resolve(target); sandboxReady.resolve({ available: true }); });
      queryClient.clear(); router.dispose();
    },
  };
}

async function previewTarget() {
  await waitFor(() => expect(screen.getByRole("button", { name: "预览当前知识点" })).toHaveAttribute("data-current", "true"));
  fireEvent.click(screen.getByRole("button", { name: "预览待学知识点" }));
  expect(screen.getByRole("status", { name: "会话节点绑定" })).toHaveTextContent("预览");
  expect(apiClient.patch).not.toHaveBeenCalled();
  expect(apiClient.postSse).not.toHaveBeenCalled();
  expect(vi.mocked(apiClient.post).mock.calls.filter(([path]) => path === "/sessions")).toHaveLength(0);
}

async function assertSingleTurn(view: ReturnType<typeof setup>) {
  await waitFor(() => expect(apiClient.postSse).toHaveBeenCalledTimes(1));
  expect(apiClient.postSse).toHaveBeenCalledWith(`/sessions/${view.target.id}/messages/stream`, expect.objectContaining({
    content: `什么是 ${view.target.current_node_id === "node-a" ? "当前知识点" : "待学知识点"}？`, node_ids: [view.target.current_node_id],
  }), expect.anything());
  expect(view.router.state.location.pathname).toBe(`/w/test-workspace/chat/${view.target.id}`);
  await waitFor(() => expect(view.router.state.location.state).toBeNull());
  await act(async () => { await view.queryClient.invalidateQueries({ queryKey: workspaceQueryKey("test-workspace", "settings") }); });
  expect(apiClient.postSse).toHaveBeenCalledTimes(1);
  expect(apiClient.patch).not.toHaveBeenCalled();
  expect(view.queryClient.getQueryData<Session>(workspaceQueryKey("test-workspace", "sessions", "detail", source.id))?.current_node_id).toBe("node-a");
  await act(async () => { view.finish.resolve(); });
  await waitFor(() => expect(getSessionStream(view.target.id)).toBeUndefined());
  expect(await screen.findByText("节点讲解已生成")).toBeInTheDocument();
  expect(apiClient.postSse).toHaveBeenCalledTimes(1);
}

describe("rail learning button navigation", () => {
  it("creates a durably bound session and submits exactly once after a double click", async () => {
    const view = setup();
    try {
      await previewTarget();
      const button = screen.getByRole("button", { name: "学习此节点" });
      fireEvent.click(button); fireEvent.click(button);
      await waitFor(() => expect(apiClient.post).toHaveBeenCalledWith("/sessions", expect.objectContaining({ graph_id: graph.id, current_node_id: "node-b", project_id: project.id }), expect.anything()));
      expect(vi.mocked(apiClient.post).mock.calls.filter(([path]) => path === "/sessions")).toHaveLength(1);
      await act(async () => { view.createReady.resolve(view.target); });
      await assertSingleTurn(view);
    } finally { await view.dispose(); }
  }, 15_000);

  it.each([false, true])("resumes an eligible node session and submits without creating another (same route: %s)", async sameNode => {
    const view = setup({ reuse: true, sameNode });
    try {
      if (!sameNode) await previewTarget();
      const button = await screen.findByRole("button", { name: sameNode ? "继续学习" : "学习此节点" });
      fireEvent.click(button); fireEvent.click(button);
      await assertSingleTurn(view);
      expect(vi.mocked(apiClient.post).mock.calls.filter(([path]) => path === "/sessions")).toHaveLength(0);
    } finally { await view.dispose(); }
  }, 15_000);

  it("ignores the trailing native double-click even after a fast answer, but accepts a fresh click", async () => {
    const view = setup({ reuse: true, sameNode: true });
    const navigation = vi.fn();
    try {
      const button = await screen.findByRole("button", { name: "继续学习" });
      fireEvent.click(button, { detail: 1 });
      await assertSingleTurn(view);
      window.addEventListener("learngraph:open-learning-project", navigation);
      fireEvent.click(screen.getByRole("button", { name: "继续学习" }), { detail: 2 });
      expect(navigation).not.toHaveBeenCalled();
      expect(apiClient.postSse).toHaveBeenCalledTimes(1);
      fireEvent.click(screen.getByRole("button", { name: "继续学习" }), { detail: 1 });
      expect(navigation).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener("learngraph:open-learning-project", navigation);
      await view.dispose();
    }
  }, 15_000);

  it("does not submit twice if learning is clicked again during sandbox preflight", async () => {
    const view = setup({ reuse: true, sameNode: true, slowSandbox: true });
    try {
      fireEvent.click(await screen.findByRole("button", { name: "继续学习" }));
      await waitFor(() => expect(apiClient.get).toHaveBeenCalledWith("/sandbox/agent/readiness"));
      await waitFor(() => expect(view.router.state.location.state).toBeNull());
      expect(apiClient.postSse).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "继续学习" }));
      await waitFor(() => expect(vi.mocked(apiClient.post).mock.calls.filter(([path]) => path.endsWith("/learning-folder"))).toHaveLength(2));
      await waitFor(() => expect(view.router.state.location.state).toBeNull());
      await act(async () => { view.sandboxReady.resolve({ available: true }); });
      await assertSingleTurn(view);
    } finally { await view.dispose(); }
  }, 15_000);

  it("handles one learning event once when desktop and mobile navigation are both mounted", async () => {
    const view = setup({ narrow: true });
    try {
      await waitFor(() => expect(view.container.querySelector(".mobile-nav-drawer .sidebar-nav")).not.toBeNull());
      await previewTarget();
      fireEvent.click(screen.getByRole("button", { name: "学习此节点" }));
      await waitFor(() => expect(vi.mocked(apiClient.post).mock.calls.filter(([path]) => path === "/sessions").length).toBeGreaterThan(0));
      await act(async () => { view.createReady.resolve(view.target); });
      await assertSingleTurn(view);
      expect(vi.mocked(apiClient.post).mock.calls.filter(([path]) => path === "/sessions")).toHaveLength(1);
    } finally { await view.dispose(); }
  }, 15_000);

  it("allows an explicit retry after a failed auto-send without re-creating the bound session", async () => {
    const view = setup({ reuse: true, sameNode: true });
    const completedStream = vi.mocked(apiClient.postSse).getMockImplementation()!;
    vi.mocked(apiClient.postSse).mockImplementationOnce(() => (async function* () {
      yield { event: "message.failed", rawData: "", data: { type: "message.failed", payload: { error: { code: "provider_error", message: "模型失败，请重试" } } } };
    })());
    try {
      fireEvent.click(await screen.findByRole("button", { name: "继续学习" }));
      await waitFor(() => expect(apiClient.postSse).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(getSessionStream(source.id)).toBeUndefined());
      await waitFor(() => expect(view.router.state.location.state).toBeNull());
      // Re-renders/refetches do not replay a failed intent. Only a new click retries.
      await act(async () => { await view.queryClient.invalidateQueries({ queryKey: workspaceQueryKey("test-workspace", "settings") }); });
      expect(apiClient.postSse).toHaveBeenCalledTimes(1);
      vi.mocked(apiClient.postSse).mockClear().mockImplementation(completedStream);
      fireEvent.click(screen.getByRole("button", { name: "继续学习" }));
      await assertSingleTurn(view);
      expect(vi.mocked(apiClient.post).mock.calls.filter(([path]) => path === "/sessions")).toHaveLength(0);
    } finally { await view.dispose(); }
  }, 15_000);

  it("releases failed creation so a retry can create and submit once", async () => {
    const view = setup({ createFails: true });
    try {
      await previewTarget();
      fireEvent.click(screen.getByRole("button", { name: "学习此节点" }));
      await waitFor(() => expect(vi.mocked(apiClient.post).mock.calls.filter(([path]) => path === "/sessions")).toHaveLength(1));
      await act(async () => { await Promise.resolve(); });
      expect(apiClient.postSse).not.toHaveBeenCalled();
      expect(view.router.state.location.pathname).toContain(source.id);
      fireEvent.click(screen.getByRole("button", { name: "学习此节点" }));
      await waitFor(() => expect(vi.mocked(apiClient.post).mock.calls.filter(([path]) => path === "/sessions")).toHaveLength(2));
      await act(async () => { view.createReady.resolve(view.target); });
      await assertSingleTurn(view);
    } finally { await view.dispose(); }
  }, 15_000);
});
