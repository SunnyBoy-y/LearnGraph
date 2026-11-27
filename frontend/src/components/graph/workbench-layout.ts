import type { Edge } from "@xyflow/react";
import type { KnowledgeNode } from "./knowledge-graph";

export type GraphPoint = { x: number; y: number };
export type CardBox = GraphPoint & { width: number; height: number; column: number; row: number };
export type WorkbenchView = "route" | "relations" | "catalog";
export type StructuralNode = { id: string; root: boolean; type: string };
export type RelationCategory = "prerequisite" | "contains" | "association";

export const CARD = { width: 220, goalWidth: 246, height: 134, column: 346, row: 174 };
export const EDGE_CORNER_RADIUS = 7;

export function classifyRelation(edge: Pick<Edge, "data">): RelationCategory {
  const relation = edge.data?.relation;
  if (relation === "prerequisite") return "prerequisite";
  if (relation === "contains") return "contains";
  return "association";
}

export function filterWorkbenchEdges(
  edges: Edge[],
  view: WorkbenchView,
  options?: {
    activeNodeIds?: ReadonlySet<string>;
    showAllRelations?: boolean;
    planEdgeIds?: ReadonlySet<string>;
  },
): Edge[] {
  if (view === "catalog") return [];
  if (view === "relations" || options?.showAllRelations) return edges;
  const active = options?.activeNodeIds;
  const planEdgeIds = options?.planEdgeIds;
  return edges.filter((edge) => {
    if (planEdgeIds?.has(edge.id)) return true;
    const category = classifyRelation(edge);
    if (category === "prerequisite" || category === "contains") return true;
    return Boolean(active && (active.has(edge.source) || active.has(edge.target)));
  });
}

export function structuralNodes(nodes: KnowledgeNode[]): StructuralNode[] {
  const explicit = nodes.find((node) => node.data.nodeType === "root") ?? nodes.find((node) => node.data.root);
  return nodes.map((node) => ({ id: node.id, root: node.id === explicit?.id, type: node.data.nodeType ?? "concept" }));
}

export function graphBounds(boxes: Iterable<Pick<CardBox, "x" | "y" | "width" | "height">>) {
  let x = Infinity, y = Infinity, right = -Infinity, bottom = -Infinity;
  for (const box of boxes) {
    x = Math.min(x, box.x); y = Math.min(y, box.y);
    right = Math.max(right, box.x + box.width); bottom = Math.max(bottom, box.y + box.height);
  }
  return Number.isFinite(x) ? { x, y, width: right - x, height: bottom - y } : undefined;
}

// Condense cycles into one stage; longest paths on the resulting DAG account
// for all prerequisites, not just the first parent. Other relations never rank cards.
export function prerequisiteStages(ids: string[], edges: Edge[]) {
  const outgoing = new Map(ids.map((id) => [id, new Set<string>()]));
  const incoming = new Map(ids.map((id) => [id, new Set<string>()]));
  for (const edge of edges) {
    if (edge.data?.relation === "prerequisite" && outgoing.has(edge.source) && incoming.has(edge.target)) {
      outgoing.get(edge.source)!.add(edge.target);
      incoming.get(edge.target)!.add(edge.source);
    }
  }
  const visited = new Set<string>();
  const finish: string[] = [];
  for (const id of ids) {
    const stack: [string, boolean][] = [[id, false]];
    while (stack.length) {
      const [next, exit] = stack.pop()!;
      if (exit) { finish.push(next); continue; }
      if (visited.has(next)) continue;
      visited.add(next);
      stack.push([next, true]);
      for (const child of outgoing.get(next)!) stack.push([child, false]);
    }
  }
  const component = new Map<string, number>();
  const members: string[][] = [];
  for (const id of finish.reverse()) {
    if (component.has(id)) continue;
    const index = members.length;
    const group: string[] = [];
    const pending = [id];
    component.set(id, index);
    for (let i = 0; i < pending.length; i++) {
      const next = pending[i];
      group.push(next);
      for (const parent of incoming.get(next)!) {
        if (!component.has(parent)) {
          component.set(parent, index);
          pending.push(parent);
        }
      }
    }
    members.push(group);
  }
  const ranks = members.map(() => 0);
  for (const [index, group] of members.entries()) {
    for (const id of group) {
      for (const child of outgoing.get(id)!) {
        const to = component.get(child)!;
        if (to !== index) ranks[to] = Math.max(ranks[to], ranks[index] + 1);
      }
    }
  }
  return new Map(ids.map((id) => [id, ranks[component.get(id)!]]));
}

/** Order nodes within each stage column using barycenter sweeps over structural edges. */
function orderColumnsByBarycenter(
  columns: StructuralNode[][],
  edges: Edge[],
) {
  if (columns.length <= 1) return;
  const parents = new Map<string, string[]>();
  const children = new Map<string, string[]>();
  for (const edge of edges) {
    const category = classifyRelation(edge);
    if (category === "association") continue;
    const p = parents.get(edge.target) ?? [];
    p.push(edge.source);
    parents.set(edge.target, p);
    const c = children.get(edge.source) ?? [];
    c.push(edge.target);
    children.set(edge.source, c);
  }
  const indexOf = new Map<string, number>();
  const refresh = () => columns.forEach((col) => col.forEach((n, idx) => indexOf.set(n.id, idx)));
  refresh();

  for (let pass = 0; pass < 4; pass++) {
    for (let c = 1; c < columns.length; c++) {
      columns[c].sort((a, b) => {
        const pa = parents.get(a.id) ?? [];
        const pb = parents.get(b.id) ?? [];
        const sa = pa.length ? pa.reduce((s, id) => s + (indexOf.get(id) ?? 0), 0) / pa.length : indexOf.get(a.id)!;
        const sb = pb.length ? pb.reduce((s, id) => s + (indexOf.get(id) ?? 0), 0) / pb.length : indexOf.get(b.id)!;
        return sa - sb;
      });
      refresh();
    }
    for (let c = columns.length - 2; c >= 0; c--) {
      columns[c].sort((a, b) => {
        const ca = children.get(a.id) ?? [];
        const cb = children.get(b.id) ?? [];
        const sa = ca.length ? ca.reduce((s, id) => s + (indexOf.get(id) ?? 0), 0) / ca.length : indexOf.get(a.id)!;
        const sb = cb.length ? cb.reduce((s, id) => s + (indexOf.get(id) ?? 0), 0) / cb.length : indexOf.get(b.id)!;
        return sa - sb;
      });
      refresh();
    }
  }
}

export type WorkbenchLayoutResult = {
  boxes: Map<string, CardBox>;
  headings: { x: number; y: number; label: string }[];
  regions: { x: number; y: number; width: number; height: number; tone: "knowledge" | "practice" }[];
  bounds: ReturnType<typeof graphBounds>;
  groups: { label: string; ids: string[] }[];
  rootId?: string;
  elkEdgePoints?: Map<string, GraphPoint[]>;
};

export function layoutWorkbench(nodes: StructuralNode[], edges: Edge[], view: WorkbenchView): WorkbenchLayoutResult {
  const ids = new Set(nodes.map((node) => node.id));
  const valid = edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target) && edge.source !== edge.target);
  const ranks = prerequisiteStages([...ids], valid);
  const root = nodes.find((node) => node.root);
  const related = new Set(valid.flatMap((edge) => [edge.source, edge.target]));
  const prereq = new Set(valid.filter((edge) => edge.data?.relation === "prerequisite").flatMap((edge) => [edge.source, edge.target]));
  const stages = new Map<number, StructuralNode[]>();
  for (const node of nodes) {
    if (node.root && !prereq.has(node.id)) continue;
    const rank = ranks.get(node.id)!;
    const group = stages.get(rank) ?? [];
    group.push(node);
    stages.set(rank, group);
  }
  const orderedStages = [...stages].sort(([a], [b]) => a - b);
  const groups = orderedStages.map(([rank, members]) => ({
    label: `阶段 ${String(rank + 1).padStart(2, "0")}`,
    ids: members.filter((node) => !node.root).map((node) => node.id),
  })).filter((group) => group.ids.length);
  const boxes = new Map<string, CardBox>();
  const headings: { x: number; y: number; label: string }[] = [];
  const regions: { x: number; y: number; width: number; height: number; tone: "knowledge" | "practice" }[] = [];
  const place = (node: StructuralNode, column: number, row: number) => {
    const width = node.root ? CARD.goalWidth : CARD.width;
    boxes.set(node.id, { column, row, x: column * CARD.column + (CARD.goalWidth - width) / 2, y: row * CARD.row, width, height: CARD.height });
  };
  const rowLimit = nodes.length <= 9 ? 3 : Math.max(4, Math.ceil(Math.sqrt(nodes.length)));
  const placeColumns = (members: StructuralNode[], start: number, direction: number, label: string) => {
    for (let offset = 0; offset < members.length; offset += rowLimit) {
      const batch = members.slice(offset, offset + rowLimit);
      const column = start + direction * Math.floor(offset / rowLimit);
      const firstRow = -Math.floor(batch.length / 2);
      batch.forEach((node, index) => place(node, column, firstRow + index));
      if (!offset) headings.push({ x: column * CARD.column + 13, y: firstRow * CARD.row - 11, label });
    }
    return Math.ceil(members.length / rowLimit);
  };

  if (view === "relations") {
    if (root) place(root, 0, 0);
    const parents = new Map(valid.filter((edge) => edge.data?.relation === "contains").map((edge) => [edge.target, edge.source]));
    const positions = new Map(nodes.map((node, i) => [node.id, i]));
    const sort = (a: StructuralNode, b: StructuralNode) => (positions.get(parents.get(a.id) ?? a.id)! - positions.get(parents.get(b.id) ?? b.id)!);
    const knowledge: StructuralNode[] = [], practice: StructuralNode[] = [], isolated: StructuralNode[] = [];
    for (const node of nodes) {
      if (node.root) continue;
      (valid.length && !related.has(node.id) ? isolated : ["practice", "assessment", "open_task"].includes(node.type) ? practice : knowledge).push(node);
    }
    const left = placeColumns(knowledge.sort(sort), -1, -1, "知识理解");
    const right = placeColumns(practice.sort(sort), 1, 1, "实践与测评");
    for (const [members, tone] of [[knowledge, "knowledge"], [practice, "practice"]] as const) {
      const bounds = graphBounds(members.map((node) => boxes.get(node.id)!));
      if (bounds) regions.push({ x: bounds.x - 15, y: bounds.y - 36, width: bounds.width + 30, height: bounds.height + 49, tone });
    }
    if (isolated.length) {
      const row = Math.floor(rowLimit / 2) + 1;
      const columns = Math.max(1, left + right + 1, Math.ceil(Math.sqrt(isolated.length)));
      const first = -Math.floor(columns / 2);
      isolated.forEach((node, i) => place(node, first + (i % columns), row + Math.floor(i / columns)));
      headings.push({ x: first * CARD.column + 13, y: row * CARD.row - 11, label: "未建立关系" });
    }
  } else if (view === "route") {
    const columnNodes: { label: string; nodes: StructuralNode[] }[] = [];
    if (root && !prereq.has(root.id)) {
      columnNodes.push({ label: "学习目标", nodes: [root] });
    }
    for (const [rank, members] of orderedStages) {
      const ordered = [...members].sort((a, b) => Number(related.has(b.id)) - Number(related.has(a.id)));
      for (let offset = 0; offset < ordered.length; offset += rowLimit) {
        columnNodes.push({
          label: offset === 0 ? `阶段 ${String(rank + 1).padStart(2, "0")}` : "",
          nodes: ordered.slice(offset, offset + rowLimit),
        });
      }
    }
    orderColumnsByBarycenter(columnNodes.map((col) => col.nodes), valid);
    columnNodes.forEach((col, column) => {
      const firstRow = -Math.floor(col.nodes.length / 2);
      col.nodes.forEach((node, index) => place(node, column, firstRow + index));
      if (col.label) {
        const firstBox = boxes.get(col.nodes[0].id)!;
        headings.push({ x: firstBox.x, y: firstBox.y - 12, label: col.label });
      }
    });
  }
  const bounds = graphBounds(boxes.values());
  return { boxes, headings, regions, bounds, groups, rootId: root?.id };
}

export const WORKBENCH_MIN_ZOOM = 0.06;

export function frameWorkbench(bounds: NonNullable<ReturnType<typeof graphBounds>>, size: { width: number; height: number }, mode: WorkbenchView, first?: CardBox) {
  const padX = Math.min(50, size.width * 0.08);
  const padY = Math.min(78, size.height * 0.19);
  const zoom = Math.max(WORKBENCH_MIN_ZOOM, Math.min(1, (size.width - padX * 2) / bounds.width, (size.height - padY * 2) / bounds.height));
  if (zoom < 0.48 && first) {
    return {
      zoom: 0.48,
      x: mode === "route" ? padX - first.x * 0.48 : size.width / 2 - (first.x + first.width / 2) * 0.48,
      y: size.height / 2 - (first.y + first.height / 2) * 0.48,
    };
  }
  return { zoom, x: (size.width - bounds.width * zoom) / 2 - bounds.x * zoom, y: (size.height - bounds.height * zoom) / 2 - bounds.y * zoom };
}

export function workbenchIsClipped(bounds: ReturnType<typeof graphBounds>, size: { width: number; height: number }, camera: GraphPoint & { zoom: number }) {
  if (!bounds || size.width <= 0 || size.height <= 0) return false;
  const x = bounds.x * camera.zoom + camera.x;
  const y = bounds.y * camera.zoom + camera.y;
  return x < -2 || y < -2 || x + bounds.width * camera.zoom > size.width + 2 || y + bounds.height * camera.zoom > size.height + 2;
}

export type EdgeRoute = {
  edge: Edge;
  points: GraphPoint[];
  path: string;
  label: GraphPoint;
  relationCategory: RelationCategory;
  sharedTrunk?: boolean;
  suppressLabel?: boolean;
};

export type TrunkJunction = {
  id: string;
  x: number;
  y: number;
  source: string;
  relationCategory: RelationCategory;
  edgeIds: string[];
};

/** Remove redundant collinear intermediate points from an orthogonal polyline. */
function simplifyOrthogonalPoints(points: GraphPoint[]): GraphPoint[] {
  if (points.length <= 2) return points;
  const deduped: GraphPoint[] = [points[0]];
  for (let i = 1; i < points.length; i++) {
    const prev = deduped[deduped.length - 1];
    if (Math.hypot(points[i].x - prev.x, points[i].y - prev.y) > 0.5) {
      deduped.push(points[i]);
    }
  }
  if (deduped.length <= 2) return deduped;
  const result: GraphPoint[] = [deduped[0]];
  for (let i = 1; i < deduped.length - 1; i++) {
    const a = result[result.length - 1];
    const b = deduped[i];
    const c = deduped[i + 1];
    const sameX = Math.abs(a.x - b.x) < 0.5 && Math.abs(b.x - c.x) < 0.5;
    const sameY = Math.abs(a.y - b.y) < 0.5 && Math.abs(b.y - c.y) < 0.5;
    if (!sameX && !sameY) result.push(b);
  }
  result.push(deduped[deduped.length - 1]);
  return result;
}

/** Convert orthogonal polyline into an SVG path with 6-8px rounded corners. */
export function buildRoundedOrthogonalPath(rawPoints: GraphPoint[], radius = EDGE_CORNER_RADIUS): string {
  const points = simplifyOrthogonalPoints(rawPoints);
  if (!points.length) return "";
  if (points.length === 1) return `M${points[0].x},${points[0].y}`;
  if (points.length === 2 || radius <= 0) {
    return points.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  }
  const parts: string[] = [`M${points[0].x.toFixed(1)},${points[0].y.toFixed(1)}`];
  for (let i = 1; i < points.length - 1; i++) {
    const prev = points[i - 1];
    const curr = points[i];
    const next = points[i + 1];
    const dx1 = curr.x - prev.x;
    const dy1 = curr.y - prev.y;
    const dx2 = next.x - curr.x;
    const dy2 = next.y - curr.y;
    const len1 = Math.hypot(dx1, dy1);
    const len2 = Math.hypot(dx2, dy2);
    const r = Math.min(radius, len1 / 2, len2 / 2);
    if (r < 1) {
      parts.push(`L${curr.x.toFixed(1)},${curr.y.toFixed(1)}`);
      continue;
    }
    const startX = curr.x - (dx1 / len1) * r;
    const startY = curr.y - (dy1 / len1) * r;
    const endX = curr.x + (dx2 / len2) * r;
    const endY = curr.y + (dy2 / len2) * r;
    parts.push(`L${startX.toFixed(1)},${startY.toFixed(1)}`);
    parts.push(`Q${curr.x.toFixed(1)},${curr.y.toFixed(1)} ${endX.toFixed(1)},${endY.toFixed(1)}`);
  }
  const last = points[points.length - 1];
  parts.push(`L${last.x.toFixed(1)},${last.y.toFixed(1)}`);
  return parts.join(" ");
}

function segmentIntersectsBox(a: GraphPoint, b: GraphPoint, box: CardBox, pad = 2): boolean {
  const minX = Math.min(a.x, b.x);
  const maxX = Math.max(a.x, b.x);
  const minY = Math.min(a.y, b.y);
  const maxY = Math.max(a.y, b.y);
  return maxX > box.x - pad && minX < box.x + box.width + pad && maxY > box.y - pad && minY < box.y + box.height + pad;
}

function polylineIntersectsAnyBox(points: GraphPoint[], boxes: Map<string, CardBox>, skipIds?: ReadonlySet<string>): boolean {
  for (let i = 1; i < points.length; i++) {
    for (const [id, box] of boxes) {
      if (skipIds?.has(id)) continue;
      if (segmentIntersectsBox(points[i - 1], points[i], box, 0)) return true;
    }
  }
  return false;
}

/** Find a clear horizontal Y coordinate outside all card boxes between minX and maxX. */
function findClearCorridorY(
  preferredY: number,
  minX: number,
  maxX: number,
  boxes: Map<string, CardBox>,
  laneIndex: number,
): number {
  const blocking = [...boxes.values()].filter((b) => b.x + b.width > minX - 8 && b.x < maxX + 8);
  if (!blocking.length) return preferredY;
  const gaps: number[] = [];
  const sortedTops = [...new Set(blocking.map((b) => b.y))].sort((a, b) => a - b);
  const sortedBottoms = [...new Set(blocking.map((b) => b.y + b.height))].sort((a, b) => a - b);
  gaps.push(sortedTops[0] - 20 - laneIndex * 10);
  gaps.push(sortedBottoms[sortedBottoms.length - 1] + 20 + laneIndex * 10);
  for (let i = 0; i < sortedBottoms.length - 1; i++) {
    const low = sortedBottoms[i];
    const high = sortedTops.find((t) => t > low + 12);
    if (high !== undefined && high - low >= 16) {
      const mid = (low + high) / 2;
      if (!blocking.some((b) => mid >= b.y - 2 && mid <= b.y + b.height + 2)) {
        gaps.push(mid + ((laneIndex % 3) - 1) * 5);
      }
    }
  }
  return gaps.reduce((best, candidate) =>
    Math.abs(candidate - preferredY) < Math.abs(best - preferredY) ? candidate : best,
  );
}

/** Compute unified left-in / right-out port Y offsets per relation category on a card. */
function computeSidePortY(box: CardBox, category: RelationCategory, presentCategories: RelationCategory[]): number {
  if (presentCategories.length <= 1) return box.y + box.height / 2;
  const order: RelationCategory[] = ["contains", "prerequisite", "association"];
  const active = order.filter((c) => presentCategories.includes(c));
  const idx = Math.max(0, active.indexOf(category));
  if (active.length === 2) {
    return box.y + box.height * (idx === 0 ? 0.42 : 0.58);
  }
  return box.y + box.height * (idx === 0 ? 0.36 : idx === 1 ? 0.5 : 0.64);
}

/**
 * Route edges with:
 * - Default left-in (WEST), right-out (EAST) unified ports
 * - Shared trunks + tree branching for same-source & same-relation forward edges
 * - Obstacle-free orthogonal routing with 6-8px rounded corners
 * - Non-overlapping label placement
 */
export function routeWorkbenchEdgesWithJunctions(
  boxes: Map<string, CardBox>,
  edges: Edge[],
  elkEdgePoints?: Map<string, GraphPoint[]>,
): { routes: EdgeRoute[]; junctions: TrunkJunction[] } {
  const seen = new Set<string>();
  const valid = edges.filter((edge) => {
    const key = JSON.stringify([edge.source, edge.target, edge.data?.relation]);
    if (seen.has(key) || !boxes.has(edge.source) || !boxes.has(edge.target)) return false;
    seen.add(key);
    return true;
  });

  // Track which relation categories enter WEST / exit EAST of each node for unified port alignment.
  const outCats = new Map<string, Set<RelationCategory>>();
  const inCats = new Map<string, Set<RelationCategory>>();
  for (const edge of valid) {
    const cat = classifyRelation(edge);
    if (!outCats.has(edge.source)) outCats.set(edge.source, new Set());
    if (!inCats.has(edge.target)) inCats.set(edge.target, new Set());
    outCats.get(edge.source)!.add(cat);
    inCats.get(edge.target)!.add(cat);
  }

  // Group forward edges with same (source, relationCategory, targetColumn) for shared trunks.
  const trunkGroups = new Map<string, Edge[]>();
  for (const edge of valid) {
    if (edge.source === edge.target) continue;
    const src = boxes.get(edge.source)!;
    const tgt = boxes.get(edge.target)!;
    if (src.x + src.width + 24 <= tgt.x) {
      const cat = classifyRelation(edge);
      const colBucket = Math.round(tgt.x / 40);
      const groupKey = `${edge.source}::${cat}::${colBucket}`;
      const list = trunkGroups.get(groupKey) ?? [];
      list.push(edge);
      trunkGroups.set(groupKey, list);
    }
  }

  const edgeTrunkInfo = new Map<string, { busX: number; groupKey: string; count: number; isFirst: boolean }>();
  const junctions: TrunkJunction[] = [];
  let trunkIndex = 0;

  for (const [groupKey, groupEdges] of trunkGroups) {
    if (groupEdges.length < 2) continue;
    const src = boxes.get(groupEdges[0].source)!;
    const minTargetX = Math.min(...groupEdges.map((e) => boxes.get(e.target)!.x));
    const gutterWidth = minTargetX - (src.x + src.width);
    if (gutterWidth < 24) continue;
    const cat = classifyRelation(groupEdges[0]);
    const catOffset = cat === "contains" ? -10 : cat === "association" ? 10 : 0;
    const busX = src.x + src.width + Math.max(18, Math.min(gutterWidth - 18, gutterWidth * 0.46 + catOffset + ((trunkIndex % 3) - 1) * 6));
    trunkIndex += 1;

    const sorted = [...groupEdges].sort((a, b) => boxes.get(a.target)!.y - boxes.get(b.target)!.y);
    const fromY = computeSidePortY(src, cat, [...(outCats.get(groupEdges[0].source) ?? [cat])]);
    junctions.push({
      id: `junction-${groupKey}`,
      x: busX,
      y: fromY,
      source: groupEdges[0].source,
      relationCategory: cat,
      edgeIds: sorted.map((e) => e.id),
    });
    sorted.forEach((e, idx) => {
      edgeTrunkInfo.set(e.id, { busX, groupKey, count: sorted.length, isFirst: idx === 0 });
    });
  }

  let corridorLane = 0;
  let gutterLane = 0;

  const routes: EdgeRoute[] = valid.map((edge) => {
    const src = boxes.get(edge.source)!;
    const tgt = boxes.get(edge.target)!;
    const cat = classifyRelation(edge);
    const from: GraphPoint = {
      x: src.x + src.width,
      y: computeSidePortY(src, cat, [...(outCats.get(edge.source) ?? [cat])]),
    };
    const to: GraphPoint = {
      x: tgt.x,
      y: computeSidePortY(tgt, cat, [...(inCats.get(edge.target) ?? [cat])]),
    };

    const trunk = edgeTrunkInfo.get(edge.id);
    let points: GraphPoint[];

    if (edge.source === edge.target) {
      const outX = src.x + src.width + 24;
      const loopY = src.y - 18;
      const inX = src.x - 24;
      points = [from, { x: outX, y: from.y }, { x: outX, y: loopY }, { x: inX, y: loopY }, { x: inX, y: to.y }, to];
    } else if (trunk) {
      const candidate = simplifyOrthogonalPoints([
        from,
        { x: trunk.busX, y: from.y },
        { x: trunk.busX, y: to.y },
        to,
      ]);
      const skipIds = new Set([edge.source, edge.target]);
      if (!polylineIntersectsAnyBox(candidate, boxes, skipIds)) {
        points = candidate;
      } else {
        const lane = corridorLane++;
        const cy = findClearCorridorY((from.y + to.y) / 2, from.x, to.x, boxes, lane);
        const endX = to.x - 20 - (lane % 3) * 6;
        const trunkCorridor = simplifyOrthogonalPoints([
          from,
          { x: trunk.busX, y: from.y },
          { x: trunk.busX, y: cy },
          { x: endX, y: cy },
          { x: endX, y: to.y },
          to,
        ]);
        points = !polylineIntersectsAnyBox(trunkCorridor, boxes, skipIds)
          ? trunkCorridor
          : routeOrthogonalFallback(from, to, boxes, skipIds, corridorLane++, gutterLane++);
      }
    } else {
      const elkPts = elkEdgePoints?.get(edge.id);
      const skipIds = new Set([edge.source, edge.target]);
      if (elkPts && elkPts.length >= 2) {
        const snapped = simplifyOrthogonalPoints([from, ...elkPts.slice(1, -1), to]);
        if (!polylineIntersectsAnyBox(snapped, boxes, skipIds)) {
          points = snapped;
        } else {
          points = routeOrthogonalFallback(from, to, boxes, skipIds, corridorLane++, gutterLane++);
        }
      } else {
        points = routeOrthogonalFallback(from, to, boxes, skipIds, corridorLane++, gutterLane++);
      }
    }

    const bestSeg = points.slice(1).map((point, i) => ({
      x: (point.x + points[i].x) / 2,
      y: (point.y + points[i].y) / 2,
      horizontal: Math.abs(point.y - points[i].y) < 1,
      length: Math.abs(point.x - points[i].x) + Math.abs(point.y - points[i].y),
    })).reduce((best, seg) => {
      const scoreA = seg.length + (seg.horizontal ? 24 : 0);
      const scoreB = best.length + (best.horizontal ? 24 : 0);
      return scoreA > scoreB ? seg : best;
    });

    return {
      edge,
      points,
      path: buildRoundedOrthogonalPath(points, EDGE_CORNER_RADIUS),
      label: { x: bestSeg.x, y: bestSeg.y },
      relationCategory: cat,
      sharedTrunk: Boolean(trunk),
      suppressLabel: Boolean(trunk && !trunk.isFirst && cat === "contains"),
    };
  });

  // Deduplicate / nudge labels so they never overlap cards or each other.
  const placedLabels: { x: number; y: number; w: number; h: number }[] = [];
  for (const route of routes) {
    if (route.suppressLabel || route.edge.label == null) continue;
    const text = String(route.edge.label);
    const w = Math.max(26, text.length * 12 + 10);
    const h = 18;
    let lx = route.label.x;
    let ly = route.label.y - 6;
    const overlaps = (cx: number, cy: number) => {
      for (const box of boxes.values()) {
        if (cx + w / 2 > box.x - 4 && cx - w / 2 < box.x + box.width + 4 && cy + h / 2 > box.y - 4 && cy - h / 2 < box.y + box.height + 4) {
          return true;
        }
      }
      for (const placed of placedLabels) {
        if (Math.abs(cx - placed.x) < (w + placed.w) / 2 + 4 && Math.abs(cy - placed.y) < (h + placed.h) / 2 + 2) {
          return true;
        }
      }
      return false;
    };
    if (overlaps(lx, ly)) {
      const offsets = [-16, 16, -28, 28];
      for (const dy of offsets) {
        if (!overlaps(lx, ly + dy)) {
          ly += dy;
          break;
        }
      }
    }
    route.label = { x: lx, y: ly };
    placedLabels.push({ x: lx, y: ly, w, h });
  }

  return { routes, junctions };
}

function routeOrthogonalFallback(
  from: GraphPoint,
  to: GraphPoint,
  boxes: Map<string, CardBox>,
  skipIds: ReadonlySet<string>,
  corridorLane: number,
  gutterLane: number,
): GraphPoint[] {
  const isForward = from.x + 24 <= to.x;
  if (isForward) {
    // Straight horizontal line if aligned and clear
    if (Math.abs(from.y - to.y) < 2) {
      const straight = [from, { x: to.x, y: from.y }];
      if (!polylineIntersectsAnyBox(straight, boxes, skipIds)) return straight;
    }
    // 2-bend Z-shape if clear
    const midX = (from.x + to.x) / 2 + ((gutterLane % 5) - 2) * 8;
    const zShape = simplifyOrthogonalPoints([from, { x: midX, y: from.y }, { x: midX, y: to.y }, to]);
    if (!polylineIntersectsAnyBox(zShape, boxes, skipIds)) return zShape;
  }

  // Obstacle-avoiding corridor route (used for skip-layer, same-column, and backward edges)
  const startGutterX = from.x + 22 + (gutterLane % 4) * 8;
  const endGutterX = to.x - 22 - (gutterLane % 4) * 8;
  const minX = Math.min(startGutterX, endGutterX);
  const maxX = Math.max(startGutterX, endGutterX);
  const preferredCorridorY = findClearCorridorY((from.y + to.y) / 2, minX, maxX, boxes, corridorLane);
  const candidate = simplifyOrthogonalPoints([
    from,
    { x: startGutterX, y: from.y },
    { x: startGutterX, y: preferredCorridorY },
    { x: endGutterX, y: preferredCorridorY },
    { x: endGutterX, y: to.y },
    to,
  ]);
  if (!polylineIntersectsAnyBox(candidate, boxes, skipIds)) return candidate;

  // Guaranteed clear outer corridor above or below all blocking cards
  let minY = Infinity;
  let maxY = -Infinity;
  for (const box of boxes.values()) {
    minY = Math.min(minY, box.y);
    maxY = Math.max(maxY, box.y + box.height);
  }
  const outerTop = minY - 24 - (corridorLane % 6) * 10;
  const outerBottom = maxY + 24 + (corridorLane % 6) * 10;
  const outerY = Math.abs(outerTop - (from.y + to.y) / 2) <= Math.abs(outerBottom - (from.y + to.y) / 2) ? outerTop : outerBottom;
  const safeStartX = from.x + 16 + (gutterLane % 4) * 6;
  const safeEndX = to.x - 16 - (gutterLane % 4) * 6;
  return simplifyOrthogonalPoints([
    from,
    { x: safeStartX, y: from.y },
    { x: safeStartX, y: outerY },
    { x: safeEndX, y: outerY },
    { x: safeEndX, y: to.y },
    to,
  ]);
}

export function routeWorkbenchEdges(
  boxes: Map<string, CardBox>,
  edges: Edge[],
  elkEdgePoints?: Map<string, GraphPoint[]>,
): EdgeRoute[] {
  return routeWorkbenchEdgesWithJunctions(boxes, edges, elkEdgePoints).routes;
}
