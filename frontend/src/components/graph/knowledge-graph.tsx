import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
} from "react";
import {
  Background,
  BackgroundVariant,
  BaseEdge,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  applyNodeChanges,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeChange,
  type NodeMouseHandler,
  type NodeProps,
  type ReactFlowInstance,
} from "@xyflow/react";
import {
  Box,
  GitFork,
  LayoutDashboard,
  Minus,
  Plus,
  RotateCcw,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { StarRating } from "@/components/common/primitives";
import {
  TREE_CARD_W,
  TREE_MAIN_W,
  TREE_NODE_H,
  TREE_ROOT_SIZE,
  buildFlatLayout,
  buildKnowledgeTreeLayout,
  buildSpatialLayout,
  countTreeDescendants,
  getTreeActivePath,
  type TreeNodeKind,
} from "./knowledge-graph-layout";
import { NodeExploreChip, RecommendDots } from "./node-explore";
import { importanceStars } from "./node-metrics";

export type KnowledgeNodeData = {
  label: string;
  description?: string;
  nodeType?: string;
  stars?: number;
  state?: string;
  root?: boolean;
  evidence?: string;
  depth?: number;
  tree?: boolean;
  rootEmphasis?: boolean;
  kind?: TreeNodeKind;
  step?: number;
  stepTotal?: number;
  collapsed?: boolean;
  hasChildren?: boolean;
  hiddenCount?: number;
  /** Local goal importance 1–100 (maps to TJ-Sylva recommend dots). */
  targetWeight?: number;
  /** Deep-dive round count for this node (conversation evidence). */
  exploreCount?: number;
  /** User-declared mastery; only these enter the capability graph. */
  mastered?: boolean;
  onToggleCollapse?: (nodeId: string) => void;
  onOpenExplore?: (nodeId: string) => void;
  [key: string]: unknown;
};
export type KnowledgeNode = Node<KnowledgeNodeData, "knowledge">;
export type KnowledgeGraphLayout = "spatial" | "flat" | "tree";

function clampGraphZoom(value: number, minimum: number, maximum: number) {
  return Math.min(maximum, Math.max(minimum, Number(value.toFixed(2))));
}

export interface KnowledgeGraphProps {
  nodes?: KnowledgeNode[];
  edges?: Edge[];
  className?: string;
  compact?: boolean;
  grow?: boolean;
  interactive?: boolean;
  layout?: KnowledgeGraphLayout;
  maxDepth?: number;
  rootEmphasis?: boolean;
  minimumZoom?: number;
  maximumZoom?: number;
  showZoomControls?: boolean;
  onSelect?: (node: KnowledgeNodeData & { id: string }) => void;
  onSelectionChange?: (
    nodes: Array<KnowledgeNodeData & { id: string }>,
  ) => void;
  onStudy?: (node: KnowledgeNodeData & { id: string }) => void;
  studyOnSelect?: boolean;
  selectedId?: string;
  selectedIds?: string[];
  multiple?: boolean;
  title?: string;
  /** Persist collapse across remounts (e.g. workbench). */
  collapsedIds?: string[];
  onCollapsedIdsChange?: (ids: string[]) => void;
}

const knowledgeNodeTypeLabels: Record<string, string> = {
  root: "目标",
  concept: "概念",
  practice: "练习",
  assessment: "测评",
};

const knowledgeStateLabels: Record<string, string> = {
  fresh: "掌握稳定",
  due_soon: "即将复习",
  due: "待复习",
  relearning: "重新学习",
  unverified: "未验证",
  none: "暂无证据",
  single: "单条证据",
  multi: "多条证据",
  cross_time: "跨时段证据",
  robust: "证据充分",
  conflicted: "证据冲突",
  interest_only: "仅兴趣记录",
  mastered: "已掌握",
};

function knowledgeStateLabel(value: string | undefined, fallback: string) {
  const state = value ?? fallback;
  return knowledgeStateLabels[state] ?? state;
}

function KnowledgeNodeView({ data, selected, id }: NodeProps<KnowledgeNode>) {
  if (data.initial)
    return (
      <div aria-label={`${data.label} 根节点`} className="knowledge-seed">
        <Handle
          className="!size-1 !border-0 !bg-transparent opacity-0"
          position={Position.Top}
          type="target"
        />
        <span />
        <Handle
          className="!size-1 !border-0 !bg-transparent opacity-0"
          position={Position.Bottom}
          type="source"
        />
      </div>
    );

  const tree = Boolean(data.tree);
  const isRoot = Boolean(data.root) || data.kind === "root" || data.depth === 0;
  const kind = data.kind ?? (isRoot ? "root" : "branch-right");
  const hasChildren = Boolean(data.hasChildren);
  const collapsed = Boolean(data.collapsed);
  const hiddenCount = data.hiddenCount ?? 0;

  // Tree edges leave/enter from the spine or card sides (TJ-Sylva anchors).
  const targetPosition =
    kind === "root" || kind === "main"
      ? Position.Bottom
      : kind === "branch-left"
        ? Position.Right
        : Position.Left;
  const sourcePosition =
    kind === "root" || kind === "main"
      ? Position.Top
      : kind === "branch-left"
        ? Position.Left
        : Position.Right;

  const handleCollapse = (event: ReactMouseEvent) => {
    event.stopPropagation();
    event.preventDefault();
    data.onToggleCollapse?.(id);
  };

  return (
    <div
      className={cn(
        "knowledge-node",
        selected && "is-selected",
        data.state?.includes("due") && "is-due",
        isRoot && "is-root",
        data.rootEmphasis && "is-root-emphasis",
        Boolean(data.focused) && "is-focused",
        Boolean(data.mastered) && "is-mastered",
        tree && "is-tree-card",
        tree && kind === "main" && "is-tree-main",
        tree && kind === "branch-left" && "is-tree-branch-left",
        tree && kind === "branch-right" && "is-tree-branch-right",
        tree && isRoot && "is-tree-root",
        collapsed && hasChildren && "is-collapsed",
      )}
      data-kind={kind}
      data-depth={data.depth ?? 0}
      data-collapsed-count={
        collapsed && hiddenCount > 0 ? `+${hiddenCount}` : undefined
      }
    >
      <Handle
        className="!size-1 !border-0 !bg-transparent opacity-0"
        position={targetPosition}
        type="target"
      />
      {tree && kind === "main" && data.step ? (
        <div className="knowledge-node__step-chip">
          第 {data.step}
          {data.stepTotal && data.stepTotal > 1 ? (
            <span className="knowledge-node__step-total">
              {" "}
              / {data.stepTotal}
            </span>
          ) : null}{" "}
          步
        </div>
      ) : null}
      {!isRoot || !tree ? (
        <div className="knowledge-node__meta">
          <span className="knowledge-node__type">
            {knowledgeNodeTypeLabels[
              data.nodeType ?? (data.root ? "root" : "concept")
            ] ??
              data.nodeType ??
              "概念"}
          </span>
          {tree && (data.depth ?? 0) >= 2 ? (
            <span className="knowledge-node__level">
              第 {data.depth ?? 0} 层
            </span>
          ) : null}
        </div>
      ) : null}
      <strong>{tree && isRoot ? "根" : data.label}</strong>
      {tree && !isRoot && data.description ? (
        <p className="knowledge-node__summary">{data.description}</p>
      ) : null}
      {tree && !isRoot ? (
        <div className="knowledge-node__status-row">
          <span
            className={cn(
              "knowledge-node__status-chip",
              data.state?.includes("due") && "is-due",
              data.mastered && "is-mastered",
            )}
          >
            {data.mastered
              ? "已掌握"
              : knowledgeStateLabel(data.state, "fresh")}
          </span>
          {typeof data.targetWeight === "number" ? (
            <span className="knowledge-node__importance">
              <StarRating
                max={3}
                tone="importance"
                value={importanceStars(data.targetWeight)}
              />
              <RecommendDots weight={data.targetWeight} />
            </span>
          ) : null}
        </div>
      ) : null}
      {typeof data.stars === "number" && (!tree || isRoot) ? (
        <StarRating value={data.stars} />
      ) : null}
      {!isRoot || !tree ? (
        tree ? (
          <div className="knowledge-node__toolbar">
            <NodeExploreChip
              count={Number(data.exploreCount ?? 0)}
              onOpen={() => data.onOpenExplore?.(id)}
            />
            {typeof data.stars === "number" && data.stars > 0 ? (
              <span className="knowledge-node__stars-inline">
                <StarRating value={data.stars} />
              </span>
            ) : null}
          </div>
        ) : (
          <small>
            {data.focused ? "重点关注 · " : ""}
            {knowledgeStateLabel(data.state, "fresh")} ·{" "}
            {knowledgeStateLabel(data.evidence, "unverified")}
          </small>
        )
      ) : (
        <span className="knowledge-node__root-dot" aria-hidden="true" />
      )}
      {tree && hasChildren ? (
        <button
          aria-label={
            collapsed
              ? `展开 ${data.label} 的 ${hiddenCount} 个子节点`
              : `折叠 ${data.label} 的子树`
          }
          className="knowledge-node__collapse"
          onClick={handleCollapse}
          onPointerDown={(event) => event.stopPropagation()}
          title={
            collapsed
              ? `展开隐藏的 ${hiddenCount} 个子节点`
              : "折叠子树，减少画布干扰"
          }
          type="button"
        >
          {collapsed ? <Plus className="size-3" /> : <Minus className="size-3" />}
        </button>
      ) : null}
      <Handle
        className="!size-1 !border-0 !bg-transparent opacity-0"
        position={sourcePosition}
        type="source"
      />
    </div>
  );
}

function KnowledgeTreeEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  style,
  markerEnd,
  data,
  selected,
}: EdgeProps) {
  const spine = Boolean(
    data &&
      typeof data === "object" &&
      (data as Record<string, unknown>).spine,
  );
  const active = Boolean(
    data &&
      typeof data === "object" &&
      (data as Record<string, unknown>).active,
  );
  // TJ-Sylva edgePath: vertical spine = line; horizontal branch = cubic @ 0.46.
  const dx = targetX - sourceX;
  const path =
    Math.abs(dx) < 16
      ? `M ${sourceX} ${sourceY} L ${targetX} ${targetY}`
      : (() => {
          const midX = sourceX + dx * 0.46;
          return `M ${sourceX} ${sourceY} C ${midX} ${sourceY}, ${midX} ${targetY}, ${targetX} ${targetY}`;
        })();
  return (
    <BaseEdge
      id={id}
      markerEnd={markerEnd}
      path={path}
      style={style}
      className={cn(
        "knowledge-tree-edge",
        spine && "is-spine",
        active && "is-active",
        !active && "is-dim",
        selected && "is-selected",
      )}
    />
  );
}

const nodeTypes = { knowledge: KnowledgeNodeView };
const edgeTypes = { knowledgeTree: KnowledgeTreeEdge };
const emptyNodes: KnowledgeNode[] = [];
const emptyEdges: Edge[] = [];

export function KnowledgeGraph({
  nodes = emptyNodes,
  edges = emptyEdges,
  className,
  compact = false,
  grow = false,
  interactive = true,
  layout = "spatial",
  maxDepth,
  rootEmphasis = false,
  minimumZoom = layout === "tree" || compact ? 0.3 : 0.5,
  maximumZoom = 1.8,
  showZoomControls = false,
  onSelect,
  onSelectionChange,
  onStudy,
  studyOnSelect = false,
  selectedId,
  selectedIds,
  multiple = false,
  title = "学习图谱",
  collapsedIds: collapsedIdsProp,
  onCollapsedIdsChange,
}: KnowledgeGraphProps) {
  const [view, setView] = useState<KnowledgeGraphLayout>(layout);
  const [visibleCount, setVisibleCount] = useState(grow ? 1 : nodes.length);
  const [internalSelectedIds, setInternalSelectedIds] = useState<string[]>(
    selectedIds ?? (selectedId ? [selectedId] : []),
  );
  const [flowInstance, setFlowInstance] =
    useState<ReactFlowInstance<KnowledgeNode>>();
  const [zoom, setZoom] = useState(1);
  const [positionOverrides, setPositionOverrides] = useState<
    Record<string, { x: number; y: number }>
  >({});
  const [internalCollapsed, setInternalCollapsed] = useState<Set<string>>(
    () => new Set(collapsedIdsProp ?? []),
  );
  const canvasRef = useRef<HTMLDivElement>(null);
  const lastFocusedId = useRef<string | null>(null);
  const suppressAutoFit = useRef(false);
  const resizeViewport = useRef<{
    width: number;
    height: number;
    zoom: number;
  } | undefined>(undefined);
  const focusId =
    internalSelectedIds[0] ?? selectedId ?? selectedIds?.[0] ?? undefined;

  useEffect(() => {
    setInternalSelectedIds(selectedIds ?? (selectedId ? [selectedId] : []));
  }, [selectedId, selectedIds]);

  useEffect(() => {
    if (collapsedIdsProp) {
      setInternalCollapsed(new Set(collapsedIdsProp));
    }
  }, [collapsedIdsProp]);

  useEffect(() => {
    setView(layout);
    setPositionOverrides({});
  }, [layout]);

  useEffect(() => {
    if (!grow) {
      setVisibleCount(nodes.length);
      return;
    }
    setVisibleCount(1);
    const timer = window.setInterval(
      () =>
        setVisibleCount((count) =>
          count >= nodes.length ? count : count + 1,
        ),
      520,
    );
    return () => window.clearInterval(timer);
  }, [grow, nodes.length]);

  useEffect(() => {
    if (!grow || !flowInstance) return;
    const timer = window.setTimeout(() => {
      void flowInstance.fitView({
        padding: compact ? 0.26 : 0.18,
        duration: 260,
      });
    }, 40);
    return () => window.clearTimeout(timer);
  }, [compact, flowInstance, grow, visibleCount]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !flowInstance || typeof ResizeObserver === "undefined")
      return;

    let frame = 0;
    let timer = 0;
    let previousSize = {
      width: canvas.clientWidth,
      height: canvas.clientHeight,
    };
    resizeViewport.current = {
      ...previousSize,
      zoom: flowInstance.getZoom(),
    };
    const resizeCanvas = (width: number, height: number) => {
      if (suppressAutoFit.current) return;
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        const focusedNode = focusId ? flowInstance.getNode(focusId) : undefined;
        if (focusedNode) {
          const previous = resizeViewport.current;
          const ratio = Math.min(
            previous && previous.width > 0 ? width / previous.width : 1,
            previous && previous.height > 0 ? height / previous.height : 1,
          );
          const targetZoom = clampGraphZoom(
            (previous?.zoom ?? flowInstance.getZoom()) * ratio,
            minimumZoom,
            maximumZoom,
          );
          const nodeWidth =
            focusedNode.measured?.width ?? focusedNode.width ?? 0;
          const nodeHeight =
            focusedNode.measured?.height ?? focusedNode.height ?? 0;
          suppressAutoFit.current = true;
          void flowInstance.setCenter(
            focusedNode.position.x + nodeWidth / 2,
            focusedNode.position.y + nodeHeight / 2,
            { zoom: targetZoom, duration: 220 },
          );
          window.setTimeout(() => {
            suppressAutoFit.current = false;
          }, 250);
          resizeViewport.current = { width, height, zoom: targetZoom };
          return;
        }
        void flowInstance.fitView({
          padding: compact ? 0.16 : view === "tree" ? 0.22 : 0.12,
          duration: 220,
        });
        resizeViewport.current = { width, height, zoom: flowInstance.getZoom() };
      });
    };
    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return;
      const { width, height } = entry.contentRect;
      const resized =
        Math.abs(width - previousSize.width) > 1 ||
        Math.abs(height - previousSize.height) > 1;
      previousSize = { width, height };
      if (!resized) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => resizeCanvas(width, height), 120);
    });
    observer.observe(canvas);
    return () => {
      observer.disconnect();
      window.cancelAnimationFrame(frame);
      window.clearTimeout(timer);
    };
  }, [compact, flowInstance, focusId, maximumZoom, minimumZoom, view]);

  const collapsedIds = internalCollapsed;

  // Depth LOD mirroring TJ-Sylva: low / mid / high.
  const zoomLevel: "low" | "mid" | "high" =
    zoom < 0.4 ? "low" : zoom < 0.8 ? "mid" : "high";

  const structuredTree = useMemo(() => {
    if (view !== "tree") {
      return buildKnowledgeTreeLayout([], []);
    }
    // Build a temporary parent map first so active path can dim inactive edges.
    const skeleton = buildKnowledgeTreeLayout(nodes, edges, {
      collapsedIds,
    });
    const activePath = getTreeActivePath(focusId, skeleton.parentByChild);
    return buildKnowledgeTreeLayout(nodes, edges, {
      collapsedIds,
      activePathIds: activePath,
    });
  }, [collapsedIds, edges, focusId, nodes, view]);

  const freeLayoutPositions = useMemo(() => {
    if (view === "spatial") return buildSpatialLayout(nodes, edges);
    if (view === "flat") return buildFlatLayout(nodes, edges);
    return {} as Record<string, { x: number; y: number }>;
  }, [edges, nodes, view]);

  const toggleCollapse = useCallback(
    (nodeId: string) => {
      setInternalCollapsed((current) => {
        const next = new Set(current);
        if (next.has(nodeId)) next.delete(nodeId);
        else next.add(nodeId);
        onCollapsedIdsChange?.([...next]);
        return next;
      });
      // After collapse/expand, re-center on the toggled card (TJ-Sylva).
      suppressAutoFit.current = true;
      window.setTimeout(() => {
        if (!flowInstance) return;
        const pos = structuredTree.positions[nodeId];
        if (!pos) return;
        // Tree layout positions are node centers, not top-left coordinates.
        void flowInstance.setCenter(pos.x, pos.y, {
          zoom: flowInstance.getZoom(),
          duration: 420,
        });
        window.setTimeout(() => {
          suppressAutoFit.current = false;
        }, 480);
      }, 40);
    },
    [flowInstance, onCollapsedIdsChange, structuredTree],
  );

  const visibleSourceNodes = useMemo(() => {
    const base = nodes.slice(0, visibleCount);
    if (view !== "tree") return base;
    return base.filter((node) => {
      const depth = structuredTree.depths[node.id] ?? 0;
      if (maxDepth !== undefined && depth > maxDepth) return false;
      // Hide descendants of collapsed ancestors.
      let cursor = structuredTree.parentByChild.get(node.id);
      while (cursor) {
        if (collapsedIds.has(cursor)) return false;
        cursor = structuredTree.parentByChild.get(cursor);
      }
      return true;
    });
  }, [
    collapsedIds,
    maxDepth,
    nodes,
    structuredTree.depths,
    structuredTree.parentByChild,
    view,
    visibleCount,
  ]);

  const visibleIds = useMemo(
    () => new Set(visibleSourceNodes.map((node) => node.id)),
    [visibleSourceNodes],
  );

  const layoutItemById = useMemo(() => {
    const map = new Map(
      structuredTree.items.map((item) => [item.id, item] as const),
    );
    return map;
  }, [structuredTree.items]);

  const laidOutNodes = useMemo(
    () =>
      visibleSourceNodes.map((node) => {
        const item = layoutItemById.get(node.id);
        const depth = structuredTree.depths[node.id] ?? item?.depth ?? 0;
        const kind = item?.kind;
        const hasChildren =
          (structuredTree.childrenByParent.get(node.id) ?? []).length > 0;
        const collapsed = collapsedIds.has(node.id);
        const hiddenCount = collapsed
          ? countTreeDescendants(node.id, structuredTree.childrenByParent)
          : 0;
        const treePosition = structuredTree.positions[node.id];
        // React Flow positions nodes by top-left; layout uses card centers.
        const width =
          kind === "root"
            ? TREE_ROOT_SIZE
            : kind === "main"
              ? TREE_MAIN_W
              : TREE_CARD_W;
        const height = kind === "root" ? TREE_ROOT_SIZE : TREE_NODE_H;
        const centered =
          view === "tree" && treePosition
            ? {
                x: treePosition.x - width / 2,
                y: treePosition.y - height / 2,
              }
            : undefined;
        return {
          ...node,
          data: {
            ...node.data,
            depth,
            kind,
            step: item?.step,
            stepTotal: item?.stepTotal,
            initial: grow && visibleCount === 1,
            rootEmphasis: rootEmphasis && Boolean(node.data.root),
            tree: view === "tree",
            collapsed,
            hasChildren,
            hiddenCount,
            onToggleCollapse: toggleCollapse,
          },
          className: "graph-node-enter",
          selected: internalSelectedIds.includes(node.id),
          position:
            positionOverrides[node.id] ??
            centered ??
            freeLayoutPositions[node.id] ??
            node.position,
          // Tree cards stay locked to the algorithm; free layouts stay draggable.
          draggable: interactive && view !== "tree",
          selectable: interactive,
          style:
            view === "tree"
              ? {
                  width,
                  height: kind === "root" ? TREE_ROOT_SIZE : undefined,
                }
              : undefined,
        };
      }),
    [
      collapsedIds,
      freeLayoutPositions,
      grow,
      interactive,
      internalSelectedIds,
      layoutItemById,
      positionOverrides,
      rootEmphasis,
      structuredTree.childrenByParent,
      structuredTree.depths,
      structuredTree.positions,
      toggleCollapse,
      view,
      visibleCount,
      visibleSourceNodes,
    ],
  );

  const [renderedNodes, setRenderedNodes] =
    useState<KnowledgeNode[]>(laidOutNodes);
  useEffect(() => setRenderedNodes(laidOutNodes), [laidOutNodes]);

  const handleNodesChange = useCallback(
    (changes: NodeChange<KnowledgeNode>[]) =>
      setRenderedNodes((current) => applyNodeChanges(changes, current)),
    [],
  );

  const visibleEdges = useMemo(() => {
    if (view !== "tree") {
      return edges.filter(
        (edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target),
      );
    }
    // Prefer layout-derived tree edges (spine + branch) so geometry matches.
    const treeEdges = structuredTree.edges
      .filter(
        (edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target),
      )
      .map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        type: "knowledgeTree" as const,
        data: {
          spine: edge.spine,
          active: edge.active,
          relation: "contains",
        },
        className: cn(
          "knowledge-tree-edge",
          edge.spine && "is-spine",
          edge.active ? "is-active" : "is-dim",
        ),
        animated: false,
      }));
    const representedPairs = new Set(
      treeEdges.map((edge) => `${edge.source}->${edge.target}`),
    );
    const semanticOverlays = edges
      .filter(
        (edge) =>
          visibleIds.has(edge.source) &&
          visibleIds.has(edge.target) &&
          !representedPairs.has(`${edge.source}->${edge.target}`),
      )
      .map((edge) => ({
        ...edge,
        type: "knowledgeTree" as const,
        data: {
          ...edge.data,
          spine: false,
          active: focusId === edge.source || focusId === edge.target,
        },
        className: "knowledge-tree-edge is-semantic-overlay",
        animated: false,
      }));
    return [...treeEdges, ...semanticOverlays];
  }, [edges, focusId, structuredTree.edges, view, visibleIds]);

  // Initial / structure fit — skip while the user is mid-focus or collapsing.
  useEffect(() => {
    if (!flowInstance || suppressAutoFit.current) return;
    const timer = window.setTimeout(() => {
      if (suppressAutoFit.current) return;
      void flowInstance.fitView({
        padding: compact ? 0.22 : view === "tree" ? 0.2 : 0.16,
        duration: 220,
      });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [compact, flowInstance, laidOutNodes.length, maxDepth, view]);

  // Focus the selected learning node at its actual center and 100% scale.
  useEffect(() => {
    if (!flowInstance || view !== "tree" || !focusId) return;
    if (lastFocusedId.current === focusId) return;
    lastFocusedId.current = focusId;
    const pos = structuredTree.positions[focusId];
    if (!pos) return;
    suppressAutoFit.current = true;
    const targetZoom = clampGraphZoom(1, minimumZoom, maximumZoom);
    void flowInstance.setCenter(pos.x, pos.y, {
      zoom: targetZoom,
      duration: 480,
    });
    window.setTimeout(() => {
      suppressAutoFit.current = false;
    }, 520);
  }, [
    focusId,
    flowInstance,
    maximumZoom,
    minimumZoom,
    structuredTree.positions,
    view,
  ]);

  const activateNode = (node: KnowledgeNode) => {
    if (!interactive) return;
    const selectedNode = { ...node.data, id: node.id };
    const nextIds = multiple
      ? internalSelectedIds.includes(node.id)
        ? internalSelectedIds.filter((id) => id !== node.id)
        : [...internalSelectedIds, node.id]
      : [node.id];
    setInternalSelectedIds(nextIds);
    onSelect?.(selectedNode);
    onSelectionChange?.(
      nodes
        .filter((candidate) => nextIds.includes(candidate.id))
        .map((candidate) => ({ ...candidate.data, id: candidate.id })),
    );
    if (studyOnSelect) onStudy?.(selectedNode);
  };

  const handleNodeClick: NodeMouseHandler<KnowledgeNode> = (_, node) =>
    activateNode(node);

  const selectLayout = (nextView: KnowledgeGraphLayout) => {
    setPositionOverrides({});
    setView(nextView);
    lastFocusedId.current = null;
  };

  const zoomBy = (delta: number) => {
    if (!flowInstance) return;
    const next = clampGraphZoom(
      flowInstance.getZoom() + delta,
      minimumZoom,
      maximumZoom,
    );
    void flowInstance.zoomTo(next, { duration: 180 });
    setZoom(next);
  };

  const resetView = () => {
    if (!flowInstance) return;
    lastFocusedId.current = null;
    void flowInstance.fitView({
      padding: compact ? 0.25 : view === "tree" ? 0.2 : 0.18,
      duration: 280,
    });
  };

  const handleCanvasKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (!interactive) return;
    const target = event.target as HTMLElement;
    if (target.closest("button,input,textarea,select")) return;
    const focusedNodeId = target.closest<HTMLElement>(".react-flow__node")
      ?.dataset.id;
    if (focusedNodeId && (event.key === "Enter" || event.key === " ")) {
      const node = renderedNodes.find(
        (candidate) => candidate.id === focusedNodeId,
      );
      if (node) {
        event.preventDefault();
        event.stopPropagation();
        activateNode(node);
      }
      return;
    }
    if (event.key === "+" || event.key === "=") {
      event.preventDefault();
      zoomBy(0.15);
    } else if (event.key === "-") {
      event.preventDefault();
      zoomBy(-0.15);
    } else if (event.key === "0") {
      event.preventDefault();
      resetView();
    }
  };

  return (
    <div
      aria-keyshortcuts="+ - 0"
      aria-label={`${title}交互画布`}
      className={cn(
        "graph-canvas relative",
        compact && "graph-canvas--compact",
        view === "tree" && "graph-canvas--tree",
        className,
      )}
      data-zoom-level={zoomLevel}
      onKeyDown={handleCanvasKeyDown}
      ref={canvasRef}
      role={interactive ? "application" : undefined}
      tabIndex={interactive ? 0 : undefined}
    >
      <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex items-center justify-between p-4">
        <div className="pointer-events-auto">
          <p className="text-sm font-semibold">{title}</p>
          <p className="text-[11px] text-muted-foreground">
            {view === "tree"
              ? `知识树 · 主干 + 左右分支 · 0–${Math.min(maxDepth ?? structuredTree.maxDepth, structuredTree.maxDepth)} 层`
              : view === "spatial"
                ? "空间布局 · 以根为中心的关系辐射图"
                : "平铺布局 · 按层级排列的紧凑网格"}
          </p>
        </div>
        {interactive && (
          <div className="pointer-events-auto flex items-center gap-1 rounded-xl border bg-card/90 p-1">
            {showZoomControls ? (
              <>
                <Button
                  aria-label="缩小图谱"
                  disabled={!flowInstance || zoom <= minimumZoom + 0.01}
                  onClick={() => zoomBy(-0.15)}
                  size="icon-sm"
                  title="缩小（-）"
                  variant="ghost"
                >
                  <ZoomOut />
                </Button>
                <output
                  aria-live="polite"
                  className="min-w-10 text-center text-[10px] tabular-nums text-muted-foreground"
                >
                  {Math.round(zoom * 100)}%
                </output>
                <Button
                  aria-label="放大图谱"
                  disabled={!flowInstance || zoom >= maximumZoom - 0.01}
                  onClick={() => zoomBy(0.15)}
                  size="icon-sm"
                  title="放大（+）"
                  variant="ghost"
                >
                  <ZoomIn />
                </Button>
              </>
            ) : null}
            <Button
              aria-label="树形布局"
              onClick={() => selectLayout("tree")}
              size="icon-sm"
              title="树形布局"
              variant={view === "tree" ? "secondary" : "ghost"}
            >
              <GitFork />
            </Button>
            <Button
              aria-label="空间布局"
              onClick={() => selectLayout("spatial")}
              size="icon-sm"
              title="空间布局"
              variant={view === "spatial" ? "secondary" : "ghost"}
            >
              <Box />
            </Button>
            <Button
              aria-label="平铺布局"
              onClick={() => selectLayout("flat")}
              size="icon-sm"
              title="平铺布局"
              variant={view === "flat" ? "secondary" : "ghost"}
            >
              <LayoutDashboard />
            </Button>
            <Button
              aria-label="重置图谱视图"
              disabled={!flowInstance}
              onClick={resetView}
              size="icon-sm"
              title="重置视图（0）"
              variant="ghost"
            >
              <RotateCcw />
            </Button>
          </div>
        )}
      </div>
      <ReactFlow
        colorMode="light"
        edges={visibleEdges}
        edgeTypes={edgeTypes}
        elementsSelectable={interactive}
        fitView
        fitViewOptions={{
          padding: compact ? 0.16 : view === "tree" ? 0.24 : 0.12,
        }}
        maxZoom={maximumZoom}
        minZoom={minimumZoom}
        nodes={renderedNodes}
        nodesConnectable={false}
        nodesDraggable={interactive && view !== "tree"}
        nodeTypes={nodeTypes}
        onInit={setFlowInstance}
        onMove={(_, viewport) => setZoom(viewport.zoom)}
        onNodeClick={handleNodeClick}
        onNodeDragStop={(_, node) =>
          setPositionOverrides((current) => ({
            ...current,
            [node.id]: node.position,
          }))
        }
        onNodesChange={handleNodesChange}
        panOnDrag={interactive}
        // Wheel pans the canvas (map-like). Hold Ctrl/⌘ to zoom; buttons/± still zoom.
        panOnScroll={false}
        preventScrolling={interactive}
        proOptions={{ hideAttribution: true }}
        zoomOnDoubleClick={interactive}
        zoomOnPinch={interactive}
        zoomOnScroll={interactive}
      >
        <Background
          color={view === "tree" ? "#d5dbd5" : "#dedede"}
          gap={view === "tree" ? 20 : 22}
          size={1}
          variant={BackgroundVariant.Dots}
        />
        {interactive && !showZoomControls && (
          <Controls position="bottom-left" showInteractive={false} />
        )}
        {!compact && interactive && view !== "tree" && (
          <MiniMap
            pannable
            className="!rounded-xl !border !bg-card"
            maskColor="rgba(251,251,250,.72)"
            nodeColor={(node) =>
              internalSelectedIds.includes(node.id) ? "#111111" : "#9a9a9a"
            }
          />
        )}
      </ReactFlow>
    </div>
  );
}
