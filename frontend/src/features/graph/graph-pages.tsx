import { LearningBuildSettings } from '@/features/learning/node-learning-page';
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  BadgeCheck,
  BookOpen,
  BookPlus,
  Brain,
  CalendarClock,
  CalendarDays,
  CircleDot,
  Crosshair,
  Database,
  Download,
  Eye,
  FileText,
  Focus,
  GitCompareArrows,
  Layers,
  LayoutGrid,
  Lock,
  ListTree,
  ListChecks,
  Minus,
  MoreHorizontal,
  MousePointer2,
  Move,
  Network,
  Pencil,
  Play,
  Plus,
  RotateCcw,
  Route,
  Save,
  Search,
  ShieldCheck,
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
  getLearningNodeState,
  getMastery,
  getMasteryAlignment,
  listGoals,
  listGraphs,
  studyMultipleNodes,
  updateGraphNode,
  updateGraphCover,
} from "@/api";
import { cn } from "@/lib/utils";
import { workspaceQueryKey } from "@/lib/query-keys";
import { saveBlobViaNative } from "@/lib/native-download";
import { DeleteImpactDialog } from "@/components/shared/delete-impact-dialog";
import { GraphLegend } from "@/components/graph/graph-legend";
import { GraphReviewDialog } from "@/components/graph/graph-review-dialog";
import { GraphCoverAIBadge, GraphCoverAIEditor } from "@/features/graph/graph-cover-ai";
import {
  getKnowledgeGraphTreeDepth,
  getKnowledgeGraphTreeDepths,
} from "@/components/graph/knowledge-graph-layout";
import {
  KnowledgeGraph,
  type KnowledgeGraphCanvasApi,
  type KnowledgeNode,
} from "@/components/graph/knowledge-graph";
import {
  NodeExploreChain,
  NodeExploreEmpty,
  RecommendDots,
} from "@/components/graph/node-explore";
import {
  AddToPlanDialog,
  GraphPlanInspector,
  PlanScheduleDialog,
  useGraphPlanController,
} from "@/features/graph/graph-plan";
import {
  buildPlanPathEdges,
  startPlanPracticeSession,
  suggestPlanSlot,
  type PlanItemView,
  type PlanNodeMarker,
} from "@/features/graph/graph-plan-model";
import { useNodeExploreRounds } from "@/components/graph/node-explore-data";
import {
  importanceToWeight,
  metricLabel,
  weightToImportance,
  type MetricLevel,
} from "@/components/graph/node-metrics";
import {
  NODE_STATUS_PROFILES,
  NODE_TYPE_ORDER,
  NODE_TYPE_PROFILES,
  graphLevelLabel,
  isPrerequisiteSatisfied,
  masteryLevelLabel,
  resolveLearningStatus,
  resolveNodeTypeProfile,
  type NodeLearningStatusId,
  type NodeTypeId,
} from "@/components/graph/node-presentation";
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
import { Progress } from "@/components/ui/progress";
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
  const blob = new Blob([JSON.stringify(value, null, 2)], {
    type: "application/json;charset=utf-8",
  });
  // 移动端 WebView：纯前端生成的 blob 交给原生 base64 通道
  void saveBlobViaNative(blob, name).then((handled) => {
    if (handled) return;
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    anchor.click();
    URL.revokeObjectURL(url);
  });
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
  /** 候选图谱：不可直接学习，需先经审核发布。 */
  needsReview: boolean;
  masteryProgress: number;
  cover: string;
  /** 该图谱有 AI 封面正在后台生成：书架显示角标并独立轮询。 */
  coverAiActive?: boolean;
};

/** 三个纯矢量模板（paper/midnight/sunrise）没有实拍底图，用调色板色块当缩略图。 */
const TEMPLATE_SWATCHES = {
  paper: "linear-gradient(135deg, #f4f0e8 0%, #d9d1c2 55%, #445849 100%)",
  midnight: "linear-gradient(135deg, #171c35 0%, #30395f 55%, #a5b4fc 100%)",
  sunrise: "linear-gradient(135deg, #fff0df 0%, #ffd2ac 55%, #db754c 100%)",
} as const;

function generatedCover(title: string, progress: number) {
  const hue = Math.abs([...title].reduce((sum, char) => sum + char.charCodeAt(0), 0)) % 360;
  const safeTitle = title.slice(0, 8).replace(/[<&>"']/g, "");
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 300"><rect width="640" height="300" fill="hsl(${hue} 18% 94%)"/><circle cx="500" cy="120" r="82" fill="hsl(${hue} 35% 78%)"/><path d="M80 230 Q180 90 280 210 T470 180" fill="none" stroke="hsl(${hue} 30% 35%)" stroke-width="8"/><circle cx="250" cy="130" r="28" fill="hsl(${hue} 30% 35%)"/><text x="36" y="52" font-family="sans-serif" font-size="24" fill="hsl(${hue} 30% 25%)">${safeTitle}</text><rect x="36" y="260" width="568" height="10" rx="5" fill="#d8dadd"/><rect x="36" y="260" width="${568 * Math.max(0, Math.min(1, progress))}" height="10" rx="5" fill="hsl(${hue} 30% 35%)"/></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

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

/**
 * The learning rail remembers the node a learner is working on
 * (`learngraph:last-learned-nodes`). The graph page only reads it, so the
 * canvas can mark “我现在在哪” without touching the rail's state.
 */
const LAST_LEARNED_NODE_STORAGE_KEY = "learngraph:last-learned-nodes";
const GRAPH_TYPE_FILTER_STORAGE_KEY = "learngraph:graph-node-type-filter";

function rememberedLearningNodeId(graphId: string): string | undefined {
  try {
    const raw = window.sessionStorage.getItem(LAST_LEARNED_NODE_STORAGE_KEY);
    if (!raw) return undefined;
    const map = JSON.parse(raw) as Record<string, string>;
    return map[graphId];
  } catch {
    return undefined;
  }
}

/** Edge entry used by the node detail panel's relation lists. */
type NodeRelation = { node: GraphNode; relation: string };

/** Materialised learning status per node id (for the header/legend filters). */
type GraphNodeStatusView = {
  id: string;
  typeId: NodeTypeId;
  statusId: NodeLearningStatusId;
  statusLabel: string;
  blockedByPrerequisite: boolean;
};

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
    const masteryProgress = graph?.id === currentGraph?.id && currentGraph?.nodes.length
      ? currentGraph.nodes.filter((node) => node.mastery_stars >= 3).length / currentGraph.nodes.length
      : 0;
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
      needsReview: graph?.status === "candidate",
      masteryProgress,
      cover: graph?.cover_svg || generatedCover(book.title, masteryProgress),
      coverAiActive: graph?.cover_ai_active,
    };
  });
  const graphEntries = graphSummaries
    .filter((graph) => !representedGoalIds.has(graph.goal_id))
    .map((graph) => ({
      masteryProgress: graph.id === currentGraph?.id && currentGraph?.nodes.length
        ? currentGraph.nodes.filter((node) => node.mastery_stars >= 3).length / currentGraph.nodes.length
        : 0,
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
      needsReview: graph.status === "candidate",
      cover: graph.cover_svg || generatedCover(graph.title, graph.id === currentGraph?.id && currentGraph?.nodes.length ? currentGraph.nodes.filter((node) => node.mastery_stars >= 3).length / currentGraph.nodes.length : 0),
      coverAiActive: graph.cover_ai_active,
    }));
  return [...goalEntries, ...graphEntries];
}

function toWorkbenchKnowledgeGraph(
  graph: Graph,
  exploreCounts: Record<string, number> = {},
  blockedNodeIds: ReadonlySet<string> = new Set<string>(),
  planMarkers: ReadonlyMap<string, PlanNodeMarker> = new Map(),
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
      achievementScore: node.achievement_score,
      state: node.retrieval_state,
      evidence: node.evidence_state,
      focused: node.attention_state === "focused",
      nodeType: node.node_type,
      targetWeight: node.target_weight,
      exploreCount: exploreCounts[node.id] ?? 0,
      mastered: node.attention_state === "mastered",
      // 装饰标记：该节点的学习页（交互页）已经生成，卡片右上角挂一枚徽章。
      hasLearningPage: Boolean(node.has_learning_page),
      blockedByPrerequisite: blockedNodeIds.has(node.id),
      // Light secondary metadata only: the plan annotates the map, it never
      // restates the node's own learning status.
      planMarker: (() => {
        const marker = planMarkers.get(node.id);
        return marker
          ? {
              label: marker.label,
              tone: marker.tone,
              blocked: marker.blocked,
            }
          : undefined;
      })(),
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

function GraphCoverEditor({ book, onClose }: { book?: ShelfBook; onClose: () => void }) {
  const { workspaceId = "" } = useParams();
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: (payload: Parameters<typeof updateGraphCover>[1]) => updateGraphCover(book!.graphId!, payload),
    onSuccess: (view) => {
      void queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "graphs") });
      void queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "graph", book?.graphId) });
      // 后端一直在回传 used_default，但此前前端从不读它：降级封面（生成失败退回默认
      // 封面）会伪装成"成功"。这里如实说明，用户才知道封面为什么不是定制的。
      if (view.used_default) {
        toast.warning("本次未能生成定制封面，已使用默认封面。");
      } else {
        toast.success("封面已更新");
      }
      onClose();
    },
    onError: (error) => toast.error(error.message),
  });
  function upload(file?: File) {
    if (!file) return;
    if (!['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(file.type) || file.size > 2 * 1024 * 1024) {
      toast.error("请选择不超过 2 MB 的 PNG、JPG、WebP 或 GIF 图片");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => save.mutate({ mode: "image", image_data_url: String(reader.result) });
    reader.onerror = () => toast.error("图片读取失败，请重新选择");
    reader.readAsDataURL(file);
  }
  return (
    <Dialog open={Boolean(book)} onOpenChange={(open) => { if (!open && !save.isPending) onClose(); }}>
      <DialogContent className="max-h-[calc(100vh-2rem)] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>替换「{book?.title}」的封面</DialogTitle>
          <DialogDescription>选择模板、上传图片、按图谱内容绘制，或让模型生成一张。</DialogDescription>
        </DialogHeader>
        {book ? <img className="mx-auto max-h-36 w-auto rounded-lg object-contain" src={book.cover} onError={(event) => { event.currentTarget.src = generatedCover(book.title, book.masteryProgress); }} alt={`${book.title} 当前封面`} /> : null}
        <div className="grid grid-cols-4 gap-2">
          {([
            { id: "ancient", label: "古风", color: "text-white", image: "/graph-covers/ancient.jpg" },
            { id: "literature", label: "文学", color: "text-white", image: "/graph-covers/literature.jpg" },
            { id: "history", label: "历史", color: "text-white", image: "/graph-covers/history.jpg" },
            { id: "science", label: "理科", color: "text-white", image: "/graph-covers/science.jpg" },
            { id: "chemistry", label: "化学", color: "text-white", image: "/graph-covers/chemistry.jpg" },
            { id: "paper", label: "纸感", color: "text-foreground", image: "" },
            { id: "midnight", label: "星夜", color: "text-white", image: "" },
            { id: "sunrise", label: "晨光", color: "text-foreground", image: "" },
          ] as const).map((template) => (
              <Button key={template.id} className={`h-16 ${template.color} relative overflow-hidden border-0`} style={template.image ? { backgroundImage: `linear-gradient(180deg, transparent 20%, rgba(0,0,0,.65)), url(${template.image})`, backgroundSize: "cover", backgroundPosition: "center" } : { backgroundImage: TEMPLATE_SWATCHES[template.id] }} disabled={save.isPending} variant="outline" onClick={() => save.mutate({ mode: "template", template: template.id })}>
              {template.label}
            </Button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button disabled={save.isPending} onClick={() => save.mutate({ mode: "generated" })}><Sparkles className="size-4" />{save.isPending ? "处理中…" : "按图谱绘制"}</Button>
          <label className="inline-flex cursor-pointer items-center rounded-lg border px-3 py-2 text-sm">
            上传图片
            <input className="sr-only" type="file" accept="image/png,image/jpeg,image/webp,image/gif" disabled={save.isPending} onChange={(event) => { upload(event.target.files?.[0]); event.target.value = ""; }} />
          </label>
          <span className="text-xs text-muted-foreground">「按图谱绘制」是按标题、节点数量和学习进度用代码画的，不调用模型；上传最大 2 MB</span>
        </div>
        {book?.graphId ? <GraphCoverAIEditor busy={save.isPending} graphId={book.graphId} /> : null}
      </DialogContent>
    </Dialog>
  );
}

function GraphBookshelf({
  books,
  selectedId,
  onDelete,
  onOpen,
  onReview,
  onStartGoal,
  onStartLearning,
}: {
  books: ShelfBook[];
  selectedId?: string;
  onDelete: (book: ShelfBook) => void;
  onOpen: (book: ShelfBook) => void;
  onReview: (book: ShelfBook) => void;
  onStartGoal: () => void;
  onStartLearning: (book: ShelfBook) => void;
}) {
  const [view, setView] = useState<"shelf" | "constellation">("shelf");
  const [coverBook, setCoverBook] = useState<ShelfBook>();
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

  function handleConstellationKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.target !== event.currentTarget) return;
    const step = event.shiftKey ? 48 : 24;
    switch (event.key) {
      case "ArrowLeft":
        event.preventDefault();
        setConstellation((current) => ({ ...current, x: current.x - step }));
        break;
      case "ArrowRight":
        event.preventDefault();
        setConstellation((current) => ({ ...current, x: current.x + step }));
        break;
      case "ArrowUp":
        event.preventDefault();
        setConstellation((current) => ({ ...current, y: current.y - step }));
        break;
      case "ArrowDown":
        event.preventDefault();
        setConstellation((current) => ({ ...current, y: current.y + step }));
        break;
      case "+":
      case "=":
        event.preventDefault();
        adjustScale(0.15);
        break;
      case "-":
      case "_":
        event.preventDefault();
        adjustScale(-0.15);
        break;
      case "0":
        event.preventDefault();
        setConstellation({ scale: 1, x: 0, y: 0 });
        break;
    }
  }

  return (
    <section className="graph-library" aria-label="图谱书架">
      <GraphCoverEditor book={coverBook} onClose={() => setCoverBook(undefined)} />
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
                  <span className="graph-library__spine" style={{ backgroundImage: `url("${entry.cover}"), url("${generatedCover(entry.title, entry.masteryProgress)}")`, backgroundSize: "cover", backgroundPosition: "center" }}>
                    <Icon />
                    <span>{entry.title.slice(0, 1)}</span>
                  </span>
                  <span className="graph-library__book-copy">
                    <strong>{entry.title}</strong>
                    <small>{entry.summary}</small>
                  </span>
                  <span className="graph-library__book-meta">
                    <b>{entry.status}</b>
                    {entry.coverAiActive && entry.graphId ? <GraphCoverAIBadge graphId={entry.graphId} /> : null}
                    <small>{entry.progress}</small>
                    <span aria-label={`掌握进度 ${Math.round(entry.masteryProgress * 100)}%`} className="mt-1 block h-1.5 w-24 overflow-hidden rounded-full bg-muted"><span className="block h-full rounded-full bg-primary" style={{ width: `${Math.round(entry.masteryProgress * 100)}%` }} /></span>
                  </span>
                </button>
                <div className="graph-library__book-actions">
                  {entry.graphId ? (
                    <Button onClick={() => setCoverBook(entry)} size="xs" variant="outline">
                      封面
                    </Button>
                  ) : null}
                  {entry.needsReview && entry.graphId ? (
                    <Button
                      onClick={() => onReview(entry)}
                      size="xs"
                      variant="secondary"
                    >
                      <ShieldCheck className="size-3.5" />
                      去审核
                    </Button>
                  ) : (
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
                  )}
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
              拖动画布 · 方向键平移
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
            {selectedBook?.needsReview && selectedBook.graphId ? (
              <Button
                aria-label={`审核图谱书架条目 ${selectedBook.title}`}
                onClick={() => onReview(selectedBook)}
                size="icon-sm"
                title={`审核「${selectedBook.title}」并发布`}
                variant="secondary"
              >
                <ShieldCheck />
              </Button>
            ) : null}
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
            aria-keyshortcuts="ArrowLeft ArrowRight ArrowUp ArrowDown + - 0"
            aria-label="以正在学习图谱为核心的分布视图"
            onKeyDown={handleConstellationKeyDown}
            onPointerCancel={endDrag}
            onPointerDown={startDrag}
            onPointerMove={moveDrag}
            onPointerUp={endDrag}
            onWheel={(event) => {
              event.preventDefault();
              adjustScale(event.deltaY > 0 ? -0.08 : 0.08);
            }}
            tabIndex={0}
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

/**
 * Desktop docks the node detail next to the canvas (no overlay, canvas stays
 * interactive); narrow viewports keep the existing drawer. One information
 * architecture, two containers.
 */
function useDockedInspector() {
  const [docked, setDocked] = useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia("(min-width: 1024px)").matches,
  );
  useEffect(() => {
    const query = window.matchMedia("(min-width: 1024px)");
    const handle = () => setDocked(query.matches);
    query.addEventListener("change", handle);
    return () => query.removeEventListener("change", handle);
  }, []);
  return docked;
}

/** Small type chip shared by search results and relation rows. */
function NodeTypeChip({ typeId }: { typeId: NodeTypeId }) {
  const profile = NODE_TYPE_PROFILES[typeId];
  const Icon = profile.icon;
  return (
    <span className={`graph-type-chip is-${profile.tone}`} title={profile.hint}>
      <Icon aria-hidden="true" />
      {profile.label}
    </span>
  );
}

export function GraphWorkspacePage() {
  const { graphId = "", workspaceId = "" } = useParams();
  const dockedInspector = useDockedInspector();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  // graphId 为空时只展示书架，不自动打开任何图谱画布。
  const hasOpenedGraph = Boolean(graphId);
  const graph = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "graph", graphId),
    queryFn: () => getGraph(graphId),
    enabled: hasOpenedGraph,
  });
  const graphs = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "graphs"),
    queryFn: listGraphs,
  });
  const goals = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "goals"),
    queryFn: listGoals,
  });
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
    return [
      "all",
      "unlearned",
      "learning",
      "mastered",
      "due",
      "locked",
      "focused",
    ].includes(saved ?? "")
      ? saved!
      : "all";
  });
  const [nodeTypeFilter, setNodeTypeFilter] = useState<"all" | NodeTypeId>(() => {
    const saved = window.localStorage.getItem(GRAPH_TYPE_FILTER_STORAGE_KEY);
    return NODE_TYPE_ORDER.includes(saved as NodeTypeId)
      ? (saved as NodeTypeId)
      : "all";
  });
  /** Cursor for Enter-to-jump through search matches. */
  const [searchCursor, setSearchCursor] = useState(0);
  const [searchOpen, setSearchOpen] = useState(false);
  const canvasApiRef = useRef<KnowledgeGraphCanvasApi | undefined>(
    undefined,
  );
  const handleCanvasApi = useCallback((api: KnowledgeGraphCanvasApi) => {
    canvasApiRef.current = api;
  }, []);
  const [depthLimit, setDepthLimit] = useState(Number.MAX_SAFE_INTEGER);
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
  /**
   * One right-hand surface at a time. `node` shows the knowledge-node detail,
   * `plan` shows the learning plan (the old standalone 路线 page folded in).
   */
  const [inspectorMode, setInspectorMode] = useState<"node" | "plan">("node");
  const [showPlanPath, setShowPlanPath] = useState(false);
  const [addToPlanNode, setAddToPlanNode] = useState<GraphNode>();
  const [schedulePlanItem, setSchedulePlanItem] = useState<PlanItemView>();
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
  useEffect(() => {
    window.localStorage.setItem(
      GRAPH_TYPE_FILTER_STORAGE_KEY,
      nodeTypeFilter,
    );
  }, [nodeTypeFilter]);
  const openedGraph = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "graph", activeGraphId),
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

  /**
   * Learning plan for the opened graph. The plan is a projection of the
   * graph's goal roadmap; it owns the time/action layer while the canvas keeps
   * owning knowledge structure and learning state.
   */
  const activeGoal = useMemo(
    () => (goals.data ?? []).find((goal) => goal.id === effectiveGraph?.goal_id),
    [effectiveGraph?.goal_id, goals.data],
  );
  const plan = useGraphPlanController({
    workspaceId,
    goal: activeGoal,
    graph: effectiveGraph,
  });
  const planItemsByNode = useMemo(() => {
    const map = new Map<string, PlanItemView[]>();
    plan.model.items.forEach((item) => {
      if (!item.nodeId) return;
      const list = map.get(item.nodeId) ?? [];
      list.push(item);
      map.set(item.nodeId, list);
    });
    return map;
  }, [plan.model.items]);
  /** Optional execution-order overlay; off by default so the map stays a map. */
  const planPathEdges = useMemo(
    () => (showPlanPath ? buildPlanPathEdges(plan.model.sequence) : []),
    [plan.model.sequence, showPlanPath],
  );
  const planPanelOpen = inspectorMode === "plan";

  // Preload explore counts for selected node; canvas shows 0 until inspector
  // loads (avoids N parallel questions calls for every card on large graphs).
  // Cache counts across selection so interactive learning updates stick.
  const selectedExplore = useNodeExploreRounds(
    workspaceId,
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

  /**
   * Availability (未解锁) comes from the same prerequisite gate the backend
   * workflow uses: a node whose prerequisite has no verified evidence yet is
   * flagged, and the UI then leads with “查看前置知识” instead of a learn CTA.
   */
  const blockedNodeIds = useMemo(() => {
    const blocked = new Set<string>();
    if (!effectiveGraph) return blocked;
    const byId = new Map(effectiveGraph.nodes.map((node) => [node.id, node]));
    for (const edge of effectiveGraph.edges) {
      if (edge.relation !== "prerequisite") continue;
      const source = byId.get(edge.source_node_id);
      if (!source || !byId.has(edge.target_node_id)) continue;
      if (!isPrerequisiteSatisfied(source)) blocked.add(edge.target_node_id);
    }
    return blocked;
  }, [effectiveGraph]);

  const workbenchGraph = useMemo(
    () =>
      effectiveGraph
        ? toWorkbenchKnowledgeGraph(
            effectiveGraph,
            exploreCounts,
            blockedNodeIds,
            plan.model.nodeMarkers,
          )
        : null,
    [blockedNodeIds, effectiveGraph, exploreCounts, plan.model.nodeMarkers],
  );

  /** Per-node presentation facts (type + learning status) for header/legend. */
  const nodeStatusViews = useMemo(() => {
    const map = new Map<string, GraphNodeStatusView>();
    effectiveGraph?.nodes.forEach((node) => {
      const profile = resolveNodeTypeProfile(node.node_type, {
        root: node.node_type === "root",
      });
      const blockedByPrerequisite = blockedNodeIds.has(node.id);
      const status = resolveLearningStatus({
        stars: node.mastery_stars,
        retrievalState: node.retrieval_state,
        attentionState: node.attention_state,
        blockedByPrerequisite,
      });
      map.set(node.id, {
        id: node.id,
        typeId: profile.id,
        statusId: status.id,
        statusLabel: status.label,
        blockedByPrerequisite,
      });
    });
    return map;
  }, [blockedNodeIds, effectiveGraph]);

  /**
   * 已生成学习页（交互页）的节点数。图例据此说明卡片右上角那枚装饰徽章，
   * 与类型 / 状态一样，缺省时保留词条但置灰。
   */
  const nodePageCount = useMemo(
    () =>
      (effectiveGraph?.nodes ?? []).filter((node) => node.has_learning_page)
        .length,
    [effectiveGraph],
  );

  /** Types actually present in this graph — the legend marks the rest as unused. */
  const nodeTypeCounts = useMemo(() => {
    const counts = new Map<NodeTypeId, number>();
    nodeStatusViews.forEach((view) => {
      counts.set(view.typeId, (counts.get(view.typeId) ?? 0) + 1);
    });
    return counts;
  }, [nodeStatusViews]);

  const learningProgress = useMemo(() => {
    const nodes =
      effectiveGraph?.nodes.filter((node) => node.node_type !== "root") ?? [];
    if (!nodes.length) return { learned: 0, total: 0, percent: 0 };
    const learned = nodes.filter((node) => (node.mastery_stars ?? 0) >= 1).length;
    return {
      learned,
      total: nodes.length,
      percent: Math.round((learned / nodes.length) * 100),
    };
  }, [effectiveGraph]);

  /** “我正在学什么” — the node the learning rail last worked on. */
  const currentLearningId = useMemo(() => {
    if (!effectiveGraph) return undefined;
    const remembered = rememberedLearningNodeId(effectiveGraph.id);
    if (remembered && effectiveGraph.nodes.some((node) => node.id === remembered))
      return remembered;
    const focused = effectiveGraph.nodes.find(
      (node) => node.attention_state === "focused",
    );
    if (focused) return focused.id;
    const rootId = effectiveGraph.nodes.find(
      (node) => node.node_type === "root",
    )?.id;
    const firstChildId = effectiveGraph.edges.find(
      (edge) =>
        edge.source_node_id === rootId &&
        (edge.relation === "contains" || edge.relation === "prerequisite"),
    )?.target_node_id;
    if (firstChildId) return firstChildId;
    return effectiveGraph.nodes.find((node) => node.node_type !== "root")?.id;
  }, [effectiveGraph]);

  const filteredWorkbenchGraph = useMemo(() => {
    if (!workbenchGraph || !effectiveGraph) return workbenchGraph;
    if (nodeStateFilter === "all" && nodeTypeFilter === "all") return workbenchGraph;
    const matchedIds = new Set(
      effectiveGraph.nodes
        .filter((node) => {
          const matchesState =
            nodeStateFilter === "all" ||
            nodeStatusViews.get(node.id)?.statusId === nodeStateFilter ||
            (nodeStateFilter === "focused" &&
              node.attention_state === "focused");
          const matchesType =
            nodeTypeFilter === "all" ||
            nodeStatusViews.get(node.id)?.typeId === nodeTypeFilter;
          return matchesState && matchesType;
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
  }, [
    effectiveGraph,
    nodeStateFilter,
    nodeStatusViews,
    nodeTypeFilter,
    workbenchGraph,
  ]);

  /**
   * Search highlights instead of hiding: matches ring up on the canvas, and the
   * result list underneath the box jumps straight to a node.
   */
  const searchMatches = useMemo(() => {
    const query = nodeSearch.trim().toLocaleLowerCase();
    if (!query || !effectiveGraph) return [];
    return effectiveGraph.nodes.filter(
      (node) =>
        node.label.toLocaleLowerCase().includes(query) ||
        node.description.toLocaleLowerCase().includes(query),
    );
  }, [effectiveGraph, nodeSearch]);

  const searchMatchIds = useMemo(
    () => searchMatches.map((node) => node.id),
    [searchMatches],
  );

  /** Tree depth per node, so the detail panel can show the same L2 metadata. */
  const nodeDepthById = useMemo(() => {
    if (!workbenchGraph) return new Map<string, number>();
    return getKnowledgeGraphTreeDepths(workbenchGraph.nodes, workbenchGraph.edges);
  }, [workbenchGraph]);

  const nodeStatusCounts = useMemo(() => {
    const counts = new Map<NodeLearningStatusId, number>();
    nodeStatusViews.forEach((view) => {
      counts.set(view.statusId, (counts.get(view.statusId) ?? 0) + 1);
    });
    return counts;
  }, [nodeStatusViews]);

  /** Upstream / downstream / cross relations of the selected node. */
  const selectedNodeRelations = useMemo(() => {
    const empty = { upstream: [], downstream: [], related: [] } as {
      upstream: NodeRelation[];
      downstream: NodeRelation[];
      related: NodeRelation[];
    };
    if (!effectiveGraph || !selectedNode) return empty;
    const byId = new Map(effectiveGraph.nodes.map((node) => [node.id, node]));
    const upstream: NodeRelation[] = [];
    const downstream: NodeRelation[] = [];
    const related: NodeRelation[] = [];
    for (const edge of effectiveGraph.edges) {
      if (
        edge.source_node_id !== selectedNode.id &&
        edge.target_node_id !== selectedNode.id
      )
        continue;
      const outgoing = edge.source_node_id === selectedNode.id;
      const other = byId.get(
        outgoing ? edge.target_node_id : edge.source_node_id,
      );
      if (!other) continue;
      const entry = { node: other, relation: edge.relation };
      if (edge.relation === "contains" || edge.relation === "prerequisite") {
        (outgoing ? downstream : upstream).push(entry);
      } else {
        related.push(entry);
      }
    }
    return { upstream, downstream, related };
  }, [effectiveGraph, selectedNode]);
  const maximumDepth = workbenchGraph
    ? getKnowledgeGraphTreeDepth(workbenchGraph.nodes, workbenchGraph.edges)
    : 0;
  // Clamp so the initial "show all" sentinel never flashes as a huge fraction.
  const effectiveDepthLimit = Math.min(depthLimit, maximumDepth);
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
          : // Default anchor: the node the learner is working on (「我正在学什么」),
            // falling back to the goal root only when nothing is known yet.
            (currentLearningId ?? graphRootId),
    );
  }, [currentLearningId, effectiveGraph?.nodes, graphRootId, requestedNodeId]);

  useEffect(() => {
    if (requestedNodeId) setInspectorOpen(true);
  }, [requestedNodeId]);

  /**
   * `?panel=plan` deep link: the folded-in learning plan (also what the legacy
   * 路线 URL redirects to). Node detail stays the default panel otherwise.
   */
  const requestedPanel = searchParams.get("panel");
  useEffect(() => {
    if (requestedPanel === "plan") {
      setInspectorMode("plan");
      setInspectorOpen(false);
    } else if (!requestedPanel) {
      setInspectorMode((current) => (current === "plan" ? "node" : current));
    }
  }, [requestedPanel]);

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
      // Default: show the full tree when a graph first loads or becomes available.
      if (graphChanged || graphBecameAvailable) {
        return maximumDepth;
      }
      return Math.max(0, Math.min(current, maximumDepth));
    });
  }, [activeGraphId, maximumDepth, workbenchGraph]);

  // 回到书架视图时清空已记录的深度状态：之后从书架打开任意一本图谱，
  // 都视为“首次打开”并恢复默认全展开（否则上次手动收起的层级会残留）。
  useEffect(() => {
    if (activeGraphId) return;
    depthStateRef.current = { graphId: undefined, maximumDepth: 0 };
  }, [activeGraphId]);

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
        queryKey: workspaceQueryKey(workspaceId, "graph", activeGraphId),
      });
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "mastery"),
      });
    },
    onError: async (error) => {
      if (
        error instanceof ApiError &&
        error.code === "graph_revision_conflict"
      ) {
        setEditingNode(false);
        await queryClient.invalidateQueries({
          queryKey: workspaceQueryKey(workspaceId, "graph", activeGraphId),
        });
        toast.warning(
          "图谱已由其他编辑更新，已刷新到最新修订，请重新确认。",
        );
        return;
      }
      toast.error(error.message);
      await queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "graph", activeGraphId),
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
      queryClient.setQueryData<Graph>(
        workspaceQueryKey(workspaceId, "graph", activeGraphId), (current) =>
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
        queryKey: workspaceQueryKey(workspaceId, "graph", activeGraphId),
      });
    },
    onError: (error) => {
      toast.error(error.message);
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "graph", activeGraphId),
      });
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
        queryClient.invalidateQueries({
          queryKey: workspaceQueryKey(workspaceId, "goals"),
        }),
        queryClient.invalidateQueries({
          queryKey: workspaceQueryKey(workspaceId, "graphs"),
        }),
        queryClient.invalidateQueries({
          queryKey: workspaceQueryKey(workspaceId, "projects"),
        }),
        queryClient.invalidateQueries({
          queryKey: workspaceQueryKey(workspaceId, "sessions"),
        }),
        queryClient.invalidateQueries({
          queryKey: workspaceQueryKey(workspaceId, "dashboard"),
        }),
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

  /** 候选图谱直达审核发布页：避免后台创建的图谱卡在待审核无法学习。 */
  function reviewGraph(goalId: string, graphId: string) {
    navigate(
      `${base}/goals/${encodeURIComponent(goalId)}/graph-review?graph=${encodeURIComponent(graphId)}`,
    );
  }

  function reviewBook(book: ShelfBook) {
    if (!book.graphId) return;
    reviewGraph(book.goalId, book.graphId);
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
          onReview={reviewBook}
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

  /** Shared "start studying this node" entry (node card, plan item, inspector). */
  function startNodeLearning(node: { id: string; label: string }) {
    if (!activeGraph) return;
    window.dispatchEvent(
      new CustomEvent("learngraph:open-learning-project", {
        detail: {
          graphId: activeGraph.id,
          title: activeGraph.title,
          nodeId: node.id,
          nodeLabel: node.label,
          learningPackage: true,
          prompt: `请围绕学习节点「${node.label}」生成图文并茂的学习内容，并按知识点逐段呈现。`,
        },
      }),
    );
  }

  function studyFromNode(node: KnowledgeNode["data"] & { id: string }) {
    startNodeLearning({ id: node.id, label: node.label });
  }

  /**
   * Learning, experiments and checkpoints share the versioned node page.
   * Spaced review retains its existing practice-session workflow.
   */
  function startPlanItem(item: PlanItemView) {
    if (item.routing.kind !== "review" && item.nodeId) {
      const tab = item.routing.kind === "assessment" ? "exam" : item.routing.kind === "practice" ? "lab" : "lesson";
      if (tab === "exam") {
        // Exam answers and grading remain in the dedicated, durable attempt
        // page; the lesson and lab stay in the conversational canvas.
        navigate(`${base}/learn/nodes/${item.nodeId}?tab=${tab}`);
      } else {
        window.dispatchEvent(
          new CustomEvent("learngraph:open-learning-project", {
            detail: {
              graphId: item.graphId ?? activeGraph?.id,
              title: activeGraph?.title ?? item.title,
              nodeId: item.nodeId,
              nodeLabel: item.title,
              learningPackage: true,
              prompt: `请围绕学习节点「${item.title}」生成图文并茂的学习内容，并按知识点逐段呈现。`,
            },
          }),
        );
      }
      return;
    }
    void startPlanPracticeSession(item)
      .then((sessionId) =>
        navigate(`${base}/practice/session/${sessionId}`),
      )
      .catch((error: Error) => {
        toast.error(error.message);
        navigate(`${base}/practice`);
      });
  }

  function openPlanPanel() {
    setInspectorMode("plan");
    setInspectorOpen(false);
    const next = new URLSearchParams(searchParams);
    next.set("panel", "plan");
    setSearchParams(next, { replace: true });
    // An empty plan should offer the one action that fixes it.
    if (plan.state === "empty" && !plan.generating) plan.generate();
  }

  function closePlanPanel() {
    setInspectorMode("node");
    const next = new URLSearchParams(searchParams);
    next.delete("panel");
    setSearchParams(next, { replace: true });
  }

  function selectWorkbenchNode(node: KnowledgeNode["data"] & { id: string }) {
    setSelectedNodeId(node.id);
    setInspectorOpen(true);
    // Clicking a node asks for node detail; the plan stays reachable from the
    // header button (only one right-hand panel is open at a time).
    setInspectorMode("node");
    const next = new URLSearchParams(searchParams);
    next.set("node", node.id);
    next.delete("panel");
    setSearchParams(next, { replace: true });
  }

  /** Locate a plan item's node without stealing focus from the plan panel. */
  function locatePlanNode(nodeId: string) {
    setSelectedNodeId(nodeId);
    locateNode(nodeId, true);
    const next = new URLSearchParams(searchParams);
    next.set("node", nodeId);
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

  /** Camera-only focus: never a mode change, just a deliberate framing. */
  function locateNode(nodeId: string, neighbors = true) {
    canvasApiRef.current?.focusNode(nodeId, { neighbors });
  }

  /** Search / relation jumps: select the node, then frame it with relations. */
  function jumpToNode(nodeId: string) {
    setSelectedNodeId(nodeId);
    setInspectorOpen(true);
    const next = new URLSearchParams(searchParams);
    next.set("node", nodeId);
    setSearchParams(next, { replace: true });
    locateNode(nodeId, true);
  }

  /** Locked nodes lead with their prerequisites instead of a learn CTA. */
  function openFirstPrerequisite() {
    if (!selectedNode) return;
    const blocker = selectedNodeRelations.upstream.find(
      (entry) =>
        entry.relation === "prerequisite" && !isPrerequisiteSatisfied(entry.node),
    );
    if (blocker) jumpToNode(blocker.node.id);
  }

  const selectedStatusView = selectedNode
    ? nodeStatusViews.get(selectedNode.id)
    : undefined;
  const selectedLearningStatus = selectedNode
    ? resolveLearningStatus({
        stars: selectedNode.mastery_stars,
        retrievalState: selectedNode.retrieval_state,
        attentionState: selectedNode.attention_state,
        blockedByPrerequisite: Boolean(selectedStatusView?.blockedByPrerequisite),
      })
    : undefined;

  const nodeInspector = (
    <GraphNodeInspector
      currentLearning={selectedNode?.id === currentLearningId}
      draft={nodeDraft}
      editMode={editMode}
      editable={editMode}
      exploreOpen={explorePanelOpen}
      exploreRounds={selectedExplore.data ?? []}
      exploreLoading={selectedExplore.isPending}
      focusBusy={focus.isPending}
      focusEditable={focusEditable}
      levelLabel={graphLevelLabel(
        selectedNode ? (nodeDepthById.get(selectedNode.id) ?? 0) : 0,
      )}
      masteryBusy={focus.isPending}
      node={selectedNode}
      onChange={setNodeDraft}
      onEdit={() => {
        setEditMode(true);
        setEditingNode(true);
      }}
      onFocus={focusSelectedNode}
      onLocate={() => selectedNode && locateNode(selectedNode.id, true)}
      onLocateRelation={(nodeId) => jumpToNode(nodeId)}
      onMastery={masterySelectedNode}
      onOpenPrerequisite={openFirstPrerequisite}
      onSplit={splitSelectedNode}
      onLearn={() =>
        selectedNode && studyFromNode({ ...selectedNode, id: selectedNode.id })
      }
      onOpenExplore={() => setExplorePanelOpen(true)}
      onCloseExplore={() => setExplorePanelOpen(false)}
      onSave={saveSelectedNode}
      schedule={
        selectedNode
          ? {
              items: planItemsByNode.get(selectedNode.id) ?? [],
              canAdd: Boolean(plan.roadmap),
              onAdd: () => setAddToPlanNode(selectedNode),
              onAdjust: (item) => setSchedulePlanItem(item),
              onStart: startPlanItem,
              onOpenPlan: openPlanPanel,
            }
          : undefined
      }
      onStopEditing={() => {
        if (selectedNode)
          setNodeDraft({
            label: selectedNode.label,
            description: selectedNode.description,
            targetWeight: selectedNode.target_weight,
          });
        setEditingNode(false);
      }}
      relations={selectedNodeRelations}
      saving={updateNode.isPending}
      statusLabel={selectedLearningStatus?.label}
      statusId={selectedLearningStatus?.id}
      typeLabel={
        selectedNode
          ? resolveNodeTypeProfile(selectedNode.node_type, {
              root: selectedNode.node_type === "root",
            }).label
          : undefined
      }
      editing={editingNode && editMode}
    />
  );

  const planInspector = (
    <GraphPlanInspector
      busy={plan.scheduling || plan.adding}
      controller={plan}
      onClose={closePlanPanel}
      onLocateNode={locatePlanNode}
      onStartLearn={startPlanItem}
      onStartPractice={startPlanItem}
      onTogglePath={setShowPlanPath}
      showPath={showPlanPath}
    />
  );
  const planButtonLabel =
    plan.state === "no-goal"
      ? "学习计划"
      : plan.state === "empty"
        ? "生成学习计划"
        : plan.state === "ready"
          ? `学习计划 · ${plan.model.days.length} 天`
          : "学习计划";

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
              onReview={reviewBook}
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
            <div className="graph-workbench-heading__title">
              <Select
                onValueChange={(bookId) => {
                  const book = books.find((item) => item.id === bookId);
                  if (book) openBook(book);
                }}
                value={selectedBook?.id}
              >
                <SelectTrigger
                  aria-label="选择学习图谱"
                  className="graph-workbench-book-select"
                >
                  <Network className="size-4" />
                  <SelectValue placeholder="选择学习图谱">
                    {selectedBook?.title ?? "选择学习图谱"}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {books.map((book) => (
                    <SelectItem key={book.id} value={book.id}>
                      {book.title} · {book.status}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {activeGraph ? (
                <span
                  className={cn(
                    "graph-status-pill",
                    activeGraph.status === "published"
                      ? "is-live"
                      : activeGraph.status === "candidate"
                        ? "is-candidate"
                        : "is-draft",
                  )}
                >
                  {activeGraph.status === "published"
                    ? "正在学习"
                    : activeGraph.status === "candidate"
                      ? "待审核"
                      : "草稿"}
                </span>
              ) : null}
            </div>
            <div className="graph-workbench-heading__meta">
              {selectedBook?.isGoalBook ? (
                <span>完成目标澄清后生成图谱。</span>
              ) : activeGraph ? (
                <>
                  <span className="graph-workbench-heading__stats">
                    {activeGraph.nodes.length} 个节点 · 已学习{" "}
                    {learningProgress.percent}%
                  </span>
                  <Progress
                    aria-label={`已学习 ${learningProgress.percent}%`}
                    className="graph-workbench-heading__progress"
                    value={learningProgress.percent}
                  />
                  <span className="graph-workbench-heading__hint">
                    点击节点查看详情，或直接从节点开始学习
                  </span>
                </>
              ) : (
                <span>选择图谱后开始学习。</span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {activeGraph?.status === "published" && <LearningBuildSettings graphId={activeGraph.id} />}
            {activeGraph ? (
              <Button
                aria-pressed={planPanelOpen}
                onClick={() =>
                  planPanelOpen ? closePlanPanel() : openPlanPanel()
                }
                size="sm"
                title="学习计划：图谱上的时间与行动安排"
                variant={planPanelOpen ? "secondary" : "outline"}
              >
                <CalendarDays className="size-4" />
                {planButtonLabel}
              </Button>
            ) : null}
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
              <DropdownMenuContent align="end" className="w-56">
                {activeGraph && !selectedBook?.isGoalBook ? (
                  <DropdownMenuItem
                    onSelect={() => startBookLearning(books.find((book) => book.graphId === activeGraph.id) ?? {
                      id: `graph-${activeGraph.id}`,
                      goalId: activeGraph.goal_id,
                      graphId: activeGraph.id,
                      title: activeGraph.title,
                      summary: "",
                      status: activeGraph.status,
                      progress: "",
                      icon: Database,
                      isGoalBook: false,
                      needsReview: false,
                      masteryProgress: 0,
                      cover: generatedCover(activeGraph.title, 0),
                    })}
                  >
                    <BookOpen className="size-4" />
                    开始学习此图谱
                  </DropdownMenuItem>
                ) : null}
                <DropdownMenuItem onSelect={() => navigate(`${base}/capabilities`)}>
                  <Brain className="size-4" />
                  能力成长视图
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={returnToBookshelf}>
                  <BookOpen className="size-4" />
                  返回图谱书架
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => setLibraryOpen(true)}>
                  <LayoutGrid className="size-4" />
                  快速切换图谱
                </DropdownMenuItem>
                {activeGraph?.status === "candidate" ? (
                  <DropdownMenuItem
                    onSelect={() =>
                      reviewGraph(activeGraph.goal_id, activeGraph.id)
                    }
                  >
                    <ShieldCheck className="size-4" />
                    审核待发布图谱
                  </DropdownMenuItem>
                ) : null}
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
            ) : activeGraph?.status === "candidate" ? (
              <Button
                onClick={() => reviewGraph(activeGraph.goal_id, activeGraph.id)}
                size="sm"
              >
                <ShieldCheck className="size-4" />
                去审核
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
          <div
            className={cn(
              "graph-workbench-canvas__body",
              dockedInspector && (planPanelOpen || (inspectorOpen && selectedNode))
                ? "graph-workbench-canvas__body--inspector"
                : "graph-workbench-canvas__body--full",
            )}
          >
            <div className="graph-workbench-canvas__main">
              <div className="graph-workbench-controls" aria-label="图谱视图控制">
                <div className="graph-workbench-search">
                  <Search className="size-3.5" />
                  <Input
                    aria-label="搜索图谱节点"
                    onBlur={() =>
                      window.setTimeout(() => setSearchOpen(false), 140)
                    }
                    onChange={(event) => {
                      setNodeSearch(event.target.value);
                      setSearchCursor(0);
                      setSearchOpen(true);
                    }}
                    onFocus={() => setSearchOpen(true)}
                    onKeyDown={(event) => {
                      if (event.key === "Escape") {
                        setNodeSearch("");
                        setSearchOpen(false);
                        return;
                      }
                      if (event.key === "Enter" && searchMatches.length) {
                        const match =
                          searchMatches[
                            Math.min(searchCursor, searchMatches.length - 1)
                          ];
                        setSearchCursor(
                          (current) => (current + 1) % searchMatches.length,
                        );
                        if (match) jumpToNode(match.id);
                      }
                    }}
                    placeholder="搜索节点或定义"
                    value={nodeSearch}
                  />
                  {searchOpen && nodeSearch.trim() ? (
                    <div
                      aria-label="搜索结果"
                      className="graph-search-results"
                      role="listbox"
                    >
                      {searchMatches.length ? (
                        <>
                          <p className="graph-search-results__count">
                             找到 {searchMatches.length} 个节点 · Enter 逐个定位
                          </p>
                          <div className="graph-search-results__list">
                            {searchMatches.slice(0, 8).map((match) => {
                              const view = nodeStatusViews.get(match.id);
                              return (
                                <button
                                  className="graph-search-result"
                                  key={match.id}
                                  onClick={() => jumpToNode(match.id)}
                                  type="button"
                                >
                                  <span className="graph-search-result__label">
                                    {match.label}
                                  </span>
                                  <span className="graph-search-result__meta">
                                    <NodeTypeChip
                                      typeId={
                                        view?.typeId ??
                                        resolveNodeTypeProfile(match.node_type, {
                                          root: match.node_type === "root",
                                        }).id
                                      }
                                    />
                                    <span
                                      className={cn(
                                        "graph-search-result__status",
                                        `is-${view?.statusId ?? "unlearned"}`,
                                      )}
                                    >
                                      {view?.statusLabel ?? "未学习"}
                                    </span>
                                  </span>
                                </button>
                              );
                            })}
                          </div>
                          {searchMatches.length > 8 ? (
                            <p className="graph-search-results__more">
                              还有 {searchMatches.length - 8} 个结果，继续输入可缩小范围
                            </p>
                          ) : null}
                        </>
                      ) : (
                        <p className="graph-search-results__empty">
                          没有匹配的节点
                        </p>
                      )}
                    </div>
                  ) : null}
                </div>
                <Select
                  onValueChange={setNodeStateFilter}
                  value={nodeStateFilter}
                >
                  <SelectTrigger aria-label="筛选节点状态" className="w-32">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部状态</SelectItem>
                    <SelectItem value="unlearned">未学习</SelectItem>
                    <SelectItem value="learning">学习中</SelectItem>
                    <SelectItem value="mastered">已掌握</SelectItem>
                    <SelectItem value="due">待复习</SelectItem>
                    <SelectItem value="locked">未解锁</SelectItem>
                    <SelectItem value="focused">重点节点</SelectItem>
                  </SelectContent>
                </Select>
                <Select
                  onValueChange={(value) =>
                    setNodeTypeFilter(value as "all" | NodeTypeId)
                  }
                  value={nodeTypeFilter}
                >
                  <SelectTrigger aria-label="筛选节点类型" className="w-32">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部类型</SelectItem>
                    {NODE_TYPE_ORDER.filter((id) => nodeTypeCounts.has(id)).map(
                      (id) => (
                        <SelectItem key={id} value={id}>
                          {NODE_TYPE_PROFILES[id].label} · {nodeTypeCounts.get(id)}
                        </SelectItem>
                      ),
                    )}
                  </SelectContent>
                </Select>
                <div
                  aria-label="展开层级"
                  className="graph-depth-control"
                  role="group"
                >
                  <Button
                    aria-label="减少展开层级"
                    disabled={effectiveDepthLimit <= 0}
                    onClick={() =>
                      setDepthLimit(Math.max(0, effectiveDepthLimit - 1))
                    }
                    size="icon-xs"
                    title="收起一层"
                    variant="ghost"
                  >
                    <Minus />
                  </Button>
                  <span title="当前展开的图谱层级">
                    <Layers className="size-3" />
                    L{effectiveDepthLimit}/{maximumDepth}
                  </span>
                  <Button
                    aria-label="增加展开层级"
                    disabled={effectiveDepthLimit >= maximumDepth}
                    onClick={() =>
                      setDepthLimit(
                        Math.min(maximumDepth, effectiveDepthLimit + 1),
                      )
                    }
                    size="icon-xs"
                    title="展开一层"
                    variant="ghost"
                  >
                    <Plus />
                  </Button>
                </div>
                <Button
                  aria-pressed={editMode}
                  className="graph-toolbar-label"
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
                  <span>{editMode ? "编辑中" : "编辑模式"}</span>
                </Button>
                <Button
                  aria-label={multiSelect ? "退出多选" : "进入多选"}
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
                  size="icon-sm"
                  title={multiSelect ? "退出多选" : "多选多个节点做联合学习"}
                  variant={multiSelect ? "secondary" : "outline"}
                >
                  <MousePointer2 className="size-3.5" />
                </Button>
              </div>
              <div className="graph-workbench-canvas__graph">
                <GraphLegend
                  className="graph-legend--floating"
                  pageCount={nodePageCount}
                  statusCounts={nodeStatusCounts}
                  typeCounts={nodeTypeCounts}
                />
                {activeGraph.nodes.length === 1 &&
                !nodeSearch.trim() &&
                nodeStateFilter === "all" &&
                nodeTypeFilter === "all" ? (
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
                    currentId={currentLearningId}
                    edges={filteredWorkbenchGraph.edges}
                    layout="tree"
                    matchIds={searchMatchIds}
                    maxDepth={effectiveDepthLimit}
                    multiple={multiSelect}
                    nodes={filteredWorkbenchGraph.nodes}
                    planEdges={planPathEdges}
                    initialFit="focus"
                    onCanvasApi={handleCanvasApi}
                    onSelect={selectWorkbenchNode}
                    onSelectionChange={updateWorkbenchSelection}
                    onStudy={studyFromNode}
                    rootEmphasis
                    selectedId={selectedNode?.id}
                    selectedIds={selectedNodeIds}
                    showHeading={false}
                    showZoomControls
                    title={activeGraph.title}
                  />
                ) : (
                  <div className="graph-workbench-filter-empty" role="status">
                    <Search className="size-5" />
                    <strong>没有匹配的节点</strong>
                    <p>调整关键词、状态或类型筛选后重试。</p>
                    <Button
                      onClick={() => {
                        setNodeSearch("");
                        setNodeStateFilter("all");
                        setNodeTypeFilter("all");
                      }}
                      size="xs"
                      variant="outline"
                    >
                      清除筛选
                    </Button>
                  </div>
                )}
                {multiSelect ? (
                  <div
                    className="graph-multi-action graph-multi-action--floating"
                    role="status"
                  >
                    <div>
                      <MousePointer2 className="size-4" />
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
            {dockedInspector &&
            (planPanelOpen || (inspectorOpen && selectedNode)) ? (
              <aside
                aria-label={planPanelOpen ? "学习计划" : "节点详情"}
                className={cn(
                  "graph-workbench-inspector",
                  planPanelOpen && "graph-workbench-inspector--plan",
                )}
              >
                {planPanelOpen ? planInspector : nodeInspector}
              </aside>
            ) : null}
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
      {!dockedInspector ? (
        <>
          <Sheet
            onOpenChange={(open) => {
              setInspectorOpen(open);
              if (open) setInspectorMode("node");
            }}
            open={!planPanelOpen && inspectorOpen && Boolean(selectedNode)}
          >
            <SheetContent className="graph-node-sheet overflow-y-auto sm:max-w-[360px]">
              <SheetTitle className="sr-only">节点详情</SheetTitle>
              <div className="p-5 pt-12">{nodeInspector}</div>
            </SheetContent>
          </Sheet>
          <Sheet
            onOpenChange={(open) => {
              if (!open) closePlanPanel();
            }}
            open={planPanelOpen}
          >
            <SheetContent className="graph-node-sheet overflow-y-auto sm:max-w-[400px]">
              <SheetTitle className="sr-only">学习计划</SheetTitle>
              <div className="p-5 pt-12">{planInspector}</div>
            </SheetContent>
          </Sheet>
        </>
      ) : null}
      {addToPlanNode ? (
        <AddToPlanDialog
          available={Boolean(plan.roadmap)}
          model={plan.model}
          node={addToPlanNode}
          onClose={() => setAddToPlanNode(undefined)}
          onSubmit={(payload) => {
            void plan
              .addNode({
                nodeId: addToPlanNode.id,
                dayIndex: payload.dayIndex,
                durationMinutes: payload.durationMinutes,
                rationale: payload.rationale,
              })
              .then(() => {
                setAddToPlanNode(undefined);
                openPlanPanel();
              })
              .catch(() => undefined);
          }}
          pending={plan.adding}
          suggestion={suggestPlanSlot(plan.model, {
            blockedByPrerequisite: blockedNodeIds.has(addToPlanNode.id),
          })}
        />
      ) : null}
      {schedulePlanItem && plan.roadmap ? (
        <PlanScheduleDialog
          item={schedulePlanItem}
          model={plan.model}
          onClose={() => setSchedulePlanItem(undefined)}
          onSubmit={(payload) => {
            void plan
              .scheduleItem(schedulePlanItem, payload)
              .then(() => setSchedulePlanItem(undefined))
              .catch(() => undefined);
          }}
          pending={plan.scheduling}
        />
      ) : null}
      {activeGraph ? (
        <GraphReviewDialog
          graph={activeGraph}
          onOpenChange={setGraphReviewOpen}
          open={graphReviewOpen}
          workspaceId={workspaceId}
        />
      ) : null}
    </PageFrame>
  );
}

/**
 * Node detail surface.
 *
 * Desktop renders it docked next to the canvas, narrow viewports render the same
 * tree inside the drawer — one information architecture, one component.
 * Content priority: what it is → what state I am in → what it relates to →
 * what I can do right now.
 */
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
  typeLabel,
  levelLabel,
  statusLabel,
  statusId,
  relations,
  currentLearning = false,
  onChange,
  onEdit,
  onStopEditing,
  onSave,
  onFocus,
  onLearn,
  onLocate,
  onLocateRelation,
  onOpenExplore,
  onCloseExplore,
  onMastery,
  masteryBusy = false,
  onSplit,
  onOpenPrerequisite,
  schedule,
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
  typeLabel?: string;
  levelLabel?: string;
  statusLabel?: string;
  statusId?: NodeLearningStatusId;
  relations?: {
    upstream: NodeRelation[];
    downstream: NodeRelation[];
    related: NodeRelation[];
  };
  currentLearning?: boolean;
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
  onLocate?: () => void;
  onLocateRelation?: (nodeId: string) => void;
  onOpenExplore?: () => void;
  onCloseExplore?: () => void;
  onMastery?: () => void;
  masteryBusy?: boolean;
  onSplit?: () => void;
  onOpenPrerequisite?: () => void;
  /**
   * Learning-plan state for this node. Deliberately small: the plan only says
   * when the node will be worked on — mastery stays a graph fact.
   */
  schedule?: {
    items: PlanItemView[];
    canAdd: boolean;
    onAdd: () => void;
    onStart: (item: PlanItemView) => void;
    onAdjust: (item: PlanItemView) => void;
    onOpenPlan: () => void;
  };
}) {
  const [tab, setTab] = useState<"core" | "relations" | "resources">("core");
  const importance = weightToImportance(draft.targetWeight);
  const statusProfile = statusId ? NODE_STATUS_PROFILES[statusId] : undefined;

  if (!node) {
    return (
      <div className="graph-node-panel">
        <p className="text-sm text-muted-foreground">
          选择画布中的节点以查看详情。
        </p>
      </div>
    );
  }

  const upstream = relations?.upstream ?? [];
  const downstream = relations?.downstream ?? [];
  const related = relations?.related ?? [];
  const relationCount = upstream.length + downstream.length + related.length;
  const primaryLabel = statusProfile?.ctaLabel ?? "学习此节点";
  const locked = statusId === "locked";

  return (
    <div className="graph-node-panel">
      <header className="graph-node-panel__head">
        <div className="graph-node-panel__meta">
          {typeLabel ? (
            <span className="graph-node-panel__type">{typeLabel}</span>
          ) : null}
          {levelLabel ? (
            <span className="graph-node-panel__level" title="图谱层级">
              {levelLabel}
            </span>
          ) : null}
          {node.has_learning_page ? (
            <span
              className="graph-node-panel__page"
              title="教材、互动实验与闯关测评已生成"
            >
              <Sparkles aria-hidden="true" className="size-3" />
              已生成交互页
            </span>
          ) : null}
          {currentLearning ? (
            <span className="graph-node-panel__current">当前学习</span>
          ) : null}
        </div>
        <div className="graph-node-panel__title-row">
          <h2>{node.label}</h2>
          <div className="graph-node-panel__head-actions">
            {!editing ? (
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
            ) : null}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  aria-label="更多节点操作"
                  size="icon-xs"
                  variant="ghost"
                >
                  <MoreHorizontal className="size-3.5" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-52">
                {onMastery ? (
                  <DropdownMenuItem
                    disabled={masteryBusy || !focusEditable}
                    onSelect={onMastery}
                  >
                    <BadgeCheck className="size-4" />
                    {node.attention_state === "mastered"
                      ? "取消已掌握"
                      : "标记为已掌握"}
                  </DropdownMenuItem>
                ) : null}
                <DropdownMenuItem
                  disabled={!focusEditable || focusBusy}
                  onSelect={onFocus}
                >
                  <Focus className="size-4" />
                  {node.attention_state === "focused"
                    ? "取消重点节点"
                    : "设为重点节点"}
                </DropdownMenuItem>
                {onLocate ? (
                  <DropdownMenuItem onSelect={onLocate}>
                    <Crosshair className="size-4" />
                    聚焦此节点与关联
                  </DropdownMenuItem>
                ) : null}
                {onSplit ? (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onSelect={onSplit}>
                      <Split className="size-4" />
                      拆分（提交图谱变更）
                    </DropdownMenuItem>
                  </>
                ) : null}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
        {statusLabel ? (
          <div className="graph-node-panel__status">
            <span
              className={cn(
                "knowledge-node__status",
                `is-${statusProfile?.tone ?? "neutral"}`,
              )}
            >
              {statusLabel}
            </span>
            {node.attention_state === "focused" ? (
              <span className="graph-node-panel__flag">重点</span>
            ) : null}
            {!focusEditable ? (
              <span className="graph-node-panel__flag is-muted">候选图谱</span>
            ) : null}
          </div>
        ) : null}
      </header>

      {editing ? (
        <div className="graph-node-panel__body space-y-3">
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
            <p className="importance-calibrator__hint">1–3 档：低 / 中 / 高。</p>
            <div
              aria-label="重要指数"
              className="importance-calibrator__levels"
              role="group"
            >
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
                  <span>
                    {level === 3
                      ? "强烈建议看"
                      : level === 2
                        ? "可以看看"
                        : "可以跳过"}
                  </span>
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
        <div className="graph-node-panel__body">
          <p className="graph-node-panel__intro">
            {node.description || "尚未补充知识点说明。"}
          </p>
          <div
            aria-label="节点详情分区"
            className="graph-node-panel__tabs"
            role="tablist"
          >
            <button
              aria-selected={tab === "core"}
              className={tab === "core" ? "is-active" : undefined}
              onClick={() => setTab("core")}
              role="tab"
              type="button"
            >
              核心内容
            </button>
            <button
              aria-selected={tab === "relations"}
              className={tab === "relations" ? "is-active" : undefined}
              onClick={() => setTab("relations")}
              role="tab"
              type="button"
            >
              关联节点 · {relationCount}
            </button>
            <button
              aria-selected={tab === "resources"}
              className={tab === "resources" ? "is-active" : undefined}
              onClick={() => setTab("resources")}
              role="tab"
              type="button"
            >
              学习资源
            </button>
          </div>

          {tab === "core" ? (
            <div className="graph-node-panel__facts">
              <div className="graph-node-panel__fact">
                <span>学习状态</span>
                <strong>{statusLabel ?? "未学习"}</strong>
              </div>
              <div className="graph-node-panel__fact">
                <span>掌握度</span>
                <strong>
                  {masteryLevelLabel(node.mastery_stars)} ·{" "}
                  {Math.max(0, Math.round(node.mastery_stars))}/5
                </strong>
              </div>
              <div className="graph-node-panel__fact">
                <span>检索状态</span>
                <strong>{graphStateLabel(node.retrieval_state)}</strong>
              </div>
              <div className="graph-node-panel__fact">
                <span>证据状态</span>
                <strong>{graphStateLabel(node.evidence_state)}</strong>
              </div>
              <div className="graph-node-panel__fact">
                <span>重要指数</span>
                <strong className="flex items-center justify-between gap-2">
                  {metricLabel(weightToImportance(node.target_weight))}
                  <RecommendDots weight={node.target_weight} />
                </strong>
              </div>
              {locked && upstream.length ? (
                <div className="graph-node-panel__fact is-warning">
                  <span>前置未完成</span>
                  <strong>{upstream.map((entry) => entry.node.label).join("、")}</strong>
                </div>
              ) : null}
            </div>
          ) : tab === "relations" ? (
            <div className="graph-node-panel__relations">
              {(
                [
                  { title: "前置 / 上级", entries: upstream },
                  { title: "后继 / 下级", entries: downstream },
                  { title: "横向关联", entries: related },
                ] as const
              ).map((group) => (
                <section key={group.title}>
                  <h3>
                    {group.title}
                    <span>{group.entries.length}</span>
                  </h3>
                  {group.entries.length ? (
                    <ul>
                      {group.entries.map((entry) => (
                        <li key={`${group.title}-${entry.node.id}`}>
                          <button
                            onClick={() => onLocateRelation?.(entry.node.id)}
                            type="button"
                          >
                            <span className="graph-relation-label">
                              {entry.node.label}
                            </span>
                            <span className="graph-relation-meta">
                              {graphRelationLabel(entry.relation)}
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="graph-relation-empty">暂无</p>
                  )}
                </section>
              ))}
            </div>
          ) : (
            <div className="graph-node-panel__resources">
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
            </div>
          )}
        </div>
      )}

      {schedule && !editing ? (
        <section className="graph-node-panel__plan" aria-label="学习计划">
          <div className="graph-node-panel__plan-head">
            <span>计划</span>
            {schedule.items.length ? null : <em>尚未安排</em>}
          </div>
          {schedule.items.length ? (
            (() => {
              const item = schedule.items[0];
              return (
                <>
                  <p className="graph-node-panel__plan-when">
                    <strong>
                      {item.isToday ? "今天" : item.headline} · {item.durationMinutes} 分钟
                    </strong>
                    {schedule.items.length > 1 ? (
                      <span>共 {schedule.items.length} 项安排</span>
                    ) : (
                      <span>{item.routing.label.replace(/^开始/, "")}</span>
                    )}
                  </p>
                  {item.blocked ? (
                    <p className="graph-node-panel__plan-warn">
                      <Lock className="size-3" />
                      前置未完成，已记为待解锁
                    </p>
                  ) : null}
                  <div className="graph-node-panel__plan-actions">
                    {item.done ? (
                      <Button disabled size="xs" variant="outline">
                        已完成
                      </Button>
                    ) : (
                      <Button
                        disabled={item.blocked}
                        onClick={() => schedule.onStart(item)}
                        size="xs"
                      >
                        <Play className="size-3" />
                        {item.routing.label}
                      </Button>
                    )}
                    <Button
                      disabled={item.done || item.blocked}
                      onClick={() => schedule.onAdjust(item)}
                      size="xs"
                      variant="outline"
                    >
                      <CalendarClock className="size-3" />
                      调整
                    </Button>
                  </div>
                </>
              );
            })()
          ) : (
            <div className="graph-node-panel__plan-actions">
              <Button
                disabled={!schedule.canAdd || node.node_type === "root"}
                onClick={schedule.onAdd}
                size="xs"
                title={
                  node.node_type === "root"
                    ? "根节点代表整个学习目标，不是可执行任务"
                    : schedule.canAdd
                      ? "把这个节点安排进学习计划"
                      : "先生成学习计划再加入节点"
                }
                variant="outline"
              >
                <CalendarClock className="size-3" />
                加入计划
              </Button>
              <Button onClick={schedule.onOpenPlan} size="xs" variant="ghost">
                查看计划
              </Button>
            </div>
          )}
        </section>
      ) : null}

      <footer className="graph-node-panel__cta">
        {locked ? (
          <Button
            className="w-full"
            onClick={onOpenPrerequisite}
            size="sm"
            variant="outline"
          >
            <Lock className="size-4" />
            查看前置知识
          </Button>
        ) : null}
        <Button className="w-full" onClick={onLearn} size="sm">
          <BookOpen className="size-4" />
          {locked ? "仍然学习此节点" : primaryLabel}
        </Button>
        {onLocate ? (
          <Button
            className="w-full"
            onClick={onLocate}
            size="sm"
            variant="outline"
          >
            <Crosshair className="size-4" />
            在图中定位
          </Button>
        ) : null}
        {!editMode ? (
          <p className="graph-node-panel__hint">
            改名、改定义与重要指数需要先打开工具栏「编辑模式」。
          </p>
        ) : null}
      </footer>
    </div>
  );
}

export function JointStudyPage() {
  const { workspaceId = "" } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const graphs = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "graphs"),
    queryFn: listGraphs,
  });
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
    queryKey: workspaceQueryKey(workspaceId, "graph", resolvedGraphId),
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

const LEARNING_STATUS_LABEL: Record<string, string> = {
  unseen: "未评估",
  weak: "薄弱",
  mastered: "已掌握",
  familiar: "熟悉",
  learning: "学习中",
  needs_review: "待复习",
};

export function CapabilityGraphPage() {
  const { workspaceId = "" } = useParams();
  const mastery = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "mastery"),
    queryFn: getMastery,
  });
  const [selectedId, setSelectedId] = useState("");
  const [depthLimit, setDepthLimit] = useState(1);
  const [alignmentOpen, setAlignmentOpen] = useState(false);
  const alignment = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "mastery-alignment", selectedId),
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
  const learningState = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "learning-node-state", selectedId),
    queryFn: () => getLearningNodeState(selectedId),
    enabled: Boolean(selectedId),
    retry: false,
  });
  const isLearningStateMissing =
    learningState.error instanceof ApiError &&
    learningState.error.status === 404 &&
    learningState.error.code === "learning_state_not_found";
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
            {selectedId && learningState.isPending ? (
              <div className="rounded-xl bg-muted/40 p-4 text-xs text-muted-foreground">
                正在读取学习状态…
              </div>
            ) : isLearningStateMissing ? (
              <div className="rounded-xl bg-muted/40 p-4">
                <p className="text-xs text-muted-foreground">掌握度</p>
                <div className="mt-1">
                  <GrowthStars value={selected?.mastery_stars ?? 0} />
                </div>
                <p className="mt-2 text-xs leading-5 text-muted-foreground">
                  该节点尚未通过新链路评估，暂无连续掌握分，暂显示成长星级。
                </p>
              </div>
            ) : learningState.data ? (
              <>
                <div className="rounded-xl bg-muted/40 p-4">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-xs text-muted-foreground">掌握度</p>
                    <Badge variant="secondary">
                      {LEARNING_STATUS_LABEL[learningState.data.status] ??
                        learningState.data.status}
                    </Badge>
                  </div>
                  <div className="mt-2 flex items-center gap-3">
                    <Progress
                      className="flex-1"
                      value={Math.round(learningState.data.mastery_score * 100)}
                    />
                    <span className="font-mono text-sm text-primary">
                      {Math.round(learningState.data.mastery_score * 100)}%
                    </span>
                  </div>
                  <p className="mt-2 text-xs text-muted-foreground">
                    置信度 {Math.round(learningState.data.confidence * 100)}% ·{" "}
                    {learningState.data.evidence_count} 条证据
                  </p>
                </div>
                {learningState.data.misconceptions.length > 0 && (
                  <div>
                    <p className="text-sm font-medium">误区记录</p>
                    <ul className="mt-2 space-y-1">
                      {learningState.data.misconceptions.map((m) => (
                        <li
                          key={m.evidence_id}
                          className="text-xs leading-5 text-muted-foreground"
                        >
                          {m.summary}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {learningState.data.next_review_at && (
                  <p className="text-xs text-muted-foreground">
                    下次复习：
                    {new Date(learningState.data.next_review_at).toLocaleDateString()}
                  </p>
                )}
              </>
            ) : selectedId ? (
              <div className="rounded-xl bg-muted/40 p-4 text-xs text-muted-foreground">
                学习状态读取失败，请稍后重试。
              </div>
            ) : (
              <div className="rounded-xl bg-muted/40 p-4 text-xs text-muted-foreground">
                选择能力节点后查看掌握度。
              </div>
            )}
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
