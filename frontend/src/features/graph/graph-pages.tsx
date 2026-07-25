import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  BadgeCheck,
  BookOpen,
  BookPlus,
  Brain,
  CircleDot,
  Database,
  Download,
  Eye,
  FileText,
  Focus,
  GitCompareArrows,
  LayoutGrid,
  ListTree,
  ListChecks,
  MessageCircle,
  MoreHorizontal,
  MousePointer2,
  Move,
  Network,
  Pencil,
  RotateCcw,
  Route,
  Save,
  Search,
  Sparkles,
  Split,
  Target,
  Trash2,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { toast } from "sonner";

import {
  ApiError,
  createSession,
  deleteGoal,
  getCapabilityReport,
  getGraph,
  getGoalDeleteImpact,
  getMastery,
  getMasteryAlignment,
  listGoals,
  listGraphs,
  studyMultipleNodes,
  updateGraphNode,
} from "@/api";
import { DeleteImpactDialog } from "@/components/shared/delete-impact-dialog";
import { GraphReviewDialog } from "@/components/graph/graph-review-dialog";
import {
  getKnowledgeGraphTreeDepth,
  getKnowledgeGraphTreeDepths,
} from "@/components/graph/knowledge-graph-layout";
import {
  KnowledgeGraph,
  type KnowledgeNode,
} from "@/components/graph/knowledge-graph";
import {
  NodeExploreChain,
  NodeExploreEmpty,
  RecommendDots,
} from "@/components/graph/node-explore";
import { useNodeExploreRounds } from "@/components/graph/node-explore-data";
import {
  importanceToWeight,
  metricLabel,
  weightToImportance,
  type MetricLevel,
} from "@/components/graph/node-metrics";
import {
  ErrorState,
  GrowthStars,
  LoadingState,
  PageFrame,
  PageIntro,
  SectionHeading,
  StatePill,
  Surface,
} from "@/components/shared/page-elements";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Slider } from "@/components/ui/slider";
import {
  Sheet,
  SheetContent,
  SheetTitle,
} from "@/components/ui/sheet";
import type {
  Graph,
  GraphNode,
  GraphSummary,
  MultiNodeStudyResponse,
} from "@/types/graphs";
import type { DeleteImpact } from "@/types/workflow";

function downloadJsonFile(name: string, value: unknown) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], {
      type: "application/json;charset=utf-8",
    }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(url);
}

type GoalShelfEntry = {
  id: string;
  title: string;
  summary: string;
  goalId: string;
  status: "待确认" | "正在学习";
  progress: string;
};

type ShelfBook = {
  id: string;
  goalId: string;
  graphId?: string;
  title: string;
  summary: string;
  status: string;
  progress: string;
  icon: typeof Database;
  isGoalBook: boolean;
};

const graphStateLabels: Record<string, string> = {
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
  weak: "证据不足",
  supported: "已有证据",
  strong: "证据充分",
  mastered: "已掌握",
};

const graphRelationLabels: Record<string, string> = {
  contains: "包含",
  prerequisite: "前置",
  related: "关联",
  contrast: "对比",
  application: "应用",
};

function graphStateLabel(value: string) {
  return graphStateLabels[value] ?? value;
}

function graphRelationLabel(value: string) {
  return graphRelationLabels[value] ?? value;
}

export type OpenLearningProjectDetail = {
  graphId: string;
  title: string;
  nodeId?: string;
  nodeLabel?: string;
  prompt?: string;
  graphAction?: "none" | "propose_create" | "propose_update";
};

function openLearningProject(detail: OpenLearningProjectDetail) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<OpenLearningProjectDetail>(
      "learngraph:open-learning-project",
      { detail },
    ),
  );
}

function graphStatus(status: string) {
  if (status === "published") return "正在学习";
  if (status === "candidate") return "待审核";
  return status || "草稿";
}

function shelfBooks(
  graphs: GraphSummary[],
  goalBooks: GoalShelfEntry[],
  currentGraph?: Graph,
): ShelfBook[] {
  const graphSummaries =
    currentGraph && !graphs.some((graph) => graph.id === currentGraph.id)
      ? [currentGraph, ...graphs]
      : graphs;
  const graphByGoalId = new Map(
    graphSummaries.map((graph) => [graph.goal_id, graph]),
  );
  const representedGoalIds = new Set(goalBooks.map((book) => book.goalId));
  const goalEntries = goalBooks.map((book) => {
    const graph = graphByGoalId.get(book.goalId);
    return {
      id: book.id,
      goalId: book.goalId,
      graphId: graph?.id,
      title: book.title,
      summary: book.summary,
      status: graph ? graphStatus(graph.status) : book.status,
      progress:
        graph?.id === currentGraph?.id && currentGraph
          ? `${currentGraph.nodes.length} 个节点`
          : graph
            ? `修订 ${graph.revision}`
            : book.progress,
      icon: graph ? Database : CircleDot,
      isGoalBook: !graph,
    };
  });
  const graphEntries = graphSummaries
    .filter((graph) => !representedGoalIds.has(graph.goal_id))
    .map((graph) => ({
      id: `graph-${graph.id}`,
      goalId: graph.goal_id,
      graphId: graph.id,
      title: graph.title,
      summary: "学习图谱已从目标意向生成",
      status: graphStatus(graph.status),
      progress:
        graph.id === currentGraph?.id
          ? `${currentGraph.nodes.length} 个节点`
          : `修订 ${graph.revision}`,
      icon: Database,
      isGoalBook: false,
    }));
  return [...goalEntries, ...graphEntries];
}

function toWorkbenchKnowledgeGraph(
  graph: Graph,
  exploreCounts: Record<string, number> = {},
) {
  // The containment relation is the reviewable teaching hierarchy. Other
  // relations (especially prerequisite) remain useful visual overlays, but
  // must not turn a dependency into a false parent/child relationship.
  const containedNodeIds = new Set(
    graph.edges
      .filter((edge) => edge.relation === "contains")
      .map((edge) => edge.target_node_id),
  );
  const hasDeclaredRoot = graph.nodes.some(
    (node) => node.node_type === "root",
  );
  const nodes: KnowledgeNode[] = graph.nodes.map((node, index) => ({
    id: node.id,
    type: "knowledge",
    position: {
      x: 160 + (index % 3) * 190,
      y: 90 + Math.floor(index / 3) * 130,
    },
    data: {
      label: node.label,
      description: node.description,
      stars: node.mastery_stars,
      state: node.retrieval_state,
      evidence: node.evidence_state,
      focused: node.attention_state === "focused",
      nodeType: node.node_type,
      targetWeight: node.target_weight,
      exploreCount: exploreCounts[node.id] ?? 0,
      mastered: node.attention_state === "mastered",
      root:
        node.node_type === "root" ||
        (!hasDeclaredRoot && !containedNodeIds.has(node.id)),
    },
  }));
  const edges = graph.edges.map((edge) => ({
    id: edge.id,
    source: edge.source_node_id,
    target: edge.target_node_id,
    label: graphRelationLabel(edge.relation),
    data: { relation: edge.relation },
    type: "smoothstep" as const,
  }));
  return { nodes, edges };
}

function GraphBookshelf({
  books,
  selectedId,
  onDelete,
  onOpen,
  onStartGoal,
  onStartLearning,
}: {
  books: ShelfBook[];
  selectedId?: string;
  onDelete: (book: ShelfBook) => void;
  onOpen: (book: ShelfBook) => void;
  onStartGoal: () => void;
  onStartLearning: (book: ShelfBook) => void;
}) {
  const [view, setView] = useState<"shelf" | "constellation">("shelf");
  const [constellation, setConstellation] = useState({ scale: 1, x: 0, y: 0 });
  const selectedBook = books.find((book) => book.id === selectedId);
  const drag = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    x: number;
    y: number;
  } | null>(null);

  function adjustScale(delta: number) {
    setConstellation((current) => ({
      ...current,
      scale: Math.max(
        0.55,
        Math.min(2.2, Number((current.scale + delta).toFixed(2))),
      ),
    }));
  }

  function startDrag(event: PointerEvent<HTMLDivElement>) {
    if ((event.target as HTMLElement).closest("button")) return;
    drag.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      x: constellation.x,
      y: constellation.y,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function moveDrag(event: PointerEvent<HTMLDivElement>) {
    const activeDrag = drag.current;
    if (!activeDrag || activeDrag.pointerId !== event.pointerId) return;
    setConstellation((current) => ({
      ...current,
      x: activeDrag.x + event.clientX - activeDrag.startX,
      y: activeDrag.y + event.clientY - activeDrag.startY,
    }));
  }

  function endDrag(event: PointerEvent<HTMLDivElement>) {
    if (drag.current?.pointerId !== event.pointerId) return;
    drag.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
  }

  return (
    <section className="graph-library" aria-label="图谱书架">
      <header className="graph-library__header">
        <div>
          <h1>图谱书架</h1>
          <p>选择图谱，查看与学习。</p>
        </div>
        <div className="flex items-center gap-2">
          <Button onClick={onStartGoal} size="sm">
            <BookPlus className="size-4" />
            新建学习意向
          </Button>
          <div
            className="graph-library__view-switch"
            role="group"
            aria-label="图谱书架视图"
          >
            <Button
              aria-label="书架视图"
              onClick={() => setView("shelf")}
              size="icon-sm"
              title="书架视图"
              variant={view === "shelf" ? "secondary" : "ghost"}
            >
              <LayoutGrid />
            </Button>
            <Button
              aria-label="核心分布视图"
              onClick={() => setView("constellation")}
              size="icon-sm"
              title="核心分布视图"
              variant={view === "constellation" ? "secondary" : "ghost"}
            >
              <Network />
            </Button>
          </div>
        </div>
      </header>
      {view === "shelf" ? (
        <div className="graph-library__shelves">
          {books.map((entry) => {
            const Icon = entry.icon;
            const selected = selectedId === entry.id;
            return (
              <div
                className={
                  selected
                    ? "graph-library__book is-active"
                    : "graph-library__book"
                }
                key={entry.id}
              >
                <button
                  aria-pressed={selected}
                  className="graph-library__book-open"
                  onClick={() => onOpen(entry)}
                  type="button"
                >
                  <span className="graph-library__spine">
                    <Icon />
                    <span>{entry.title.slice(0, 1)}</span>
                  </span>
                  <span className="graph-library__book-copy">
                    <strong>{entry.title}</strong>
                    <small>{entry.summary}</small>
                  </span>
                  <span className="graph-library__book-meta">
                    <b>{entry.status}</b>
                    <small>{entry.progress}</small>
                  </span>
                </button>
                <div className="graph-library__book-actions">
                  <Button
                    onClick={() =>
                      entry.graphId ? onStartLearning(entry) : onStartGoal()
                    }
                    size="xs"
                    variant={entry.graphId ? "default" : "outline"}
                  >
                    {entry.graphId ? (
                      <>
                        <BookOpen className="size-3.5" />
                        立即学习
                      </>
                    ) : (
                      "继续澄清"
                    )}
                  </Button>
                  <Button
                    aria-label={`删除图谱书架条目 ${entry.title}`}
                    className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                    onClick={() => onDelete(entry)}
                    size="icon-xs"
                    title={`删除「${entry.title}」`}
                    variant="ghost"
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              </div>
            );
          })}
          {!books.length ? (
            <button
              className="graph-library__book is-locked"
              onClick={onStartGoal}
              type="button"
            >
              <span className="graph-library__spine">
                <BookOpen />
                <span>+</span>
              </span>
              <span className="graph-library__book-copy">
                <strong>创建第一本图谱</strong>
                <small>先告诉 AI 你要学什么</small>
              </span>
              <span className="graph-library__book-meta">
                <small>待创建</small>
              </span>
            </button>
          ) : null}
        </div>
      ) : (
        <div className="graph-library__constellation-wrap">
          <div
            className="graph-library__constellation-tools"
            aria-label="核心分布视图操作"
          >
            <span>
              <Move className="size-3.5" />
              拖动画布
            </span>
            <Button
              aria-label="缩小核心分布图"
              onClick={() => adjustScale(-0.15)}
              size="icon-sm"
              title="缩小"
            >
              <ZoomOut />
            </Button>
            <Button
              aria-label="放大核心分布图"
              onClick={() => adjustScale(0.15)}
              size="icon-sm"
              title="放大"
            >
              <ZoomIn />
            </Button>
            <Button
              aria-label="重置核心分布图"
              onClick={() => setConstellation({ scale: 1, x: 0, y: 0 })}
              size="icon-sm"
              title="重置"
            >
              <RotateCcw />
            </Button>
            {selectedBook ? (
              <Button
                aria-label={`删除图谱书架条目 ${selectedBook.title}`}
                className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                onClick={() => onDelete(selectedBook)}
                size="icon-sm"
                title={`删除「${selectedBook.title}」`}
                variant="ghost"
              >
                <Trash2 />
              </Button>
            ) : null}
          </div>
          <div
            className="graph-library__constellation"
            aria-label="以正在学习图谱为核心的分布视图"
            onPointerCancel={endDrag}
            onPointerDown={startDrag}
            onPointerMove={moveDrag}
            onPointerUp={endDrag}
            onWheel={(event) => {
              event.preventDefault();
              adjustScale(event.deltaY > 0 ? -0.08 : 0.08);
            }}
          >
            <div
              className="graph-library__constellation-stage"
              style={{
                transform: `translate(${constellation.x}px, ${constellation.y}px) scale(${constellation.scale})`,
              }}
            >
              <span className="graph-library__orbit graph-library__orbit--one" />
              <span className="graph-library__orbit graph-library__orbit--two" />
              {books.map((entry, index) => {
                const Icon = entry.icon;
                const selected = selectedId === entry.id;
                const angle = books.length
                  ? (Math.PI * 2 * index) / books.length - Math.PI / 2
                  : 0;
                const style = selected
                  ? { left: "50%", top: "50%" }
                  : {
                      left: `calc(50% + ${Math.round(Math.cos(angle) * 190)}px)`,
                      top: `calc(50% + ${Math.round(Math.sin(angle) * 145)}px)`,
                    };
                return (
                  <button
                    aria-pressed={selected}
                    className={`graph-library__planet${selected ? " is-active" : ""}`}
                    key={entry.id}
                    onClick={() => onOpen(entry)}
                    style={style}
                    type="button"
                  >
                    <Icon />
                    <strong>{entry.title}</strong>
                    <small>{entry.status}</small>
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

export function GraphWorkspacePage() {
  const { graphId = "", workspaceId = "" } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  // graphId 为空时只展示书架，不自动打开任何图谱画布。
  const hasOpenedGraph = Boolean(graphId);
  const graph = useQuery({
    queryKey: ["graph", graphId],
    queryFn: () => getGraph(graphId),
    enabled: hasOpenedGraph,
  });
  const graphs = useQuery({ queryKey: ["graphs"], queryFn: listGraphs });
  const goals = useQuery({ queryKey: ["goals"], queryFn: listGoals });
  const goalBooks: GoalShelfEntry[] = (goals.data ?? []).map((goal) => ({
    id: `goal-${goal.id}`,
    title: goal.title,
    summary: goal.intent || goal.raw_prompt,
    goalId: goal.id,
    status: goal.status === "approved" ? "正在学习" : "待确认",
    progress: "学习意向",
  }));
  const [selectedNodeId, setSelectedNodeId] = useState<string>();
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
  const [multiSelect, setMultiSelect] = useState(false);
  const [nodeSearch, setNodeSearch] = useState("");
  const [nodeStateFilter, setNodeStateFilter] = useState(() => {
    const saved = window.localStorage.getItem("learngraph:graph-node-filter");
    return ["all", "due", "relearning", "fresh", "focused"].includes(saved ?? "")
      ? saved!
      : "all";
  });
  const [depthLimit, setDepthLimit] = useState(2);
  const [editingNode, setEditingNode] = useState(false);
  /** Workbench "edit mode": unlocks manual node edits across the inspector. */
  const [editMode, setEditMode] = useState(false);
  const [explorePanelOpen, setExplorePanelOpen] = useState(false);
  const [nodeDraft, setNodeDraft] = useState({
    label: "",
    description: "",
    targetWeight: 50,
  });
  const [graphReviewOpen, setGraphReviewOpen] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const depthStateRef = useRef<{ graphId?: string; maximumDepth: number }>({
    maximumDepth: 0,
  });
  const goalDeleteRequestId = useRef(0);
  const [goalPendingDeletion, setGoalPendingDeletion] = useState<ShelfBook>();
  const [goalDeleteImpact, setGoalDeleteImpact] = useState<DeleteImpact>();
  const [goalDeleteError, setGoalDeleteError] = useState<string>();
  const [goalDeleteLoading, setGoalDeleteLoading] = useState(false);
  const [goalDeleteConfirming, setGoalDeleteConfirming] = useState(false);
  const selectedShelfId = searchParams.get("shelf") ?? undefined;
  const requestedNodeId = searchParams.get("node") ?? undefined;
  const base = `/w/${workspaceId}`;

  const seedBooks = useMemo(
    () => shelfBooks(graphs.data ?? [], goalBooks, graph.data),
    [goalBooks, graph.data, graphs.data],
  );
  const requestedBook = seedBooks.find((book) => book.id === selectedShelfId);
  // 仅在已打开某本图谱时解析 activeGraphId；书架页不自动选中。
  const activeGraphId = hasOpenedGraph
    ? (requestedBook?.graphId ??
      seedBooks.find((book) => book.graphId === graphId)?.graphId ??
      graphId)
    : "";
  useEffect(() => {
    if (activeGraphId)
      window.localStorage.setItem("learngraph:last-graph-id", activeGraphId);
  }, [activeGraphId]);
  useEffect(() => {
    window.localStorage.setItem("learngraph:graph-node-filter", nodeStateFilter);
  }, [nodeStateFilter]);
  const openedGraph = useQuery({
    queryKey: ["graph", activeGraphId],
    queryFn: () => getGraph(activeGraphId),
    enabled: Boolean(activeGraphId) && activeGraphId !== graphId,
  });
  const sourceGraph = activeGraphId === graphId ? graph.data : openedGraph.data;

  const effectiveGraph = sourceGraph;
  const activeGraphRevision = effectiveGraph?.revision ?? 0;
  const books = useMemo(
    () =>
      shelfBooks(graphs.data ?? [], goalBooks, effectiveGraph ?? graph.data),
    [effectiveGraph, goalBooks, graph.data, graphs.data],
  );
  const selectedBook =
    books.find((book) => book.id === selectedShelfId) ??
    books.find((book) => book.graphId === activeGraphId) ??
    books.find((book) => Boolean(book.graphId));
  const graphRootId =
    effectiveGraph?.nodes.find((node) => node.node_type === "root")?.id ??
    effectiveGraph?.nodes[0]?.id;
  const selectedNode =
    effectiveGraph?.nodes.find((node) => node.id === selectedNodeId) ??
    effectiveGraph?.nodes.find((node) => node.id === requestedNodeId) ??
    effectiveGraph?.nodes.find((node) => node.id === graphRootId);

  // Preload explore counts for selected node; canvas shows 0 until inspector
  // loads (avoids N parallel questions calls for every card on large graphs).
  // Cache counts across selection so interactive learning updates stick.
  const selectedExplore = useNodeExploreRounds(
    activeGraphId || undefined,
    selectedNode?.id,
  );
  const [exploreCounts, setExploreCounts] = useState<Record<string, number>>(
    {},
  );
  useEffect(() => {
    if (!selectedNode?.id || selectedExplore.data === undefined) return;
    const next = selectedExplore.data.length;
    setExploreCounts((current) =>
      current[selectedNode.id] === next
        ? current
        : { ...current, [selectedNode.id]: next },
    );
  }, [selectedExplore.data, selectedNode?.id]);

  const workbenchGraph = useMemo(
    () =>
      effectiveGraph
        ? toWorkbenchKnowledgeGraph(effectiveGraph, exploreCounts)
        : null,
    [effectiveGraph, exploreCounts],
  );
  const filteredWorkbenchGraph = useMemo(() => {
    if (!workbenchGraph || !effectiveGraph) return workbenchGraph;
    const normalizedSearch = nodeSearch.trim().toLocaleLowerCase();
    if (!normalizedSearch && nodeStateFilter === "all") return workbenchGraph;
    const matchedIds = new Set(
      effectiveGraph.nodes
        .filter((node) => {
          const matchesSearch =
            !normalizedSearch ||
            node.label.toLocaleLowerCase().includes(normalizedSearch) ||
            node.description.toLocaleLowerCase().includes(normalizedSearch);
          const matchesState =
            nodeStateFilter === "all" ||
            node.retrieval_state === nodeStateFilter ||
            (nodeStateFilter === "focused" &&
              node.attention_state === "focused");
          return matchesSearch && matchesState;
        })
        .map((node) => node.id),
    );
    const visibleIds = new Set(matchedIds);
    workbenchGraph.edges.forEach((edge) => {
      if (matchedIds.has(edge.source) || matchedIds.has(edge.target)) {
        visibleIds.add(edge.source);
        visibleIds.add(edge.target);
      }
    });
    const root = workbenchGraph.nodes.find((node) => node.data.root);
    if (root) visibleIds.add(root.id);
    return {
      nodes: workbenchGraph.nodes.filter((node) => visibleIds.has(node.id)),
      edges: workbenchGraph.edges.filter(
        (edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target),
      ),
    };
  }, [effectiveGraph, nodeSearch, nodeStateFilter, workbenchGraph]);
  const maximumDepth = workbenchGraph
    ? getKnowledgeGraphTreeDepth(workbenchGraph.nodes, workbenchGraph.edges)
    : 0;
  const requestedNodeDepth =
    workbenchGraph && requestedNodeId
      ? (getKnowledgeGraphTreeDepths(
          workbenchGraph.nodes,
          workbenchGraph.edges,
        ).get(requestedNodeId) ?? 0)
      : 0;

  useEffect(() => {
    setSelectedNodeId((current) =>
      requestedNodeId &&
      effectiveGraph?.nodes.some((node) => node.id === requestedNodeId)
        ? requestedNodeId
        : effectiveGraph?.nodes.some((node) => node.id === current)
          ? current
          : graphRootId,
    );
  }, [effectiveGraph?.nodes, graphRootId, requestedNodeId]);

  useEffect(() => {
    if (requestedNodeId) setInspectorOpen(true);
  }, [requestedNodeId]);

  useEffect(() => {
    const validIds = new Set(effectiveGraph?.nodes.map((node) => node.id) ?? []);
    setSelectedNodeIds((current) => {
      const retained = current.filter((id) => validIds.has(id));
      if (retained.length) return retained;
      return graphRootId ? [graphRootId] : [];
    });
  }, [activeGraphId, effectiveGraph?.nodes, graphRootId]);

  useEffect(() => {
    if (!activeGraphId || !workbenchGraph) return;
    const previous = depthStateRef.current;
    const graphChanged = previous.graphId !== activeGraphId;
    const graphBecameAvailable =
      previous.graphId === activeGraphId &&
      previous.maximumDepth === 0 &&
      maximumDepth > 0;
    depthStateRef.current = { graphId: activeGraphId, maximumDepth };
    setDepthLimit((current) => {
      if (graphChanged || graphBecameAvailable) {
        return Math.min(2, maximumDepth);
      }
      return Math.max(0, Math.min(current, maximumDepth));
    });
  }, [activeGraphId, maximumDepth, workbenchGraph]);

  useEffect(() => {
    setDepthLimit((current) =>
      Math.min(maximumDepth, Math.max(current, requestedNodeDepth)),
    );
  }, [activeGraphId, maximumDepth, requestedNodeDepth, requestedNodeId]);

  useEffect(() => {
    const handleRailSelection = (event: Event) => {
      const detail = (
        event as CustomEvent<{ graphId?: string; nodeId?: string }>
      ).detail;
      if (
        detail?.graphId !== activeGraphId ||
        !detail.nodeId ||
        !effectiveGraph?.nodes.some((node) => node.id === detail.nodeId)
      )
        return;
      setSelectedNodeId(detail.nodeId);
    };
    window.addEventListener("learngraph:learning-node-selected", handleRailSelection);
    return () =>
      window.removeEventListener(
        "learngraph:learning-node-selected",
        handleRailSelection,
      );
  }, [activeGraphId, effectiveGraph?.nodes]);

  useEffect(() => {
    if (!selectedNode) return;
    setNodeDraft({
      label: selectedNode.label,
      description: selectedNode.description,
      targetWeight: selectedNode.target_weight,
    });
    setEditingNode(false);
  }, [selectedNode]);

  useEffect(() => {
    if (!multiSelect && selectedNode) setSelectedNodeIds([selectedNode.id]);
  }, [multiSelect, selectedNode]);

  const focus = useMutation({
    mutationFn: ({ nodeId, state }: { nodeId: string; state: string }) =>
      updateGraphNode(activeGraphId, nodeId, { attention_state: state }),
    onSuccess: (updated) => {
      toast.success(
        updated.attention_state === "mastered"
          ? "已标记为已掌握，将出现在能力成长图谱"
          : updated.attention_state === "focused"
            ? "已设为重点节点"
            : "已取消节点关注状态",
      );
      void queryClient.invalidateQueries({
        queryKey: ["graph", activeGraphId],
      });
      void queryClient.invalidateQueries({ queryKey: ["mastery"] });
    },
    onError: async (error) => {
      if (
        error instanceof ApiError &&
        error.code === "graph_revision_conflict"
      ) {
        setEditingNode(false);
        await queryClient.invalidateQueries({
          queryKey: ["graph", activeGraphId],
        });
        toast.warning(
          "图谱已由其他编辑更新，已刷新到最新修订，请重新确认。",
        );
        return;
      }
      toast.error(error.message);
      await queryClient.invalidateQueries({
        queryKey: ["graph", activeGraphId],
      });
    },
  });
  const updateNode = useMutation({
    mutationFn: ({
      nodeId,
      label,
      description,
      targetWeight,
    }: {
      nodeId: string;
      label: string;
      description: string;
      targetWeight: number;
    }) =>
      updateGraphNode(activeGraphId, nodeId, {
        expected_revision: activeGraphRevision,
        label,
        description,
        target_weight: targetWeight,
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData<Graph>(["graph", activeGraphId], (current) =>
        current
          ? {
              ...current,
              nodes: current.nodes.map((node) =>
                node.id === updated.id ? { ...node, ...updated } : node,
              ),
            }
          : current,
      );
      setEditingNode(false);
      toast.success("节点内容已保存");
      void queryClient.invalidateQueries({
        queryKey: ["graph", activeGraphId],
      });
    },
    onError: (error) => {
      toast.error(error.message);
      void queryClient.invalidateQueries({ queryKey: ["graph", activeGraphId] });
    },
  });

  function openBook(book: ShelfBook) {
    if (book.graphId)
      window.localStorage.setItem("learngraph:last-graph-id", book.graphId);
    // 单开对应图谱：从书架进入画布，或在画布内切换到另一本。
    if (book.graphId && book.graphId !== graphId) {
      navigate(
        `${base}/graphs/${encodeURIComponent(book.graphId)}?shelf=${encodeURIComponent(book.id)}`,
      );
      return;
    }
    if (!book.graphId) {
      const returnTo = `${base}/graphs`;
      navigate(
        `${base}/goals/new/clarify?returnTo=${encodeURIComponent(returnTo)}&pendingGoal=${encodeURIComponent(book.goalId)}`,
      );
      return;
    }
    const next = new URLSearchParams(searchParams);
    next.set("shelf", book.id);
    next.delete("node");
    next.delete("pendingGoal");
    setSearchParams(next);
  }

  function returnToBookshelf() {
    navigate(`${base}/graphs`);
  }

  function closeGoalDeleteDialog() {
    goalDeleteRequestId.current += 1;
    setGoalPendingDeletion(undefined);
    setGoalDeleteImpact(undefined);
    setGoalDeleteError(undefined);
    setGoalDeleteLoading(false);
  }

  async function requestGoalDeletion(book: ShelfBook) {
    const requestId = goalDeleteRequestId.current + 1;
    goalDeleteRequestId.current = requestId;
    setGoalPendingDeletion(book);
    setGoalDeleteImpact(undefined);
    setGoalDeleteError(undefined);
    setGoalDeleteLoading(true);
    try {
      const impact = await getGoalDeleteImpact(book.goalId);
      if (goalDeleteRequestId.current === requestId)
        setGoalDeleteImpact(impact);
    } catch (error) {
      if (goalDeleteRequestId.current === requestId)
        setGoalDeleteError(
          error instanceof Error ? error.message : "无法检查图谱删除影响",
        );
    } finally {
      if (goalDeleteRequestId.current === requestId)
        setGoalDeleteLoading(false);
    }
  }

  async function confirmGoalDeletion() {
    if (!goalPendingDeletion || !goalDeleteImpact || goalDeleteConfirming)
      return;
    setGoalDeleteConfirming(true);
    setGoalDeleteError(undefined);
    try {
      await deleteGoal(
        goalPendingDeletion.goalId,
        goalDeleteImpact.confirmation_text,
      );

      const routeBook = books.find((book) => book.graphId === graphId);
      const remainingBooks = books.filter(
        (book) => book.goalId !== goalPendingDeletion.goalId,
      );
      const fallbackBook =
        remainingBooks.find((book) => Boolean(book.graphId)) ??
        remainingBooks[0];
      const deletingRouteGraph =
        routeBook?.goalId === goalPendingDeletion.goalId ||
        goalPendingDeletion.graphId === graphId;

      toast.success(`已删除「${goalPendingDeletion.title}」及其关联图谱数据`);
      closeGoalDeleteDialog();
      if (deletingRouteGraph) {
        navigate(
          fallbackBook?.graphId
            ? `${base}/graphs/${fallbackBook.graphId}?shelf=${encodeURIComponent(fallbackBook.id)}`
            : `${base}/graphs`,
          { replace: true },
        );
      } else if (selectedBook?.goalId === goalPendingDeletion.goalId) {
        const next = new URLSearchParams(searchParams);
        if (routeBook) next.set("shelf", routeBook.id);
        else if (fallbackBook) next.set("shelf", fallbackBook.id);
        else next.delete("shelf");
        setSearchParams(next, { replace: true });
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["goals"] }),
        queryClient.invalidateQueries({ queryKey: ["graphs"] }),
        queryClient.invalidateQueries({ queryKey: ["projects"] }),
        queryClient.invalidateQueries({ queryKey: ["sessions"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ]);
    } catch (error) {
      setGoalDeleteError(
        error instanceof Error ? error.message : "删除失败，请稍后重试",
      );
    } finally {
      setGoalDeleteConfirming(false);
    }
  }

  function startBookLearning(book: ShelfBook) {
    if (!book.graphId) {
      startGoalClarification();
      return;
    }
    openLearningProject({ graphId: book.graphId, title: book.title });
    toast.message(`正在为「${book.title}」创建学习项目…`);
  }

  function startGoalClarification() {
    const returnTo = hasOpenedGraph
      ? `${base}/graphs/${graphId}${selectedShelfId ? `?shelf=${encodeURIComponent(selectedShelfId)}` : ""}`
      : `${base}/graphs`;
    navigate(
      `${base}/goals/new/clarify?returnTo=${encodeURIComponent(returnTo)}`,
    );
  }

  if (
    (hasOpenedGraph && graph.isPending) ||
    graphs.isPending ||
    goals.isPending
  )
    return (
      <PageFrame>
        <LoadingState
          label={
            hasOpenedGraph ? "正在读取学习图谱…" : "正在读取图谱书架…"
          }
        />
      </PageFrame>
    );
  if (hasOpenedGraph && graph.isError)
    return (
      <PageFrame>
        <ErrorState
          message={graph.error.message}
          onRetry={() => void graph.refetch()}
        />
      </PageFrame>
    );
  if (graphs.isError || goals.isError)
    return (
      <PageFrame>
        <ErrorState
          message={
            graphs.error?.message ?? goals.error?.message ?? "无法读取图谱书架"
          }
          onRetry={() => {
            void graphs.refetch();
            void goals.refetch();
          }}
        />
      </PageFrame>
    );

  // 默认入口：图谱书架。只有 URL 指定 graphId 时才进入单图谱工作台。
  if (!hasOpenedGraph) {
    const libraryBooks = shelfBooks(graphs.data ?? [], goalBooks);
    return (
      <PageFrame className="graph-library-page">
        <DeleteImpactDialog
          confirmLabel={
            goalPendingDeletion?.graphId ? "删除目标与图谱" : "删除学习目标"
          }
          error={goalDeleteError}
          impact={goalDeleteImpact}
          isConfirming={goalDeleteConfirming}
          isLoading={goalDeleteLoading}
          objectLabel={goalPendingDeletion?.title ?? "学习目标"}
          onConfirm={confirmGoalDeletion}
          onOpenChange={(open) => {
            if (!open && !goalDeleteConfirming) closeGoalDeleteDialog();
          }}
          open={Boolean(goalPendingDeletion)}
          title={
            goalPendingDeletion
              ? goalPendingDeletion.graphId
                ? `永久删除「${goalPendingDeletion.title}」及其图谱？`
                : `永久删除学习目标「${goalPendingDeletion.title}」？`
              : undefined
          }
        />
        <GraphBookshelf
          books={libraryBooks}
          onDelete={(book) => void requestGoalDeletion(book)}
          onOpen={openBook}
          onStartGoal={startGoalClarification}
          onStartLearning={startBookLearning}
          selectedId={selectedShelfId}
        />
      </PageFrame>
    );
  }

  const activeGraph = effectiveGraph;
  const focusEditable = Boolean(activeGraph);

  function saveSelectedNode() {
    if (!selectedNode || !activeGraph) return;
    const nextDraft = {
      label: nodeDraft.label.trim(),
      description: nodeDraft.description.trim(),
      targetWeight: nodeDraft.targetWeight,
    };
    updateNode.mutate({ nodeId: selectedNode.id, ...nextDraft });
  }

  function focusSelectedNode() {
    if (!selectedNode || !activeGraph) return;
    focus.mutate({
      nodeId: selectedNode.id,
      state: selectedNode.attention_state === "focused" ? "normal" : "focused",
    });
  }

  function masterySelectedNode() {
    if (!selectedNode || !activeGraph) return;
    focus.mutate({
      nodeId: selectedNode.id,
      state:
        selectedNode.attention_state === "mastered" ? "normal" : "mastered",
    });
  }

  function splitSelectedNode() {
    if (!selectedNode || !activeGraph) return;
    const siblings = activeGraph.edges
      .filter((edge) => edge.source_node_id === selectedNode.id)
      .map((edge) =>
        activeGraph.nodes.find((node) => node.id === edge.target_node_id),
      )
      .filter((node): node is NonNullable<typeof node> => Boolean(node));
    const childHint = siblings.length
      ? `已有下级：${siblings
          .slice(0, 8)
          .map((child) => child.label)
          .join("、")}。不要重复创建近义子节点。`
      : "该节点目前还没有下级。";
    const prompt =
      `请对当前学习节点「${selectedNode.label}」做图谱拆分细化：` +
      `在保持教学树 contains 结构的前提下，增加 2～5 个更具体的子概念/练习节点，` +
      `或修正本节点定义；与现有概念去重。${childHint}` +
      `输出需进入图谱变更审核，不要声称已写入正式图谱。`;
    openLearningProject({
      graphId: activeGraph.id,
      title: activeGraph.title,
      nodeId: selectedNode.id,
      nodeLabel: selectedNode.label,
      prompt,
      graphAction: "propose_update",
    });
    toast.message(`正在对「${selectedNode.label}」发起拆分变更…`);
  }

  function studyFromNode(node: KnowledgeNode["data"] & { id: string }) {
    if (!activeGraph) return;
    openLearningProject({
      graphId: activeGraph.id,
      title: activeGraph.title,
      nodeId: node.id,
      nodeLabel: node.label,
      prompt: `请从「${node.label}」开始讲解：它是什么、在「${activeGraph.title}」中承担什么角色，以及我接下来应如何练习？`,
    });
    toast.message(`正在从「${node.label}」开始学习…`);
  }

  function selectWorkbenchNode(node: KnowledgeNode["data"] & { id: string }) {
    setSelectedNodeId(node.id);
    setInspectorOpen(true);
    const next = new URLSearchParams(searchParams);
    next.set("node", node.id);
    setSearchParams(next, { replace: true });
  }

  function updateWorkbenchSelection(
    nodes: Array<KnowledgeNode["data"] & { id: string }>,
  ) {
    const nextIds = Array.from(
      new Set(
        nodes
          .filter((node) => node.nodeType !== "root")
          .map((node) => node.id),
      ),
    );
    if (nextIds.length > 8) {
      toast.warning("联合学习最多选择 8 个节点");
      setSelectedNodeIds(nextIds.slice(0, 8));
      return;
    }
    setSelectedNodeIds(nextIds);
  }

  function openJointStudy() {
    if (!activeGraph || selectedNodeIds.length < 2) return;
    const params = new URLSearchParams({
      graphId: activeGraph.id,
      nodeIds: selectedNodeIds.join(","),
    });
    navigate(`${base}/learn/joint?${params.toString()}`);
  }

  return (
    <PageFrame className="graph-library-page graph-workbench-page">
      <Sheet onOpenChange={setLibraryOpen} open={libraryOpen}>
        <SheetContent className="graph-library-sheet overflow-y-auto sm:max-w-xl">
          <SheetTitle className="sr-only">图谱书架</SheetTitle>
          <div className="p-5 pt-12">
            <GraphBookshelf
              books={books}
              onDelete={(book) => void requestGoalDeletion(book)}
              onOpen={(book) => {
                openBook(book);
                setLibraryOpen(false);
              }}
              onStartGoal={startGoalClarification}
              onStartLearning={startBookLearning}
              selectedId={selectedBook?.id}
            />
          </div>
        </SheetContent>
      </Sheet>
      <DeleteImpactDialog
        confirmLabel={
          goalPendingDeletion?.graphId ? "删除目标与图谱" : "删除学习目标"
        }
        error={goalDeleteError}
        impact={goalDeleteImpact}
        isConfirming={goalDeleteConfirming}
        isLoading={goalDeleteLoading}
        objectLabel={goalPendingDeletion?.title ?? "学习目标"}
        onConfirm={confirmGoalDeletion}
        onOpenChange={(open) => {
          if (!open && !goalDeleteConfirming) closeGoalDeleteDialog();
        }}
        open={Boolean(goalPendingDeletion)}
        title={
          goalPendingDeletion
            ? goalPendingDeletion.graphId
              ? `永久删除「${goalPendingDeletion.title}」及其图谱？`
              : `永久删除学习目标「${goalPendingDeletion.title}」？`
            : undefined
        }
      />
      <section className="graph-workbench-canvas" aria-label="图谱工作台画布">
        <header className="graph-workbench-canvas__header">
          <div className="graph-workbench-heading">
            <Select
              onValueChange={(bookId) => {
                const book = books.find((item) => item.id === bookId);
                if (book) openBook(book);
              }}
              value={selectedBook?.id}
            >
              <SelectTrigger aria-label="选择学习图谱" className="graph-workbench-book-select">
                <Network className="size-4" />
                <SelectValue placeholder="选择学习图谱" />
              </SelectTrigger>
              <SelectContent>
                {books.map((book) => (
                  <SelectItem key={book.id} value={book.id}>
                    {book.title} · {book.status}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p>
              {selectedBook?.isGoalBook
                ? "完成目标澄清后生成图谱。"
                : activeGraph
                  ? `${activeGraph.nodes.length} 个节点 · 点击节点查看详情`
                  : "选择图谱后开始学习。"}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              onClick={returnToBookshelf}
              size="sm"
              title="返回图谱书架"
              variant="outline"
            >
              <BookOpen className="size-4" />
              书架
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button aria-label="更多图谱操作" size="icon-sm" variant="outline">
                  <MoreHorizontal className="size-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-48">
                <DropdownMenuItem onSelect={returnToBookshelf}>
                  <BookOpen className="size-4" />
                  返回图谱书架
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => setLibraryOpen(true)}>
                  <LayoutGrid className="size-4" />
                  快速切换图谱
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => navigate(`${base}/capabilities`)}>
                  <Brain className="size-4" />
                  能力成长视图
                </DropdownMenuItem>
                {activeGraph && !selectedBook?.isGoalBook ? (
                  <DropdownMenuItem onSelect={() => setGraphReviewOpen(true)}>
                    <GitCompareArrows className="size-4" />
                    合并与修订
                  </DropdownMenuItem>
                ) : null}
                <DropdownMenuItem onSelect={startGoalClarification}>
                  <BookPlus className="size-4" />
                  新建学习意向
                </DropdownMenuItem>
                {selectedBook ? <DropdownMenuSeparator /> : null}
                {selectedBook ? (
                  <DropdownMenuItem
                    onSelect={() => void requestGoalDeletion(selectedBook)}
                    variant="destructive"
                  >
                    <Trash2 className="size-4" />
                    删除目标与图谱
                  </DropdownMenuItem>
                ) : null}
              </DropdownMenuContent>
            </DropdownMenu>
            {selectedBook?.isGoalBook ? (
              <Button onClick={startGoalClarification} size="sm">
                <Sparkles className="size-4" />
                继续澄清
              </Button>
            ) : activeGraph ? (
              <Button
                onClick={() =>
                  startBookLearning({
                    id: `graph-${activeGraph.id}`,
                    goalId: activeGraph.goal_id,
                    graphId: activeGraph.id,
                    title: activeGraph.title,
                    summary: "",
                    status: activeGraph.status,
                    progress: "",
                    icon: Database,
                    isGoalBook: false,
                  })
                }
                size="sm"
              >
                <BookOpen className="size-4" />
                开始学习
              </Button>
            ) : null}
          </div>
        </header>
        {selectedBook?.isGoalBook ? (
          <div className="graph-workbench-canvas__empty">
            <CircleDot className="size-5" />
            <div>
              <strong>等待目标意向补全</strong>
              <p>
                {selectedBook.summary ||
                  "告诉 AI 你想学什么，它会逐步生成可学习的知识层级。"}
              </p>
            </div>
            <Button onClick={startGoalClarification} size="sm">
              <Sparkles className="size-4" />
              继续澄清
            </Button>
          </div>
        ) : openedGraph.isPending && activeGraphId !== graphId ? (
          <LoadingState label="正在打开选中的图谱…" />
        ) : openedGraph.isError && activeGraphId !== graphId ? (
          <ErrorState
            message={openedGraph.error.message}
            onRetry={() => void openedGraph.refetch()}
          />
        ) : activeGraph ? (
          <div className="graph-workbench-canvas__body graph-workbench-canvas__body--full">
            <div className="graph-workbench-canvas__main">
              <div className="graph-workbench-controls" aria-label="图谱视图控制">
                <div className="graph-workbench-search">
                  <Search className="size-3.5" />
                  <Input
                    aria-label="搜索图谱节点"
                    onChange={(event) => setNodeSearch(event.target.value)}
                    placeholder="搜索节点或定义"
                    value={nodeSearch}
                  />
                </div>
                <Select onValueChange={setNodeStateFilter} value={nodeStateFilter}>
                  <SelectTrigger aria-label="筛选节点状态" className="w-36">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部状态</SelectItem>
                    <SelectItem value="due">待复习</SelectItem>
                    <SelectItem value="relearning">重新学习</SelectItem>
                    <SelectItem value="fresh">状态新鲜</SelectItem>
                    <SelectItem value="focused">重点节点</SelectItem>
                  </SelectContent>
                </Select>
                <div className="graph-depth-control" role="group" aria-label="展开层级">
                  <Button
                    aria-label="减少展开层级"
                    disabled={depthLimit <= 0}
                    onClick={() => setDepthLimit((current) => Math.max(0, current - 1))}
                    size="icon-sm"
                    title="减少展开层级"
                    variant="ghost"
                  >
                    <ZoomOut />
                  </Button>
                  <span>{depthLimit}/{maximumDepth} 层</span>
                  <Button
                    aria-label="增加展开层级"
                    disabled={depthLimit >= maximumDepth}
                    onClick={() =>
                      setDepthLimit((current) => Math.min(maximumDepth, current + 1))
                    }
                    size="icon-sm"
                    title="增加展开层级"
                    variant="ghost"
                  >
                    <ZoomIn />
                  </Button>
                </div>
                <Button
                  aria-pressed={editMode}
                  onClick={() => {
                    const next = !editMode;
                    setEditMode(next);
                    if (!next) setEditingNode(false);
                    else if (selectedNode) {
                      setInspectorOpen(true);
                      setEditingNode(true);
                    }
                  }}
                  size="sm"
                  title={editMode ? "退出编辑模式" : "进入编辑模式，手动修订节点"}
                  variant={editMode ? "secondary" : "outline"}
                >
                  <Pencil className="size-3.5" />
                  {editMode ? "编辑中" : "编辑模式"}
                </Button>
                <Button
                  aria-pressed={multiSelect}
                  onClick={() => {
                    const nextMultiSelect = !multiSelect;
                    setMultiSelect(nextMultiSelect);
                    setSelectedNodeIds(
                      nextMultiSelect
                        ? selectedNode && selectedNode.node_type !== "root"
                          ? [selectedNode.id]
                          : []
                        : selectedNode
                          ? [selectedNode.id]
                          : [],
                    );
                  }}
                  size="sm"
                  variant={multiSelect ? "secondary" : "outline"}
                >
                  <MousePointer2 className="size-3.5" />
                  {multiSelect ? "多选中" : "多选"}
                </Button>
              </div>
              <div className="graph-workbench-canvas__graph">
                {activeGraph.nodes.length === 1 &&
                !nodeSearch.trim() &&
                nodeStateFilter === "all" ? (
                  <div className="graph-single-node" role="status">
                    <CircleDot className="size-6" />
                    <div>
                      <strong>当前图谱只有根节点</strong>
                      <p>
                        继续学习可形成新的对话证据；图谱结构变更仍需通过正式修订审核。
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        onClick={() => setGraphReviewOpen(true)}
                        size="sm"
                        variant="outline"
                      >
                        <GitCompareArrows className="size-4" />
                        审核图谱修订
                      </Button>
                      <Button
                        onClick={() => setInspectorOpen(true)}
                        size="sm"
                        variant="ghost"
                      >
                        查看根节点
                      </Button>
                    </div>
                  </div>
                ) : filteredWorkbenchGraph?.nodes.length ? (
                  <KnowledgeGraph
                    edges={filteredWorkbenchGraph.edges}
                    layout="tree"
                    maxDepth={depthLimit}
                    multiple={multiSelect}
                    nodes={filteredWorkbenchGraph.nodes}
                    onSelect={selectWorkbenchNode}
                    onSelectionChange={updateWorkbenchSelection}
                    rootEmphasis
                    selectedId={selectedNode?.id}
                    selectedIds={selectedNodeIds}
                    showZoomControls
                    title={activeGraph.title}
                  />
                ) : (
                  <div className="graph-workbench-filter-empty" role="status">
                    <Search className="size-5" />
                    <strong>没有匹配的节点</strong>
                    <p>调整关键词或状态筛选后重试。</p>
                    <Button
                      onClick={() => {
                        setNodeSearch("");
                        setNodeStateFilter("all");
                      }}
                      size="xs"
                      variant="outline"
                    >
                      清除筛选
                    </Button>
                  </div>
                )}
              </div>
              {multiSelect ? (
                <div className="graph-multi-action" role="status">
                  <div>
                    <Network className="size-4" />
                    <span>已选择 {selectedNodeIds.length}/8 个节点</span>
                  </div>
                  <Button
                    disabled={selectedNodeIds.length < 2}
                    onClick={openJointStudy}
                    size="sm"
                  >
                    <ListTree className="size-4" />
                    联合学习
                  </Button>
                </div>
              ) : null}
            </div>
          </div>
        ) : (
          <div className="graph-workbench-canvas__empty">
            <BookOpen className="size-5" />
            <div>
              <strong>还没有可打开的图谱</strong>
              <p>从书架新建学习意向，AI 会把目标转化为可学习的知识结构。</p>
            </div>
            <Button onClick={startGoalClarification} size="sm">
              <Sparkles className="size-4" />
              新建学习意向
            </Button>
          </div>
        )}
      </section>
      <Sheet onOpenChange={setInspectorOpen} open={inspectorOpen && Boolean(selectedNode)}>
        <SheetContent className="graph-node-sheet overflow-y-auto sm:max-w-md">
          <SheetTitle className="sr-only">节点详情</SheetTitle>
          <div className="p-5 pt-12">
            <GraphNodeInspector
              draft={nodeDraft}
              editMode={editMode}
              editable={editMode}
              exploreOpen={explorePanelOpen}
              exploreRounds={selectedExplore.data ?? []}
              exploreLoading={selectedExplore.isPending}
              focusBusy={focus.isPending}
              focusEditable={focusEditable}
              masteryBusy={focus.isPending}
              node={selectedNode}
              onChange={setNodeDraft}
              onEdit={() => {
                setEditMode(true);
                setEditingNode(true);
              }}
              onFocus={focusSelectedNode}
              onMastery={masterySelectedNode}
              onSplit={splitSelectedNode}
              onLearn={() =>
                selectedNode &&
                studyFromNode({ ...selectedNode, id: selectedNode.id })
              }
              onOpenExplore={() => setExplorePanelOpen(true)}
              onCloseExplore={() => setExplorePanelOpen(false)}
              onSave={saveSelectedNode}
              onStopEditing={() => {
                if (selectedNode)
                  setNodeDraft({
                    label: selectedNode.label,
                    description: selectedNode.description,
                    targetWeight: selectedNode.target_weight,
                  });
                setEditingNode(false);
              }}
              saving={updateNode.isPending}
              editing={editingNode && editMode}
            />
          </div>
        </SheetContent>
      </Sheet>
      {activeGraph ? (
        <GraphReviewDialog
          graph={activeGraph}
          onOpenChange={setGraphReviewOpen}
          open={graphReviewOpen}
        />
      ) : null}
    </PageFrame>
  );
}

function GraphNodeInspector({
  node,
  draft,
  editing,
  editable,
  editMode = false,
  exploreOpen = false,
  exploreRounds = [],
  exploreLoading = false,
  focusEditable,
  saving,
  focusBusy,
  onChange,
  onEdit,
  onStopEditing,
  onSave,
  onFocus,
  onLearn,
  onOpenExplore,
  onCloseExplore,
  onMastery,
  masteryBusy = false,
  onSplit,
}: {
  node?: GraphNode;
  draft: { label: string; description: string; targetWeight: number };
  editing: boolean;
  editable: boolean;
  editMode?: boolean;
  exploreOpen?: boolean;
  exploreRounds?: Array<{ id: string; content: string; created_at: string }>;
  exploreLoading?: boolean;
  focusEditable: boolean;
  saving: boolean;
  focusBusy: boolean;
  onChange: (draft: {
    label: string;
    description: string;
    targetWeight: number;
  }) => void;
  onEdit: () => void;
  onStopEditing: () => void;
  onSave: () => void;
  onFocus: () => void;
  onLearn: () => void;
  onOpenExplore?: () => void;
  onCloseExplore?: () => void;
  onMastery?: () => void;
  masteryBusy?: boolean;
  onSplit?: () => void;
}) {
  const importance = weightToImportance(draft.targetWeight);
  return (
    <Surface className="h-fit p-4 graph-node-inspector">
      <SectionHeading
        action={
          node && !editing ? (
            <Button
              disabled={!editable && !editMode}
              onClick={onEdit}
              size="xs"
              title={
                editMode
                  ? "编辑节点字段"
                  : "请先打开工具栏「编辑模式」再手动修订"
              }
              variant="outline"
            >
              <Pencil className="size-3.5" />
              {editMode ? "编辑" : "需编辑模式"}
            </Button>
          ) : null
        }
        description={
          node?.node_type === "root"
            ? "目标根节点"
            : editMode
              ? "知识点 · 编辑模式已开启"
              : "知识点 · 点选展开细节"
        }
        title={node ? `节点 · ${node.label}` : "节点检查器"}
      />
      {!node ? (
        <p className="mt-4 text-sm text-muted-foreground">
          选择画布中的节点以查看详情。
        </p>
      ) : editing ? (
        <div className="mt-4 space-y-3">
          <label className="grid gap-1.5 text-xs font-medium">
            节点名称
            <Input
              maxLength={200}
              onChange={(event) =>
                onChange({ ...draft, label: event.target.value })
              }
              value={draft.label}
            />
          </label>
          <label className="grid gap-1.5 text-xs font-medium">
            定义与边界
            <Textarea
              className="min-h-28 resize-y"
              maxLength={4000}
              onChange={(event) =>
                onChange({ ...draft, description: event.target.value })
              }
              value={draft.description}
            />
          </label>
          <div className="importance-calibrator">
            <div className="importance-calibrator__head">
              <span>重要指数</span>
              <RecommendDots weight={draft.targetWeight} />
            </div>
            <p className="importance-calibrator__hint">
              1–3 档：低 / 中 / 高。
            </p>
            <div className="importance-calibrator__levels" role="group" aria-label="重要指数">
              {([1, 2, 3] as MetricLevel[]).map((level) => (
                <button
                  aria-pressed={importance === level}
                  className={
                    importance === level
                      ? "importance-level is-active"
                      : "importance-level"
                  }
                  key={level}
                  onClick={() =>
                    onChange({
                      ...draft,
                      targetWeight: importanceToWeight(level),
                    })
                  }
                  type="button"
                >
                  <strong>{metricLabel(level)}</strong>
                  <span>{level === 3 ? "强烈建议看" : level === 2 ? "可以看看" : "可以跳过"}</span>
                </button>
              ))}
            </div>
            <label className="grid gap-2 text-xs font-medium">
              <span className="flex items-center justify-between gap-3">
                精确权重
                <output className="tabular-nums text-muted-foreground">
                  {draft.targetWeight}/100
                </output>
              </span>
              <Slider
                aria-label="节点目标权重"
                max={100}
                min={1}
                onValueChange={(value) =>
                  onChange({ ...draft, targetWeight: value[0] ?? 1 })
                }
                step={1}
                value={[draft.targetWeight]}
              />
            </label>
          </div>
          <div className="flex justify-end gap-2">
            <Button
              disabled={saving}
              onClick={onStopEditing}
              size="xs"
              type="button"
              variant="ghost"
            >
              取消
            </Button>
            <Button
              disabled={saving || !draft.label.trim()}
              onClick={onSave}
              size="xs"
              type="button"
            >
              <Save className="size-3.5" />
              {saving ? "保存中…" : "保存"}
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-4 space-y-4">
          <p className="text-sm leading-6 text-muted-foreground">
            {node.description || "尚未补充知识点说明。"}
          </p>
          <div className="graph-node-inspector__facts">
            <div className="rounded-lg bg-muted/50 p-2">
              <span className="block text-muted-foreground">掌握度</span>
              <strong className="mt-1 block">{node.mastery_stars} 星</strong>
            </div>
            <div className="rounded-lg bg-muted/50 p-2">
              <span className="block text-muted-foreground">检索状态</span>
              <strong className="mt-1 block">
                {graphStateLabel(node.retrieval_state)}
              </strong>
            </div>
            <div className="rounded-lg bg-muted/50 p-2">
              <span className="block text-muted-foreground">证据状态</span>
              <strong className="mt-1 block">
                {graphStateLabel(node.evidence_state)}
              </strong>
            </div>
            <div className="rounded-lg bg-muted/50 p-2">
              <span className="block text-muted-foreground">重要指数</span>
              <div className="mt-1 flex items-center justify-between gap-2">
                <strong>
                  {metricLabel(weightToImportance(node.target_weight))}
                </strong>
                <RecommendDots weight={node.target_weight} />
              </div>
            </div>
          </div>

          <section className="node-detail-expand" aria-label="深入记录">
            <div className="node-detail-expand__head">
              <div>
                <span>探索链</span>
                <strong>
                  {exploreLoading
                    ? "加载中…"
                    : exploreRounds.length
                      ? `已深入 ×${exploreRounds.length}`
                      : "未深入"}
                </strong>
              </div>
              {exploreRounds.length ? (
                <Button
                  onClick={() =>
                    exploreOpen ? onCloseExplore?.() : onOpenExplore?.()
                  }
                  size="xs"
                  variant="ghost"
                >
                  {exploreOpen ? "收起" : "展开"}
                </Button>
              ) : null}
            </div>
            {exploreOpen && exploreRounds.length ? (
              <NodeExploreChain
                onClose={onCloseExplore}
                rounds={exploreRounds}
                title={node.label}
              />
            ) : null}
            {!exploreRounds.length && !exploreLoading ? (
              <NodeExploreEmpty onLearn={onLearn} />
            ) : null}
          </section>

          <Button className="w-full" onClick={onLearn} size="sm">
            <MessageCircle className="size-4" />
            从此概念开始问答
          </Button>
          {onMastery ? (
            <Button
              className="w-full"
              disabled={masteryBusy || !focusEditable}
              onClick={onMastery}
              size="sm"
              title={
                node.attention_state === "mastered"
                  ? "取消已掌握：节点将移出能力成长图谱"
                  : "标记已掌握：节点进入能力成长图谱"
              }
              variant={
                node.attention_state === "mastered" ? "secondary" : "outline"
              }
            >
              <BadgeCheck className="size-4" />
              {masteryBusy
                ? "更新中…"
                : node.attention_state === "mastered"
                  ? "已掌握"
                  : "标为已掌握"}
            </Button>
          ) : null}
          {onSplit ? (
            <Button
              className="w-full"
              onClick={onSplit}
              size="sm"
              title="调用智能体拆分该节点并生成图谱变更提案"
              variant="outline"
            >
              <Split className="size-4" />
              拆分（图谱变更）
            </Button>
          ) : null}
          <Button
            className="w-full"
            disabled={!focusEditable || focusBusy}
            onClick={onFocus}
            size="sm"
            variant="outline"
          >
            <Focus className="size-4" />
            {focusBusy
              ? "设置中…"
              : node.attention_state === "focused"
                ? "取消重点"
                : "设为重点"}
          </Button>
          {!editMode ? (
            <p className="text-xs leading-5 text-muted-foreground">
              手动改名、改定义与重要指数需要先打开工具栏「编辑模式」。
            </p>
          ) : !focusEditable ? (
            <p className="text-xs leading-5 text-muted-foreground">
              名称和说明会保存为本地工作区编辑；重点状态仍需在候选修订中保存。
            </p>
          ) : null}
        </div>
      )}
    </Surface>
  );
}

export function JointStudyPage() {
  const { workspaceId = "" } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const graphs = useQuery({ queryKey: ["graphs"], queryFn: listGraphs });
  const requestedGraphId = searchParams.get("graphId") ?? "";
  const requestedNodeIds = useMemo(
    () =>
      (searchParams.get("nodeIds") ?? "")
        .split(",")
        .map((id) => id.trim())
        .filter(Boolean)
        .filter((id, index, values) => values.indexOf(id) === index)
        .slice(0, 8),
    [searchParams],
  );
  const [graphId, setGraphId] = useState(requestedGraphId);
  const resolvedGraphId = graphId || graphs.data?.[0]?.id || "";
  const graph = useQuery({
    queryKey: ["graph", resolvedGraphId],
    queryFn: () => getGraph(resolvedGraphId),
    enabled: Boolean(resolvedGraphId),
  });
  const [selected, setSelected] = useState<string[]>(requestedNodeIds);
  useEffect(() => {
    if (!graph.data) return;
    const validIds = new Set(graph.data.nodes.map((node) => node.id));
    setSelected((current) => {
      const retained = current.filter((id) => validIds.has(id)).slice(0, 8);
      if (retained.length) return retained;
      const requested = requestedNodeIds
        .filter((id) => validIds.has(id))
        .slice(0, 8);
      return requested.length
        ? requested
        : graph.data.nodes.slice(0, 3).map((node) => node.id);
    });
  }, [graph.data, requestedNodeIds]);
  const [result, setResult] = useState<MultiNodeStudyResponse | null>(null);
  const selectedKey = selected.join(",");
  useEffect(() => setResult(null), [resolvedGraphId, selectedKey]);
  const study = useMutation({
    mutationFn: () =>
      studyMultipleNodes(resolvedGraphId, { node_ids: selected }),
    onSuccess: setResult,
    onError: (error) => toast.error(error.message),
  });
  const startSession = useMutation({
    mutationFn: () =>
      createSession({
        title: `${graph.data?.title ?? "联合"}学习`,
        graph_id: resolvedGraphId,
      }),
    onSuccess: (session) =>
      navigate(`/w/${workspaceId}/chat/${session.id}`, {
        state: {
          pendingPrompt:
            result?.next_actions.join("；") ||
            `请联合讲解：${selectedNodes.map((node) => node.label).join("、")}`,
          learningNode: {
            graphId: resolvedGraphId,
            nodeIds: selectedNodes.map((node) => node.id),
          },
        },
      }),
  });
  const splitSessions = useMutation({
    mutationFn: () =>
      Promise.all(
        selectedNodes.map((node) =>
          createSession({
            title: `${node.label} · 独立学习`,
            graph_id: resolvedGraphId,
          }),
        ),
      ),
    onSuccess: (sessions) => {
      const first = sessions[0];
      const firstNode = selectedNodes[0];
      toast.success(`已创建 ${sessions.length} 个独立会话`);
      if (first && firstNode) {
        navigate(`/w/${workspaceId}/chat/${first.id}`, {
          state: {
            pendingPrompt: `请单独讲解“${firstNode.label}”，不要强行关联其他节点。`,
            learningNode: { graphId: resolvedGraphId, nodeId: firstNode.id },
          },
        });
      }
    },
    onError: (error) => toast.error(error.message),
  });
  if (graph.isPending)
    return (
      <PageFrame>
        <LoadingState />
      </PageFrame>
    );
  if (graph.isError)
    return (
      <PageFrame>
        <ErrorState message={graph.error.message} />
      </PageFrame>
    );
  const selectedNodes = graph.data.nodes.filter((node) =>
    selected.includes(node.id),
  );
  const rationale = result?.rationale ?? "尚未运行模型关联判断。";
  return (
    <PageFrame>
      <PageIntro
        actions={
          <Button
            disabled={selected.length < 2 || study.isPending}
            onClick={() => study.mutate()}
          >
            <Sparkles className="size-4" />
            {study.isPending ? "正在判断…" : "重新判断关联"}
          </Button>
        }
        description="先判断有关联、弱关联或无直接关联；无关联时不会强行编造联合讲解。"
        eyebrow="Joint learning"
        title="多节点联合学习"
      />
      <Surface className="p-5">
        <Select
          onValueChange={(value) => {
            setGraphId(value);
            setSelected([]);
            setResult(null);
          }}
          value={resolvedGraphId}
        >
          <SelectTrigger className="mb-4">
            <SelectValue placeholder="选择图谱" />
          </SelectTrigger>
          <SelectContent>
            {graphs.data?.map((item) => (
              <SelectItem key={item.id} value={item.id}>
                {item.title}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <SectionHeading description="选择 2～8 个节点" title="已选择节点" />
        <div className="mt-4 flex flex-wrap gap-2">
          {graph.data.nodes.map((node) => {
            const checked = selected.includes(node.id);
            return (
              <label
                className={
                  checked
                    ? "flex cursor-pointer items-center gap-2 rounded-full border border-primary bg-primary/5 px-3 py-2 text-xs text-primary"
                    : "flex cursor-pointer items-center gap-2 rounded-full border px-3 py-2 text-xs text-muted-foreground"
                }
                key={node.id}
              >
                <Checkbox
                  checked={checked}
                  disabled={!checked && selected.length >= 8}
                  onCheckedChange={() => {
                    setResult(null);
                    setSelected((current) => {
                      if (checked)
                        return current.filter((id) => id !== node.id);
                      if (current.length >= 8) {
                        toast.warning("联合学习最多选择 8 个节点");
                        return current;
                      }
                      return [...current, node.id];
                    });
                  }}
                />
                {node.label}
              </label>
            );
          })}
        </div>
      </Surface>
      <div className="grid gap-5 xl:grid-cols-[1fr_300px]">
        <div className="space-y-5">
          <Surface className="p-5">
            <div className="flex flex-wrap items-center gap-2">
              <StatePill
                label={result?.relationship ?? "待判断"}
                status={result?.related ? "approved" : "pending"}
              />
              {result ? (
                <>
                  <Badge variant="secondary">Provider：{result.provider}</Badge>
                  <Badge variant="secondary">图谱修订 v{result.graph_revision}</Badge>
                  <Badge variant="secondary">仅使用图结构</Badge>
                </>
              ) : null}
              <Badge variant="secondary">禁止强行关联：开启</Badge>
            </div>
            <h2 className="mt-4 text-base font-semibold">关联判断</h2>
            <p className="mt-2 text-sm leading-7 text-muted-foreground">
              {rationale}
            </p>
            {result ? (
              <div className="mt-4 grid gap-3 border-t pt-4 md:grid-cols-2">
                <div>
                  <p className="text-xs font-medium">所选节点间关系</p>
                  <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
                    {result.selected_edges.length ? (
                      result.selected_edges.map((edge) => (
                        <li key={edge.edge_id}>
                          · {selectedNodes.find((node) => node.id === edge.source_node_id)?.label ?? edge.source_node_id}
                          {" → "}
                          {selectedNodes.find((node) => node.id === edge.target_node_id)?.label ?? edge.target_node_id}
                          {" · "}{graphRelationLabel(edge.relation)}
                        </li>
                      ))
                    ) : (
                      <li>没有持久化的直接边。</li>
                    )}
                  </ul>
                </div>
                <div>
                  <p className="text-xs font-medium">共同前置</p>
                  <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
                    {result.shared_prerequisites.length ? (
                      result.shared_prerequisites.map((item) => (
                        <li key={item.node_id}>· {item.label}</li>
                      ))
                    ) : (
                      <li>没有共同指向两个以上所选节点的前置。</li>
                    )}
                  </ul>
                </div>
              </div>
            ) : null}
          </Surface>
          <Surface className="overflow-hidden">
            <div className="border-b p-4">
              <SectionHeading
                description="当前状态、建议动作和验收方式"
                title="联合学习对比"
              />
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[680px] text-left text-sm">
                <thead className="bg-muted/45 text-xs text-muted-foreground">
                  <tr>
                    <th className="px-4 py-3">概念</th>
                    <th className="px-4 py-3">当前状态</th>
                    <th className="px-4 py-3">节点角色</th>
                    <th className="px-4 py-3">建议动作</th>
                    <th className="px-4 py-3">验收</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {selectedNodes.map((node) => (
                    <tr key={node.id}>
                      <td className="px-4 py-3 font-medium">{node.label}</td>
                      <td className="px-4 py-3">
                        <StatePill status={node.retrieval_state} />
                      </td>
                      <td className="px-4 py-3 text-muted-foreground">
                        {result?.roles[node.id] ?? "—"}
                      </td>
                      <td className="px-4 py-3">
                        {result
                          ? result.related
                            ? "参与联合任务"
                            : "建议独立处理"
                          : "—"}
                      </td>
                      <td className="px-4 py-3">
                        {result?.exercise_prompt ? "已生成综合练习" : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Surface>
        </div>
        <Surface className="p-5">
          <SectionHeading title="联合学习控制" />
          <div className="mt-4 space-y-4 text-sm">
            <div>
              <p className="font-medium">节点角色</p>
              <ul className="mt-2 space-y-1 text-xs leading-5 text-muted-foreground">
                {selectedNodes.map((node) => (
                  <li key={node.id}>
                    · {node.label}：{result?.roles[node.id] ?? "等待判断"}
                  </li>
                ))}
              </ul>
            </div>
            <div className="border-t pt-4">
              <p className="font-medium">输出绑定</p>
              <p className="mt-2 text-xs leading-5 text-muted-foreground">
                首条消息携带全部所选节点 ID；只有真实回答、练习或解释产出才进入证据分析。
              </p>
            </div>
            {result?.next_actions.length ? (
              <div className="border-t pt-4">
                <p className="font-medium">全局下一步</p>
                <ul className="mt-2 space-y-1 text-xs leading-5 text-muted-foreground">
                  {result.next_actions.map((action) => (
                    <li key={action}>· {action}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            <Button
              className="w-full"
              disabled={!result || !result.related || startSession.isPending}
              onClick={() => startSession.mutate()}
            >
              进入联合讲解
              <ArrowRight className="size-4" />
            </Button>
            <Button
              className="w-full"
              disabled={selectedNodes.length < 2 || splitSessions.isPending}
              onClick={() => splitSessions.mutate()}
              variant="outline"
            >
              <Split className="size-4" />
              {splitSessions.isPending ? "创建中…" : "拆分独立会话"}
            </Button>
          </div>
        </Surface>
      </div>
    </PageFrame>
  );
}

export function CapabilityGraphPage() {
  const { workspaceId = "" } = useParams();
  const mastery = useQuery({ queryKey: ["mastery"], queryFn: getMastery });
  const [selectedId, setSelectedId] = useState("");
  const [depthLimit, setDepthLimit] = useState(1);
  const [alignmentOpen, setAlignmentOpen] = useState(false);
  const alignment = useQuery({
    queryKey: ["mastery-alignment", selectedId],
    queryFn: () => getMasteryAlignment(selectedId),
    enabled: alignmentOpen && Boolean(selectedId),
  });
  const report = useMutation({
    mutationFn: getCapabilityReport,
    onSuccess: (data) => {
      downloadJsonFile(
        `learngraph-capability-report-${new Date().toISOString().slice(0, 10)}.json`,
        data,
      );
      toast.success("能力报告已从服务端生成并下载");
    },
    onError: (error) => toast.error(error.message),
  });
  const capabilityNodes = useMemo(() => {
    const root: KnowledgeNode = {
      id: "capability-root",
      type: "knowledge",
      position: { x: 0, y: 0 },
      data: {
        label: "能力成长",
        root: true,
        stars: 0,
        state: `${mastery.data?.length ?? 0} 个概念`,
      },
    };
    return [
      root,
      ...(mastery.data ?? []).map((item) => ({
        id: item.node_id,
        type: "knowledge" as const,
        position: { x: 0, y: 0 },
        data: {
          label: item.label,
          stars: item.mastery_stars,
          state: item.retrieval_state,
          evidence: `${item.accepted_evidence_count} 条已接受证据`,
        },
      })),
    ];
  }, [mastery.data]);
  const capabilityEdges = useMemo(
    () =>
      (mastery.data ?? []).map((item) => ({
        id: `capability-root-${item.node_id}`,
        source: "capability-root",
        target: item.node_id,
        type: "smoothstep",
      })),
    [mastery.data],
  );
  const maxDepth = useMemo(
    () => getKnowledgeGraphTreeDepth(capabilityNodes, capabilityEdges),
    [capabilityEdges, capabilityNodes],
  );
  useEffect(
    () => setDepthLimit((current) => Math.min(Math.max(0, current), maxDepth)),
    [maxDepth],
  );
  const selected = useMemo(
    () => mastery.data?.find((node) => node.node_id === selectedId),
    [mastery.data, selectedId],
  );
  if (mastery.isPending)
    return (
      <PageFrame>
        <LoadingState />
      </PageFrame>
    );
  if (mastery.isError)
    return (
      <PageFrame>
        <ErrorState message={mastery.error.message} />
      </PageFrame>
    );
  return (
    <PageFrame>
      <PageIntro
        description="查看证据驱动的能力状态。"
        eyebrow="Capability graph"
        title="用户能力成长图谱"
      />
      <div className="flex flex-wrap items-center gap-2">
        <StatePill label="已达里程碑" status="approved" />
        <StatePill label="复习中" status="pending" />
        <StatePill label="证据冲突" status="conflicted" />
        <Badge variant="secondary">跨目标共享</Badge>
        <div className="ml-auto flex items-center gap-1 rounded-lg border bg-background p-1 text-xs">
          <Button
            aria-label="隐藏最深一层"
            disabled={depthLimit <= 0}
            onClick={() => setDepthLimit((current) => Math.max(0, current - 1))}
            size="xs"
            variant="ghost"
          >
            收起一层
          </Button>
          <span className="px-1 text-muted-foreground">0–{depthLimit} 层</span>
          <Button
            aria-label="显示下一层"
            disabled={depthLimit >= maxDepth}
            onClick={() =>
              setDepthLimit((current) => Math.min(maxDepth, current + 1))
            }
            size="xs"
            variant="ghost"
          >
            展开一层
          </Button>
        </div>
      </div>
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_300px]">
        <KnowledgeGraph
          edges={capabilityEdges}
          layout="tree"
          maxDepth={depthLimit}
          maximumZoom={3}
          minimumZoom={0.2}
          nodes={capabilityNodes}
          onSelect={(node) => setSelectedId(node.id)}
          rootEmphasis
          selectedId={selectedId}
          showZoomControls
          title="能力成长图谱"
        />
        <Surface className="p-5">
          <SectionHeading
            title={selected ? `能力节点 · ${selected.label}` : "能力节点"}
          />
          <div className="mt-4 space-y-4">
            <div className="rounded-xl bg-muted/40 p-4">
              <p className="text-xs text-muted-foreground">成长星级</p>
              <div className="mt-1">
                <GrowthStars value={selected?.mastery_stars ?? 0} />
              </div>
            </div>
            <div>
              <p className="text-sm font-medium">证据来源</p>
              <p className="mt-2 text-xs leading-5 text-muted-foreground">
                {selected
                  ? `${selected.accepted_evidence_count} 条已接受证据 · ${selected.evidence_state}`
                  : "选择能力节点后查看证据状态。"}
              </p>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Button asChild size="sm" variant="outline">
                <Link to={`/w/${workspaceId}/evidence/review`}>
                  <Eye className="size-4" />
                  查看证据
                </Link>
              </Button>
              <Button asChild size="sm" variant="outline">
                <Link to={`/w/${workspaceId}/practice`}>
                  <ListChecks className="size-4" />
                  生成练习
                </Link>
              </Button>
              <Button
                disabled={!selected}
                onClick={() => setAlignmentOpen(true)}
                size="sm"
                variant="outline"
              >
                <Target className="size-4" />
                目标对齐
              </Button>
              <Button
                disabled={report.isPending || !mastery.data.length}
                onClick={() => report.mutate()}
                size="sm"
                variant="outline"
              >
                <Download className="size-4" />
                {report.isPending ? "生成中…" : "能力报告"}
              </Button>
            </div>
          </div>
        </Surface>
      </div>
      <Surface className="p-5">
        <SectionHeading title="能力图谱图例" />
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {[
            [Network, "成长星级", "节点里程碑，只增不减"],
            [Route, "可提取性", "随复习和时间变化"],
            [FileText, "证据状态", "小盾牌与状态文字"],
            [Focus, "关注状态", "重复追问显示聚焦"],
          ].map(([Icon, title, text]) => {
            const Comp = Icon as typeof Network;
            return (
              <div className="rounded-xl border p-3" key={String(title)}>
                <Comp className="size-4 text-primary" />
                <p className="mt-2 text-sm font-medium">{String(title)}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {String(text)}
                </p>
              </div>
            );
          })}
        </div>
      </Surface>
      <Dialog onOpenChange={setAlignmentOpen} open={alignmentOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {selected ? `“${selected.label}”的目标对齐` : "目标对齐"}
            </DialogTitle>
            <DialogDescription>
              展示该规范概念在当前工作区可访问 Goal 与图谱中的真实出现位置。
            </DialogDescription>
          </DialogHeader>
          {alignment.isPending ? (
            <LoadingState label="正在查询目标关联…" />
          ) : null}
          {alignment.isError ? (
            <ErrorState
              message={alignment.error.message}
              onRetry={() => void alignment.refetch()}
            />
          ) : null}
          {alignment.data ? (
            <div className="space-y-4">
              <p className="text-sm leading-6 text-muted-foreground">
                {alignment.data.explanation}
              </p>
              {alignment.data.occurrences.length ? (
                <div className="space-y-2">
                  {alignment.data.occurrences.map((item) => (
                    <Link
                      className="block rounded-xl border p-3 transition-colors hover:border-primary hover:bg-primary/[.025]"
                      key={`${item.goal_id}:${item.graph_id}`}
                      onClick={() => setAlignmentOpen(false)}
                      to={`/w/${encodeURIComponent(workspaceId)}/graphs/${encodeURIComponent(item.graph_id)}`}
                    >
                      <p className="text-sm font-semibold">{item.goal_title}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {item.graph_title} · {item.graph_status}
                      </p>
                    </Link>
                  ))}
                </div>
              ) : (
                <p className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground">
                  当前没有关联目标。
                </p>
              )}
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </PageFrame>
  );
}
