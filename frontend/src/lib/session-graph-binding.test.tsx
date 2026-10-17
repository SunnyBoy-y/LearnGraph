import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getGraph, listGraphs, listSessions, updateSession } from "@/api";
import { SessionGraphBindingMenu } from "@/components/layout/session-graph-binding";
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import type { Graph, GraphSummary } from "@/types/graphs";
import type { Session } from "@/types/sessions";
import { workspaceQueryKey } from "./query-keys";
import { useSessionGraphBinding } from "./session-graph-binding";

vi.mock("@/api", () => ({ getGraph: vi.fn(), listGraphs: vi.fn(), listSessions: vi.fn(), updateSession: vi.fn() }));

const graphA = { id: "g1", title: "图谱 A", goal_id: "goal", status: "published" } as GraphSummary;
const graphB = { ...graphA, id: "g2", title: "图谱 B" };
const graph = { ...graphA, nodes: [{ id: "n1", label: "节点 A" }, { id: "n2", label: "节点 B" }], edges: [] } as unknown as Graph;
const sessionsKey = workspaceQueryKey("w", "sessions");
let stored: Session;

beforeEach(() => {
  stored = { id: "s", graph_id: "g1", current_node_id: "n1", goal_id: "goal" } as Session;
  vi.mocked(listSessions).mockImplementation(async () => [stored]);
  vi.mocked(listGraphs).mockResolvedValue([graphA, graphB]);
  vi.mocked(getGraph).mockImplementation(async (id) => ({ ...graph, id }));
  vi.mocked(updateSession).mockImplementation(async (_id, payload) => (stored = { ...stored, ...payload }));
});

function setup(overrides: Partial<Session> = {}) {
  stored = { ...stored, ...overrides };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  client.setQueryData(sessionsKey, [stored]);
  client.setQueryData(workspaceQueryKey("w", "graphs"), [graphA, graphB]);
  client.setQueryData(workspaceQueryKey("w", "graph", "g1"), graph);
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  return { client, wrapper };
}

describe("session binding requires an explicit unbind", () => {
  it("blocks changing a bound graph or node through rail handlers", async () => {
    const { wrapper } = setup();
    const { result } = renderHook(() => useSessionGraphBinding("w", "s"), { wrapper });
    const selected = vi.fn();
    window.addEventListener("learngraph:learning-node-selected", selected);
    try {
      await act(async () => {
        expect(await result.current.bindGraph(graphB)).toBe(false);
        expect(await result.current.bindNode("n2")).toBe(false);
      });
      expect(updateSession).not.toHaveBeenCalled();
      expect(selected).not.toHaveBeenCalled();
      expect(result.current.boundNodeId).toBe("n1");
    } finally { window.removeEventListener("learngraph:learning-node-selected", selected); }
  });

  it("clears graph and node together before allowing a new graph", async () => {
    const { client, wrapper } = setup();
    const { result } = renderHook(() => useSessionGraphBinding("w", "s"), { wrapper });
    const cleared = vi.fn();
    window.addEventListener("learngraph:learning-node-clear", cleared);
    try {
      await act(async () => { expect(await result.current.clearGraph()).toBe(true); });
      expect(updateSession).toHaveBeenNthCalledWith(1, "s", { graph_id: null, current_node_id: null });
      expect(result.current.binding).toBeUndefined();
      expect(cleared.mock.calls[0][0].detail).toEqual({ sessionId: "s", persisted: true });
      await act(async () => { expect(await result.current.bindGraph(graphB)).toBe(true); });
      expect(client.getQueryData<Session[]>(sessionsKey)?.[0].graph_id).toBe("g2");
      expect(result.current.boundNodeId).toBeNull();
    } finally { window.removeEventListener("learngraph:learning-node-clear", cleared); }
  });

  it("allows a new node only after node unbinding and publishes the saved context", async () => {
    const { wrapper } = setup();
    const { result } = renderHook(() => useSessionGraphBinding("w", "s"), { wrapper });
    await act(async () => { expect(await result.current.bindNode(null)).toBe(true); });
    expect(result.current.binding?.graphId).toBe("g1");
    await act(async () => { expect(await result.current.bindNode("n2")).toBe(true); });
    expect(updateSession).toHaveBeenNthCalledWith(2, "s", { current_node_id: "n2" });
    expect(JSON.parse(window.sessionStorage.getItem("learngraph:active-learning-node")!)).toMatchObject({ sessionId: "s", graphId: "g1", nodeId: "n2" });
  });

  it("retains both bindings when unbinding fails", async () => {
    const { wrapper } = setup();
    vi.mocked(updateSession).mockRejectedValueOnce(new Error("offline"));
    const { result } = renderHook(() => useSessionGraphBinding("w", "s"), { wrapper });
    await act(async () => { expect(await result.current.clearGraph()).toBe(false); });
    expect(result.current.binding?.graphId).toBe("g1");
    expect(result.current.boundNodeId).toBe("n1");
    await act(async () => { expect(await result.current.bindGraph(graphB)).toBe(false); });
    expect(updateSession).toHaveBeenCalledTimes(1);
  });

  it("shares the pending lock across menu and rail, including stale callbacks", async () => {
    const { wrapper } = setup();
    let finish!: (value: Session) => void;
    vi.mocked(updateSession).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const { result } = renderHook(() => ({ menu: useSessionGraphBinding("w", "s"), rail: useSessionGraphBinding("w", "s") }), { wrapper });
    const staleBindNode = result.current.rail.bindNode;
    let saving!: Promise<boolean>;
    await act(async () => { saving = result.current.menu.clearGraph(); });
    await waitFor(() => expect(result.current.rail.bindingPending).toBe(true));
    await act(async () => { expect(await staleBindNode(null)).toBe(false); });
    expect(updateSession).toHaveBeenCalledTimes(1);
    await act(async () => { finish({ ...stored, graph_id: null, current_node_id: null }); await saving; });
    await act(async () => { expect(await staleBindNode("n2")).toBe(false); });
    await waitFor(() => expect(result.current.rail.binding).toBeUndefined());
  });

  it("retains the published graph, goal, and node membership restrictions", async () => {
    const { wrapper } = setup({ graph_id: null, current_node_id: null });
    const { result } = renderHook(() => useSessionGraphBinding("w", "s"), { wrapper });
    await act(async () => {
      expect(await result.current.bindGraph({ ...graphB, status: "candidate" })).toBe(false);
      expect(await result.current.bindGraph({ ...graphB, goal_id: "other" })).toBe(false);
    });
    expect(updateSession).not.toHaveBeenCalled();
  });
});

describe("page tools binding menu", () => {
  function menu() {
    return <DropdownMenu defaultOpen><DropdownMenuTrigger>页面工具</DropdownMenuTrigger><DropdownMenuContent><SessionGraphBindingMenu workspaceId="w" sessionId="s" /></DropdownMenuContent></DropdownMenu>;
  }

  it("shows unbind actions instead of graph and node switchers while bound", async () => {
    const { wrapper } = setup();
    render(menu(), { wrapper });
    expect(await screen.findByRole("menuitem", { name: "解绑图谱" })).toBeVisible();
    expect(screen.getByRole("menuitem", { name: "解绑节点" })).toBeVisible();
    expect(screen.queryByRole("menuitem", { name: "绑定图谱" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "绑定节点" })).toBeNull();
    fireEvent.click(screen.getByRole("menuitem", { name: "解绑图谱" }));
    expect(await screen.findByRole("menuitem", { name: "绑定图谱" })).toBeVisible();
    expect(screen.queryByRole("menuitem", { name: "绑定节点" })).toBeNull();
  });

  it("opens node choices only after unbinding and saves the new choice", async () => {
    const { wrapper } = setup();
    render(menu(), { wrapper });
    fireEvent.click(await screen.findByRole("menuitem", { name: "解绑节点" }));
    const trigger = await screen.findByRole("menuitem", { name: "绑定节点" });
    fireEvent.keyDown(trigger, { key: "ArrowRight" });
    fireEvent.click(await screen.findByRole("menuitem", { name: "节点 B" }));
    expect(await screen.findByText("绑定节点 · 节点 B")).toBeVisible();
    expect(screen.queryByRole("menuitem", { name: "绑定节点" })).toBeNull();
  });
});
