/**
 * Learning-plan model layer (pure): vocabulary, learning-day ⇄ date adapters,
 * the Roadmap → plan projection, drift detection and the add/reschedule
 * adapters.
 *
 * Kept apart from `graph-plan.tsx` so the plan's user-facing wording, routes
 * and day maths stay unit-testable without React, and so the UI module only
 * exports components/hooks (fast refresh).
 */
import { MarkerType, type Edge } from "@xyflow/react";

import { createPracticeSession } from "@/api";
import type { Goal } from "@/types/goals";
import type { Graph, GraphNode } from "@/types/graphs";
import type { ActionItem, Roadmap } from "@/types/workflow";

/* ------------------------------------------------------------------ *
 * Vocabulary
 * ------------------------------------------------------------------ */

/** Where a plan item's "开始" button must send the learner. */
export type PlanItemRoute = "learn" | "practice" | "none";

export type PlanActionRouting = {
  route: PlanItemRoute;
  label: string;
  kind: "learn" | "review" | "practice" | "assessment" | "other";
};

/**
 * Action type → execution surface.
 *
 * `learn` opens the existing node study flow; `review` / `practice` /
 * `assessment` create a Practice Session scoped to that node, so a review never
 * silently becomes a second study surface. An unknown planner action type keeps
 * the least surprising behaviour (study the node) instead of inventing a route.
 */
export function planActionRouting(actionType: string): PlanActionRouting {
  switch ((actionType ?? "").trim().toLowerCase()) {
    case "review":
      return { route: "practice", label: "开始复习", kind: "review" };
    case "practice":
      return { route: "practice", label: "开始练习", kind: "practice" };
    case "assessment":
      return { route: "practice", label: "开始测验", kind: "assessment" };
    case "learn":
      return { route: "learn", label: "开始学习", kind: "learn" };
    default:
      return { route: "learn", label: "开始学习", kind: "other" };
  }
}

/** Task status vocabulary — never a mastery claim. */
const PLAN_STATUS_LABELS: Record<string, string> = {
  pending: "待开始",
  in_progress: "进行中",
  completed: "已完成",
  blocked: "待解锁",
  archived: "已归档",
};

export function planStatusLabel(status: string): string {
  return PLAN_STATUS_LABELS[status] ?? (status || "待开始");
}

const PLAN_REASON_LABELS: Record<string, string> = {
  eligible_after_prerequisite_check: "前置已满足，按目标优先级排入",
  blocked_by_prerequisite: "前置未完成",
  user_rescheduled: "已手动调整过时间",
  manual_insert: "手动加入计划",
};

/** Planner titles carry a routing prefix; the plan row shows the node name. */
export function planItemTitle(item: Pick<ActionItem, "title">): string {
  return (item.title ?? "").replace(/^(学习|复习|练习|测验|待解锁)：/, "").trim();
}

/* ------------------------------------------------------------------ *
 * Dates
 * ------------------------------------------------------------------ */

const WEEKDAY_LABELS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

export function startOfDay(value: Date): Date {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate());
}

export function addDays(value: Date, amount: number): Date {
  const next = new Date(value);
  next.setDate(next.getDate() + amount);
  return next;
}

export function calendarDayDiff(target: Date, reference: Date): number {
  return Math.round(
    (startOfDay(target).getTime() - startOfDay(reference).getTime()) / 86_400_000,
  );
}

export function planMonthDayLabel(date: Date): string {
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

/** Stable local-day key for calendar cells and group lookups. */
export function planDayKey(date: Date): string {
  return `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`;
}

/**
 * Learning-day offset, mirroring `WorkflowService._scheduled_due_at`:
 * `day_index` 1 is the plan start, and later days are spread across the
 * declared weekly availability instead of promising specific weekdays.
 */
export function planDayOffset(dayIndex: number, daysPerWeek: number): number {
  const safeDay = Math.max(1, Math.round(dayIndex || 1));
  const safeWeek = Math.max(1, Math.min(7, Math.round(daysPerWeek || 1)));
  return Math.ceil(((safeDay - 1) * 7) / safeWeek);
}

/** Inverse of {@link planDayOffset}: a calendar offset → learning day. */
export function planDayIndexForOffset(
  offsetDays: number,
  daysPerWeek: number,
): number {
  const safeOffset = Math.max(0, Math.round(offsetDays || 0));
  const safeWeek = Math.max(1, Math.min(7, Math.round(daysPerWeek || 1)));
  return Math.floor((safeOffset * safeWeek) / 7) + 1;
}

/** Real date of learning day N, based on the plan's own start date. */
export function planDateForDayIndex(
  planStartedAt: Date,
  dayIndex: number,
  daysPerWeek: number,
): Date {
  return addDays(startOfDay(planStartedAt), planDayOffset(dayIndex, daysPerWeek));
}

/** Learning day that contains a chosen calendar date. */
export function planDayIndexForDate(
  date: Date,
  planStartedAt: Date,
  daysPerWeek: number,
): number {
  return planDayIndexForOffset(calendarDayDiff(date, planStartedAt), daysPerWeek);
}

/** "今天" / "明天" / "周二" / "9月20日" — never D1/D2. */
export function planDayHeadline(date: Date, today: Date): string {
  const diff = calendarDayDiff(date, today);
  if (diff === 0) return "今天";
  if (diff === 1) return "明天";
  if (diff === 2) return "后天";
  if (diff > 2 && diff < 7) return WEEKDAY_LABELS[date.getDay()];
  return planMonthDayLabel(date);
}

/** Headline plus the concrete date, so "今天" is never ambiguous. */
export function planDayLabel(date: Date, today: Date): string {
  return `${planDayHeadline(date, today)} · ${planMonthDayLabel(date)}`;
}

/** "约 6 小时" / "45 分钟" */
export function planDurationLabel(minutes: number): string {
  const value = Math.max(0, Math.round(minutes || 0));
  if (value < 60) return `${value} 分钟`;
  const hours = Math.round((value / 60) * 10) / 10;
  return `${Number.isInteger(hours) ? hours : hours.toFixed(1)} 小时`;
}

/* ------------------------------------------------------------------ *
 * Model
 * ------------------------------------------------------------------ */

export type PlanTimeInputs = {
  minutesPerDay: number;
  daysPerWeek: number;
  sessionMinutes: number;
  deadlineAt: Date | null;
  /** True when these numbers come from the plan snapshot rather than a goal. */
  fromSnapshot: boolean;
};

export const PLAN_TIME_DEFAULTS: PlanTimeInputs = {
  minutesPerDay: 60,
  daysPerWeek: 5,
  sessionMinutes: 30,
  deadlineAt: null,
  fromSnapshot: false,
};

export type PlanItemView = {
  id: string;
  nodeId?: string;
  graphId?: string;
  title: string;
  description: string;
  actionType: string;
  routing: PlanActionRouting;
  durationMinutes: number;
  status: string;
  statusLabel: string;
  done: boolean;
  blocked: boolean;
  inProgress: boolean;
  /** Internal planner fields kept for the adapter — never rendered raw. */
  dayIndex: number;
  position: number;
  date: Date;
  headline: string;
  dayLabel: string;
  isToday: boolean;
  isOverdue: boolean;
  reason?: string;
  warnings: string[];
  whyRanked?: string;
  acceptanceCriteria: string[];
  blockedBy: string[];
  origin?: string;
  /** Live node facts, so the row can say why the plan disagrees with the map. */
  nodeMastered?: boolean;
  nodeDue?: boolean;
  raw: ActionItem;
};

export type PlanDayView = {
  dayIndex: number;
  date: Date;
  headline: string;
  label: string;
  items: PlanItemView[];
  actionable: PlanItemView[];
  minutes: number;
  capacityMinutes: number;
  overMinutes: number;
  isToday: boolean;
};

export type PlanChange = { label: string; nodeId?: string };

export type PlanNodeMarker = {
  dayIndex: number;
  label: string;
  tone: "today" | "soon" | "later";
  done: boolean;
  blocked: boolean;
};

export type PlanModel = {
  roadmap?: Roadmap;
  inputs: PlanTimeInputs;
  items: PlanItemView[];
  days: PlanDayView[];
  today: Date;
  planStartedAt: Date;
  todayItems: PlanItemView[];
  upcomingItems: PlanItemView[];
  totals: {
    itemCount: number;
    completedCount: number;
    openCount: number;
    blockedCount: number;
    minutes: number;
    windowDays: number;
    windowItemCount: number;
    windowMinutes: number;
    windowLabel: "接下来 7 天" | "计划全量";
  };
  overCapacityDays: Array<{
    dayIndex: number;
    headline: string;
    label: string;
    overMinutes: number;
    item?: PlanItemView;
  }>;
  unresolvedGaps: string[];
  /** True when the plan has items and none of them are still actionable. */
  finished: boolean;
  changes: PlanChange[];
  nodeMarkers: Map<string, PlanNodeMarker>;
  sequence: PlanItemView[];
};

function asPositiveNumber(value: unknown): number | undefined {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

function snapshotGoal(snapshot: unknown): Record<string, unknown> {
  const raw = (snapshot ?? {}) as Record<string, unknown>;
  return (raw.goal ?? {}) as Record<string, unknown>;
}

/**
 * Planning inputs, preferring the facts frozen into the plan (what it was
 * generated with) over today's goal row, so the panel explains the plan the
 * learner is actually looking at.
 */
export function planTimeInputs(
  roadmap?: Roadmap | null,
  goal?: Goal | null,
): PlanTimeInputs {
  const goalSnapshot = snapshotGoal(roadmap?.planning_snapshot);
  const availability = (goalSnapshot.availability ?? {}) as Record<string, unknown>;
  const preferences = (goalSnapshot.preferences ?? {}) as Record<string, unknown>;
  const snapshotMinutes = asPositiveNumber(availability.minutes_per_day);
  const snapshotDays = asPositiveNumber(availability.days_per_week);
  const snapshotSession = asPositiveNumber(preferences.session_minutes);
  const fromSnapshot = Boolean(snapshotMinutes || snapshotDays || snapshotSession);
  const rawDeadline = goalSnapshot.deadline_at;
  const deadlineValue =
    fromSnapshot && typeof rawDeadline === "string" ? rawDeadline : (goal?.deadline_at ?? null);
  const parsedDeadline = deadlineValue ? new Date(deadlineValue) : null;

  return {
    minutesPerDay:
      snapshotMinutes ??
      asPositiveNumber(goal?.availability?.minutes_per_day) ??
      PLAN_TIME_DEFAULTS.minutesPerDay,
    daysPerWeek:
      snapshotDays ??
      asPositiveNumber(goal?.availability?.days_per_week) ??
      PLAN_TIME_DEFAULTS.daysPerWeek,
    sessionMinutes:
      snapshotSession ??
      asPositiveNumber(goal?.preferences?.session_minutes) ??
      PLAN_TIME_DEFAULTS.sessionMinutes,
    deadlineAt:
      parsedDeadline && !Number.isNaN(parsedDeadline.getTime())
        ? parsedDeadline
        : null,
    fromSnapshot,
  };
}

export function planStartDate(roadmap?: Roadmap | null): Date {
  const raw = (roadmap?.planning_snapshot ?? {}) as Record<string, unknown>;
  if (typeof raw.plan_started_at === "string") {
    const parsed = new Date(raw.plan_started_at);
    if (!Number.isNaN(parsed.getTime())) return startOfDay(parsed);
  }
  if (roadmap?.created_at) {
    const parsed = new Date(roadmap.created_at);
    if (!Number.isNaN(parsed.getTime())) return startOfDay(parsed);
  }
  return startOfDay(new Date());
}

function planItemWarnings(
  metadata: Record<string, unknown>,
  inputs: PlanTimeInputs,
): string[] {
  const schedule = (metadata.schedule ?? {}) as Record<string, unknown>;
  const warnings: string[] = [];
  if (schedule.exceeds_daily_capacity === true) {
    const dayTotal = asPositiveNumber(schedule.day_total_minutes);
    warnings.push(
      dayTotal
        ? `当天已安排 ${dayTotal} 分钟，超出每日 ${inputs.minutesPerDay} 分钟容量`
        : `超出每日 ${inputs.minutesPerDay} 分钟容量`,
    );
  }
  if (schedule.scheduled_after_deadline === true) {
    warnings.push("排期超出目标截止日期");
  }
  return warnings;
}

/** Two largest ranking drivers, so "为什么排在这里" stays explainable. */
function planItemWhyRanked(metadata: Record<string, unknown>): string | undefined {
  const breakdown = metadata.score_breakdown as Record<string, number> | undefined;
  if (!breakdown) return undefined;
  const parts = (
    [
      ["importance", "重要度"],
      ["mastery_gap", "掌握缺口"],
      ["retrieval_urgency", "复习紧迫度"],
      ["evidence_gap", "证据缺口"],
      ["deadline_urgency", "期限压力"],
    ] as const
  )
    .map(([key, label]) => ({
      label,
      value: Math.round((breakdown[key] ?? 0) * 100),
    }))
    .filter((entry) => entry.value > 0)
    .sort((left, right) => right.value - left.value)
    .slice(0, 2);
  if (!parts.length) return undefined;
  return parts.map((entry) => `${entry.label} ${entry.value}%`).join(" · ");
}

/**
 * Project plan items from the roadmap.
 *
 * Day order and intra-day order come from the persisted `day_index` /
 * `position`; the display date comes from the plan start date plus the same
 * learning-day spread the backend uses (an explicit `due_at` wins when the
 * planner stored one).
 */
export function planItemViews(
  roadmap: Roadmap | null | undefined,
  inputs: PlanTimeInputs,
  today: Date,
  nodesById: Map<string, GraphNode> = new Map(),
): PlanItemView[] {
  if (!roadmap) return [];
  const planStartedAt = planStartDate(roadmap);
  const items = [...roadmap.items].sort((left, right) => {
    const leftBlocked = left.status === "blocked" ? 1 : 0;
    const rightBlocked = right.status === "blocked" ? 1 : 0;
    if (leftBlocked !== rightBlocked) return leftBlocked - rightBlocked;
    if (left.day_index !== right.day_index) return left.day_index - right.day_index;
    return left.position - right.position;
  });

  return items.map((raw) => {
    const routing = planActionRouting(raw.action_type);
    const dayIndex = raw.day_index > 0 ? raw.day_index : 1;
    const dueAt = raw.due_at ? new Date(raw.due_at) : null;
    const date =
      dueAt && !Number.isNaN(dueAt.getTime())
        ? startOfDay(dueAt)
        : planDateForDayIndex(planStartedAt, dayIndex, inputs.daysPerWeek);
    const metadata = (raw.metadata_json ?? {}) as Record<string, unknown>;
    const prerequisites = (metadata.prerequisites ?? {}) as {
      blocked_by?: Array<string | { label?: string; node_id?: string }>;
    };
    const blockedBy = (prerequisites.blocked_by ?? []).map((entry) =>
      typeof entry === "string" ? entry : (entry.label ?? entry.node_id ?? "前置"),
    );
    const diff = calendarDayDiff(date, today);
    const node = raw.node_id ? nodesById.get(raw.node_id) : undefined;
    const item: PlanItemView = {
      id: raw.id,
      nodeId: raw.node_id ?? undefined,
      graphId: raw.graph_id ?? undefined,
      title: planItemTitle(raw),
      description: raw.description ?? "",
      actionType: raw.action_type,
      routing,
      durationMinutes: raw.duration_minutes,
      status: raw.status,
      statusLabel: planStatusLabel(raw.status),
      done: raw.status === "completed",
      blocked: raw.status === "blocked",
      inProgress: raw.status === "in_progress",
      dayIndex,
      position: raw.position,
      date,
      headline: planDayHeadline(date, today),
      dayLabel: planDayLabel(date, today),
      isToday: diff === 0,
      isOverdue: diff < 0 && raw.status !== "completed" && raw.status !== "blocked",
      warnings: planItemWarnings(metadata, inputs),
      whyRanked: planItemWhyRanked(metadata),
      acceptanceCriteria: Array.isArray(metadata.acceptance_criteria)
        ? (metadata.acceptance_criteria as unknown[]).filter(
            (entry): entry is string => typeof entry === "string",
          )
        : [],
      blockedBy,
      origin: typeof metadata.origin === "string" ? metadata.origin : undefined,
      nodeMastered: Boolean(
        node && (node.attention_state === "mastered" || node.mastery_stars >= 5),
      ),
      nodeDue: Boolean(
        node && ["due", "due_soon", "relearning"].includes(node.retrieval_state),
      ),
      raw,
    };
    return { ...item, reason: planItemReason(item) };
  });
}

export function planItemReason(item: PlanItemView): string | undefined {
  if (item.blocked && item.blockedBy.length) {
    return `前置未完成：${item.blockedBy.join("、")}`;
  }
  const metadata = (item.raw.metadata_json ?? {}) as Record<string, unknown>;
  const ranking =
    typeof metadata.ranking_reason === "string" ? metadata.ranking_reason : "";
  if (ranking === "manual_insert") return "手动加入计划";
  if (ranking === "user_rescheduled") return "已手动调整过时间";
  if (item.routing.kind === "review") return "复习已到期，需要重新检索";
  // The map may already disagree with the plan: say so on the row instead of
  // letting the learner start a task that is no longer the next thing to do.
  if (item.routing.kind === "learn" && item.nodeDue) {
    return "节点已进入待复习，建议先复习";
  }
  if (item.routing.kind === "learn" && item.nodeMastered) {
    return "节点已掌握，计划仍在安排学习";
  }
  if (ranking && PLAN_REASON_LABELS[ranking]) return PLAN_REASON_LABELS[ranking];
  const why = planItemWhyRanked(metadata);
  return why ? `按 ${why} 排入` : undefined;
}

export function planDays(
  items: PlanItemView[],
  inputs: PlanTimeInputs,
  today: Date,
): PlanDayView[] {
  const byDay = new Map<number, PlanItemView[]>();
  for (const item of items) {
    const list = byDay.get(item.dayIndex) ?? [];
    list.push(item);
    byDay.set(item.dayIndex, list);
  }
  return [...byDay.entries()]
    .sort((left, right) => left[0] - right[0])
    .map(([dayIndex, dayItems]) => {
      const sorted = [...dayItems].sort(
        (left, right) => left.position - right.position,
      );
      const date = sorted[0]?.date ?? today;
      const minutes = sorted
        .filter((item) => !item.blocked)
        .reduce((sum, item) => sum + item.durationMinutes, 0);
      return {
        dayIndex,
        date,
        headline: planDayHeadline(date, today),
        label: planDayLabel(date, today),
        items: sorted,
        actionable: sorted.filter((item) => !item.blocked),
        minutes,
        capacityMinutes: inputs.minutesPerDay,
        overMinutes: Math.max(0, minutes - inputs.minutesPerDay),
        isToday: calendarDayDiff(date, today) === 0,
      };
    });
}

/** Canvas markers: near-term work is labelled, the rest stays a small dot. */
export function planNodeMarkers(
  items: PlanItemView[],
  today: Date,
): Map<string, PlanNodeMarker> {
  const markers = new Map<string, PlanNodeMarker>();
  const ranked = [...items].sort(
    (left, right) => left.dayIndex - right.dayIndex || left.position - right.position,
  );
  for (const item of ranked) {
    if (!item.nodeId || item.done) continue;
    const diff = calendarDayDiff(item.date, today);
    const tone: PlanNodeMarker["tone"] =
      diff <= 0 ? "today" : diff <= 3 ? "soon" : "later";
    const label =
      diff < 0
        ? "补做"
        : diff === 0
          ? "今天"
          : diff === 1
            ? "明天"
            : diff <= 6
              ? WEEKDAY_LABELS[item.date.getDay()]
              : "计划中";
    const existing = markers.get(item.nodeId);
    // Nearest scheduled work wins when a node appears more than once.
    if (existing && existing.tone !== "later") continue;
    markers.set(item.nodeId, {
      dayIndex: item.dayIndex,
      label,
      tone,
      done: item.done,
      blocked: item.blocked,
    });
  }
  return markers;
}

/** Execution order for the optional "显示计划路径" overlay. */
export function planSequence(items: PlanItemView[], limit = 6): PlanItemView[] {
  return items
    .filter((item) => !item.blocked && !item.done && item.nodeId)
    .sort(
      (left, right) =>
        left.dayIndex - right.dayIndex || left.position - right.position,
    )
    .slice(0, Math.max(0, limit));
}

/**
 * What changed since the plan was generated.
 *
 * Only facts the frontend can actually read are reported, and only facts a
 * regeneration would actually act on (graph revision, orphaned nodes, changed
 * goal constraints, tasks finished since generation). A node whose live
 * mastery disagrees with its row is *not* plan drift — the planner schedules
 * those nodes too — so it is surfaced on the row itself
 * (`planItemReason`) instead of promising an update that changes nothing.
 */
export function planChanges(
  roadmap: Roadmap | null | undefined,
  graph: Graph | null | undefined,
  items: PlanItemView[],
  goal: Goal | null | undefined,
  inputs: PlanTimeInputs,
): PlanChange[] {
  if (!roadmap) return [];
  const snapshot = (roadmap.planning_snapshot ?? {}) as Record<string, unknown>;
  const changes: PlanChange[] = [];
  const snapshotRevision = asPositiveNumber(snapshot.graph_revision);
  if (graph && snapshotRevision && graph.revision !== snapshotRevision) {
    changes.push({
      label: `图谱已更新（修订 v${snapshotRevision} → v${graph.revision}）`,
    });
  }
  if (graph) {
    const nodesById = new Map(graph.nodes.map((node) => [node.id, node]));
    // A node that left the graph must not stay scheduled: that task can never
    // be executed, so it is plan-level drift rather than a row-level hint.
    const orphaned = items.filter(
      (item) => item.nodeId && !nodesById.has(item.nodeId),
    );
    if (orphaned.length) {
      changes.push({ label: `${orphaned.length} 项任务对应的节点已不在图谱中` });
    }
  }
  const completed = items.filter((item) => item.done).length;
  if (completed) {
    changes.push({ label: `${completed} 项计划任务已完成，计划尚未重排` });
  }
  const goalSnapshot = snapshotGoal(snapshot);
  const snapshotAvailability = (goalSnapshot.availability ?? {}) as Record<
    string,
    unknown
  >;
  const snapshotSession = ((goalSnapshot.preferences ?? {}) as Record<string, unknown>)
    .session_minutes;
  const liveMinutes = goal?.availability?.minutes_per_day;
  const liveDays = goal?.availability?.days_per_week;
  const liveSession = goal?.preferences?.session_minutes;
  if (
    goal &&
    inputs.fromSnapshot &&
    ((liveMinutes !== undefined &&
      asPositiveNumber(snapshotAvailability.minutes_per_day) !== liveMinutes) ||
      (liveDays !== undefined &&
        asPositiveNumber(snapshotAvailability.days_per_week) !== liveDays) ||
      (liveSession !== undefined &&
        asPositiveNumber(snapshotSession) !== liveSession))
  ) {
    changes.push({
      label: `时间约束已调整，计划仍按每日 ${inputs.minutesPerDay} 分钟 / 每周 ${inputs.daysPerWeek} 天生成`,
    });
  }
  return changes;
}

export function buildPlanModel(args: {
  roadmap?: Roadmap | null;
  goal?: Goal | null;
  graph?: Graph | null;
  today?: Date;
}): PlanModel {
  const today = startOfDay(args.today ?? new Date());
  const inputs = planTimeInputs(args.roadmap, args.goal);
  const nodesById = new Map<string, GraphNode>(
    (args.graph?.nodes ?? []).map((node) => [node.id, node]),
  );
  const items = planItemViews(args.roadmap, inputs, today, nodesById);
  const days = planDays(items, inputs, today);
  const actionable = items.filter((item) => !item.blocked);
  const completedCount = items.filter((item) => item.done).length;
  const windowEnd = addDays(today, 7);
  const windowItems = items.filter(
    (item) =>
      !item.blocked &&
      calendarDayDiff(item.date, today) >= 0 &&
      item.date.getTime() < windowEnd.getTime(),
  );
  const windowDays = new Set(windowItems.map((item) => planDayKey(item.date))).size;
  const inWindow = windowItems.length > 0;
  const rawGaps = (args.roadmap?.planning_snapshot as Record<string, unknown> | undefined)
    ?.unresolved_gaps;

  return {
    roadmap: args.roadmap ?? undefined,
    inputs,
    items,
    days,
    today,
    planStartedAt: planStartDate(args.roadmap),
    todayItems: items.filter((item) => item.isToday),
    upcomingItems: items.filter((item) => calendarDayDiff(item.date, today) > 0),
    totals: {
      itemCount: items.length,
      completedCount,
      openCount: Math.max(0, actionable.length - completedCount),
      blockedCount: items.length - actionable.length,
      minutes: actionable.reduce((sum, item) => sum + item.durationMinutes, 0),
      windowDays: inWindow ? windowDays : days.length,
      windowItemCount: inWindow ? windowItems.length : actionable.length,
      windowMinutes: inWindow
        ? windowItems.reduce((sum, item) => sum + item.durationMinutes, 0)
        : actionable.reduce((sum, item) => sum + item.durationMinutes, 0),
      windowLabel: inWindow ? "接下来 7 天" : "计划全量",
    },
    overCapacityDays: days
      .filter((day) => day.overMinutes > 0)
      .map((day) => ({
        dayIndex: day.dayIndex,
        headline: day.headline,
        label: day.label,
        overMinutes: day.overMinutes,
        item: day.actionable[day.actionable.length - 1],
      })),
    unresolvedGaps: Array.isArray(rawGaps)
      ? rawGaps.filter((entry): entry is string => typeof entry === "string")
      : [],
    finished: items.length > 0 && items.every((item) => item.done || item.blocked),
    changes: planChanges(args.roadmap, args.graph, items, args.goal, inputs),
    nodeMarkers: planNodeMarkers(items, today),
    sequence: planSequence(items),
  };
}

export type PlanSuggestion = {
  dayIndex: number;
  date: Date;
  label: string;
  durationMinutes: number;
  reasons: string[];
};

/**
 * Where a node would land if it were added now: the first upcoming learning day
 * that still fits one session, and the same default duration the planner uses.
 */
export function suggestPlanSlot(
  model: PlanModel,
  options: { blockedByPrerequisite?: boolean } = {},
): PlanSuggestion {
  const durationMinutes = Math.min(
    model.inputs.sessionMinutes,
    model.inputs.minutesPerDay,
  );
  const upcoming = model.days.filter(
    (day) => calendarDayDiff(day.date, model.today) >= 0,
  );
  const fitting =
    upcoming.find(
      (day) => day.minutes + durationMinutes <= model.inputs.minutesPerDay,
    ) ?? upcoming[upcoming.length - 1];
  const dayIndex = fitting?.dayIndex ?? 1;
  const date =
    fitting?.date ??
    planDateForDayIndex(model.planStartedAt, dayIndex, model.inputs.daysPerWeek);
  const minutesOnDay = fitting?.minutes ?? 0;
  const remaining = Math.max(0, model.inputs.minutesPerDay - minutesOnDay);
  const reasons: string[] = [
    options.blockedByPrerequisite
      ? "前置节点尚未完成，会先记为待解锁"
      : "前置节点已满足",
    `目标优先级较高，单次按 ${durationMinutes} 分钟安排`,
    remaining >= durationMinutes
      ? `${planDayHeadline(date, model.today)} 仍有 ${remaining} 分钟可用`
      : `${planDayHeadline(date, model.today)} 已排 ${minutesOnDay} 分钟，加入后会超出容量`,
  ];
  return {
    dayIndex,
    date,
    label: planDayLabel(date, model.today),
    durationMinutes,
    reasons,
  };
}

/** Day choices for the schedule dialog, expressed as real dates. */
export function planDayChoices(
  model: PlanModel,
  keepDayIndex = 1,
): Array<{ dayIndex: number; date: Date; label: string; minutes: number }> {
  const lastScheduled = model.days.reduce(
    (max, day) => Math.max(max, day.dayIndex),
    keepDayIndex,
  );
  const last = Math.max(lastScheduled + 2, keepDayIndex + 6, 8);
  const byDay = new Map(model.days.map((day) => [day.dayIndex, day]));
  const choices: Array<{
    dayIndex: number;
    date: Date;
    label: string;
    minutes: number;
  }> = [];
  for (let dayIndex = 1; dayIndex <= last; dayIndex += 1) {
    const date = planDateForDayIndex(
      model.planStartedAt,
      dayIndex,
      model.inputs.daysPerWeek,
    );
    const isPast = calendarDayDiff(date, model.today) < 0;
    if (isPast && dayIndex !== keepDayIndex) continue;
    const existing = byDay.get(dayIndex);
    choices.push({
      dayIndex,
      date,
      label: planDayLabel(date, model.today),
      minutes: existing?.minutes ?? 0,
    });
  }
  return choices;
}

/** Human wording for the plan's own revision reason (never a UI field name). */
export function planRevisionRationale(
  model: PlanModel,
  dayIndex: number,
  durationMinutes: number,
): string {
  const date = planDateForDayIndex(
    model.planStartedAt,
    dayIndex,
    model.inputs.daysPerWeek,
  );
  return `手动调整到 ${planDayLabel(date, model.today)} · ${durationMinutes} 分钟`;
}

/**
 * Create the Practice Session a review / practice plan item points at, and
 * return its id so the caller owns navigation.
 */
export async function startPlanPracticeSession(
  item: PlanItemView,
  count = 5,
): Promise<string> {
  if (!item.nodeId) throw new Error("该任务没有关联节点，无法开始练习");
  const view = await createPracticeSession({
    mode: "node",
    node_ids: [item.nodeId],
    count,
  });
  return view.session.id;
}

/**
 * Dashed execution-order overlay for "显示计划路径".
 *
 * Knowledge edges say "this depends on that" (fact, solid). These say "I plan
 * to do this next" (projection, dashed + arrow), so the two can never be read
 * as the same thing. Only the near-term sequence is linked — the canvas must
 * not turn into a second roadmap.
 */
export function buildPlanPathEdges(sequence: PlanItemView[]): Edge[] {
  const edges: Edge[] = [];
  for (let index = 0; index < sequence.length - 1; index += 1) {
    const source = sequence[index].nodeId;
    const target = sequence[index + 1].nodeId;
    if (!source || !target || source === target) continue;
    edges.push({
      id: `plan-path-${sequence[index].id}-${sequence[index + 1].id}`,
      source,
      target,
      type: "planSequence",
      animated: false,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 12,
        height: 12,
        color: "var(--muted-foreground)",
      },
    });
  }
  return edges;
}
