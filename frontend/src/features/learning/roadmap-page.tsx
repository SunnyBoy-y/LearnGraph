import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  BarChart3,
  Calendar as CalendarIcon,
  CalendarClock,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Download,
  ListTree,
  MoreHorizontal,
  Play,
  RefreshCcw,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { workspaceQueryKey } from "@/lib/query-keys";
import { toast } from "sonner";

import {
  ApiError,
  getRoadmap,
  replanRoadmap,
  rescheduleRoadmapItem,
} from "@/api";
import {
  LoadingState,
  PageFrame,
  PageIntro,
  SectionHeading,
  StatePill,
  Surface,
} from "@/components/shared/page-elements";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Badge } from "@/components/ui/badge";
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
import { Textarea } from "@/components/ui/textarea";
import type { ActionItem, Roadmap } from "@/types/workflow";

type ViewMode = "list" | "chart" | "calendar";
type DayMarker =
  | "empty"
  | "has-tasks"
  | "in-progress"
  | "completed"
  | "blocked"
  | "mixed";

type PrerequisiteEntry = {
  label: string;
  node_id?: string;
  satisfied?: boolean;
};

const DAY_PREVIEW_COUNT = 2;
const CHART_COLORS = {
  scheduled: "var(--primary)",
  blocked: "#d97706",
  completed: "#16a34a",
  pending: "#64748b",
  inProgress: "#2563eb",
};

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function startOfDay(date: Date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function addDays(date: Date, amount: number) {
  const next = new Date(date);
  next.setDate(next.getDate() + amount);
  return next;
}

function sameDay(left: Date, right: Date) {
  return (
    left.getFullYear() === right.getFullYear() &&
    left.getMonth() === right.getMonth() &&
    left.getDate() === right.getDate()
  );
}

function formatDayKey(date: Date) {
  const y = date.getFullYear();
  const m = `${date.getMonth() + 1}`.padStart(2, "0");
  const d = `${date.getDate()}`.padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function formatDayLabel(date: Date) {
  return date.toLocaleDateString("zh-CN", {
    month: "short",
    day: "numeric",
    weekday: "short",
  });
}

function parseDueDate(value: string | null | undefined, fallback: Date) {
  if (!value) return startOfDay(fallback);
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return startOfDay(fallback);
  return startOfDay(parsed);
}

function itemPrerequisites(item: ActionItem): PrerequisiteEntry[] {
  const metadata = item.metadata_json as Record<string, unknown>;
  const prerequisites = metadata.prerequisites as
    | {
        items?: Array<
          string | { label?: string; node_id?: string; satisfied?: boolean }
        >;
        blocked_by?: Array<
          string | { label?: string; node_id?: string; satisfied?: boolean }
        >;
      }
    | undefined;
  const source = prerequisites?.items?.length
    ? prerequisites.items
    : (prerequisites?.blocked_by ?? []);
  return source.map((entry) => {
    if (typeof entry === "string") {
      return { label: entry, satisfied: item.status !== "blocked" };
    }
    return {
      label: entry.label ?? entry.node_id ?? "未知前置",
      node_id: entry.node_id,
      satisfied: entry.satisfied ?? item.status !== "blocked",
    };
  });
}

function dayMarkerForItems(items: ActionItem[]): DayMarker {
  if (!items.length) return "empty";
  const statuses = new Set(items.map((item) => item.status));
  if (statuses.size === 1 && statuses.has("completed")) return "completed";
  if (statuses.size === 1 && statuses.has("blocked")) return "blocked";
  if (statuses.has("in_progress")) return "in-progress";
  if (
    statuses.has("blocked") &&
    (statuses.has("pending") || statuses.has("completed"))
  ) {
    return "mixed";
  }
  if (statuses.has("completed") && statuses.has("pending")) return "mixed";
  if (statuses.has("completed")) return "completed";
  return "has-tasks";
}

function markerLabel(marker: DayMarker) {
  switch (marker) {
    case "completed":
      return "全部完成";
    case "in-progress":
      return "进行中";
    case "blocked":
      return "有阻断";
    case "mixed":
      return "部分完成";
    case "has-tasks":
      return "有任务";
    default:
      return "空";
  }
}

function RoadmapRescheduleDialog({
  item,
  open,
  pending,
  onOpenChange,
  onSubmit,
}: {
  item: ActionItem;
  open: boolean;
  pending: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (value: {
    dayIndex: number;
    position: number;
    durationMinutes: number;
    rationale: string;
  }) => void;
}) {
  const [dayIndex, setDayIndex] = useState(item.day_index || 1);
  const [position, setPosition] = useState(item.position + 1);
  const [durationMinutes, setDurationMinutes] = useState(item.duration_minutes);
  const [rationale, setRationale] = useState("");
  const valid =
    dayIndex >= 1 &&
    position >= 1 &&
    durationMinutes >= 15 &&
    durationMinutes <= 1440 &&
    Boolean(rationale.trim());

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>调整「{item.title}」</DialogTitle>
          <DialogDescription>
            保存会直接更新当前学习路线，不会创建草稿版本。
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-2 sm:grid-cols-3">
          <Label className="grid gap-2 text-xs">
            学习日
            <Input
              min={1}
              onChange={(event) => setDayIndex(Number(event.target.value))}
              type="number"
              value={dayIndex}
            />
          </Label>
          <Label className="grid gap-2 text-xs">
            日内顺序
            <Input
              min={1}
              onChange={(event) => setPosition(Number(event.target.value))}
              type="number"
              value={position}
            />
          </Label>
          <Label className="grid gap-2 text-xs">
            时长（分钟）
            <Input
              max={1440}
              min={15}
              onChange={(event) =>
                setDurationMinutes(Number(event.target.value))
              }
              step={5}
              type="number"
              value={durationMinutes}
            />
          </Label>
        </div>
        <Label className="grid gap-2 text-xs">
          调整理由
          <Textarea
            maxLength={2000}
            onChange={(event) => setRationale(event.target.value)}
            placeholder="例如：第三个学习日有完整时间，可集中完成实践任务。"
            value={rationale}
          />
        </Label>
        <DialogFooter>
          <Button onClick={() => onOpenChange(false)} variant="outline">
            取消
          </Button>
          <Button
            disabled={!valid || pending}
            onClick={() =>
              onSubmit({
                dayIndex,
                position: Math.max(0, position - 1),
                durationMinutes,
                rationale: rationale.trim(),
              })
            }
          >
            {pending ? "保存中…" : "保存调整"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function TaskCard({
  item,
  workspaceId,
  canRevise,
  reschedulePending,
  onReschedule,
}: {
  item: ActionItem;
  workspaceId: string;
  canRevise: boolean;
  reschedulePending: boolean;
  onReschedule: (item: ActionItem) => void;
}) {
  const metadata = item.metadata_json as Record<string, unknown>;
  const scoreBreakdown = metadata.score_breakdown as
    | Record<string, number>
    | undefined;
  const schedule = metadata.schedule as
    | {
        scheduled_after_deadline?: boolean;
        exceeds_daily_capacity?: boolean;
      }
    | undefined;
  const acceptance = stringList(metadata.acceptance_criteria);
  const prerequisites = itemPrerequisites(item);
  const isBlocked = item.status === "blocked";
  const isCompleted = item.status === "completed";
  const warning = [
    schedule?.scheduled_after_deadline ? "超出截止" : "",
    schedule?.exceeds_daily_capacity ? "超出容量" : "",
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <article
      className={`lg-roadmap-task${isBlocked ? " is-blocked" : ""}${isCompleted ? " is-completed" : ""}`}
    >
      <div className="lg-roadmap-task__main">
        <div className="lg-roadmap-task__lead">
          <Badge variant="outline">{item.action_type}</Badge>
          <StatePill status={item.status} />
          <span className="lg-roadmap-task__meta">
            {item.duration_minutes} 分钟
          </span>
        </div>

        <div className="lg-roadmap-task__content">
          <h3 className="lg-roadmap-task__title break-words">{item.title}</h3>
          {item.description ? (
            <p className="lg-roadmap-task__desc">{item.description}</p>
          ) : null}

          <div className="lg-roadmap-task__chips">
            {prerequisites.map((entry) => (
              <span
                className={`lg-roadmap-chip${entry.satisfied ? " is-ok" : " is-blocked"}`}
                key={`${item.id}-${entry.node_id ?? entry.label}`}
              >
                {entry.satisfied ? (
                  <CheckCircle2 className="size-3" />
                ) : (
                  <AlertCircle className="size-3" />
                )}
                {entry.label}
              </span>
            ))}
            {acceptance[0] ? (
              <span className="lg-roadmap-chip is-accept">
                验收 · {acceptance[0]}
              </span>
            ) : null}
            {warning ? (
              <span className="lg-roadmap-chip is-warn">{warning}</span>
            ) : null}
          </div>

          <details className="lg-roadmap-task__score">
            <summary>排序依据</summary>
            <p>
              优先级 {item.priority} · 权重{" "}
              {Math.round((scoreBreakdown?.importance ?? 0) * 100)}% · 掌握缺口{" "}
              {Math.round((scoreBreakdown?.mastery_gap ?? 0) * 100)}% · 复习紧迫度{" "}
              {Math.round((scoreBreakdown?.retrieval_urgency ?? 0) * 100)}% ·
              证据缺口 {Math.round((scoreBreakdown?.evidence_gap ?? 0) * 100)}% ·
              期限 {Math.round((scoreBreakdown?.deadline_urgency ?? 0) * 100)}%
            </p>
          </details>
        </div>

        <div className="lg-roadmap-task__actions">
          {isBlocked ? (
            <Button disabled size="xs" variant="outline">
              待解锁
            </Button>
          ) : (
            <Button asChild size="xs" variant="outline">
              <Link
                to={
                  item.graph_id
                    ? `/w/${workspaceId}/graphs/${item.graph_id}?node=${encodeURIComponent(item.node_id ?? "")}`
                    : `/w/${workspaceId}/home`
                }
              >
                <Play className="size-3" />
                开始
              </Link>
            </Button>
          )}
          <Button
            disabled={
              !canRevise || reschedulePending || isBlocked || isCompleted
            }
            onClick={() => onReschedule(item)}
            size="icon-xs"
            variant="outline"
            aria-label="调整时间"
          >
            <CalendarClock className="size-3" />
          </Button>
        </div>
      </div>
    </article>
  );
}

export function RoadmapPlannerPage() {
  const { workspaceId = "", goalId = "" } = useParams();
  const queryClient = useQueryClient();
  const [editingItem, setEditingItem] = useState<ActionItem>();
  const [viewMode, setViewMode] = useState<ViewMode>("list");
  const [selectedDayKey, setSelectedDayKey] = useState<string>();
  const [expandedDays, setExpandedDays] = useState<Set<number>>(
    () => new Set([1]),
  );
  const [dayShowAll, setDayShowAll] = useState<Set<number>>(() => new Set());
  const [calendarMonth, setCalendarMonth] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });

  const roadmap = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "roadmap", goalId),
    queryFn: () => getRoadmap(goalId),
    enabled: Boolean(goalId),
  });

  function acceptLatest(result: Roadmap) {
    queryClient.setQueryData(workspaceQueryKey(workspaceId, "roadmap", goalId), result);
    void Promise.all([
      queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "actions") }),
      queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "dashboard") }),
    ]);
  }

  const replan = useMutation({
    mutationFn: () => replanRoadmap(goalId),
    onSuccess: (result) => {
      acceptLatest(result);
      toast.success("学习路线已更新并立即生效");
    },
    onError: (error) => toast.error(error.message),
  });

  const reschedule = useMutation({
    mutationFn: ({
      roadmapId,
      actionId,
      baseVersion,
      dayIndex,
      position,
      durationMinutes,
      rationale,
    }: {
      roadmapId: string;
      actionId: string;
      baseVersion: number;
      dayIndex: number;
      position: number;
      durationMinutes: number;
      rationale: string;
    }) =>
      rescheduleRoadmapItem(roadmapId, actionId, {
        base_version: baseVersion,
        day_index: dayIndex,
        position,
        duration_minutes: durationMinutes,
        rationale,
      }),
    onSuccess: (result) => {
      setEditingItem(undefined);
      acceptLatest(result);
      toast.success("任务时间已更新");
    },
    onError: (error) => toast.error(error.message),
  });

  const planData = roadmap.data;
  const planStartedAt = useMemo(() => {
    if (!planData) return startOfDay(new Date());
    const snapshot = planData.planning_snapshot as Record<string, unknown>;
    const raw = snapshot.plan_started_at;
    if (typeof raw === "string") {
      const parsed = new Date(raw);
      if (!Number.isNaN(parsed.getTime())) return startOfDay(parsed);
    }
    return startOfDay(new Date(planData.created_at));
  }, [planData]);

  const dayGroups = useMemo(() => {
    if (!planData)
      return [] as Array<{
        dayIndex: number;
        date: Date;
        key: string;
        items: ActionItem[];
        minutes: number;
        marker: DayMarker;
      }>;

    const byDay = new Map<number, ActionItem[]>();
    for (const item of planData.items) {
      const day = item.day_index > 0 ? item.day_index : 1;
      const list = byDay.get(day) ?? [];
      list.push(item);
      byDay.set(day, list);
    }

    return [...byDay.entries()]
      .sort((left, right) => left[0] - right[0])
      .map(([dayIndex, items]) => {
        const sorted = [...items].sort((a, b) => {
          if (a.status === "blocked" && b.status !== "blocked") return 1;
          if (a.status !== "blocked" && b.status === "blocked") return -1;
          return a.position - b.position;
        });
        const date =
          sorted.find((item) => item.due_at)?.due_at != null
            ? parseDueDate(
                sorted.find((item) => item.due_at)?.due_at,
                addDays(planStartedAt, dayIndex - 1),
              )
            : addDays(planStartedAt, dayIndex - 1);
        return {
          dayIndex,
          date,
          key: formatDayKey(date),
          items: sorted,
          minutes: sorted
            .filter((item) => item.status !== "blocked")
            .reduce((sum, item) => sum + item.duration_minutes, 0),
          marker: dayMarkerForItems(sorted),
        };
      });
  }, [planData, planStartedAt]);

  const itemsByDateKey = useMemo(() => {
    const map = new Map<string, ActionItem[]>();
    for (const group of dayGroups) {
      map.set(group.key, group.items);
    }
    return map;
  }, [dayGroups]);

  if (roadmap.isPending) {
    return (
      <PageFrame>
        <LoadingState label="正在读取路线…" />
      </PageFrame>
    );
  }

  if (roadmap.isError) {
    const roadmapMissing =
      roadmap.error instanceof ApiError && roadmap.error.status === 404;

    return (
      <PageFrame>
        <PageIntro
          description={
            roadmapMissing
              ? "路线只会由显式规划创建；生成后立即生效，无需草稿发布。"
              : "路线数据暂时不可用，请重新读取或稍后再试。"
          }
          title={roadmapMissing ? "尚未生成学习路线" : "暂时无法读取路线"}
        />
        <Surface
          className="flex flex-wrap items-center justify-between gap-4 p-5"
          role="alert"
        >
          <p className="max-w-2xl text-sm text-muted-foreground">
            {roadmapMissing
              ? "为当前目标生成学习安排后，可以在这里查看每日任务、前置条件与日历进度。"
              : roadmap.error.message}
          </p>
          <div className="flex gap-2">
            {!roadmapMissing ? (
              <Button
                onClick={() => void roadmap.refetch()}
                size="sm"
                variant="outline"
              >
                重新读取
              </Button>
            ) : null}
            <Button
              disabled={replan.isPending}
              onClick={() => replan.mutate()}
              size="sm"
            >
              {replan.isPending ? "生成中…" : "生成学习路线"}
            </Button>
          </div>
        </Surface>
      </PageFrame>
    );
  }

  const data = roadmap.data;
  const scheduledItems = data.items.filter((item) => item.status !== "blocked");
  const blockedItems = data.items.filter((item) => item.status === "blocked");
  const completedItems = data.items.filter(
    (item) => item.status === "completed",
  );
  const pendingItems = data.items.filter((item) => item.status === "pending");
  const inProgressItems = data.items.filter(
    (item) => item.status === "in_progress",
  );
  const totalMinutes = scheduledItems.reduce(
    (sum, item) => sum + item.duration_minutes,
    0,
  );
  const snapshot = data.planning_snapshot as Record<string, unknown>;
  const snapshotGoal = snapshot.goal as
    | {
        deadline_at?: string | null;
        availability?: { minutes_per_day?: number; days_per_week?: number };
        preferences?: {
          preferred_action_types?: string[];
          session_minutes?: number;
        };
      }
    | undefined;
  const capacity = snapshot.capacity_summary as
    | {
        over_capacity_days?: number[];
        scheduled_after_deadline_count?: number;
      }
    | undefined;
  const unresolvedGaps = stringList(snapshot.unresolved_gaps);
  const canRevise = data.status === "published" || data.status === "draft";
  const dailyCapacity = snapshotGoal?.availability?.minutes_per_day ?? 60;

  const dayLoadChart = dayGroups.map((group) => {
    const scheduled = group.items
      .filter((item) => item.status !== "blocked")
      .reduce((sum, item) => sum + item.duration_minutes, 0);
    const blocked = group.items
      .filter((item) => item.status === "blocked")
      .reduce((sum, item) => sum + item.duration_minutes, 0);
    return {
      day: `D${group.dayIndex}`,
      label: formatDayLabel(group.date),
      scheduled,
      blocked,
      capacity: dailyCapacity,
      tasks: group.items.length,
    };
  });

  const statusPie = [
    { name: "待处理", value: pendingItems.length, color: CHART_COLORS.pending },
    {
      name: "进行中",
      value: inProgressItems.length,
      color: CHART_COLORS.inProgress,
    },
    {
      name: "已完成",
      value: completedItems.length,
      color: CHART_COLORS.completed,
    },
    { name: "待解锁", value: blockedItems.length, color: CHART_COLORS.blocked },
  ].filter((entry) => entry.value > 0);

  const typeBars = Object.entries(
    data.items.reduce<Record<string, number>>((acc, item) => {
      acc[item.action_type] = (acc[item.action_type] ?? 0) + 1;
      return acc;
    }, {}),
  ).map(([name, value]) => ({ name, value }));

  const activeDayKey =
    selectedDayKey ??
    dayGroups.find((group) => group.marker !== "empty")?.key ??
    dayGroups[0]?.key;
  const activeDayItems = activeDayKey
    ? (itemsByDateKey.get(activeDayKey) ?? [])
    : [];
  const activeDayGroup = dayGroups.find((group) => group.key === activeDayKey);

  const exportCalendar = () => {
    const escapeIcs = (value: string) =>
      value
        .replace(/\\/g, "\\\\")
        .replace(/\n/g, "\\n")
        .replace(/,/g, "\\,")
        .replace(/;/g, "\\;");
    const lines = [
      "BEGIN:VCALENDAR",
      "VERSION:2.0",
      "PRODID:-//LearnGraph//Roadmap//ZH",
      "CALSCALE:GREGORIAN",
    ];
    scheduledItems.forEach((item) => {
      const start = new Date(item.due_at ?? data.created_at);
      lines.push(
        "BEGIN:VEVENT",
        `UID:${item.id}@learngraph`,
        `DTSTAMP:${new Date(data.updated_at).toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z")}`,
        `DTSTART:${start.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z")}`,
        `DURATION:PT${item.duration_minutes}M`,
        `SUMMARY:${escapeIcs(item.title)}`,
        `DESCRIPTION:${escapeIcs(item.description)}`,
        "END:VEVENT",
      );
    });
    lines.push("END:VCALENDAR");
    const url = URL.createObjectURL(
      new Blob([lines.join("\r\n")], {
        type: "text/calendar;charset=utf-8",
      }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `roadmap-${data.goal_id}.ics`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const monthCells = (() => {
    const year = calendarMonth.getFullYear();
    const month = calendarMonth.getMonth();
    const first = new Date(year, month, 1);
    const startOffset = (first.getDay() + 6) % 7;
    const gridStart = addDays(first, -startOffset);
    return Array.from({ length: 42 }, (_, index) => {
      const date = addDays(gridStart, index);
      const key = formatDayKey(date);
      const items = itemsByDateKey.get(key) ?? [];
      return {
        date,
        key,
        inMonth: date.getMonth() === month,
        isToday: sameDay(date, new Date()),
        items,
        marker: dayMarkerForItems(items),
        minutes: items
          .filter((item) => item.status !== "blocked")
          .reduce((sum, item) => sum + item.duration_minutes, 0),
      };
    });
  })();

  const viewTabs: Array<{
    id: ViewMode;
    label: string;
    icon: typeof ListTree;
  }> = [
    { id: "list", label: "学习安排", icon: ListTree },
    { id: "chart", label: "预览图表", icon: BarChart3 },
    { id: "calendar", label: "日历", icon: CalendarIcon },
  ];

  return (
    <PageFrame className="lg-roadmap-page">
      <PageIntro
        actions={
          <div className="flex items-center gap-2">
            <Button
              disabled={replan.isPending}
              onClick={() => replan.mutate()}
              size="sm"
              variant="outline"
            >
              <RefreshCcw className="size-3.5" />
              {replan.isPending ? "重排中…" : "重新规划"}
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  aria-label="更多路线操作"
                  size="icon-sm"
                  variant="outline"
                >
                  <MoreHorizontal className="size-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-52">
                <DropdownMenuLabel>路线操作</DropdownMenuLabel>
                <DropdownMenuItem onSelect={exportCalendar}>
                  <Download className="size-4" />
                  导出日程
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        }
        description="路线按图谱节点、前置关系、掌握证据和时间约束生成；调整后立即生效。"
        title={data.title}
      />

      <div className="lg-roadmap-tabs" role="tablist" aria-label="路线视图">
        {viewTabs.map((tab) => {
          const Icon = tab.icon;
          const active = viewMode === tab.id;
          return (
            <button
              aria-selected={active}
              className={`lg-roadmap-tabs__item${active ? " is-active" : ""}`}
              key={tab.id}
              onClick={() => setViewMode(tab.id)}
              role="tab"
              type="button"
            >
              <Icon className="size-3.5" />
              {tab.label}
            </button>
          );
        })}
        <div className="lg-roadmap-tabs__meta">
          <StatePill status="published" label="生效中" />
          <span>
            {dayGroups.length} 天 · {totalMinutes} 分钟 · {scheduledItems.length}{" "}
            可执行
            {blockedItems.length ? ` · ${blockedItems.length} 待解锁` : ""}
          </span>
        </div>
      </div>

      {unresolvedGaps.length ? (
        <div className="lg-roadmap-gaps" role="alert">
          <strong>建议处理</strong>
          <ul>
            {unresolvedGaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {viewMode === "chart" ? (
        <Surface className="lg-roadmap-chart">
          <div className="lg-roadmap-chart__header">
            <SectionHeading
              description="每日负荷、任务状态与类型分布；容量帮助识别超载学习日。"
              title="预览图表"
            />
          </div>

          <div className="lg-roadmap-chart__kpis" aria-label="关键指标">
            <div>
              <span>学习日</span>
              <strong>{dayGroups.length}</strong>
            </div>
            <div>
              <span>总时长</span>
              <strong>
                {totalMinutes}
                <small>分钟</small>
              </strong>
            </div>
            <div>
              <span>可执行</span>
              <strong>
                {scheduledItems.length}
                <small>{completedItems.length} 完成</small>
              </strong>
            </div>
            <div>
              <span>待解锁</span>
              <strong>{blockedItems.length}</strong>
            </div>
            <div>
              <span>日容量</span>
              <strong>
                {dailyCapacity}
                <small>分钟</small>
              </strong>
            </div>
            <div>
              <span>检查</span>
              <strong className="is-text">
                {capacity?.over_capacity_days?.length ||
                capacity?.scheduled_after_deadline_count
                  ? `${capacity.over_capacity_days?.length ?? 0} 超量`
                  : "通过"}
              </strong>
            </div>
          </div>

          <div className="lg-roadmap-chart__grid">
            <section className="lg-roadmap-chart__panel lg-roadmap-chart__panel--wide">
              <header>
                <strong>每日学习负荷</strong>
                <span>可执行分钟 / 待解锁分钟</span>
              </header>
              <div className="lg-roadmap-chart__canvas">
                <ResponsiveContainer height="100%" width="100%">
                  <BarChart
                    data={dayLoadChart}
                    margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
                  >
                    <CartesianGrid
                      stroke="var(--border)"
                      strokeDasharray="3 3"
                      vertical={false}
                    />
                    <XAxis
                      axisLine={false}
                      dataKey="day"
                      fontSize={11}
                      tickLine={false}
                    />
                    <YAxis
                      axisLine={false}
                      fontSize={11}
                      tickLine={false}
                      width={36}
                    />
                    <ChartTooltip
                      contentStyle={{
                        border: "1px solid var(--border)",
                        borderRadius: 8,
                        fontSize: 12,
                      }}
                      formatter={(value, name) => {
                        const label =
                          name === "scheduled"
                            ? "可执行"
                            : name === "blocked"
                              ? "待解锁"
                              : String(name);
                        return [`${value} 分钟`, label];
                      }}
                      labelFormatter={(_, payload) => {
                        const point = payload?.[0]?.payload as
                          | { day?: string; label?: string; tasks?: number }
                          | undefined;
                        return `${point?.day ?? ""} · ${point?.label ?? ""} · ${point?.tasks ?? 0} 项`;
                      }}
                    />
                    <Legend
                      formatter={(value) =>
                        value === "scheduled"
                          ? "可执行"
                          : value === "blocked"
                            ? "待解锁"
                            : value
                      }
                    />
                    <Bar
                      dataKey="scheduled"
                      fill={CHART_COLORS.scheduled}
                      maxBarSize={28}
                      name="scheduled"
                      radius={[4, 4, 0, 0]}
                      stackId="load"
                    />
                    <Bar
                      dataKey="blocked"
                      fill={CHART_COLORS.blocked}
                      maxBarSize={28}
                      name="blocked"
                      radius={[4, 4, 0, 0]}
                      stackId="load"
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </section>

            <section className="lg-roadmap-chart__panel">
              <header>
                <strong>任务状态</strong>
                <span>当前路线任务构成</span>
              </header>
              <div className="lg-roadmap-chart__canvas lg-roadmap-chart__canvas--pie">
                {statusPie.length ? (
                  <ResponsiveContainer height="100%" width="100%">
                    <PieChart>
                      <Pie
                        cx="50%"
                        cy="50%"
                        data={statusPie}
                        dataKey="value"
                        innerRadius={48}
                        nameKey="name"
                        outerRadius={74}
                        paddingAngle={2}
                      >
                        {statusPie.map((entry) => (
                          <Cell fill={entry.color} key={entry.name} />
                        ))}
                      </Pie>
                      <ChartTooltip
                        contentStyle={{
                          border: "1px solid var(--border)",
                          borderRadius: 8,
                          fontSize: 12,
                        }}
                        formatter={(value, name) => [
                          `${value} 项`,
                          String(name),
                        ]}
                      />
                      <Legend />
                    </PieChart>
                  </ResponsiveContainer>
                ) : (
                  <p className="lg-roadmap-chart__empty">暂无任务</p>
                )}
              </div>
            </section>

            <section className="lg-roadmap-chart__panel">
              <header>
                <strong>行动类型</strong>
                <span>learn / review / practice 等</span>
              </header>
              <div className="lg-roadmap-chart__canvas">
                {typeBars.length ? (
                  <ResponsiveContainer height="100%" width="100%">
                    <BarChart
                      data={typeBars}
                      layout="vertical"
                      margin={{ top: 4, right: 12, left: 8, bottom: 0 }}
                    >
                      <CartesianGrid
                        horizontal={false}
                        stroke="var(--border)"
                        strokeDasharray="3 3"
                      />
                      <XAxis
                        allowDecimals={false}
                        axisLine={false}
                        fontSize={11}
                        tickLine={false}
                        type="number"
                      />
                      <YAxis
                        axisLine={false}
                        dataKey="name"
                        fontSize={11}
                        tickLine={false}
                        type="category"
                        width={72}
                      />
                      <ChartTooltip
                        contentStyle={{
                          border: "1px solid var(--border)",
                          borderRadius: 8,
                          fontSize: 12,
                        }}
                        formatter={(value) => [`${value} 项`, "数量"]}
                      />
                      <Bar
                        dataKey="value"
                        fill={CHART_COLORS.inProgress}
                        maxBarSize={18}
                        radius={[0, 4, 4, 0]}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <p className="lg-roadmap-chart__empty">暂无类型数据</p>
                )}
              </div>
            </section>
          </div>
        </Surface>
      ) : null}

      {viewMode === "calendar" ? (
        <Surface className="lg-roadmap-calendar">
          <div className="lg-roadmap-calendar__header">
            <SectionHeading
              description="用日历标记区分：有任务、进行中、全部完成、有阻断、部分完成。"
              title="日历视图"
            />
            <div className="lg-roadmap-calendar__nav">
              <Button
                onClick={() =>
                  setCalendarMonth(
                    new Date(
                      calendarMonth.getFullYear(),
                      calendarMonth.getMonth() - 1,
                      1,
                    ),
                  )
                }
                size="icon-sm"
                variant="outline"
              >
                <ChevronLeft className="size-4" />
              </Button>
              <strong>
                {calendarMonth.getFullYear()} 年 {calendarMonth.getMonth() + 1}{" "}
                月
              </strong>
              <Button
                onClick={() =>
                  setCalendarMonth(
                    new Date(
                      calendarMonth.getFullYear(),
                      calendarMonth.getMonth() + 1,
                      1,
                    ),
                  )
                }
                size="icon-sm"
                variant="outline"
              >
                <ChevronRight className="size-4" />
              </Button>
            </div>
          </div>

          <div className="lg-roadmap-calendar__legend" aria-label="日历图例">
            <span data-marker="has-tasks">有任务</span>
            <span data-marker="in-progress">进行中</span>
            <span data-marker="completed">全部完成</span>
            <span data-marker="blocked">有阻断</span>
            <span data-marker="mixed">部分完成</span>
          </div>

          <div className="lg-roadmap-calendar__weekdays" aria-hidden>
            {["一", "二", "三", "四", "五", "六", "日"].map((label) => (
              <span key={label}>{label}</span>
            ))}
          </div>

          <div
            className="lg-roadmap-calendar__grid"
            role="grid"
            aria-label="学习日历"
          >
            {monthCells.map((cell) => {
              const selected = cell.key === activeDayKey;
              return (
                <button
                  className={[
                    "lg-roadmap-calendar__cell",
                    cell.inMonth ? "" : "is-outside",
                    cell.isToday ? "is-today" : "",
                    selected ? "is-selected" : "",
                    `marker-${cell.marker}`,
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  key={cell.key}
                  onClick={() => setSelectedDayKey(cell.key)}
                  type="button"
                >
                  <span className="lg-roadmap-calendar__date">
                    {cell.date.getDate()}
                  </span>
                  {cell.marker !== "empty" ? (
                    <span className="lg-roadmap-calendar__dot" aria-hidden />
                  ) : null}
                  {cell.items.length ? (
                    <span className="lg-roadmap-calendar__meta">
                      {cell.items.length} 项
                      {cell.minutes ? ` · ${cell.minutes}m` : ""}
                    </span>
                  ) : (
                    <span className="lg-roadmap-calendar__meta is-muted">—</span>
                  )}
                  <span className="sr-only">
                    {formatDayLabel(cell.date)} · {markerLabel(cell.marker)}
                  </span>
                </button>
              );
            })}
          </div>

          <div className="lg-roadmap-calendar__detail">
            <header>
              <div>
                <strong>
                  {activeDayGroup
                    ? `第 ${activeDayGroup.dayIndex} 学习日`
                    : activeDayKey
                      ? activeDayKey
                      : "选择日期"}
                </strong>
                <span>
                  {activeDayGroup
                    ? formatDayLabel(activeDayGroup.date)
                    : "点击日历格子查看当日任务与前置条件"}
                </span>
              </div>
              {activeDayGroup ? (
                <StatePill
                  status={
                    activeDayGroup.marker === "completed"
                      ? "approved"
                      : activeDayGroup.marker === "blocked"
                        ? "warning"
                        : "pending"
                  }
                  label={markerLabel(activeDayGroup.marker)}
                />
              ) : null}
            </header>
            {activeDayItems.length ? (
              <div className="lg-roadmap-day__items">
                {activeDayItems.map((item) => (
                  <TaskCard
                    canRevise={canRevise}
                    item={item}
                    key={item.id}
                    onReschedule={setEditingItem}
                    reschedulePending={reschedule.isPending}
                    workspaceId={workspaceId}
                  />
                ))}
              </div>
            ) : (
              <p className="lg-roadmap-calendar__empty">
                这一天还没有安排任务。
              </p>
            )}
          </div>
        </Surface>
      ) : null}

      {viewMode === "list" ? (
        <Surface className="lg-roadmap-board overflow-hidden">
          <div className="lg-roadmap-board__header">
            <SectionHeading
              description="按学习日折叠展开；默认展开首日，其余点击标题查看任务。"
              title="学习安排"
            />
            {dayGroups.length > 1 ? (
              <div className="lg-roadmap-board__header-actions">
                <Button
                  onClick={() =>
                    setExpandedDays(
                      new Set(dayGroups.map((group) => group.dayIndex)),
                    )
                  }
                  size="xs"
                  variant="ghost"
                >
                  全部展开
                </Button>
                <Button
                  onClick={() => setExpandedDays(new Set())}
                  size="xs"
                  variant="ghost"
                >
                  全部折叠
                </Button>
              </div>
            ) : null}
          </div>
          <div className="lg-roadmap-list" role="list" aria-label="路线学习日">
            {dayGroups.map((group) => {
              const overCapacity =
                (snapshotGoal?.availability?.minutes_per_day ?? Infinity) <
                group.minutes;
              const expanded = expandedDays.has(group.dayIndex);
              const showAll = dayShowAll.has(group.dayIndex);
              const visibleItems = showAll
                ? group.items
                : group.items.slice(0, DAY_PREVIEW_COUNT);
              const hiddenCount = Math.max(
                0,
                group.items.length - DAY_PREVIEW_COUNT,
              );
              return (
                <section
                  className={`lg-roadmap-list-day${expanded ? " is-open" : ""}`}
                  key={group.dayIndex}
                  role="listitem"
                >
                  <button
                    aria-expanded={expanded}
                    className="lg-roadmap-list-day__toggle"
                    onClick={() =>
                      setExpandedDays((current) => {
                        const next = new Set(current);
                        if (next.has(group.dayIndex)) next.delete(group.dayIndex);
                        else next.add(group.dayIndex);
                        return next;
                      })
                    }
                    type="button"
                  >
                    <span className="lg-roadmap-list-day__title">
                      <ChevronDown
                        aria-hidden
                        className={`lg-roadmap-list-day__chevron size-4${expanded ? " is-open" : ""}`}
                      />
                      <strong>D{group.dayIndex}</strong>
                      <span>{formatDayLabel(group.date)}</span>
                    </span>
                    <span className="lg-roadmap-list-day__meta">
                      <span>
                        {group.minutes} 分钟 · {group.items.length} 项
                      </span>
                      <em data-marker={group.marker}>
                        {markerLabel(group.marker)}
                      </em>
                      {overCapacity ? (
                        <StatePill label="超出容量" status="warning" />
                      ) : null}
                    </span>
                  </button>
                  {expanded ? (
                    <div className="lg-roadmap-list-day__body">
                      <div className="lg-roadmap-list-day__items">
                        {visibleItems.map((item) => (
                          <TaskCard
                            canRevise={canRevise}
                            item={item}
                            key={item.id}
                            onReschedule={setEditingItem}
                            reschedulePending={reschedule.isPending}
                            workspaceId={workspaceId}
                          />
                        ))}
                      </div>
                      {hiddenCount > 0 ? (
                        <button
                          className="lg-roadmap-list-day__more"
                          onClick={() =>
                            setDayShowAll((current) => {
                              const next = new Set(current);
                              if (next.has(group.dayIndex))
                                next.delete(group.dayIndex);
                              else next.add(group.dayIndex);
                              return next;
                            })
                          }
                          type="button"
                        >
                          {showAll
                            ? "收起任务"
                            : `显示更多任务（还有 ${hiddenCount} 项）`}
                        </button>
                      ) : null}
                    </div>
                  ) : (
                    <div className="lg-roadmap-list-day__preview" aria-hidden>
                      {group.items.slice(0, 2).map((item) => (
                        <span key={item.id}>
                          {item.title.replace(/^学习：|^待解锁：/, "")}
                        </span>
                      ))}
                      {group.items.length > 2 ? (
                        <span>等 {group.items.length} 项</span>
                      ) : null}
                    </div>
                  )}
                </section>
              );
            })}
          </div>
        </Surface>
      ) : null}

      {editingItem ? (
        <RoadmapRescheduleDialog
          item={editingItem}
          key={`${data.id}-${editingItem.id}`}
          onOpenChange={(open) => {
            if (!open && !reschedule.isPending) setEditingItem(undefined);
          }}
          onSubmit={(value) =>
            reschedule.mutate({
              roadmapId: data.id,
              actionId: editingItem.id,
              baseVersion: data.version,
              ...value,
            })
          }
          open
          pending={reschedule.isPending}
        />
      ) : null}
    </PageFrame>
  );
}
