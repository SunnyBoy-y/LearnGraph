// Preload the real canvas so lazy module transformation is not part of interaction timing.
import "@/components/graph/knowledge-graph";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Route, Routes, useNavigate } from "react-router-dom";
import { apiClient } from "@/api/client";
import { ContextRail } from "@/components/layout/workspace-shell";
import type { KnowledgeNode } from "@/components/graph/knowledge-graph";
import { TooltipProvider } from "@/components/ui/tooltip";
import { workspaceQueryKey } from "@/lib/query-keys";
import { setSessionComposerPrefs } from "@/lib/session-composer-prefs";
import { getSessionStream } from "@/lib/session-streams";
import { renderWithProviders } from "@/test/render";
import type { Graph } from "@/types/graphs";
import type { Session } from "@/types/sessions";
import { ChatCanvasPage } from "./chat-pages";

vi.mock("@/features/auth/auth-context-value", () => ({
  useAuth: () => ({ workspaceId: "test-workspace", username: "tester", effectivePermissions: [] }),
}));

// Replace only the browser canvas; keep the real graph, rail, binding hook and chat.
vi.mock("@xyflow/react", () => ({
  ReactFlow: (props: { nodes: KnowledgeNode[]; edges: unknown[]; fitView: boolean; minZoom: number; onNodeClick: (event: unknown, node: KnowledgeNode) => void }) => <div
    data-testid="rail-graph-flow" data-fit-view={String(props.fitView)} data-min-zoom={props.minZoom} data-edge-count={props.edges.length}>
    {props.nodes.map((node) => <button type="button" key={node.id}
      aria-label={`预览${node.data.label}`} data-current={String(node.data.current)} data-highlight={node.data.highlight}
      onClick={() => props.onNodeClick({}, node)}>{node.data.label}</button>)}
  </div>,
  Background: () => null, Controls: () => null, MiniMap: () => null, Handle: () => null, BaseEdge: () => null,
  Position: { Top: "top", Bottom: "bottom", Left: "left", Right: "right" },
  BackgroundVariant: { Dots: "dots" },
  applyNodeChanges: (_changes: unknown, nodes: unknown) => nodes,
}));

const graph = {
  id: "g1", title: "测试学习图谱", goal_id: "goal", status: "published", revision: 1,
  nodes: [
    { id: "root", label: "根节点", node_type: "root" },
    { id: "n1", label: "选料与食材准备", node_type: "concept" },
    { id: "n2", label: "辣椒品种与辣度选择", node_type: "concept" },
  ].map((node) => ({ ...node, description: "学习说明", mastery_stars: 0, retrieval_state: "unlearned", evidence_state: "none", attention_state: "normal" })),
  edges: ["n1", "n2"].map((id) => ({ id: `e-${id}`, source_node_id: "root", target_node_id: id, relation: "contains" })),
} as Graph;
const records = ["active", "other"].map((id) => ({
  id, workspace_id: "test-workspace", title: id, status: "active", memory_enabled: true,
  graph_id: "g1", current_node_id: "n1", context_capsule: {},
  created_at: "2026-11-11T00:00:00Z", updated_at: "2026-11-11T00:00:00Z",
  model_snapshot: { provider_id: "provider-1", model_id: "model-1", thinking_mode: "off", agent_mode: false },
}) as Session);
const sessionsKey = workspaceQueryKey("test-workspace", "sessions", "detail", "active");

function setup(withChat = false, activeGraph = graph) {
  vi.spyOn(apiClient, "get").mockImplementation(async (path) => {
    if (path === '/sessions/page') return { items: [], has_more: false, next_cursor: null, has_previous: false, previous_cursor: null };
    if (/^\/sessions\/[^/]+$/.test(path)) return records.find(record => record.id === decodeURIComponent(path.split('/').at(-1)!));
    if (path === "/graphs") return [activeGraph];
    if (path === "/graphs/g1") return activeGraph;
    if (path === "/providers") return [{
      id: "provider-1", provider_type: "openai_compatible_chat", display_name: "测试模型",
      enabled: true, remote_capability: true, capabilities: { default_model: "model-1" },
    }];
    if (path === "/providers/provider-1/models") return {
      provider_id: "provider-1", status: "ok",
      models: [{ id: "model-1", roles: ["llm"], enabled: true, remote: true, streaming: true }],
    };
    if (path.endsWith("/messages")) return { items: [], total_count: 0, has_more_before: false };
    return [];
  });
  vi.spyOn(apiClient, "patch").mockResolvedValue(records[0]);
  vi.spyOn(apiClient, "post").mockResolvedValue(records[0]);
  vi.spyOn(apiClient, "postSse").mockImplementation(() => (async function* () {
    yield { event: "message.completed", rawData: "", data: { type: "message.completed", message_id: "answer" } };
  })());
  setSessionComposerPrefs("active", { responseMode: "fast", thinkingMode: "off" });
  function Page() {
    const navigate = useNavigate();
    return <>
      <button type="button" onClick={() => navigate("/w/test-workspace/chat/other")}>切换会话</button>
      {withChat ? <ChatCanvasPage /> : null}
      <ContextRail />
    </>;
  }
  return renderWithProviders(<TooltipProvider><Routes>
    <Route path="/w/:workspaceId/chat/:sessionId" element={<Page />} />
  </Routes></TooltipProvider>, { initialEntries: ["/w/test-workspace/chat/active"] });
}

async function previewOtherNode() {
  const bound = await screen.findByRole("button", { name: "预览选料与食材准备" });
  await waitFor(() => expect(bound).toHaveAttribute("data-current", "true"));
  fireEvent.change(screen.getByRole("textbox", { name: "搜索图谱节点" }), { target: { value: "辣椒品种" } });
  fireEvent.click(await screen.findByRole("button", { name: "辣椒品种与辣度选择" }));
  expect(await screen.findByRole("status", { name: "会话节点绑定" })).toHaveTextContent("预览");
  fireEvent.click(screen.getByRole("button", { name: "预览根节点" }));
  expect(await screen.findByRole("button", { name: "预览选料与食材准备" })).toHaveAttribute("data-current", "true");
  fireEvent.click(screen.getByRole("button", { name: "预览辣椒品种与辣度选择" }));
  expect(screen.getByRole("button", { name: "预览辣椒品种与辣度选择" })).toHaveAttribute("data-current", "false");
}

describe("chat rail keeps the conversation binding while browsing", () => {
  it("shows every branch and deep descendant in an automatically fitted full graph", async () => {
    const siblings = Array.from({ length: 15 }, (_, index) => ({ ...graph.nodes[1]!, id: `branch-${index}`, label: `分支${index}` }));
    const deepNode = { ...graph.nodes[1]!, id: "deep", label: "深层知识点" };
    const disconnected = { ...graph.nodes[1]!, id: "disconnected", label: "独立知识点" };
    const fullGraph: Graph = {
      ...graph,
      nodes: [...graph.nodes, ...siblings, deepNode, disconnected],
      edges: [
        ...graph.edges,
        ...siblings.map(node => ({ ...graph.edges[0]!, id: `e-${node.id}`, target_node_id: node.id })),
        { ...graph.edges[0]!, id: "e-deep", source_node_id: "branch-14", target_node_id: "deep" },
      ],
    };
    const view = setup(false, fullGraph);
    const flow = await screen.findByTestId("rail-graph-flow");
    for (const node of fullGraph.nodes) expect(await screen.findByRole("button", { name: `预览${node.label}` })).toBeVisible();
    expect(flow).toHaveAttribute("data-fit-view", "true");
    expect(flow).toHaveAttribute("data-min-zoom", "0.01");
    expect(flow).toHaveAttribute("data-edge-count", String(fullGraph.edges.length));
    expect(screen.getByRole("button", { name: "适应完整图谱" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "预览深层知识点" }));
    expect(screen.getByRole("status", { name: "会话节点绑定" })).toHaveTextContent("深层知识点");
    for (const node of fullGraph.nodes) {
      const card = screen.getByRole("button", { name: `预览${node.label}` });
      expect(card).toBeVisible();
      expect(card).toHaveAttribute("data-highlight", "none");
    }
    expect(view.queryClient.getQueryData<Session>(sessionsKey)?.current_node_id).toBe("n1");
    expect(apiClient.patch).not.toHaveBeenCalled();
  });
  it("never mounts the composer graph selector after loading, switching or remounting a bound conversation", async () => {
    const assertNoGraphSelector = () => {
      expect(screen.queryByRole("button", { name: "管理当前会话图谱上下文" })).toBeNull();
      expect(screen.queryByLabelText("当前会话学习上下文")).toBeNull();
      expect(document.querySelector(".graph-composer-context")).toBeNull();
      expect(document.querySelector(".learning-package-canvas")).toBeNull();
      expect(screen.queryByText("展开学习包")).toBeNull();
      expect(screen.getByText("从一个问题开始")).toBeVisible();
      expect(vi.mocked(apiClient.get).mock.calls.some(([path]) => /learning-page|learning-builds|learning-policy|learning-attempt/.test(path))).toBe(false);
      expect(screen.getByRole("button", { name: "目标" })).toBeVisible();
      expect(screen.getByRole("button", { name: /^联网/ })).toBeVisible();
    };
    const view = setup(true);
    assertNoGraphSelector();
    await waitFor(() => expect(screen.getByRole("button", { name: "预览选料与食材准备" })).toHaveAttribute("data-current", "true"), { timeout: 5_000 });
    assertNoGraphSelector();

    fireEvent.click(screen.getByRole("button", { name: "切换会话" }));
    await waitFor(() => expect(view.container.querySelector(".chat-canvas-page")).toHaveAttribute("data-session-id", "other"));
    await waitFor(() => expect(apiClient.get).toHaveBeenCalledWith("/sessions/other", expect.objectContaining({ signal: expect.any(AbortSignal) })));
    await waitFor(() => expect(screen.getByRole("button", { name: "预览选料与食材准备" })).toHaveAttribute("data-current", "true"), { timeout: 5_000 });
    assertNoGraphSelector();

    view.unmount();
    const remounted = setup(true);
    await waitFor(() => expect(screen.getByRole("button", { name: "预览选料与食材准备" })).toHaveAttribute("data-current", "true"), { timeout: 5_000 });
    assertNoGraphSelector();
    expect(remounted.queryClient.getQueryData<Session>(sessionsKey)?.current_node_id).toBe("n1");
    expect(apiClient.patch).not.toHaveBeenCalled();
  }, 15_000);
  it("opens a graph folder from the shelf without binding the current conversation", async () => {
    setup();
    await screen.findByRole("button", { name: "切换图谱" });
    fireEvent.click(screen.getByRole("button", { name: "切换图谱" }));
    const open = vi.fn();
    window.addEventListener("learngraph:open-learning-project", open);
    try {
      fireEvent.click(await screen.findByRole("button", { name: "打开图谱文件夹 测试学习图谱" }));
      expect(open).toHaveBeenCalledOnce();
      expect((open.mock.calls[0][0] as CustomEvent).detail).toMatchObject({ graphId: "g1" });
      expect(apiClient.patch).not.toHaveBeenCalled();
      expect(apiClient.post).not.toHaveBeenCalled();
    } finally { window.removeEventListener("learngraph:open-learning-project", open); }
  });
  it("keeps only a primary learning action and more, and can return to the current node", async () => {
    setup();
    await previewOtherNode();
    expect(screen.queryByRole("button", { name: "下一节点" })).toBeNull();
    expect(screen.queryByRole("button", { name: "标为已掌握" })).toBeNull();
    expect(screen.queryByRole("button", { name: "学习动作" })).toBeNull();
    expect(screen.queryByRole("button", { name: "树形布局" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "回到当前学习节点" }));
    expect(await screen.findByRole("button", { name: "继续学习" })).toBeVisible();
    expect(screen.getByRole("status", { name: "会话节点绑定" })).toHaveTextContent("当前学习");
    expect(apiClient.patch).not.toHaveBeenCalled();
  });
  it("uses real keyboard tabs and keeps secondary actions outside the tablist", async () => {
    setup();
    const learning = await screen.findByRole("tab", { name: "当前学习" });
    const conversation = screen.getByRole("tab", { name: "侧边对话" });
    expect(screen.queryByRole("tab", { name: "语音延迟" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "能力成长" })).toBeNull();
    expect(screen.getByRole("button", { name: "新建侧边对话" }).closest('[role="tablist"]')).toBeNull();
    learning.focus();
    fireEvent.keyDown(learning, { key: "ArrowRight" });
    await waitFor(() => expect(conversation).toHaveFocus());
    await waitFor(() => expect(conversation).toHaveAttribute("aria-selected", "true"));
    expect(document.getElementById(conversation.getAttribute("aria-controls")!)).toHaveAttribute("role", "tabpanel");
  });
  it("keeps the bound marker and saved binding when clicking another card, and resets preview for another session", async () => {
    const view = setup();
    const selected = vi.fn();
    window.addEventListener("learngraph:learning-node-selected", selected);
    try {
      await previewOtherNode();
      expect(screen.getByRole("status", { name: "会话节点绑定" })).toHaveTextContent("正在学：选料与食材准备");
      expect(apiClient.patch).not.toHaveBeenCalled();
      expect(selected).not.toHaveBeenCalled();
      expect(view.queryClient.getQueryData<Session>(sessionsKey)?.current_node_id).toBe("n1");
      // Learning another node opens its destination folder without rebinding
      // or publishing context into the source conversation.
      const open = vi.fn();
      window.addEventListener("learngraph:open-learning-project", open);
      try {
        fireEvent.click(screen.getByRole("button", { name: "学习此节点" }));
        expect(apiClient.patch).not.toHaveBeenCalled();
        expect(selected).not.toHaveBeenCalled();
        expect(open).toHaveBeenCalledOnce();
        expect((open.mock.calls[0][0] as CustomEvent).detail).not.toHaveProperty("learningPackage");
        expect((open.mock.calls[0][0] as CustomEvent).detail).toMatchObject({ graphId: "g1", nodeId: "n2" });
      } finally { window.removeEventListener("learngraph:open-learning-project", open); }
      fireEvent.click(screen.getByRole("button", { name: "切换会话" }));
      await waitFor(() => expect(screen.getByRole("status", { name: "会话节点绑定" })).toHaveTextContent("当前学习"));
    } finally { window.removeEventListener("learngraph:learning-node-selected", selected); }
  });

  it("keeps composer context and outgoing node_ids on the saved node after a click and conflicting node events", async () => {
    const view = setup(true);
    await waitFor(() => expect(apiClient.get).toHaveBeenCalledWith("/providers/provider-1/models"));
    await previewOtherNode();
    const input = view.container.querySelector("textarea")!;
    fireEvent.change(input, { target: { value: "什么是 选料与食材准备？" } });
    for (const sessionId of [undefined, "active"]) {
      act(() => window.dispatchEvent(new CustomEvent("learngraph:learning-node-selected", {
        detail: { graphId: "g1", nodeId: "n2", nodeLabel: "辣椒品种与辣度选择", sessionId },
      })));
      expect(input).toHaveValue("什么是 选料与食材准备？");
    }
    fireEvent.change(input, { target: { value: "解释当前绑定节点" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(apiClient.postSse).toHaveBeenCalledTimes(1));
    expect(vi.mocked(apiClient.postSse).mock.calls[0][1]).toMatchObject({ node_ids: ["n1"] });
    await waitFor(() => expect(getSessionStream("active")).toBeUndefined());
    expect(view.queryClient.getQueryData<Session>(sessionsKey)?.current_node_id).toBe("n1");
  });
});
