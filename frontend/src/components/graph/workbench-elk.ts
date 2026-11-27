import ELK from "elkjs/lib/elk.bundled.js";
import type { ElkExtendedEdge, ElkNode } from "elkjs/lib/elk-api";
import type { Edge } from "@xyflow/react";
import {
  CARD,
  classifyRelation,
  graphBounds,
  prerequisiteStages,
  type CardBox,
  type GraphPoint,
  type StructuralNode,
  type WorkbenchLayoutResult,
  type WorkbenchView,
} from "./workbench-layout";

export const ELK_WORKBENCH_LAYOUT_OPTIONS: Record<string, string> = {
  "elk.algorithm": "layered",
  "elk.direction": "RIGHT",
  "elk.edgeRouting": "ORTHOGONAL",
  "elk.partitioning.activate": "true",
  "elk.spacing.nodeNode": "40",
  "elk.spacing.edgeEdge": "12",
  "elk.spacing.edgeNode": "24",
  "elk.layered.spacing.nodeNodeBetweenLayers": "100",
  "elk.layered.spacing.edgeNodeBetweenLayers": "24",
  "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
  "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
  "elk.portConstraints": "FIXED_POS",
};

const elk = new ELK();
const elkCache = new Map<string, WorkbenchLayoutResult>();
const MAX_CACHE_ENTRIES = 24;

function buildCacheKey(nodes: StructuralNode[], edges: Edge[], view: WorkbenchView) {
  return JSON.stringify({ view, nodes, edges: edges.map((e) => [e.id, e.source, e.target, e.data?.relation]) });
}

export async function layoutWorkbenchWithElk(
  nodes: StructuralNode[],
  edges: Edge[],
  view: WorkbenchView,
): Promise<WorkbenchLayoutResult | null> {
  if (view !== "route" || !nodes.length) return null;

  const cacheKey = buildCacheKey(nodes, edges, view);
  const cached = elkCache.get(cacheKey);
  if (cached) return cached;

  const ids = new Set(nodes.map((n) => n.id));
  const structuralEdges = edges.filter(
    (e) => ids.has(e.source) && ids.has(e.target) && e.source !== e.target && classifyRelation(e) !== "association",
  );
  const ranks = prerequisiteStages([...ids], structuralEdges);
  const root = nodes.find((n) => n.root);
  const prereq = new Set(
    structuralEdges
      .filter((e) => e.data?.relation === "prerequisite")
      .flatMap((e) => [e.source, e.target]),
  );

  // Assign stage partition index: 0 = 学习目标 (if root has no prerequisite edges), 1+ = 阶段 01, 02, ...
  const hasGoalColumn = Boolean(root && !prereq.has(root.id));
  const stageByNode = new Map<string, number>();
  const stagesMap = new Map<number, StructuralNode[]>();

  for (const node of nodes) {
    if (hasGoalColumn && node.id === root!.id) {
      stageByNode.set(node.id, 0);
      continue;
    }
    const rank = ranks.get(node.id) ?? 0;
    const partition = hasGoalColumn ? rank + 1 : rank;
    stageByNode.set(node.id, partition);
    const list = stagesMap.get(rank) ?? [];
    list.push(node);
    stagesMap.set(rank, list);
  }

  const orderedStages = [...stagesMap].sort(([a], [b]) => a - b);
  const groups = orderedStages
    .map(([rank, members]) => ({
      label: `阶段 ${String(rank + 1).padStart(2, "0")}`,
      ids: members.filter((n) => !n.root).map((n) => n.id),
    }))
    .filter((g) => g.ids.length);

  // Remap partitions to contiguous 0..K so ELK never creates empty layers.
  const uniquePartitions = [...new Set(stageByNode.values())].sort((a, b) => a - b);
  const partitionIndex = new Map(uniquePartitions.map((p, idx) => [p, idx]));

  const children: ElkNode[] = nodes.map((node) => {
    const width = node.root ? CARD.goalWidth : CARD.width;
    const height = CARD.height;
    const p = partitionIndex.get(stageByNode.get(node.id) ?? 0) ?? 0;
    return {
      id: node.id,
      width,
      height,
      layoutOptions: {
        "elk.partitioning.partition": String(p),
        "elk.portConstraints": "FIXED_POS",
      },
      ports: [
        {
          id: `${node.id}:in`,
          x: 0,
          y: height / 2,
          properties: { "port.side": "WEST" },
        },
        {
          id: `${node.id}:out`,
          x: width,
          y: height / 2,
          properties: { "port.side": "EAST" },
        },
      ],
    };
  });

  const elkEdges: ElkExtendedEdge[] = structuralEdges.map((edge) => ({
    id: edge.id,
    sources: [`${edge.source}:out`],
    targets: [`${edge.target}:in`],
  }));

  const graph: ElkNode = {
    id: "root",
    layoutOptions: ELK_WORKBENCH_LAYOUT_OPTIONS,
    children,
    edges: elkEdges,
  };

  const laidOut = await elk.layout(graph);
  const boxes = new Map<string, CardBox>();
  const placedChildren = laidOut.children ?? [];

  // Group placed nodes by their stage partition for column/row indexing.
  const byStage = new Map<number, { id: string; x: number; y: number; width: number; height: number }[]>();
  for (const child of placedChildren) {
    const x = child.x ?? 0;
    const y = child.y ?? 0;
    const width = child.width ?? CARD.width;
    const height = child.height ?? CARD.height;
    const stage = stageByNode.get(child.id) ?? 0;
    const list = byStage.get(stage) ?? [];
    list.push({ id: child.id, x, y, width, height });
    byStage.set(stage, list);
  }

  const sortedStageKeys = [...byStage.keys()].sort((a, b) => a - b);
  const headings: { x: number; y: number; label: string }[] = [];

  sortedStageKeys.forEach((stage, colIdx) => {
    const items = byStage.get(stage)!;
    items.sort((a, b) => a.y - b.y);
    items.forEach((item, rowIdx) => {
      boxes.set(item.id, {
        x: item.x,
        y: item.y,
        width: item.width,
        height: item.height,
        column: colIdx,
        row: rowIdx,
      });
    });
    const minX = Math.min(...items.map((i) => i.x));
    const minY = Math.min(...items.map((i) => i.y));
    const label =
      hasGoalColumn && stage === 0
        ? "学习目标"
        : `阶段 ${String((hasGoalColumn ? stage : stage + 1)).padStart(2, "0")}`;
    headings.push({ x: minX, y: minY - 12, label });
  });

  const elkEdgePoints = new Map<string, GraphPoint[]>();
  for (const edge of laidOut.edges ?? []) {
    const section = edge.sections?.[0];
    if (!section) continue;
    const pts: GraphPoint[] = [
      section.startPoint,
      ...(section.bendPoints ?? []),
      section.endPoint,
    ];
    elkEdgePoints.set(edge.id, pts);
  }

  const result: WorkbenchLayoutResult = {
    boxes,
    headings,
    regions: [],
    bounds: graphBounds(boxes.values()),
    groups,
    rootId: root?.id,
    elkEdgePoints,
  };

  if (elkCache.size >= MAX_CACHE_ENTRIES) {
    const oldest = elkCache.keys().next().value;
    if (oldest !== undefined) elkCache.delete(oldest);
  }
  elkCache.set(cacheKey, result);
  return result;
}
