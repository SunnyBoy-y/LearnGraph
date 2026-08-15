import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Check,
  Pencil,
  RotateCcw,
  Save,
  Trash2,
} from "lucide-react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { toast } from "sonner";

import {
  deleteGraphNode,
  getRoadmap,
  replanRoadmap,
  retryGraphNode,
  updateGraphNode,
  updateGoalPlanning,
} from "@/api";
import { ApiError, apiClient } from "@/api/client";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageFrame,
  PageIntro,
  SectionHeading,
  StatePill,
  Surface,
  SuccessNotice,
} from "@/components/shared/page-elements";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  KnowledgeGraph,
  type KnowledgeNode,
} from "@/components/graph/knowledge-graph";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { workspaceQueryKey } from "@/lib/query-keys";
import type {
  Goal,
  Graph,
  GraphNode,
  GraphSummary,
} from "@/types/domain";

type GoalConfirmBody = {
  title: string;
  intent: string;
  time_limit: string;
  desired_outcome: string;
  constraints: Record<string, unknown>;
  assumptions: Array<Record<string, unknown>>;
};

type GoalPlanningDraft = {
  targetWeight: number;
  deadlineAt: string;
  minutesPerDay: number;
  daysPerWeek: number;
  sessionMinutes: number;
  preferredActionTypes: string[];
};

const PLANNING_ACTION_TYPES = [
  { value: "learn", label: "学习" },
  { value: "practice", label: "练习" },
  { value: "review", label: "复习" },
  { value: "assessment", label: "测验/验收" },
] as const;

function localDateTimeInputValue(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 16);
}

function initialPlanningDraft(goal?: Goal): GoalPlanningDraft {
  const availability = goal?.availability ?? {
    minutes_per_day: 30,
    days_per_week: 5,
  };
  const preferences = goal?.preferences ?? {
    preferred_action_types: ["learn", "practice", "review"],
    session_minutes: 30,
  };
  return {
    targetWeight: goal?.target_weight ?? 50,
    deadlineAt: goal?.deadline_at
      ? localDateTimeInputValue(goal.deadline_at)
      : "",
    minutesPerDay: availability.minutes_per_day ?? 30,
    daysPerWeek: availability.days_per_week ?? 5,
    sessionMinutes: preferences.session_minutes ?? 30,
    preferredActionTypes: preferences.preferred_action_types?.length
      ? preferences.preferred_action_types
      : ["learn", "practice", "review"],
  };
}

function GoalDraftField({
  label,
  multiline = false,
  onChange,
  value,
}: {
  label: string;
  multiline?: boolean;
  onChange: (value: string) => void;
  value: string;
}) {
  return (
    <label
      className={
        multiline
          ? "goal-draft-field goal-draft-field--multiline"
          : "goal-draft-field"
      }
    >
      <span>{label}</span>
      {multiline ? (
        <Textarea
          aria-label={label}
          onChange={(event) => onChange(event.target.value)}
          value={value}
        />
      ) : (
        <Input
          aria-label={label}
          onChange={(event) => onChange(event.target.value)}
          value={value}
        />
      )}
    </label>
  );
}

export function GoalConfirmPage() {
  const { goalId = "", workspaceId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const goals = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "goals"),
    queryFn: () => apiClient.get<Goal[]>("/goals"),
  });
  const goal = goals.data?.find((item) => item.id === goalId);
  const [draft, setDraft] = useState<GoalConfirmBody | null>(null);
  const [planningDraft, setPlanningDraft] = useState<GoalPlanningDraft | null>(
    null,
  );

  useEffect(() => {
    if (goal && !draft)
      setDraft({
        title: goal.title,
        intent: goal.intent,
        time_limit: goal.time_limit,
        desired_outcome: goal.desired_outcome,
        constraints: goal.constraints,
        assumptions: goal.assumptions,
      });
  }, [goal, draft]);

  useEffect(() => {
    if (goal && !planningDraft) setPlanningDraft(initialPlanningDraft(goal));
  }, [goal, planningDraft]);

  const confirm = useMutation({
    mutationFn: (body: GoalConfirmBody) =>
      apiClient.put<Goal, GoalConfirmBody>(`/goals/${goalId}/confirm`, body),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "goals"),
      });
      toast.success(`Goal「${data.title}」已确认`);
    },
  });
  const candidate = useMutation({
    mutationFn: () =>
      apiClient.post<GraphSummary, { seed_concepts: string[] }>(
        `/goals/${goalId}/candidate-graph`,
        { seed_concepts: [] },
      ),
  });
  const updatePlanning = useMutation({
    mutationFn: (payload: {
      target_weight: number;
      deadline_at: string | null;
      availability: { minutes_per_day: number; days_per_week: number };
      preferences: {
        preferred_action_types: string[];
        session_minutes: number;
      };
    }) => updateGoalPlanning(goalId, payload),
  });
  const initialRoadmap = useMutation({
    mutationFn: () => replanRoadmap(goalId),
  });

  if (goals.isPending)
    return (
      <PageFrame>
        <LoadingState label="正在读取 GoalDraft…" />
      </PageFrame>
    );
  if (goals.isError)
    return (
      <PageFrame>
        <ErrorState
          message={goals.error.message}
          onRetry={() => void goals.refetch()}
        />
      </PageFrame>
    );
  if (!goal)
    return (
      <PageFrame>
        <EmptyState
          description="请先完成目标澄清问卷。"
          title="没有可确认的 Goal"
        />
      </PageFrame>
    );
  if (!draft || !planningDraft)
    return (
      <PageFrame>
        <LoadingState label="正在准备 GoalDraft…" />
      </PageFrame>
    );

  async function confirmAndGenerate() {
    if (!draft || !planningDraft) return;
    try {
      const saved = await confirm.mutateAsync(draft);
      if (!saved) return;
      await updatePlanning.mutateAsync({
        target_weight: planningDraft.targetWeight,
        deadline_at: planningDraft.deadlineAt
          ? new Date(planningDraft.deadlineAt).toISOString()
          : null,
        availability: {
          minutes_per_day: planningDraft.minutesPerDay,
          days_per_week: planningDraft.daysPerWeek,
        },
        preferences: {
          preferred_action_types: planningDraft.preferredActionTypes,
          session_minutes: planningDraft.sessionMinutes,
        },
      });
      const graph = await candidate.mutateAsync();
      await initialRoadmap.mutateAsync();
      navigate(`/w/${workspaceId}/goals/${goalId}/graph-review?graph=${graph.id}`);
    } catch {
      // Mutation state renders the durable API error without an unhandled promise.
    }
  }

  return (
    <PageFrame>
      <PageIntro
        actions={<StatePill status={goal.status} />}
        description="这些字段会约束候选图谱和路线。未确认的假设会保持显式，不会被悄悄当作事实。"
        eyebrow="Goal · 2/3"
        title="确认结构化学习目标"
      />
      <Surface className="p-5">
        <SectionHeading
          description="直接在卡片内修改，生成初始图谱时会使用当前内容。"
          title="Goal 卡片"
        />
        <div className="goal-draft-grid mt-5">
          <GoalDraftField
            label="目标名称"
            onChange={(value) => setDraft({ ...draft, title: value })}
            value={draft.title}
          />
          <GoalDraftField
            label="学习目的"
            multiline
            onChange={(value) => setDraft({ ...draft, intent: value })}
            value={draft.intent}
          />
          <GoalDraftField
            label="时间约束"
            onChange={(value) => setDraft({ ...draft, time_limit: value })}
            value={draft.time_limit}
          />
          <GoalDraftField
            label="目标水平"
            multiline
            onChange={(value) => setDraft({ ...draft, desired_outcome: value })}
            value={draft.desired_outcome}
          />
          <GoalDraftField
            label="排除范围"
            multiline
            onChange={(value) =>
              setDraft({
                ...draft,
                constraints: { ...draft.constraints, exclude: value },
              })
            }
            value={String(draft.constraints.exclude ?? "")}
          />
        </div>
      </Surface>
      <Surface className="p-5">
        <SectionHeading
          description="这些是路线排序的持久输入。未填写期限时系统会明确显示“无固定截止日期”，不会从自然语言时间约束偷偷猜测。"
          title="行动规划约束"
        />
        <div className="goal-draft-grid mt-5">
          <label className="goal-draft-field">
            <span>目标权重（1–100）</span>
            <Input
              aria-label="目标权重"
              max={100}
              min={1}
              onChange={(event) =>
                setPlanningDraft({
                  ...planningDraft,
                  targetWeight: Number(event.target.value) || 1,
                })
              }
              type="number"
              value={planningDraft.targetWeight}
            />
          </label>
          <label className="goal-draft-field">
            <span>结构化截止时间（可选）</span>
            <Input
              aria-label="结构化截止时间"
              onChange={(event) =>
                setPlanningDraft({ ...planningDraft, deadlineAt: event.target.value })
              }
              type="datetime-local"
              value={planningDraft.deadlineAt}
            />
          </label>
          <label className="goal-draft-field">
            <span>每天可用分钟数</span>
            <Input
              aria-label="每天可用分钟数"
              max={1440}
              min={15}
              onChange={(event) =>
                setPlanningDraft({
                  ...planningDraft,
                  minutesPerDay: Number(event.target.value) || 15,
                })
              }
              type="number"
              value={planningDraft.minutesPerDay}
            />
          </label>
          <label className="goal-draft-field">
            <span>每周可学习天数</span>
            <Input
              aria-label="每周可学习天数"
              max={7}
              min={1}
              onChange={(event) =>
                setPlanningDraft({
                  ...planningDraft,
                  daysPerWeek: Number(event.target.value) || 1,
                })
              }
              type="number"
              value={planningDraft.daysPerWeek}
            />
          </label>
          <label className="goal-draft-field">
            <span>单次学习时长（分钟）</span>
            <Input
              aria-label="单次学习时长"
              max={240}
              min={15}
              onChange={(event) =>
                setPlanningDraft({
                  ...planningDraft,
                  sessionMinutes: Number(event.target.value) || 15,
                })
              }
              type="number"
              value={planningDraft.sessionMinutes}
            />
          </label>
        </div>
        <fieldset className="mt-5">
          <legend className="text-sm font-medium">偏好的下一步行动</legend>
          <div className="mt-3 flex flex-wrap gap-3">
            {PLANNING_ACTION_TYPES.map((item) => {
              const checked = planningDraft.preferredActionTypes.includes(item.value);
              return (
                <label className="flex items-center gap-2 text-sm" key={item.value}>
                  <input
                    checked={checked}
                    onChange={() =>
                      setPlanningDraft({
                        ...planningDraft,
                        preferredActionTypes: checked
                          ? planningDraft.preferredActionTypes.filter(
                              (value) => value !== item.value,
                            )
                          : [...planningDraft.preferredActionTypes, item.value],
                      })
                    }
                    type="checkbox"
                  />
                  {item.label}
                </label>
              );
            })}
          </div>
        </fieldset>
      </Surface>
      {confirm.isError ||
      updatePlanning.isError ||
      candidate.isError ||
      initialRoadmap.isError ? (
        <ErrorState
          message={
            (
              confirm.error ??
              updatePlanning.error ??
              candidate.error ??
              initialRoadmap.error
            )?.message ??
            "无法继续"
          }
        />
      ) : null}
      <Surface className="flex flex-wrap items-center justify-between gap-3 p-4">
        <p className="text-sm text-muted-foreground">
          确认后先生成候选图谱，不会直接写入正式图谱。
        </p>
        <div className="flex gap-2">
          <Button asChild variant="outline">
            <Link to={`/w/${workspaceId}/goals/new/clarify`}>补充问卷</Link>
          </Button>
          <Button
            disabled={
              confirm.isPending ||
              updatePlanning.isPending ||
              candidate.isPending ||
              initialRoadmap.isPending
            }
            onClick={() => void confirmAndGenerate()}
          >
            {confirm.isPending ||
            updatePlanning.isPending ||
            candidate.isPending ||
            initialRoadmap.isPending
              ? "正在保存并生成…"
              : "生成初始图谱与路线草稿"}
            <ArrowRight className="size-4" />
          </Button>
        </div>
      </Surface>
    </PageFrame>
  );
}

const REVIEW_RELATION_LABELS: Record<string, string> = {
  contains: "包含",
  prerequisite: "前置",
  related: "关联",
  contrast: "对比",
  application: "应用",
};

/** Graph → KnowledgeGraph 视图：contains 为教学层级，其余关系为视觉叠加。 */
function toReviewGraphView(graph: Graph) {
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
      nodeType: node.node_type,
      targetWeight: node.target_weight,
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
    label: REVIEW_RELATION_LABELS[edge.relation] ?? edge.relation,
    data: { relation: edge.relation },
    type: "smoothstep" as const,
  }));
  return { nodes, edges };
}

export function GraphReviewPage() {
  const { goalId = "", workspaceId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const requestedGraph = searchParams.get("graph");
  const [selected, setSelected] = useState<string>();
  const queryClient = useQueryClient();
  const graphs = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "graphs"),
    queryFn: () => apiClient.get<GraphSummary[]>("/graphs"),
  });
  const graphId =
    requestedGraph ??
    graphs.data?.find((graph) => graph.goal_id === goalId)?.id;
  const graph = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "graph", graphId),
    enabled: Boolean(graphId),
    queryFn: () => apiClient.get<Graph>(`/graphs/${graphId}`),
  });
  const roadmap = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "roadmap", goalId),
    enabled: Boolean(goalId),
    queryFn: () => getRoadmap(goalId),
    retry: false,
  });
  function currentGraphRevision() {
    const revision = graph.data?.revision;
    if (!revision)
      throw new Error("候选图谱 Revision 尚未载入，请稍后重试。");
    return revision;
  }
  async function handleGraphMutationError(error: Error) {
    if (
      error instanceof ApiError &&
      error.status === 409 &&
      error.code === "graph_revision_conflict"
    ) {
      toast.error("候选图谱已被其他操作更新，请按最新 Revision 重新审核。");
      await graph.refetch();
      return;
    }
    toast.error(error.message);
  }
  const publish = useMutation({
    mutationFn: () =>
      apiClient.post<
        { status: string; graph_id: string },
        { graph_id: string; expected_revision: number }
      >(
        `/goals/${goalId}/publish`,
        { graph_id: graphId!, expected_revision: graph.data!.revision },
      ),
    onSuccess: (data) => {
      toast.success("图谱已发布为正式版本");
      void Promise.all([
        queryClient.invalidateQueries({
          queryKey: workspaceQueryKey(workspaceId, "graphs"),
        }),
        queryClient.invalidateQueries({
          queryKey: workspaceQueryKey(workspaceId, "graph", data.graph_id),
        }),
        queryClient.invalidateQueries({
          queryKey: workspaceQueryKey(workspaceId, "goals"),
        }),
        queryClient.invalidateQueries({
          queryKey: workspaceQueryKey(workspaceId, "roadmap", goalId),
        }),
      ]);
    },
    onError: handleGraphMutationError,
  });
  const updateNode = useMutation({
    mutationFn: ({
      nodeId,
      body,
    }: {
      nodeId: string;
      body: { label: string; description: string; target_weight: number };
    }) =>
      updateGraphNode(graphId!, nodeId, {
        expected_revision: currentGraphRevision(),
        ...body,
      }),
    onSuccess: async () => {
      toast.success("候选知识卡片已更新");
      await queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "graph", graphId),
      });
    },
    onError: handleGraphMutationError,
  });
  const retryNode = useMutation({
    mutationFn: ({
      nodeId,
      instruction,
    }: {
      nodeId: string;
      instruction: string;
    }) =>
      retryGraphNode(
        graphId!,
        nodeId,
        currentGraphRevision(),
        instruction.trim(),
      ),
    onSuccess: async (node) => {
      toast.success(`已局部重建「${node.label}」`);
      await queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "graph", graphId),
      });
    },
    onError: handleGraphMutationError,
  });
  const removeNode = useMutation({
    mutationFn: (nodeId: string) =>
      deleteGraphNode(graphId!, nodeId, currentGraphRevision()),
    onSuccess: async () => {
      setSelected(undefined);
      toast.success("候选节点及相连关系已删除");
      await queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "graph", graphId),
      });
    },
    onError: handleGraphMutationError,
  });
  const replan = useMutation({
    mutationFn: () => replanRoadmap(goalId),
    onSuccess: () => {
      toast.success("学习路线已按当前图谱重新生成并立即生效");
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "roadmap", goalId),
      });
    },
    onError: (error) => toast.error(error.message),
  });
  // NOTE: this useMemo MUST stay above the early returns below. React requires
  // hooks to be called unconditionally on every render; a hook after a
  // conditional return changes the hook count between the loading and loaded
  // renders and crashes the whole app with React error #310 (white screen).
  const reviewGraph = useMemo(
    () => (graph.data ? toReviewGraphView(graph.data) : undefined),
    [graph.data],
  );

  if (graphs.isPending || (Boolean(graphId) && graph.isPending))
    return (
      <PageFrame>
        <LoadingState label="正在装配候选图谱…" />
      </PageFrame>
    );
  if (graphs.isError || (Boolean(graphId) && graph.isError))
    return (
      <PageFrame>
        <ErrorState
          message={(graphs.error ?? graph.error)?.message ?? "图谱读取失败"}
          onRetry={() => {
            void graphs.refetch();
            void graph.refetch();
          }}
        />
      </PageFrame>
    );
  if (!graph.data)
    return (
      <PageFrame>
        <EmptyState
          description="先从 Goal 确认页生成候选图谱。"
          title="没有候选图谱"
        />
      </PageFrame>
    );
  const selectedNode =
    graph.data.nodes.find((node) => node.id === selected) ??
    graph.data.nodes[0];
  const allAccepted = graph.data.nodes.length > 0;
  const actionsDisabled = graph.data.status === "published";

  return (
    <PageFrame>
      <PageIntro
        actions={<StatePill status={graph.data.status} />}
        description="节点与知识卡片双向联动。正式图谱只能由你发布，后台不会静默改写。"
        eyebrow="Goal · 3/3"
        title="审核初始图谱"
      />
      {graph.data.status === "published" ? (
        <SuccessNotice>
          这是已发布版本。当前页面仍以审核视图展示；修改会生成新的候选修订。
        </SuccessNotice>
      ) : null}
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_400px]">
        <Surface className="candidate-knowledge-surface p-5">
          <SectionHeading
            description="点击编辑后直接在卡片内解锁输入框；候选节点以平铺方式审核，右侧图谱点击节点可定位卡片。"
            title="拟新增知识卡片"
          />
          <div className="candidate-knowledge-grid mt-5">
            {graph.data.nodes.map((node) => (
              <CandidateKnowledgeCard
                busy={updateNode.isPending}
                disabled={actionsDisabled}
                key={node.id}
                node={node}
                onDelete={() => removeNode.mutate(node.id)}
                onRetry={(instruction) =>
                  retryNode.mutate({ nodeId: node.id, instruction })
                }
                onSave={(body) => updateNode.mutate({ nodeId: node.id, body })}
                onSelect={() => setSelected(node.id)}
                retrying={retryNode.isPending}
                selected={selectedNode?.id === node.id}
              />
            ))}
          </div>
        </Surface>

        <div className="min-w-0 xl:sticky xl:top-6 xl:self-start">
          <Surface className="p-4">
            <SectionHeading
              description="树状展示 contains 教学层级；点击节点定位到左侧候选卡片。"
              title="图谱结构预览"
            />
            <div className="mt-4 h-[540px] overflow-hidden rounded-xl">
              {reviewGraph ? (
                <KnowledgeGraph
                  compact
                  edges={reviewGraph.edges}
                  layout="tree"
                  nodes={reviewGraph.nodes}
                  onSelect={(node) => setSelected(node.id)}
                  rootEmphasis
                  selectedId={selectedNode?.id}
                  showZoomControls
                  title={graph.data.title}
                />
              ) : null}
            </div>
          </Surface>
        </div>
      </div>

      <Surface className="p-5">
        <SectionHeading
          description="路线与候选图谱一起展示；重新规划后立即生效，无需草稿发布。"
          title="学习路线预览"
        />
        {roadmap.isPending ? (
          <p className="mt-4 text-sm text-muted-foreground">
            正在读取学习路线…
          </p>
        ) : null}
        {roadmap.isError ? (
          <div className="mt-4 flex flex-wrap items-center gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
            <p className="min-w-0 flex-1 text-sm text-destructive">
              {roadmap.error.message}
            </p>
            <Button
              disabled={replan.isPending}
              onClick={() => replan.mutate()}
              size="sm"
              variant="outline"
            >
              {replan.isPending ? "生成中…" : "生成学习路线"}
            </Button>
          </div>
        ) : null}
        {roadmap.data ? (
          <div className="mt-4 space-y-4">
            <div className="rounded-xl border bg-muted/20 p-4 text-sm">
              <p className="font-medium">{roadmap.data.rationale}</p>
              <p className="mt-2 text-muted-foreground">
                图谱修订 v{roadmap.data.graph_revision ?? "—"} ·{" "}
                {roadmap.data.status === "published" ? "已生效" : roadmap.data.status}
                {" · "}
                {roadmap.data.items.length} 项任务
              </p>
            </div>
            <ol className="grid gap-3 md:grid-cols-2">
              {roadmap.data.items.map((item) => {
                const metadata = item.metadata_json as Record<string, unknown>;
                const scoreBreakdown = metadata.score_breakdown as
                  | Record<string, number>
                  | undefined;
                const prerequisites = metadata.prerequisites as
                  | {
                      items?: Array<
                        string | { label?: string; node_id?: string; satisfied?: boolean }
                      >;
                      blocked_by?: Array<
                        string | { label?: string; node_id?: string }
                      >;
                    }
                  | undefined;
                const source =
                  prerequisites?.items?.length
                    ? prerequisites.items
                    : prerequisites?.blocked_by ?? [];
                const prereqLabels = source.map((entry) =>
                  typeof entry === "string"
                    ? entry
                    : entry.label ?? entry.node_id ?? "未知节点",
                );
                return (
                  <li className="rounded-xl border p-4" key={item.id}>
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="font-medium">{item.title}</p>
                        <p className="mt-1 text-sm text-muted-foreground">
                          第 {item.day_index || "—"} 天 · {item.duration_minutes} 分钟 ·{" "}
                          {item.action_type}
                        </p>
                        <p className="mt-2 text-xs leading-5 text-muted-foreground">
                          权重 {Math.round((scoreBreakdown?.importance ?? 0) * 100)}%
                          {" · "}掌握缺口 {Math.round((scoreBreakdown?.mastery_gap ?? 0) * 100)}%
                          {" · "}证据缺口 {Math.round((scoreBreakdown?.evidence_gap ?? 0) * 100)}%
                          {" · "}期限 {Math.round((scoreBreakdown?.deadline_urgency ?? 0) * 100)}%
                        </p>
                      </div>
                      <StatePill status={item.status} />
                    </div>
                    {prereqLabels.length ? (
                      <p className="mt-3 text-xs text-muted-foreground">
                        前置：{prereqLabels.join("、")}
                      </p>
                    ) : null}
                  </li>
                );
              })}
            </ol>
            <div className="flex flex-wrap gap-2">
              <Button
                disabled={replan.isPending}
                onClick={() => replan.mutate()}
                size="sm"
                variant="outline"
              >
                {replan.isPending ? "重排中…" : "按当前图谱重排"}
              </Button>
              <StatePill
                status={roadmap.data.status === "published" ? "published" : "pending"}
                label={roadmap.data.status === "published" ? "已生效" : roadmap.data.status}
              />
            </div>
          </div>
        ) : null}
      </Surface>

      <Surface className="p-5">
        <div className="grid gap-5 lg:grid-cols-[1fr_auto]">
          <div>
            <SectionHeading title="变更清单与审核动作" />
            <div className="mt-3 flex flex-wrap gap-2">
              <Badge className="bg-primary/10 text-primary" variant="secondary">
                新增 {graph.data.nodes.length} 节点
              </Badge>
              <Badge variant="secondary">
                新增 {graph.data.edges.length} 关系
              </Badge>
              <Badge variant="secondary">未修改即默认接受</Badge>
            </div>
          </div>
          <div className="flex items-end gap-2">
            <Button
              disabled={publish.isPending || actionsDisabled || !allAccepted}
              onClick={() => publish.mutate()}
            >
              {publish.isPending
                ? "正在发布…"
                : allAccepted
                  ? "单独发布图谱"
                  : "等待候选节点就绪"}
              <ArrowRight className="size-4" />
            </Button>
          </div>
        </div>
      </Surface>
      {publish.isError ||
      updateNode.isError ||
      retryNode.isError ||
      removeNode.isError ? (
        <ErrorState
          message={
            (
              publish.error ??
              updateNode.error ??
              retryNode.error ??
              removeNode.error
            )?.message ?? "候选图谱操作失败"
          }
        />
      ) : null}
    </PageFrame>
  );
}

function CandidateKnowledgeCard({
  busy,
  disabled,
  node,
  onDelete,
  onRetry,
  onSave,
  onSelect,
  retrying,
  selected,
}: {
  busy: boolean;
  disabled: boolean;
  node: GraphNode;
  onDelete: () => void;
  onRetry: (instruction: string) => void;
  onSave: (body: {
    label: string;
    description: string;
    target_weight: number;
  }) => void;
  onSelect: () => void;
  retrying: boolean;
  selected: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [retryOpen, setRetryOpen] = useState(false);
  const [retryInstruction, setRetryInstruction] = useState("");
  const [draft, setDraft] = useState({
    label: node.label,
    description: node.description,
    targetWeight: node.target_weight ?? 50,
  });

  useEffect(() => {
    if (!editing)
      setDraft({
        label: node.label,
        description: node.description,
        targetWeight: node.target_weight ?? 50,
      });
  }, [editing, node.description, node.id, node.label, node.target_weight]);

  function cancelEdit() {
    setDraft({
      label: node.label,
      description: node.description,
      targetWeight: node.target_weight ?? 50,
    });
    setEditing(false);
  }

  function saveEdit() {
    if (!draft.label.trim()) return;
    onSave({
      label: draft.label.trim(),
      description: draft.description.trim(),
      target_weight: draft.targetWeight,
    });
    setEditing(false);
  }

  return (
    <article
      className={
        selected
          ? "candidate-knowledge-card is-selected"
          : "candidate-knowledge-card"
      }
      onClick={onSelect}
    >
      <div className="candidate-knowledge-card__head">
        <span>候选知识点</span>
        <Badge className="bg-primary/10 text-primary" variant="secondary">
          <Check className="size-3" />
          默认接受
        </Badge>
      </div>
      <label className="candidate-knowledge-card__field">
        <span>节点名称</span>
        <Input
          aria-label={`${node.label} 节点名称`}
          disabled={disabled || !editing || busy}
          maxLength={200}
          onChange={(event) =>
            setDraft((current) => ({ ...current, label: event.target.value }))
          }
          onFocus={onSelect}
          value={draft.label}
        />
      </label>
      <label className="candidate-knowledge-card__field">
        <span>目标内权重（1–100）</span>
        <Input
          aria-label={`${node.label} 目标内权重`}
          disabled={disabled || !editing || busy}
          max={100}
          min={1}
          onChange={(event) =>
            setDraft((current) => ({
              ...current,
              targetWeight: Number(event.target.value) || 1,
            }))
          }
          onFocus={onSelect}
          type="number"
          value={draft.targetWeight}
        />
      </label>
      <label className="candidate-knowledge-card__field candidate-knowledge-card__field--description">
        <span>定义与边界</span>
        <Textarea
          aria-label={`${node.label} 定义与边界`}
          disabled={disabled || !editing || busy}
          maxLength={4000}
          onChange={(event) =>
            setDraft((current) => ({
              ...current,
              description: event.target.value,
            }))
          }
          onFocus={onSelect}
          placeholder="补充定义、边界与前置关系"
          value={draft.description}
        />
      </label>
      {retryOpen ? (
        <label className="candidate-knowledge-card__field candidate-knowledge-card__field--description">
          <span>重写建议（必填）</span>
          <Textarea
            aria-label={`${node.label} 重写建议`}
            maxLength={2000}
            onChange={(event) => setRetryInstruction(event.target.value)}
            onFocus={onSelect}
            placeholder="说明希望如何改写：强调边界、补充前置、更正定义等"
            value={retryInstruction}
          />
        </label>
      ) : null}
      <div className="candidate-knowledge-card__actions">
        {editing ? (
          <>
            <Button
              disabled={disabled || busy || !draft.label.trim()}
              onClick={saveEdit}
              size="xs"
            >
              <Save className="size-3" />
              {busy ? "保存中…" : "保存"}
            </Button>
            <Button
              disabled={busy}
              onClick={cancelEdit}
              size="xs"
              type="button"
              variant="ghost"
            >
              取消
            </Button>
          </>
        ) : (
          <Button
            disabled={disabled}
            onClick={() => setEditing(true)}
            size="xs"
            type="button"
            variant="outline"
          >
            <Pencil className="size-3" />
            编辑
          </Button>
        )}
        {retryOpen ? (
          <>
            <Button
              disabled={
                disabled || retrying || retryInstruction.trim().length < 2
              }
              onClick={() => {
                onRetry(retryInstruction.trim());
                setRetryOpen(false);
                setRetryInstruction("");
              }}
              size="xs"
              type="button"
            >
              <RotateCcw className="size-3" />
              提交重试
            </Button>
            <Button
              onClick={() => {
                setRetryOpen(false);
                setRetryInstruction("");
              }}
              size="xs"
              type="button"
              variant="ghost"
            >
              取消
            </Button>
          </>
        ) : (
          <Button
            disabled={disabled || retrying}
            onClick={() => setRetryOpen(true)}
            size="xs"
            type="button"
            variant="outline"
          >
            <RotateCcw className="size-3" />
            局部重试
          </Button>
        )}
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button
              aria-label={`删除 ${node.label}`}
              disabled={disabled}
              size="icon-xs"
              type="button"
              variant="ghost"
            >
              <Trash2 className="size-3 text-destructive" />
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>
                删除候选节点「{node.label}」？
              </AlertDialogTitle>
              <AlertDialogDescription>
                该操作会同时删除候选图谱中与此节点相连的关系。正式图谱和其他节点不会被修改。
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>取消</AlertDialogCancel>
              <AlertDialogAction onClick={onDelete}>删除节点</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </article>
  );
}
