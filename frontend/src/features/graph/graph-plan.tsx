/**
 * Learning-plan UI: the controller hook, the schedule / add-to-plan dialogs and
 * the plan inspector that the Graph workbench docks as its right-hand panel.
 *
 * Pure logic lives in `graph-plan-model.ts`; this module owns React state and
 * rendering only.
 */
import { useCallback, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CalendarClock,
  Check,
  ChevronLeft,
  ChevronRight,
  Crosshair,
  LoaderCircle,
  Lock,
  Play,
  RefreshCcw,
  Route,
  Settings2,
  Sparkles,
  Timer,
  X,
} from "lucide-react";
import { toast } from "sonner";

import {
  ApiError,
  getRoadmap,
  insertRoadmapItem,
  replanRoadmap,
  rescheduleRoadmapItem,
  updateAction,
  updateGoalPlanning,
} from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { workspaceQueryKey } from "@/lib/query-keys";
import type { GoalPlanningUpdate } from "@/types/goals";
import type { Goal } from "@/types/goals";
import type { Graph, GraphNode } from "@/types/graphs";
import type { Roadmap } from "@/types/workflow";
import {
  addDays,
  buildPlanModel,
  calendarDayDiff,
  planDayChoices,
  planDayKey,
  planDurationLabel,
  planMonthDayLabel,
  planRevisionRationale,
  type PlanDayView,
  type PlanItemView,
  type PlanModel,
  type PlanSuggestion,
} from "./graph-plan-model";

/* ------------------------------------------------------------------ *
 * Query / mutation wiring
 * ------------------------------------------------------------------ */

export type PlanControllerState =
  | "no-goal"
  | "loading"
  | "ready"
  | "empty"
  | "error";

export type PlanController = {
  goalId?: string;
  state: PlanControllerState;
  errorMessage?: string;
  model: PlanModel;
  roadmap?: Roadmap;
  generate: () => void;
  generating: boolean;
  generateError?: string;
  scheduleItem: (
    item: PlanItemView,
    payload: { dayIndex: number; durationMinutes: number; rationale: string },
  ) => Promise<void>;
  scheduling: boolean;
  addNode: (payload: {
    nodeId: string;
    dayIndex?: number;
    durationMinutes?: number;
    rationale?: string;
  }) => Promise<void>;
  adding: boolean;
  /** Finish a plan task. Task status ≠ node mastery (see the module docs). */
  completeItem: (item: PlanItemView) => Promise<void>;
  completing: boolean;
  saveSettings: (payload: GoalPlanningUpdate) => Promise<void>;
  savingSettings: boolean;
  reload: () => void;
};

export function useGraphPlanController(args: {
  workspaceId: string;
  goal?: Goal | null;
  graph?: Graph | null;
}): PlanController {
  const { workspaceId, goal, graph } = args;
  const goalId = goal?.id;
  const queryClient = useQueryClient();
  const roadmapQuery = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "roadmap", goalId),
    queryFn: () => getRoadmap(goalId as string),
    enabled: Boolean(goalId),
    // A missing plan is an expected answer (404), not a transient failure:
    // retrying only delays the "生成学习计划" state the learner needs.
    retry: 0,
  });

  const roadmap = roadmapQuery.data;
  const model = useMemo(
    () => buildPlanModel({ roadmap, goal, graph }),
    [goal, graph, roadmap],
  );

  const missing =
    roadmapQuery.isError &&
    roadmapQuery.error instanceof ApiError &&
    roadmapQuery.error.status === 404;

  /**
   * Plan surface state. Deliberately keyed off `data` rather than
   * `isPending`: a paused/offline query keeps `isPending` true forever, which
   * must not look like an endless spinner. "No roadmap data" is simply
   * "还没有学习计划" — the one state that offers 生成学习计划.
   */
  const state: PlanControllerState = !goalId
    ? "no-goal"
    : roadmap
      ? "ready"
      : roadmapQuery.isError && !missing
        ? "error"
        : roadmapQuery.isLoading
          ? "loading"
          : "empty";


  const acceptRoadmap = useCallback(
    (next: Roadmap) => {
      queryClient.setQueryData(
        workspaceQueryKey(workspaceId, "roadmap", goalId),
        next,
      );
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "actions"),
      });
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "dashboard"),
      });
      if (graph?.id) {
        void queryClient.invalidateQueries({
          queryKey: workspaceQueryKey(workspaceId, "graph", graph.id),
        });
      }
    },
    [goalId, graph?.id, queryClient, workspaceId],
  );

  const replan = useMutation({
    mutationFn: () => replanRoadmap(goalId as string),
    onSuccess: (next) => {
      acceptRoadmap(next);
      toast.success("学习计划已更新并立即生效");
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const reschedule = useMutation({
    mutationFn: (input: {
      roadmapId: string;
      actionId: string;
      baseVersion: number;
      dayIndex: number;
      durationMinutes: number;
      rationale: string;
    }) =>
      rescheduleRoadmapItem(input.roadmapId, input.actionId, {
        base_version: input.baseVersion,
        day_index: input.dayIndex,
        position: 0,
        duration_minutes: input.durationMinutes,
        rationale: input.rationale,
      }),
    onSuccess: (next) => {
      acceptRoadmap(next);
      toast.success("任务时间已更新");
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const insert = useMutation({
    mutationFn: (input: {
      roadmapId: string;
      baseVersion: number;
      nodeId: string;
      dayIndex?: number;
      durationMinutes?: number;
      rationale?: string;
    }) =>
      insertRoadmapItem(input.roadmapId, {
        base_version: input.baseVersion,
        node_id: input.nodeId,
        day_index: input.dayIndex ?? null,
        duration_minutes: input.durationMinutes ?? null,
        rationale: input.rationale ?? "",
      }),
    onSuccess: (next) => {
      acceptRoadmap(next);
      toast.success("已加入学习计划");
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const complete = useMutation({
    mutationFn: (item: PlanItemView) =>
      updateAction(item.id, { status: "completed" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "roadmap", goalId),
      });
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "actions"),
      });
      toast.success("任务已完成（节点掌握仍以练习证据为准）");
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const settings = useMutation({
    mutationFn: async (payload: GoalPlanningUpdate) => {
      await updateGoalPlanning(goalId as string, payload);
      return replanRoadmap(goalId as string);
    },
    onSuccess: (next) => {
      acceptRoadmap(next);
      toast.success("计划设置已保存并重新规划");
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return {
    goalId,
    state,
    errorMessage: roadmapQuery.isError ? roadmapQuery.error.message : undefined,
    model,
    roadmap,
    generate: () => replan.mutate(),
    generating: replan.isPending,
    generateError: replan.isError ? replan.error.message : undefined,
    scheduleItem: async (item, payload) => {
      if (!roadmap) throw new Error("没有可调整的计划");
      await reschedule.mutateAsync({
        roadmapId: roadmap.id,
        actionId: item.id,
        baseVersion: roadmap.version,
        dayIndex: payload.dayIndex,
        durationMinutes: payload.durationMinutes,
        rationale: payload.rationale,
      });
    },
    scheduling: reschedule.isPending,
    addNode: async (payload) => {
      if (!roadmap) throw new Error("还没有学习计划，先生成计划再加入");
      await insert.mutateAsync({
        roadmapId: roadmap.id,
        baseVersion: roadmap.version,
        nodeId: payload.nodeId,
        dayIndex: payload.dayIndex,
        durationMinutes: payload.durationMinutes,
        rationale: payload.rationale,
      });
    },
    adding: insert.isPending,
    completeItem: async (item) => {
      await complete.mutateAsync(item);
    },
    completing: complete.isPending,
    saveSettings: async (payload) => {
      await settings.mutateAsync(payload);
    },
    savingSettings: settings.isPending,
    reload: () => void roadmapQuery.refetch(),
  };
}

/* ------------------------------------------------------------------ *
 * Dialogs
 * ------------------------------------------------------------------ */

const DURATION_OPTIONS = [15, 20, 30, 45, 60, 90];

export function PlanScheduleDialog({
  item,
  model,
  pending,
  onClose,
  onSubmit,
}: {
  item: PlanItemView;
  model: PlanModel;
  pending: boolean;
  onClose: () => void;
  onSubmit: (payload: {
    dayIndex: number;
    durationMinutes: number;
    rationale: string;
  }) => void;
}) {
  const [dayIndex, setDayIndex] = useState(item.dayIndex);
  const [durationMinutes, setDurationMinutes] = useState(item.durationMinutes);
  const [rationale, setRationale] = useState("");
  const choices = useMemo(
    () => planDayChoices(model, item.dayIndex),
    [item.dayIndex, model],
  );

  return (
    <Dialog onOpenChange={(open) => (open ? undefined : onClose())} open>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>调整「{item.title}」</DialogTitle>
          <DialogDescription>
            只改这一项的安排方式；保存后计划立即生效，不需要重新规划。
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-1">
          <Label className="grid gap-2 text-xs">
            日期
            <Select
              onValueChange={(value) => setDayIndex(Number(value))}
              value={String(dayIndex)}
            >
              <SelectTrigger aria-label="选择日期">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {choices.map((choice) => (
                  <SelectItem key={choice.dayIndex} value={String(choice.dayIndex)}>
                    {choice.label}
                    {choice.minutes ? ` · 已排 ${choice.minutes} 分钟` : " · 暂无安排"}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Label>
          <Label className="grid gap-2 text-xs">
            预计用时
            <Select
              onValueChange={(value) => setDurationMinutes(Number(value))}
              value={String(durationMinutes)}
            >
              <SelectTrigger aria-label="选择预计用时">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DURATION_OPTIONS.map((value) => (
                  <SelectItem key={value} value={String(value)}>
                    {value} 分钟
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Label>
          <Label className="grid gap-2 text-xs">
            原因（可选）
            <Input
              maxLength={80}
              onChange={(event) => setRationale(event.target.value)}
              placeholder="例如：今天时间不足"
              value={rationale}
            />
          </Label>
        </div>
        <DialogFooter>
          <Button onClick={onClose} variant="outline">
            取消
          </Button>
          <Button
            disabled={pending}
            onClick={() =>
              onSubmit({
                dayIndex,
                durationMinutes,
                rationale:
                  rationale.trim() ||
                  planRevisionRationale(model, dayIndex, durationMinutes),
              })
            }
          >
            {pending ? "保存中…" : "保存"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function AddToPlanDialog({
  node,
  model,
  suggestion,
  pending,
  available,
  onClose,
  onSubmit,
}: {
  node: GraphNode;
  model: PlanModel;
  suggestion: PlanSuggestion;
  pending: boolean;
  available: boolean;
  onClose: () => void;
  onSubmit: (payload: {
    dayIndex: number;
    durationMinutes: number;
    rationale: string;
  }) => void;
}) {
  const [dayIndex, setDayIndex] = useState(suggestion.dayIndex);
  const [durationMinutes, setDurationMinutes] = useState(
    suggestion.durationMinutes,
  );
  const choices = useMemo(() => planDayChoices(model), [model]);

  return (
    <Dialog onOpenChange={(open) => (open ? undefined : onClose())} open>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>安排「{node.label}」</DialogTitle>
          <DialogDescription>
            加入计划只会生成可执行任务；节点是否掌握仍由练习与证据决定。
          </DialogDescription>
        </DialogHeader>
        <div className="lg-plan-suggest">
          <p className="lg-plan-suggest__head">
            系统建议：{suggestion.label}
            <span>· {suggestion.durationMinutes} 分钟</span>
          </p>
          <ul>
            {suggestion.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
        <div className="grid gap-4 py-1">
          <Label className="grid gap-2 text-xs">
            日期
            <Select
              onValueChange={(value) => setDayIndex(Number(value))}
              value={String(dayIndex)}
            >
              <SelectTrigger aria-label="选择加入的日期">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {choices.map((choice) => (
                  <SelectItem key={choice.dayIndex} value={String(choice.dayIndex)}>
                    {choice.label}
                    {choice.minutes ? ` · 已排 ${choice.minutes} 分钟` : " · 暂无安排"}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Label>
          <Label className="grid gap-2 text-xs">
            预计用时
            <Select
              onValueChange={(value) => setDurationMinutes(Number(value))}
              value={String(durationMinutes)}
            >
              <SelectTrigger aria-label="选择预计用时">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DURATION_OPTIONS.map((value) => (
                  <SelectItem key={value} value={String(value)}>
                    {value} 分钟
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Label>
        </div>
        {available ? null : (
          <p className="lg-plan__hint is-warn">
            还没有学习计划：先在「学习计划 · 生成学习计划」里生成一份，再加入具体节点。
          </p>
        )}
        <DialogFooter>
          <Button onClick={onClose} variant="outline">
            取消
          </Button>
          <Button
            disabled={pending || !available}
            onClick={() =>
              onSubmit({
                dayIndex,
                durationMinutes,
                rationale: `手动加入计划：${node.label}`,
              })
            }
          >
            {pending ? "加入中…" : "加入计划"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ *
 * Plan inspector
 * ------------------------------------------------------------------ */

type PlanTab = "today" | "upcoming" | "calendar" | "settings";

function PlanItemRow({
  item,
  busy,
  onLocate,
  onStart,
  onComplete,
  onReschedule,
}: {
  item: PlanItemView;
  busy: boolean;
  onLocate: (item: PlanItemView) => void;
  onStart: (item: PlanItemView) => void;
  onComplete?: (item: PlanItemView) => void;
  onReschedule?: (item: PlanItemView) => void;
}) {
  return (
    <article
      className={[
        "lg-plan-item",
        item.isToday ? "is-today" : "",
        item.done ? "is-done" : "",
        item.blocked ? "is-blocked" : "",
        item.isOverdue ? "is-overdue" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div className="lg-plan-item__main">
        <div className="lg-plan-item__title-row">
          <h4 className="lg-plan-item__title">{item.title}</h4>
          <span className={`lg-plan-item__status is-${item.status}`}>
            {item.statusLabel}
          </span>
        </div>
        <p className="lg-plan-item__meta">
          <span>{item.routing.label.replace(/^开始/, "")}</span>
          <span>·</span>
          <span>{item.durationMinutes} 分钟</span>
          <span>·</span>
          <span>{item.dayLabel}</span>
        </p>
        {item.reason ? (
          <p className="lg-plan-item__reason">
            {item.blocked ? <Lock className="size-3" /> : null}
            {item.reason}
          </p>
        ) : null}
        {item.warnings.length ? (
          <p className="lg-plan-item__warn">
            <AlertTriangle className="size-3" />
            {item.warnings[0]}
          </p>
        ) : null}
      </div>
      <div className="lg-plan-item__actions">
        {item.blocked ? (
          <Button disabled size="xs" variant="outline">
            待解锁
          </Button>
        ) : (
          <Button disabled={busy || item.done} onClick={() => onStart(item)} size="xs">
            <Play className="size-3" />
            {item.done ? "已完成" : item.routing.label}
          </Button>
        )}
        {item.nodeId ? (
          <Button
            aria-label="在图中定位"
            onClick={() => onLocate(item)}
            size="icon-xs"
            title="在图中定位"
            variant="outline"
          >
            <Crosshair className="size-3" />
          </Button>
        ) : null}
        {onReschedule && !item.blocked && !item.done ? (
          <Button
            aria-label="调整时间"
            onClick={() => onReschedule(item)}
            size="icon-xs"
            title="调整时间"
            variant="outline"
          >
            <CalendarClock className="size-3" />
          </Button>
        ) : null}
        {onComplete && !item.blocked && !item.done ? (
          <Button
            aria-label="标记任务完成"
            onClick={() => onComplete(item)}
            size="icon-xs"
            title="标记任务完成（不等于节点已掌握）"
            variant="outline"
          >
            <Check className="size-3" />
          </Button>
        ) : null}
      </div>
    </article>
  );
}

function PlanDaySection({
  day,
  busy,
  onLocate,
  onStart,
  onComplete,
  onReschedule,
}: {
  day: PlanDayView;
  busy: boolean;
  onLocate: (item: PlanItemView) => void;
  onStart: (item: PlanItemView) => void;
  onComplete?: (item: PlanItemView) => void;
  onReschedule?: (item: PlanItemView) => void;
}) {
  return (
    <section className="lg-plan-day">
      <header className="lg-plan-day__head">
        <strong>{day.headline}</strong>
        <span>{planMonthDayLabel(day.date)}</span>
        <em className={day.overMinutes > 0 ? "is-warn" : undefined}>
          {day.minutes} 分钟 · {day.items.length} 项
        </em>
      </header>
      <div className="lg-plan-day__items">
        {day.items.map((item) => (
          <PlanItemRow
            busy={busy}
            item={item}
            key={item.id}
            onComplete={onComplete}
            onLocate={onLocate}
            onReschedule={onReschedule}
            onStart={onStart}
          />
        ))}
      </div>
    </section>
  );
}

export function GraphPlanInspector({
  controller,
  busy,
  showPath,
  onTogglePath,
  onClose,
  onLocateNode,
  onStartLearn,
  onStartPractice,
}: {
  controller: PlanController;
  busy: boolean;
  showPath: boolean;
  onTogglePath: (next: boolean) => void;
  onClose: () => void;
  onLocateNode: (nodeId: string) => void;
  onStartLearn: (item: PlanItemView) => void;
  onStartPractice: (item: PlanItemView) => void;
}) {
  const { model, state } = controller;
  const [tab, setTab] = useState<PlanTab>("today");
  const [scheduleTarget, setScheduleTarget] = useState<PlanItemView>();
  const [showAllChanges, setShowAllChanges] = useState(false);
  const [calendarMonth, setCalendarMonth] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });
  const [selectedDayKey, setSelectedDayKey] = useState<string>();

  const locate = useCallback(
    (item: PlanItemView) => {
      if (item.nodeId) onLocateNode(item.nodeId);
    },
    [onLocateNode],
  );
  const start = useCallback(
    (item: PlanItemView) => {
      if (item.routing.route === "practice") onStartPractice(item);
      else onStartLearn(item);
    },
    [onStartLearn, onStartPractice],
  );
  const complete = useCallback(
    (item: PlanItemView) => {
      void controller.completeItem(item).catch(() => undefined);
    },
    [controller],
  );

  const tabs: Array<{ id: PlanTab; label: string }> = [
    { id: "today", label: `今天 (${model.todayItems.length})` },
    { id: "upcoming", label: `接下来 (${model.upcomingItems.length})` },
    { id: "calendar", label: "日历" },
    { id: "settings", label: "设置" },
  ];

  const upcomingDays = model.days.filter(
    (day) => calendarDayDiff(day.date, model.today) > 0,
  );
  const calendarCells = useMemo(() => {
    const year = calendarMonth.getFullYear();
    const month = calendarMonth.getMonth();
    const first = new Date(year, month, 1);
    const startOffset = (first.getDay() + 6) % 7;
    const gridStart = addDays(first, -startOffset);
    const byKey = new Map(model.days.map((day) => [planDayKey(day.date), day]));
    return Array.from({ length: 42 }, (_, index) => {
      const date = addDays(gridStart, index);
      const key = planDayKey(date);
      return {
        key,
        date,
        inMonth: date.getMonth() === month,
        isToday: calendarDayDiff(date, model.today) === 0,
        day: byKey.get(key),
      };
    });
  }, [calendarMonth, model.days, model.today]);

  const selectedDay =
    model.days.find((day) => planDayKey(day.date) === selectedDayKey) ??
    model.days.find((day) => day.isToday) ??
    model.days[0];
  const headlineChanges = model.changes.slice(0, 3);
  const blockedItems = model.items.filter((item) => item.blocked);

  return (
    <div className="lg-plan">
      <header className="lg-plan__head">
        <div>
          <span className="lg-plan__eyebrow">学习计划</span>
          <h2>{controller.roadmap?.title ?? "学习计划"}</h2>
        </div>
        <Button
          aria-label="关闭学习计划"
          onClick={onClose}
          size="icon-xs"
          title="关闭"
          variant="ghost"
        >
          <X className="size-3.5" />
        </Button>
      </header>

      {state === "no-goal" ? (
        <p className="lg-plan__empty">
          这张图谱还没有可规划的学习目标。完成目标澄清后，计划才能按可用时间安排。
        </p>
      ) : state === "error" ? (
        <div className="lg-plan__empty" role="alert">
          <p>{controller.errorMessage ?? "计划暂时不可用。"}</p>
          <Button onClick={controller.reload} size="xs" variant="outline">
            <RefreshCcw className="size-3" />
            重新读取
          </Button>
        </div>
      ) : (
        <>
          <nav aria-label="学习计划分区" className="lg-plan__tabs">
            {tabs.map((entry) => (
              <button
                aria-selected={tab === entry.id}
                className={`lg-plan__tab${tab === entry.id ? " is-active" : ""}`}
                key={entry.id}
                onClick={() => setTab(entry.id)}
                role="tab"
                type="button"
              >
                {entry.label}
              </button>
            ))}
          </nav>

          {controller.roadmap && model.changes.length ? (
            <div className="lg-plan-stale" role="status">
              <div className="lg-plan-stale__head">
                <AlertTriangle className="size-3.5" />
                <strong>学习计划需要更新</strong>
              </div>
              <ul>
                {(showAllChanges ? model.changes : headlineChanges).map((change) => (
                  <li key={change.label}>{change.label}</li>
                ))}
              </ul>
              <div className="lg-plan-stale__actions">
                {model.changes.length > headlineChanges.length ? (
                  <Button
                    onClick={() => setShowAllChanges((current) => !current)}
                    size="xs"
                    variant="ghost"
                  >
                    {showAllChanges ? "收起变化" : "查看变化"}
                  </Button>
                ) : null}
                <Button
                  disabled={controller.generating}
                  onClick={controller.generate}
                  size="xs"
                >
                  {controller.generating ? "更新中…" : "更新计划"}
                </Button>
              </div>
            </div>
          ) : null}

          {state === "loading" ? (
            <p className="lg-plan__empty">
              <LoaderCircle className="size-3.5 animate-spin" />
              正在读取学习计划…
            </p>
          ) : state === "empty" ? (
            <div className="lg-plan__empty">
              <strong>还没有学习计划。</strong>
              <p>
                LearnGraph 会按你的目标、知识前置关系、当前掌握状态与可用时间安排下一步。
              </p>
              <Button
                disabled={controller.generating}
                onClick={controller.generate}
                size="xs"
              >
                <Sparkles className="size-3" />
                {controller.generating ? "生成中…" : "生成学习计划"}
              </Button>
            </div>
          ) : (
            <>
              {tab === "today" ? (
                <div className="lg-plan__body">
                  <div className="lg-plan__summary">
                    <strong>{model.totals.windowLabel}</strong>
                    <p>
                      {model.totals.windowDays} 天 · {model.totals.windowItemCount} 项任务 ·
                      约 {planDurationLabel(model.totals.windowMinutes)}
                    </p>
                    <p className="lg-plan__summary-sub">
                      已完成 {model.totals.completedCount}/{model.totals.itemCount}
                      {model.totals.blockedCount
                        ? ` · ${model.totals.blockedCount} 项待解锁`
                        : ""}
                    </p>
                  </div>

                  {model.overCapacityDays.map((day) => (
                    <div className="lg-plan__alert" key={day.dayIndex} role="status">
                      <div>
                        <strong>{day.headline}</strong>
                        <span>超出你的日学习容量 {day.overMinutes} 分钟</span>
                      </div>
                      {day.item ? (
                        <Button
                          onClick={() => setScheduleTarget(day.item)}
                          size="xs"
                          variant="outline"
                        >
                          调整
                        </Button>
                      ) : null}
                    </div>
                  ))}

                  {model.finished ? (
                    <div className="lg-plan__empty">
                      <strong>本轮计划已完成 🎉</strong>
                      <p>按当前掌握状态生成下一阶段安排即可继续。</p>
                      <Button
                        disabled={controller.generating}
                        onClick={controller.generate}
                        size="xs"
                      >
                        <Sparkles className="size-3" />
                        根据当前状态生成下一阶段计划
                      </Button>
                    </div>
                  ) : model.todayItems.length ? (
                    <div className="lg-plan-day__items">
                      {model.todayItems.map((item) => (
                        <PlanItemRow
                          busy={busy}
                          item={item}
                          key={item.id}
                          onComplete={complete}
                          onLocate={locate}
                          onReschedule={setScheduleTarget}
                          onStart={start}
                        />
                      ))}
                    </div>
                  ) : (
                    <p className="lg-plan__empty">
                      今天没有安排任务。
                      {model.upcomingItems.length
                        ? `下一项在${model.upcomingItems[0].dayLabel}。`
                        : ""}
                    </p>
                  )}

                  {model.upcomingItems.length && !model.finished ? (
                    <>
                      <h3 className="lg-plan__section-title">接下来</h3>
                      <div className="lg-plan-day__items">
                        {model.upcomingItems.slice(0, 3).map((item) => (
                          <PlanItemRow
                            busy={busy}
                            item={item}
                            key={item.id}
                            onComplete={complete}
                            onLocate={locate}
                            onReschedule={setScheduleTarget}
                            onStart={start}
                          />
                        ))}
                      </div>
                      {model.upcomingItems.length > 3 ? (
                        <Button
                          className="lg-plan__more"
                          onClick={() => setTab("upcoming")}
                          size="xs"
                          variant="ghost"
                        >
                          查看全部 {model.items.length} 项
                        </Button>
                      ) : null}
                    </>
                  ) : null}
                </div>
              ) : null}

              {tab === "upcoming" ? (
                <div className="lg-plan__body">
                  {upcomingDays.length ? (
                    upcomingDays.slice(0, 5).map((day) => (
                      <PlanDaySection
                        busy={busy}
                        day={day}
                        onComplete={complete}
                        key={day.dayIndex}
                        onLocate={locate}
                        onReschedule={setScheduleTarget}
                        onStart={start}
                      />
                    ))
                  ) : (
                    <p className="lg-plan__empty">没有后续安排。</p>
                  )}
                  {upcomingDays.length > 5 ? (
                    <Button
                      className="lg-plan__more"
                      onClick={() => setTab("calendar")}
                      size="xs"
                      variant="ghost"
                    >
                      其余 {upcomingDays.length - 5} 天在日历中查看
                    </Button>
                  ) : null}
                  {blockedItems.length ? (
                    <section className="lg-plan-day">
                      <header className="lg-plan-day__head">
                        <strong>待解锁</strong>
                        <span>前置未完成</span>
                        <em>{blockedItems.length} 项</em>
                      </header>
                      <div className="lg-plan-day__items">
                        {blockedItems.map((item) => (
                          <PlanItemRow
                            busy={busy}
                            item={item}
                            key={item.id}
                            onComplete={complete}
                            onLocate={locate}
                            onStart={start}
                          />
                        ))}
                      </div>
                    </section>
                  ) : null}
                </div>
              ) : null}

              {tab === "calendar" ? (
                <div className="lg-plan__body">
                  <div className="lg-plan-calendar__nav">
                    <Button
                      aria-label="上一个月"
                      onClick={() =>
                        setCalendarMonth(
                          new Date(
                            calendarMonth.getFullYear(),
                            calendarMonth.getMonth() - 1,
                            1,
                          ),
                        )
                      }
                      size="icon-xs"
                      variant="outline"
                    >
                      <ChevronLeft className="size-3.5" />
                    </Button>
                    <strong>
                      {calendarMonth.getFullYear()} 年 {calendarMonth.getMonth() + 1} 月
                    </strong>
                    <Button
                      aria-label="下一个月"
                      onClick={() =>
                        setCalendarMonth(
                          new Date(
                            calendarMonth.getFullYear(),
                            calendarMonth.getMonth() + 1,
                            1,
                          ),
                        )
                      }
                      size="icon-xs"
                      variant="outline"
                    >
                      <ChevronRight className="size-3.5" />
                    </Button>
                  </div>
                  <div className="lg-plan-calendar__weekdays" aria-hidden>
                    {["一", "二", "三", "四", "五", "六", "日"].map((label) => (
                      <span key={label}>{label}</span>
                    ))}
                  </div>
                  <div className="lg-plan-calendar__grid" role="grid">
                    {calendarCells.map((cell) => (
                      <button
                        aria-label={`${planMonthDayLabel(cell.date)} · ${
                          cell.day ? `${cell.day.items.length} 项安排` : "无安排"
                        }`}
                        className={[
                          "lg-plan-calendar__cell",
                          cell.inMonth ? "" : "is-outside",
                          cell.isToday ? "is-today" : "",
                          selectedDay && cell.key === planDayKey(selectedDay.date)
                            ? "is-selected"
                            : "",
                          cell.day && cell.day.overMinutes > 0 ? "is-over" : "",
                        ]
                          .filter(Boolean)
                          .join(" ")}
                        key={cell.key}
                        onClick={() => setSelectedDayKey(cell.key)}
                        type="button"
                      >
                        <span>{cell.date.getDate()}</span>
                        {cell.day ? <em>{cell.day.items.length} 项</em> : null}
                      </button>
                    ))}
                  </div>
                  <div className="lg-plan-calendar__detail">
                    {selectedDay ? (
                      <PlanDaySection
                        busy={busy}
                        day={selectedDay}
                        onComplete={complete}
                        onLocate={locate}
                        onReschedule={setScheduleTarget}
                        onStart={start}
                      />
                    ) : (
                      <p className="lg-plan__empty">选择一个日期查看当天安排。</p>
                    )}
                  </div>
                </div>
              ) : null}

              {tab === "settings" ? (
                <PlanSettingsSection controller={controller} model={model} />
              ) : null}
            </>
          )}

          <footer className="lg-plan__footer">
            <label className="lg-plan__toggle">
              <Switch
                aria-label="显示计划路径"
                checked={showPath}
                onCheckedChange={onTogglePath}
              />
              <span>
                <Route className="size-3" />
                显示计划路径
              </span>
            </label>
            <div className="lg-plan__footer-actions">
              <Button
                disabled={controller.generating}
                onClick={() => setTab("settings")}
                size="xs"
                variant="ghost"
              >
                <Settings2 className="size-3" />
                计划设置
              </Button>
              <Button
                disabled={controller.generating || state === "empty"}
                onClick={controller.generate}
                size="xs"
                variant="outline"
              >
                <RefreshCcw className="size-3" />
                {controller.generating ? "重新规划中…" : "重新规划"}
              </Button>
            </div>
          </footer>
        </>
      )}

      {scheduleTarget && controller.roadmap ? (
        <PlanScheduleDialog
          item={scheduleTarget}
          key={`${controller.roadmap.id}-${scheduleTarget.id}-${scheduleTarget.dayIndex}`}
          model={model}
          onClose={() => setScheduleTarget(undefined)}
          onSubmit={(payload) => {
            void controller
              .scheduleItem(scheduleTarget, payload)
              .then(() => setScheduleTarget(undefined))
              .catch(() => undefined);
          }}
          pending={controller.scheduling}
        />
      ) : null}
    </div>
  );
}

function PlanSettingsSection({
  controller,
  model,
}: {
  controller: PlanController;
  model: PlanModel;
}) {
  const formatDate = (value: Date | null) =>
    value
      ? `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(
          value.getDate(),
        ).padStart(2, "0")}`
      : "";
  const [minutesPerDay, setMinutesPerDay] = useState(model.inputs.minutesPerDay);
  const [daysPerWeek, setDaysPerWeek] = useState(model.inputs.daysPerWeek);
  const [sessionMinutes, setSessionMinutes] = useState(model.inputs.sessionMinutes);
  const [deadline, setDeadline] = useState(formatDate(model.inputs.deadlineAt));

  const dirty =
    minutesPerDay !== model.inputs.minutesPerDay ||
    daysPerWeek !== model.inputs.daysPerWeek ||
    sessionMinutes !== model.inputs.sessionMinutes ||
    deadline !== formatDate(model.inputs.deadlineAt);

  return (
    <div className="lg-plan__body">
      <div className="lg-plan-settings">
        <label className="lg-plan-settings__row">
          <span>
            每日可学习时间
            <em>计划按这个容量分摊任务</em>
          </span>
          <Input
            aria-label="每日可学习时间（分钟）"
            max={1440}
            min={15}
            onChange={(event) => setMinutesPerDay(Number(event.target.value))}
            step={15}
            type="number"
            value={minutesPerDay}
          />
        </label>
        <label className="lg-plan-settings__row">
          <span>
            每周学习天数
            <em>只声明天数，不指定具体星期</em>
          </span>
          <Select
            onValueChange={(value) => setDaysPerWeek(Number(value))}
            value={String(daysPerWeek)}
          >
            <SelectTrigger aria-label="每周学习天数">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {[1, 2, 3, 4, 5, 6, 7].map((value) => (
                <SelectItem key={value} value={String(value)}>
                  {value} 天
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <label className="lg-plan-settings__row">
          <span>
            单次学习时长
            <em>新任务默认使用的时长</em>
          </span>
          <Select
            onValueChange={(value) => setSessionMinutes(Number(value))}
            value={String(sessionMinutes)}
          >
            <SelectTrigger aria-label="单次学习时长">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {DURATION_OPTIONS.map((value) => (
                <SelectItem key={value} value={String(value)}>
                  {value} 分钟
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <label className="lg-plan-settings__row">
          <span>
            目标日期
            <em>留空表示不设截止日期</em>
          </span>
          <Input
            aria-label="目标日期"
            onChange={(event) => setDeadline(event.target.value)}
            type="date"
            value={deadline}
          />
        </label>
      </div>

      <Button
        className="lg-plan-settings__save"
        disabled={!dirty || controller.savingSettings || !controller.goalId}
        onClick={() => {
          void controller
            .saveSettings({
              availability: {
                minutes_per_day: minutesPerDay,
                days_per_week: daysPerWeek,
              },
              preferences: { session_minutes: sessionMinutes },
              deadline_at: deadline
                ? new Date(`${deadline}T23:59:00`).toISOString()
                : null,
            })
            .catch(() => undefined);
        }}
        size="sm"
      >
        <Timer className="size-3.5" />
        {controller.savingSettings ? "保存中…" : "保存并重新规划"}
      </Button>

      <p className="lg-plan__hint">
        计划只保存你给出的约束；优先级、掌握缺口与复习紧迫度由系统按真实数据计算，不在这里手填。
      </p>
      {controller.generateError ? (
        <p className="lg-plan__hint is-warn">{controller.generateError}</p>
      ) : null}
    </div>
  );
}
