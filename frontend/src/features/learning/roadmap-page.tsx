import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CalendarClock,
  Download,
  History,
  MoreHorizontal,
  Play,
  RefreshCcw,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";
import {
  Bar,
  BarChart,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
} from "recharts";
import { toast } from "sonner";

import {
  ApiError,
  getRoadmap,
  getRoadmapVersion,
  listRoadmaps,
  publishRoadmap,
  rejectRoadmap,
  replanRoadmap,
  rescheduleRoadmapItem,
} from "@/api";
import {
  ErrorState,
  LoadingState,
  PageFrame,
  PageIntro,
  SectionHeading,
  StatePill,
  Surface,
} from "@/components/shared/page-elements";
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
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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

function roadmapStatusLabel(status: string) {
  if (status === "draft") return "待审核";
  if (status === "published") return "已发布";
  if (status === "superseded") return "已被取代";
  if (status === "rejected") return "已拒绝";
  return status;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
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
  const [dayIndex, setDayIndex] = useState(item.day_index);
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
            保存会创建新的路线版本；当前版本及其 ActionItem 保持可追溯。
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
            {pending ? "正在创建版本…" : "保存为新版本"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function RoadmapPlannerPage() {
  const { workspaceId = "", goalId = "" } = useParams();
  const queryClient = useQueryClient();
  const [selectedVersionId, setSelectedVersionId] = useState<string>();
  const [editingItem, setEditingItem] = useState<ActionItem>();
  const [rejectRationale, setRejectRationale] = useState("");
  const roadmap = useQuery({
    queryKey: ["roadmap", goalId],
    queryFn: () => getRoadmap(goalId),
    enabled: Boolean(goalId),
  });
  const versions = useQuery({
    queryKey: ["roadmap-versions", goalId],
    queryFn: () => listRoadmaps(goalId),
    enabled: Boolean(goalId) && roadmap.isSuccess,
  });
  const selectedVersion = useQuery({
    queryKey: ["roadmap-version", selectedVersionId],
    queryFn: () => getRoadmapVersion(selectedVersionId ?? ""),
    enabled: Boolean(selectedVersionId),
  });

  function acceptLatest(result: Roadmap) {
    setSelectedVersionId(undefined);
    queryClient.setQueryData(["roadmap", goalId], result);
    void Promise.all([
      queryClient.invalidateQueries({ queryKey: ["roadmap-versions", goalId] }),
      queryClient.invalidateQueries({ queryKey: ["actions"] }),
      queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
    ]);
  }

  const replan = useMutation({
    mutationFn: () => replanRoadmap(goalId),
    onSuccess: (result) => {
      acceptLatest(result);
      toast.success(`已生成路线 v${result.version}`);
    },
    onError: (error) => toast.error(error.message),
  });
  const publish = useMutation({
    mutationFn: (id: string) => publishRoadmap(id),
    onSuccess: (result) => {
      acceptLatest(result);
      toast.success(`路线 v${result.version} 已发布`);
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
      toast.success(`调整已保存为路线 v${result.version}`);
    },
    onError: (error) => toast.error(error.message),
  });
  const reject = useMutation({
    mutationFn: ({
      id,
      version,
      rationale,
    }: {
      id: string;
      version: number;
      rationale: string;
    }) => rejectRoadmap(id, { base_version: version, rationale }),
    onSuccess: (result) => {
      setRejectRationale("");
      acceptLatest(result);
      toast.success(`路线 v${result.version} 已拒绝`);
    },
    onError: (error) => toast.error(error.message),
  });

  if (roadmap.isPending)
    return (
      <PageFrame>
        <LoadingState label="正在读取路线…" />
      </PageFrame>
    );
  if (roadmap.isError) {
    const roadmapMissing =
      roadmap.error instanceof ApiError && roadmap.error.status === 404;

    return (
      <PageFrame>
        <PageIntro
          description={
            roadmapMissing
              ? "路线只会由显式规划创建；读取操作不会悄悄写入新的草稿。"
              : "路线数据暂时不可用，请重新读取或稍后再试。"
          }
          title={roadmapMissing ? "尚未生成路线草稿" : "暂时无法读取路线"}
        />
        <Surface
          className="flex flex-wrap items-center justify-between gap-4 p-5"
          role="alert"
        >
          <p className="max-w-2xl text-sm text-muted-foreground">
            {roadmapMissing
              ? "为当前目标生成第一版学习安排后，可以在这里审核、调整并发布。"
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
              {replan.isPending ? "生成中…" : "生成路线草稿"}
            </Button>
          </div>
        </Surface>
      </PageFrame>
    );
  }
  if (selectedVersionId && selectedVersion.isPending)
    return (
      <PageFrame>
        <LoadingState label="正在读取历史路线…" />
      </PageFrame>
    );
  if (selectedVersionId && selectedVersion.isError)
    return (
      <PageFrame>
        <ErrorState
          message={selectedVersion.error.message}
          onRetry={() => void selectedVersion.refetch()}
        />
      </PageFrame>
    );

  const data = selectedVersion.data ?? roadmap.data;
  const isLatest = data.id === roadmap.data.id;
  const scheduledItems = data.items.filter((item) => item.status !== "blocked");
  const blockedItems = data.items.filter((item) => item.status === "blocked");
  const days = [...new Set(scheduledItems.map((item) => item.day_index))].sort(
    (left, right) => left - right,
  );
  const dayOverview = days.map((day) => {
    const items = scheduledItems.filter((item) => item.day_index === day);
    return {
      day: `D${day}`,
      minutes: items.reduce((sum, item) => sum + item.duration_minutes, 0),
      tasks: items.length,
    };
  });
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
  const assumptions = stringList(snapshot.assumptions);
  const unresolvedGaps = stringList(snapshot.unresolved_gaps);
  const canRevise = isLatest && ["draft", "published"].includes(data.status);
  const canPublish = isLatest && data.status === "draft";

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
    anchor.download = `roadmap-v${data.version}.ics`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <PageFrame className="roadmap-page">
      <PageIntro
        actions={
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button aria-label="更多路线操作" size="icon-sm" variant="outline">
                <MoreHorizontal className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuLabel className="flex items-center gap-2">
                <History className="size-3.5" />
                路线版本
              </DropdownMenuLabel>
              {(versions.data ?? [roadmap.data]).map((version) => (
                <DropdownMenuItem
                  key={version.id}
                  onSelect={() =>
                    setSelectedVersionId(
                      version.id === roadmap.data.id ? undefined : version.id,
                    )
                  }
                >
                  v{version.version} · {roadmapStatusLabel(version.status)}
                  {version.id === data.id ? " · 当前" : ""}
                </DropdownMenuItem>
              ))}
              <DropdownMenuSeparator />
              <DropdownMenuItem
                disabled={replan.isPending}
                onSelect={() => replan.mutate()}
              >
                <RefreshCcw className="size-4" />
                {replan.isPending ? "重排中…" : "自动重排"}
              </DropdownMenuItem>
              <DropdownMenuItem
                disabled={data.status !== "published"}
                onSelect={exportCalendar}
              >
                <Download className="size-4" />
                导出日程
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        }
        description="路线按图谱节点、前置关系、掌握证据和时间约束生成；每次人工调整都会保存为新版本。"
        title={data.title}
      />

      <section className="roadmap-review-bar" aria-label="路线审核状态">
        <div className="roadmap-review-bar__copy">
          <div>
            <StatePill status={data.status} label={`路线 v${data.version} · ${roadmapStatusLabel(data.status)}`} />
            <strong>
              {blockedItems.length
                ? `发现 ${blockedItems.length} 个前置阻断`
                : "前置关系检查完成"}
            </strong>
          </div>
          <p>
            {data.rationale || "此版本保留生成时的目标、证据、容量与排序快照。"}
          </p>
        </div>
        <div className="roadmap-review-bar__actions">
          {isLatest && data.status === "draft" ? (
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button size="sm" variant="ghost">拒绝修改</Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>拒绝路线 v{data.version}？</AlertDialogTitle>
                  <AlertDialogDescription>
                    拒绝不会发布或删除路线；审核理由会写入快照与审计记录。
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <Label className="grid gap-2 text-sm">
                  拒绝理由
                  <Textarea
                    maxLength={2000}
                    onChange={(event) => setRejectRationale(event.target.value)}
                    placeholder="说明需要重新规划的约束或顺序。"
                    value={rejectRationale}
                  />
                </Label>
                <AlertDialogFooter>
                  <AlertDialogCancel>取消</AlertDialogCancel>
                  <AlertDialogAction
                    disabled={!rejectRationale.trim() || reject.isPending}
                    onClick={() =>
                      reject.mutate({
                        id: data.id,
                        version: data.version,
                        rationale: rejectRationale.trim(),
                      })
                    }
                  >
                    确认拒绝
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          ) : null}
          {canPublish ? (
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button disabled={publish.isPending} size="sm">发布路线</Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>发布路线 v{data.version}？</AlertDialogTitle>
                  <AlertDialogDescription>
                    发布会让此版本的行动进入首页，并取代同目标的旧发布路线。图谱仍需独立完成审核。
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>取消</AlertDialogCancel>
                  <AlertDialogAction onClick={() => publish.mutate(data.id)}>
                    确认发布
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          ) : null}
        </div>
      </section>

      {!isLatest ? (
        <div className="roadmap-history-notice" role="status">
          <span>
            正在查看历史路线 v{data.version}。历史版本只读，返回最新版本后才能调整或发布。
          </span>
          <Button
            onClick={() => setSelectedVersionId(undefined)}
            size="xs"
            variant="outline"
          >
            返回最新版本
          </Button>
        </div>
      ) : null}

      <section className="roadmap-summary" aria-label="路线概览">
        <span>
          <CalendarClock className="size-4" />
          {days.length} 个学习日
        </span>
        <span>
          <History className="size-4" />
          共 {scheduledItems.reduce((sum, item) => sum + item.duration_minutes, 0)} 分钟
        </span>
        <span>
          <Play className="size-4" />
          {scheduledItems.length} 个任务
        </span>
        {blockedItems.length ? <span>{blockedItems.length} 个前置阻断</span> : null}
      </section>

      {days.length > 3 ? (
        <section className="roadmap-overview__chart" aria-label="每日学习时长">
          <div>
            <span>每日学习负荷</span>
            <strong>{days.length} 天</strong>
          </div>
          <div className="roadmap-overview__canvas">
            <ResponsiveContainer height="100%" width="100%">
              <BarChart data={dayOverview} margin={{ left: 0, right: 0 }}>
                <XAxis axisLine={false} dataKey="day" fontSize={10} tickLine={false} />
                <ChartTooltip
                  contentStyle={{ border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }}
                  formatter={(value) => [`${value} 分钟`, "学习时长"]}
                />
                <Bar dataKey="minutes" fill="var(--primary)" maxBarSize={34} radius={[4, 4, 2, 2]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>
      ) : null}

      {unresolvedGaps.length ? (
        <section className="roadmap-gaps" aria-label="路线待解决问题">
          <strong>发布前待处理</strong>
          <ul>
            {unresolvedGaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </section>
      ) : null}

      <Surface className="overflow-hidden">
        <div className="roadmap-board-header">
          <SectionHeading
            description="排序依据、容量、验收条件和阻断理由均来自本版本的持久快照。"
            title="学习安排"
          />
        </div>
        <div className="roadmap-days" role="list" aria-label="路线学习日">
          {days.map((day) => {
            const dayItems = scheduledItems.filter(
              (item) => item.day_index === day,
            );
            const dayMinutes = dayItems.reduce(
              (sum, item) => sum + item.duration_minutes,
              0,
            );
            const overCapacity =
              (snapshotGoal?.availability?.minutes_per_day ?? Infinity) <
              dayMinutes;
            return (
              <section className="roadmap-day" key={day} role="listitem">
                <header>
                  <div>
                    <span>第 {day} 学习日</span>
                    <strong>{dayMinutes} 分钟</strong>
                  </div>
                  {overCapacity ? (
                    <StatePill label="超出容量" status="warning" />
                  ) : null}
                </header>
                <div className="roadmap-day__items">
                  {dayItems.map((item) => {
                    const metadata = item.metadata_json as Record<
                      string,
                      unknown
                    >;
                    const scoreBreakdown = metadata.score_breakdown as
                      | Record<string, number>
                      | undefined;
                    const schedule = metadata.schedule as
                      | {
                          scheduled_after_deadline?: boolean;
                          exceeds_daily_capacity?: boolean;
                        }
                      | undefined;
                    const acceptance = stringList(
                      metadata.acceptance_criteria,
                    );
                    return (
                      <article className="roadmap-task" key={item.id}>
                        <div className="roadmap-task__topline">
                          <span>第 {item.position + 1} 项</span>
                          <span>{item.duration_minutes} 分钟</span>
                        </div>
                        <h3 className="break-words">{item.title}</h3>
                        <p>{item.description || "无补充说明"}</p>
                        {acceptance.length ? (
                          <div className="roadmap-task__acceptance">
                            <strong>验收</strong>
                            <span>{acceptance[0]}</span>
                          </div>
                        ) : null}
                        {schedule?.scheduled_after_deadline ||
                        schedule?.exceeds_daily_capacity ? (
                          <p className="roadmap-task__warning">
                            {schedule.scheduled_after_deadline
                              ? "超出截止时间"
                              : ""}
                            {schedule.scheduled_after_deadline &&
                            schedule.exceeds_daily_capacity
                              ? " · "
                              : ""}
                            {schedule.exceeds_daily_capacity
                              ? "超出当日容量"
                              : ""}
                          </p>
                        ) : null}
                        <details className="roadmap-task__score">
                          <summary>查看任务详情与排序依据</summary>
                          <p>
                            类型 {item.action_type} · 优先级 {item.priority} · 权重 {Math.round((scoreBreakdown?.importance ?? 0) * 100)}% ·
                            掌握缺口 {Math.round((scoreBreakdown?.mastery_gap ?? 0) * 100)}% ·
                            复习紧迫度 {Math.round((scoreBreakdown?.retrieval_urgency ?? 0) * 100)}% ·
                            证据缺口 {Math.round((scoreBreakdown?.evidence_gap ?? 0) * 100)}% ·
                            期限 {Math.round((scoreBreakdown?.deadline_urgency ?? 0) * 100)}%
                          </p>
                        </details>
                        <div className="roadmap-task__actions">
                          {data.status === "published" ? (
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
                          ) : (
                            <Button disabled size="xs" variant="outline">
                              <Play className="size-3" />
                              发布后开始
                            </Button>
                          )}
                          <Button
                            disabled={!canRevise || reschedule.isPending}
                            onClick={() => setEditingItem(item)}
                            size="xs"
                            variant="outline"
                          >
                            <CalendarClock className="size-3" />
                            调整时间
                          </Button>
                        </div>
                      </article>
                    );
                  })}
                </div>
              </section>
            );
          })}
        </div>
      </Surface>

      {blockedItems.length ? (
        <section
          className="roadmap-blocked"
          aria-label="被前置条件阻断的行动"
        >
          <SectionHeading
            description="完成对应前置并重新规划后，这些行动才会进入可执行时间盒。"
            title="前置阻断"
          />
          <ul>
            {blockedItems.map((item) => {
              const metadata = item.metadata_json as Record<string, unknown>;
              const prerequisites = metadata.prerequisites as
                | {
                    blocked_by?: Array<
                      string | { label?: string; node_id?: string }
                    >;
                  }
                | undefined;
              const blockedBy = (prerequisites?.blocked_by ?? []).map(
                (entry) =>
                  typeof entry === "string"
                    ? entry
                    : entry.label ?? entry.node_id ?? "未知前置",
              );
              return (
                <li key={item.id}>
                  <div>
                    <strong>{item.title}</strong>
                    <span>{item.description}</span>
                  </div>
                  <p>需要先完成：{blockedBy.join("、") || "待核验前置"}</p>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      <section className="roadmap-rules" aria-label="路线规划输入">
        <SectionHeading
          description="这些值是当前版本生成时的快照；修改 Goal 后必须重新规划。"
          title="规划输入与假设"
        />
        <dl>
          <div>
            <dt>时间容量</dt>
            <dd>
              {snapshotGoal?.availability?.minutes_per_day ?? "—"} 分钟/天 · {snapshotGoal?.availability?.days_per_week ?? "—"} 天/周
            </dd>
          </div>
          <div>
            <dt>截止时间</dt>
            <dd>
              {snapshotGoal?.deadline_at
                ? new Date(snapshotGoal.deadline_at).toLocaleString()
                : "无固定截止时间"}
            </dd>
          </div>
          <div>
            <dt>行动偏好</dt>
            <dd>
              {snapshotGoal?.preferences?.preferred_action_types?.join("、") ||
                "未指定"}
            </dd>
          </div>
          <div>
            <dt>单次时长</dt>
            <dd>
              {snapshotGoal?.preferences?.session_minutes ?? "—"} 分钟
            </dd>
          </div>
        </dl>
        {assumptions.length ? (
          <div className="roadmap-rules__assumptions">
            <strong>显式假设</strong>
            <ul>
              {assumptions.map((assumption) => (
                <li key={assumption}>{assumption}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {capacity?.over_capacity_days?.length ||
        capacity?.scheduled_after_deadline_count ? (
          <p className="roadmap-rules__capacity">
            容量检查：{capacity.over_capacity_days?.length ?? 0} 个超量学习日 · {capacity.scheduled_after_deadline_count ?? 0} 个行动超出截止时间
          </p>
        ) : null}
      </section>

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
