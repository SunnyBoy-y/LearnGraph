import { describe, expect, it } from "vitest";
import { graphEdgeRoute, graphLabelLines, graphNodeHeight, layoutGraph, layoutMindMap } from "./graph-layout";

const node = (id: string, label = id) => ({id, label, description: "", group: ""});
const edge = (source: string, target: string) => ({id: `${source}_${target}`, source, target, label: ""});
describe("relationship layout", () => {
  it("lays out the screenshot's dependency chain by edges instead of input order", () => {
    const nodes = [node("question"), node("feeling"), node("behavior"), node("fact")];
    const edges = [edge("question", "feeling"), edge("question", "behavior"), edge("behavior", "fact")];
    const layout = layoutGraph(nodes.toReversed(), edges);
    for (const e of edges) expect(layout.positions[e.target].x).toBeGreaterThan(layout.positions[e.source].x);
    expect(layout.positions.feeling.x).toBe(layout.positions.behavior.x);
    expect(Math.abs(layout.positions.feeling.y - layout.positions.behavior.y)).toBeGreaterThan(48);
  });
  it("reduces a crossed two-layer ordering and routes skipped layers through free lanes", () => {
    const nodes = [node("a"), node("b"), node("c"), node("d"), node("e")];
    const edges = [edge("a", "d"), edge("b", "c"), edge("d", "e"), edge("a", "e")];
    const layout = layoutGraph(nodes, edges);
    expect((layout.positions.a.y - layout.positions.b.y) * (layout.positions.d.y - layout.positions.c.y)).toBeGreaterThan(0);
    const route = graphEdgeRoute(edges[3], layout);
    expect(route.d.match(/ C /g)!.length).toBe(2);
  });
  it("handles cycles, self-loops, disconnected nodes and long text without clipping", () => {
    const nodes = [node("a", "这是一段需要完整换行显示而不能用省略号截断的中文标签"), node("b"), node("c")];
    const edges = [edge("a", "b"), edge("b", "a"), edge("c", "c")];
    const layout = layoutGraph(nodes, edges);
    expect(graphLabelLines(nodes[0].label).join("")).toBe(nodes[0].label);
    expect(graphNodeHeight(nodes[0].label)).toBeGreaterThan(48);
    edges.forEach((e, i) => expect(graphEdgeRoute(e, layout, {}, i).d).not.toMatch(/NaN|undefined|Infinity/));
    expect(layoutGraph([], []).positions).toEqual({});
  });
  it("fits narrow screens using vertical ranks and routes loops to the side", () => {
    const nodes = [node("a"), node("b"), node("c")], edges = [edge("a", "b"), edge("b", "c"), edge("c", "a")];
    const layout = layoutGraph(nodes, edges, "TB");
    expect(layout.positions.a.y).toBeLessThan(layout.positions.b.y);
    expect(layout.positions.b.y).toBeLessThan(layout.positions.c.y);
    expect(graphEdgeRoute(edges[2], layout).left).toBeLessThan(layout.positions.a.x - 88);
  });
  it("renders a balanced mind map around its central topic with readable left branches", () => {
    const nodes = [node("root"), node("a"), node("b"), node("c"), node("detail")];
    const edges = [edge("root", "a"), edge("root", "b"), edge("root", "c"), edge("b", "detail"), edge("detail", "root")];
    const map = layoutMindMap(nodes, edges);
    expect(map.roots.has("root")).toBe(true);
    expect(map.positions.a.x).toBeGreaterThan(map.positions.root.x);
    expect(map.positions.b.x).toBeLessThan(map.positions.root.x);
    expect(map.positions.detail.x).toBeLessThan(map.positions.b.x);
    expect(map.back.has("detail_root")).toBe(true);
    expect(graphEdgeRoute(edges[1], map).d).not.toContain("NaN");
    expect(Math.abs(map.positions.a.y - map.positions.c.y)).toBeGreaterThan(48);
    expect(layoutMindMap(nodes, edges, "TB").positions.a.y).toBeGreaterThan(map.positions.root.y);
  });
});
