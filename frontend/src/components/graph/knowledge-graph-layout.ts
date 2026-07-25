import type { Edge } from "@xyflow/react";

import type { KnowledgeNode } from "./knowledge-graph";

/** TJ-Sylva style tree geometry (px). */
export const TREE_CARD_W = 230;
export const TREE_MAIN_W = 250;
export const TREE_ROOT_SIZE = 64;
export const TREE_NODE_H = 148;
export const TREE_ROW_GAP = 34;
export const TREE_LEVEL_GAP = 370;
export const TREE_MAIN_GAP = 96;
export const TREE_MAIN_BAND_FLOOR = 170;

export type TreeNodeKind = "root" | "main" | "branch-left" | "branch-right";

export type TreeLayoutItem = {
  id: string;
  x: number;
  y: number;
  depth: number;
  kind: TreeNodeKind;
  step?: number;
  stepTotal?: number;
  parentId?: string;
};

export type TreeLayoutEdge = {
  id: string;
  source: string;
  target: string;
  spine: boolean;
  active: boolean;
};

export type KnowledgeTreeLayout = {
  items: TreeLayoutItem[];
  edges: TreeLayoutEdge[];
  positions: Record<string, { x: number; y: number }>;
  depths: Record<string, number>;
  maxDepth: number;
  parentByChild: Map<string, string>;
  childrenByParent: Map<string, string[]>;
};

function relationOf(edge: Edge) {
  const data = edge.data;
  if (!data || typeof data !== "object") return undefined;
  const relation = (data as Record<string, unknown>).relation;
  return typeof relation === "string" ? relation : undefined;
}

/**
 * Build the containment hierarchy used for teaching-tree layout.
 * Prefer explicit `contains` edges; fall back to all edges for legacy graphs.
 */
export function buildKnowledgeTreeHierarchy(
  nodes: KnowledgeNode[],
  edges: Edge[],
) {
  const ids = new Set(nodes.map((node) => node.id));
  const nodeOrder = new Map(nodes.map((node, index) => [node.id, index]));
  const children = new Map<string, string[]>();
  const parentByChild = new Map<string, string>();
  nodes.forEach((node) => {
    children.set(node.id, []);
  });

  const usableEdges = edges.filter(
    (edge) =>
      ids.has(edge.source) &&
      ids.has(edge.target) &&
      edge.source !== edge.target,
  );
  const containmentEdges = usableEdges.filter(
    (edge) => relationOf(edge) === "contains",
  );
  const hierarchyEdges = containmentEdges.length
    ? containmentEdges
    : usableEdges;
  const explicitRootIds = new Set(
    nodes.filter((node) => node.data.root).map((node) => node.id),
  );
  const orderedEdges = [...hierarchyEdges].sort(
    (left, right) =>
      (nodeOrder.get(left.source) ?? 0) - (nodeOrder.get(right.source) ?? 0) ||
      (nodeOrder.get(left.target) ?? 0) - (nodeOrder.get(right.target) ?? 0),
  );

  const wouldCreateCycle = (parentId: string, childId: string) => {
    let cursor: string | undefined = parentId;
    while (cursor) {
      if (cursor === childId) return true;
      cursor = parentByChild.get(cursor);
    }
    return false;
  };

  for (const edge of orderedEdges) {
    // One node gets one layout parent. Extra semantic edges stay visible but
    // do not duplicate a card or make the drawing cyclic.
    if (
      explicitRootIds.has(edge.target) ||
      parentByChild.has(edge.target) ||
      wouldCreateCycle(edge.source, edge.target)
    ) {
      continue;
    }
    children.get(edge.source)?.push(edge.target);
    parentByChild.set(edge.target, edge.source);
  }

  for (const childIds of children.values()) {
    childIds.sort(
      (left, right) =>
        (nodeOrder.get(left) ?? 0) - (nodeOrder.get(right) ?? 0),
    );
  }

  const roots = [
    ...nodes
      .filter((node) => explicitRootIds.has(node.id))
      .map((node) => node.id),
    ...nodes
      .filter(
        (node) =>
          !explicitRootIds.has(node.id) && !parentByChild.has(node.id),
      )
      .map((node) => node.id),
  ];

  return {
    children,
    parentByChild,
    rootId: roots[0] ?? nodes[0]?.id,
    nodeOrder,
  };
}

/**
 * TJ-Sylva-style "knowledge tree":
 * - root seed sits near the bottom of a vertical spine
 * - main topics climb the spine upward
 * - deeper branches alternate left / right
 * - collapsed subtrees collapse into the parent band height
 */
export function buildKnowledgeTreeLayout(
  nodes: KnowledgeNode[],
  edges: Edge[],
  options: {
    collapsedIds?: Set<string>;
    activePathIds?: Set<string>;
    reservedTrunkHeight?: number;
  } = {},
): KnowledgeTreeLayout {
  if (!nodes.length) {
    return {
      items: [],
      edges: [],
      positions: {},
      depths: {},
      maxDepth: 0,
      parentByChild: new Map(),
      childrenByParent: new Map(),
    };
  }

  const collapsedIds = options.collapsedIds ?? new Set<string>();
  const activePathIds = options.activePathIds ?? new Set<string>();
  const reservedFloor =
    options.reservedTrunkHeight ?? TREE_MAIN_BAND_FLOOR;
  const { children, parentByChild, rootId } = buildKnowledgeTreeHierarchy(
    nodes,
    edges,
  );
  if (!rootId) {
    return {
      items: [],
      edges: [],
      positions: {},
      depths: {},
      maxDepth: 0,
      parentByChild,
      childrenByParent: children,
    };
  }

  const heightCache = new Map<string, number>();
  const branchHeight = (
    nodeId: string,
    ancestors = new Set<string>(),
  ): number => {
    if (heightCache.has(nodeId)) return heightCache.get(nodeId)!;
    if (ancestors.has(nodeId)) return TREE_NODE_H;
    if (collapsedIds.has(nodeId)) {
      heightCache.set(nodeId, TREE_NODE_H);
      return TREE_NODE_H;
    }
    const nextAncestors = new Set(ancestors);
    nextAncestors.add(nodeId);
    const childIds = (children.get(nodeId) ?? []).filter(
      (childId) => !ancestors.has(childId),
    );
    const height = childIds.length
      ? Math.max(
          TREE_NODE_H,
          childIds.reduce(
            (total, childId) => total + branchHeight(childId, nextAncestors),
            0,
          ) +
            Math.max(0, childIds.length - 1) * TREE_ROW_GAP,
        )
      : TREE_NODE_H;
    heightCache.set(nodeId, height);
    return height;
  };

  const items: TreeLayoutItem[] = [];
  const layoutEdges: TreeLayoutEdge[] = [];
  const positions: Record<string, { x: number; y: number }> = {};
  const depths: Record<string, number> = {};
  const placed = new Set<string>();

  // Root near the bottom of the canvas; main spine grows upward (-y).
  const rootX = 0;
  const rootY = 0;
  positions[rootId] = { x: rootX, y: rootY };
  depths[rootId] = 0;
  placed.add(rootId);
  items.push({
    id: rootId,
    x: rootX,
    y: rootY,
    depth: 0,
    kind: "root",
  });

  const mainIds = children.get(rootId) ?? [];
  // Climb upward from just above the root seed.
  let cursorY = rootY - 230;
  let previousMainId = rootId;
  let previousMainY = rootY;

  mainIds.forEach((mainId, index) => {
    const height = Math.max(reservedFloor, branchHeight(mainId));
    // Center the main card inside its reserved band.
    const y = cursorY - height / 2;
    positions[mainId] = { x: rootX, y };
    depths[mainId] = 1;
    placed.add(mainId);
    items.push({
      id: mainId,
      x: rootX,
      y,
      depth: 1,
      kind: "main",
      step: index + 1,
      stepTotal: mainIds.length,
      parentId: rootId,
    });
    layoutEdges.push({
      id: `spine-${previousMainId}->${mainId}`,
      source: previousMainId,
      target: mainId,
      spine: true,
      active: activePathIds.has(mainId),
    });

    const side: 1 | -1 = index % 2 === 0 ? 1 : -1;
    if (!collapsedIds.has(mainId)) {
      placeBranchChildren(mainId, 2, rootX, y, side, "main");
    }

    previousMainId = mainId;
    previousMainY = y;
    cursorY -= height + TREE_MAIN_GAP;
  });

  function placeBranchChildren(
    parentId: string,
    depth: number,
    parentX: number,
    parentY: number,
    side: 1 | -1,
    _parentKind: TreeNodeKind,
  ) {
    const childIds = (children.get(parentId) ?? []).filter(
      (childId) => !placed.has(childId),
    );
    if (!childIds.length) return;
    const childHeights = childIds.map((childId) => branchHeight(childId));
    const totalHeight =
      childHeights.reduce((sum, value) => sum + value, 0) +
      Math.max(0, childIds.length - 1) * TREE_ROW_GAP;
    let cursor = parentY - totalHeight / 2;
    childIds.forEach((childId, index) => {
      const height = childHeights[index];
      const y = cursor + height / 2;
      const x = parentX + side * TREE_LEVEL_GAP;
      const kind: TreeNodeKind =
        side > 0 ? "branch-right" : "branch-left";
      positions[childId] = { x, y };
      depths[childId] = depth;
      placed.add(childId);
      items.push({
        id: childId,
        x,
        y,
        depth,
        kind,
        parentId,
      });
      layoutEdges.push({
        id: `${parentId}->${childId}`,
        source: parentId,
        target: childId,
        spine: false,
        active:
          activePathIds.has(parentId) && activePathIds.has(childId),
      });
      if (!collapsedIds.has(childId)) {
        placeBranchChildren(childId, depth + 1, x, y, side, kind);
      }
      cursor += height + TREE_ROW_GAP;
    });
  }

  // Descendants of collapsed nodes are intentionally omitted from the stage
  // (TJ-Sylva visibleNodeIdSet). Mark them so they do not fall into orphans.
  const isUnderCollapsedAncestor = (nodeId: string) => {
    let cursor = parentByChild.get(nodeId);
    while (cursor) {
      if (collapsedIds.has(cursor)) return true;
      cursor = parentByChild.get(cursor);
    }
    return false;
  };
  nodes.forEach((node) => {
    if (placed.has(node.id)) return;
    if (isUnderCollapsedAncestor(node.id)) {
      placed.add(node.id);
    }
  });

  // True orphans / cyclic leftovers sit in an auxiliary column so the graph
  // stays inspectable instead of silently dropping facts.
  const orphanDepth =
    (Object.values(depths).length
      ? Math.max(...Object.values(depths))
      : 0) + 1;
  let orphanY = Math.min(previousMainY - TREE_NODE_H, cursorY) - TREE_NODE_H;
  nodes.forEach((node) => {
    if (placed.has(node.id)) return;
    positions[node.id] = { x: TREE_LEVEL_GAP * 2, y: orphanY };
    depths[node.id] = orphanDepth;
    items.push({
      id: node.id,
      x: TREE_LEVEL_GAP * 2,
      y: orphanY,
      depth: orphanDepth,
      kind: "branch-right",
    });
    placed.add(node.id);
    orphanY -= TREE_NODE_H + TREE_ROW_GAP;
  });

  const depthValues = Object.values(depths);
  return {
    items,
    edges: layoutEdges,
    positions,
    depths,
    maxDepth: depthValues.length ? Math.max(...depthValues) : 0,
    parentByChild,
    childrenByParent: children,
  };
}

export function getKnowledgeGraphTreeDepths(
  nodes: KnowledgeNode[],
  edges: Edge[],
) {
  if (!nodes.length) return new Map<string, number>();
  const layout = buildKnowledgeTreeLayout(nodes, edges);
  return new Map(Object.entries(layout.depths));
}

export function getKnowledgeGraphTreeDepth(
  nodes: KnowledgeNode[],
  edges: Edge[],
) {
  const depths = getKnowledgeGraphTreeDepths(nodes, edges);
  return depths.size ? Math.max(...depths.values()) : 0;
}

/** Active path = selected node + all ancestors up to root. */
export function getTreeActivePath(
  nodeId: string | undefined,
  parentByChild: Map<string, string>,
) {
  const path = new Set<string>();
  let cursor = nodeId;
  while (cursor) {
    path.add(cursor);
    cursor = parentByChild.get(cursor);
  }
  return path;
}

export function countTreeDescendants(
  nodeId: string,
  childrenByParent: Map<string, string[]>,
) {
  let count = 0;
  const queue = [...(childrenByParent.get(nodeId) ?? [])];
  while (queue.length) {
    const next = queue.shift()!;
    count += 1;
    for (const child of childrenByParent.get(next) ?? []) {
      queue.push(child);
    }
  }
  return count;
}

/** Card size used when packing free (non-tree) layouts. */
export const FREE_NODE_W = 150;
export const FREE_NODE_H = 96;

/**
 * Spatial layout: radial rings around the root so relations read as an
 * organic knowledge map (distinct from the ordered flat grid).
 */
export function buildSpatialLayout(
  nodes: KnowledgeNode[],
  edges: Edge[],
): Record<string, { x: number; y: number }> {
  if (!nodes.length) return {};

  const { children, rootId, nodeOrder } =
    buildKnowledgeTreeHierarchy(nodes, edges);
  const originId = rootId ?? nodes[0]!.id;
  const positions: Record<string, { x: number; y: number }> = {};
  const depths: Record<string, number> = { [originId]: 0 };
  positions[originId] = { x: 0, y: 0 };

  // BFS depths from the teaching hierarchy.
  const queue = [originId];
  while (queue.length) {
    const current = queue.shift()!;
    const depth = depths[current] ?? 0;
    for (const childId of children.get(current) ?? []) {
      if (depths[childId] !== undefined) continue;
      depths[childId] = depth + 1;
      queue.push(childId);
    }
  }

  // Group by depth; fall back unplaced nodes into a trailing ring.
  const byDepth = new Map<number, string[]>();
  for (const node of nodes) {
    const depth = depths[node.id] ?? -1;
    if (depth < 0) continue;
    const bucket = byDepth.get(depth) ?? [];
    bucket.push(node.id);
    byDepth.set(depth, bucket);
  }
  for (const bucket of byDepth.values()) {
    bucket.sort(
      (left, right) =>
        (nodeOrder.get(left) ?? 0) - (nodeOrder.get(right) ?? 0),
    );
  }

  const ringGap = 210;
  for (const [depth, ids] of byDepth) {
    if (depth === 0) continue;
    const radius = depth * ringGap;
    const count = ids.length;
    // Slight phase offset per ring so spokes do not stack perfectly.
    const phase = (depth % 2 === 0 ? 0 : Math.PI / count) - Math.PI / 2;
    ids.forEach((id, index) => {
      const angle = phase + (index / count) * Math.PI * 2;
      // Mild radius jitter by sibling index keeps dense rings readable.
      const r = radius + (index % 3) * 12;
      positions[id] = {
        x: Math.cos(angle) * r,
        y: Math.sin(angle) * r,
      };
    });
  }

  // Orphans / cycles that never joined the hierarchy sit in an outer arc.
  const orphans = nodes
    .filter((node) => positions[node.id] === undefined)
    .sort(
      (left, right) =>
        (nodeOrder.get(left.id) ?? 0) - (nodeOrder.get(right.id) ?? 0),
    );
  if (orphans.length) {
    const maxDepth = Math.max(0, ...Object.values(depths));
    const radius = (maxDepth + 1) * ringGap;
    orphans.forEach((node, index) => {
      const angle =
        -Math.PI / 2 + ((index + 0.5) / orphans.length) * Math.PI * 2;
      positions[node.id] = {
        x: Math.cos(angle) * radius,
        y: Math.sin(angle) * radius,
      };
    });
  }

  // Convert center points to React Flow top-left positions.
  const halfW = FREE_NODE_W / 2;
  const halfH = FREE_NODE_H / 2;
  for (const id of Object.keys(positions)) {
    const point = positions[id]!;
    positions[id] = { x: point.x - halfW, y: point.y - halfH };
  }
  // Root emphasis circle is larger — center it the same way.
  if (positions[originId]) {
    positions[originId] = {
      x: -FREE_NODE_W / 2,
      y: -FREE_NODE_H / 2,
    };
  }

  return positions;
}

/**
 * Flat layout: compact left-to-right grid ordered by depth then source
 * order — a clean, scannable board distinct from the radial spatial map.
 */
export function buildFlatLayout(
  nodes: KnowledgeNode[],
  edges: Edge[],
): Record<string, { x: number; y: number }> {
  if (!nodes.length) return {};

  const { children, rootId, nodeOrder } = buildKnowledgeTreeHierarchy(
    nodes,
    edges,
  );
  const originId = rootId ?? nodes[0]!.id;
  const depths: Record<string, number> = { [originId]: 0 };
  const queue = [originId];
  while (queue.length) {
    const current = queue.shift()!;
    const depth = depths[current] ?? 0;
    for (const childId of children.get(current) ?? []) {
      if (depths[childId] !== undefined) continue;
      depths[childId] = depth + 1;
      queue.push(childId);
    }
  }

  const ordered = [...nodes].sort((left, right) => {
    const depthDelta =
      (depths[left.id] ?? Number.MAX_SAFE_INTEGER) -
      (depths[right.id] ?? Number.MAX_SAFE_INTEGER);
    if (depthDelta !== 0) return depthDelta;
    return (nodeOrder.get(left.id) ?? 0) - (nodeOrder.get(right.id) ?? 0);
  });

  const columns = Math.max(3, Math.ceil(Math.sqrt(ordered.length)));
  const gapX = FREE_NODE_W + 48;
  const gapY = FREE_NODE_H + 42;
  const positions: Record<string, { x: number; y: number }> = {};
  ordered.forEach((node, index) => {
    const col = index % columns;
    const row = Math.floor(index / columns);
    positions[node.id] = {
      x: col * gapX,
      y: row * gapY,
    };
  });
  return positions;
}
