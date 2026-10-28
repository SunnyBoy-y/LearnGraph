import type { UISnapshot } from "./stream-ui";

export type Point = { x: number; y: number };
export const GRAPH_NODE_WIDTH = 176;
export function graphLabelLines(label: string): string[] {
  const lines: string[] = [];
  let line = "", width = 0;
  for (const char of label) {
    const size = /[\u0000-\u00ff]/u.test(char) ? .55 : 1;
    if (char === "\n" || width + size > 12) { lines.push(line); line = ""; width = 0; }
    if (char !== "\n") { line += char; width += size; }
  }
  if (line || !lines.length) lines.push(line);
  return lines;
}
export const graphNodeHeight = (label: string) => Math.max(48, graphLabelLines(label).length * 19 + 22);

/** Layered directed layout with virtual vertices and barycenter crossing reduction.
 * Back edges are routed above the layers; disconnected/cyclic inputs are bounded.
 * This is a deterministic heuristic, not a minimum-crossing guarantee.
 */
export function layoutGraph(nodes: UISnapshot["nodes"], edges: UISnapshot["edges"], direction: "LR" | "TB" = "LR") {
  const byId = new Map(nodes.map(n => [n.id, n]));
  const outgoing = new Map(nodes.map(n => [n.id, edges.filter(e => e.source === n.id && byId.has(e.target))]));
  const color = new Map<string, number>(), back = new Set<string>(), order: string[] = [];
  function visit(id: string) {
    color.set(id, 1);
    for (const edge of outgoing.get(id) ?? []) {
      if (color.get(edge.target) === 1) back.add(edge.id);
      else if (!color.has(edge.target)) visit(edge.target);
    }
    color.set(id, 2); order.push(id);
  }
  // Start at roots so shuffling node records doesn't turn a DAG into a cycle.
  const incoming = new Set(edges.map(e => e.target));
  for (const n of [...nodes.filter(n => !incoming.has(n.id)), ...nodes]) if (!color.has(n.id)) visit(n.id);
  const rank = new Map(nodes.map(n => [n.id, 0]));
  for (const id of order.reverse()) for (const e of outgoing.get(id) ?? []) {
    if (!back.has(e.id)) rank.set(e.target, Math.max(rank.get(e.target)!, rank.get(id)! + 1));
  }
  const layers: string[][] = Array.from({ length: Math.max(0, ...rank.values()) + 1 }, () => []);
  nodes.forEach(n => layers[rank.get(n.id)!].push(n.id));
  const links: [string, string][] = [], chains = new Map<string, string[]>();
  for (const e of edges) {
    if (!byId.has(e.source) || !byId.has(e.target) || back.has(e.id)) continue;
    const chain = [e.source];
    for (let r = rank.get(e.source)! + 1; r < rank.get(e.target)!; r++) {
      const id = `@${e.id}:${r}`; layers[r].push(id); chain.push(id);
    }
    chain.push(e.target); chains.set(e.id, chain);
    for (let i = 1; i < chain.length; i++) links.push([chain[i - 1], chain[i]]);
  }
  const before = new Map<string, string[]>(), after = new Map<string, string[]>();
  for (const [a, b] of links) { before.set(b, [...before.get(b) ?? [], a]); after.set(a, [...after.get(a) ?? [], b]); }
  for (let sweep = 0; sweep < 8; sweep++) {
    const down = sweep % 2 === 0, neighbors = down ? before : after;
    const indices = down ? layers.map((_, i) => i) : layers.map((_, i) => i).reverse();
    const index = new Map(layers.flatMap(layer => layer.map((id, i) => [id, i] as const)));
    for (const r of indices) {
      const score = (id: string) => {
        const ns = neighbors.get(id) ?? [];
        return ns.length ? ns.reduce((sum, n) => sum + index.get(n)!, 0) / ns.length : index.get(id)!;
      };
      layers[r].sort((a, b) => score(a) - score(b));
      layers[r].forEach((id, i) => index.set(id, i));
    }
  }
  const heightOf = (id: string) => byId.has(id) ? graphNodeHeight(byId.get(id)!.label) : 12;
  const crossSize = (id: string) => direction === "LR" ? heightOf(id) : byId.has(id) ? GRAPH_NODE_WIDTH : 12;
  const layerHeights = layers.map(layer => layer.reduce((h, id) => h + crossSize(id) + 28, 0));
  const height = Math.max(180, ...layerHeights), positions: Record<string, Point> = {};
  let depth = 64 + back.size * 14;
  layers.forEach((layer, r) => {
    let cross = (height - layerHeights[r]) / 2 + (direction === "LR" ? 64 + back.size * 14 : 24);
    const maxDepth = Math.max(48, ...layer.map(heightOf));
    layer.forEach(id => {
      const size = crossSize(id);
      positions[id] = direction === "LR" ? { x: 112 + r * 260, y: cross + size / 2 } : {x: cross + size / 2, y: depth + maxDepth / 2};
      cross += size + 28;
    });
    depth += maxDepth + 58;
  });
  return { positions, chains, back, byId, direction, mindmap: false, roots: new Set<string>() };
}

/** A mind map uses a spanning forest, balanced branches and subtree spans.
 * Extra relationships remain explicit cross-links rather than duplicating nodes.
 */
export function layoutMindMap(nodes: UISnapshot["nodes"], edges: UISnapshot["edges"], direction: "LR" | "TB" = "LR"): ReturnType<typeof layoutGraph> {
  const layout = layoutGraph(nodes, edges, direction);
  layout.mindmap = true;
  if (!nodes.length) return layout;
  const incoming = new Set(edges.map(e => e.target));
  const children = new Map<string, string[]>(), visited = new Set<string>(), treeEdges = new Set<string>();
  function visit(id: string) {
    visited.add(id); children.set(id, []);
    for (const edge of edges.filter(e => e.source === id)) {
      if (!layout.byId.has(edge.target) || visited.has(edge.target)) continue;
      children.get(id)!.push(edge.target); treeEdges.add(edge.id); visit(edge.target);
    }
  }
  for (const n of [...nodes.filter(n => !incoming.has(n.id)), ...nodes]) {
    if (!visited.has(n.id)) { layout.roots.add(n.id); visit(n.id); }
  }
  if (direction === "TB") return layout;
  layout.positions = {}; layout.chains = new Map(); layout.back = new Set(edges.filter(e => !treeEdges.has(e.id)).map(e => e.id));
  const spans = new Map<string, number>();
  function span(id: string): number {
    const value = Math.max(graphNodeHeight(layout.byId.get(id)!.label) + 32, children.get(id)!.reduce((sum, child) => sum + span(child), 0));
    spans.set(id, value); return value;
  }
  function place(id: string, x: number, y: number, side: number) {
    layout.positions[id] = {x, y};
    const cs = children.get(id)!;
    let offset = y - cs.reduce((sum, child) => sum + spans.get(child)!, 0) / 2;
    for (const child of cs) {
      const size = spans.get(child)!; place(child, x + side * 260, offset + size / 2, side); offset += size;
    }
  }
  let cursor = 64;
  for (const root of layout.roots) {
    span(root);
    const sides: string[][] = [[], []], weights = [0, 0];
    for (const child of children.get(root)!) {
      const side = weights[0] <= weights[1] ? 0 : 1;
      sides[side].push(child); weights[side] += spans.get(child)!;
    }
    const rootY = cursor + Math.max(...weights, graphNodeHeight(layout.byId.get(root)!.label)) / 2;
    layout.positions[root] = {x: 0, y: rootY};
    sides.forEach((side, i) => {
      let offset = rootY - weights[i] / 2;
      for (const child of side) { const size = spans.get(child)!; place(child, i === 0 ? 260 : -260, offset + size / 2, i === 0 ? 1 : -1); offset += size; }
    });
    cursor += Math.max(...weights, graphNodeHeight(layout.byId.get(root)!.label)) + 96;
  }
  return layout;
}

export function graphEdgeRoute(edge: UISnapshot["edges"][number], layout: ReturnType<typeof layoutGraph>, overrides: Record<string, Point> = {}, lane = 0) {
  const a = overrides[edge.source] ?? layout.positions[edge.source];
  const b = overrides[edge.target] ?? layout.positions[edge.target];
  const ah = graphNodeHeight(layout.byId.get(edge.source)!.label) / 2;
  const bh = graphNodeHeight(layout.byId.get(edge.target)!.label) / 2;
  const vertical = layout.direction === "TB";
  if (layout.mindmap && !vertical && !layout.back.has(edge.id)) {
    const side = b.x >= a.x ? 1 : -1;
    const start = a.x + side * GRAPH_NODE_WIDTH / 2, end = b.x - side * (GRAPH_NODE_WIDTH / 2 + 3), mid = (start + end) / 2;
    return {d: `M ${start} ${a.y} C ${mid} ${a.y} ${mid} ${b.y} ${end} ${b.y}`, label: {x: mid, y: (a.y + b.y) / 2 - 9}, top: Math.min(a.y, b.y) - 30, left: Math.min(a.x, b.x) - GRAPH_NODE_WIDTH / 2};
  }
  if (layout.back.has(edge.id) || (vertical ? b.y <= a.y : b.x <= a.x)) {
    if (vertical) {
      const x = Math.min(...Object.entries(layout.positions).filter(([id]) => layout.byId.has(id)).map(([id, p]) => (overrides[id] ?? p).x - GRAPH_NODE_WIDTH / 2)) - 28 - lane * 18;
      const endY = edge.source === edge.target ? b.y + 20 : b.y;
      return {d: `M ${a.x - GRAPH_NODE_WIDTH / 2} ${a.y} C ${x} ${a.y} ${x} ${endY} ${b.x - GRAPH_NODE_WIDTH / 2} ${endY}`, label:{x: x + 6, y: (a.y + endY) / 2}, top: 0, left: x};
    }
    const y = Math.min(...Object.entries(layout.positions).filter(([id]) => layout.byId.has(id)).map(([id, p]) => (overrides[id] ?? p).y - graphNodeHeight(layout.byId.get(id)!.label) / 2)) - 28 - lane * 18;
    const endX = edge.source === edge.target ? b.x + 36 : b.x;
    return { d: `M ${a.x} ${a.y - ah} C ${a.x} ${y} ${endX} ${y} ${endX} ${b.y - bh}`, label: { x: (a.x + endX) / 2, y: y + 3 }, top: y, left: 0 };
  }
  const ids = !overrides[edge.source] && !overrides[edge.target] ? layout.chains.get(edge.id) ?? [] : [];
  const points = [vertical ? {x: a.x, y: a.y + ah} : {x: a.x + GRAPH_NODE_WIDTH / 2, y: a.y}, ...ids.slice(1, -1).map(id => layout.positions[id]), vertical ? {x: b.x, y: b.y - bh - 3} : {x: b.x - GRAPH_NODE_WIDTH / 2 - 3, y: b.y}];
  let d = `M ${points[0].x} ${points[0].y}`;
  for (let i = 1; i < points.length; i++) {
    const p = points[i - 1], q = points[i], mid = (p.x + q.x) / 2;
    d += vertical ? ` C ${p.x} ${(p.y + q.y) / 2} ${q.x} ${(p.y + q.y) / 2} ${q.x} ${q.y}` : ` C ${mid} ${p.y} ${mid} ${q.y} ${q.x} ${q.y}`;
  }
  const mid = Math.floor(points.length / 2), p = points[mid - 1], q = points[mid];
  return { d, label: {x: (p.x + q.x) / 2 + (vertical ? 26 : 0), y: (p.y + q.y) / 2 - (vertical ? 0 : 9)}, top: Math.min(a.y, b.y) - 30, left: 0 };
}
