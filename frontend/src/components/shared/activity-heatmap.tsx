import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CalendarClock,
  CalendarDays,
  CheckCircle2,
  MessageSquare,
} from "lucide-react";

import { listActions, renderSessionActivitySummary } from "@/api";
import { useAuth } from "@/features/auth/auth-context-value";
import { workspaceQueryKey } from "@/lib/query-keys";
import { getSessionActivityDays, listSessionPage, type SessionSummary, type SessionPage, type SessionPageParam } from '@/api/session-browser'
import type { InfiniteData } from '@tanstack/react-query';

const HEATMAP_WEEKS = 53;
const HEATMAP_GAP = 3;
const MIN_CELL = 11;
const MAX_CELL = 22;
const DAY_VISIBLE_LIMIT = 5;

/**
 * One row in a day's activity list. ``text`` is the human-facing event
 * description shown directly (no mechanical prefix); ``kind`` keeps the
 * behaviour category so "活动构成" can still break out the three types.
 */
type ActivityKind = "session" | "completed" | "planned";

interface DayActivityItem {
  text: string;
  kind: ActivityKind;
}

const DEFAULT_TITLES = new Set([
  "新会话",
  "新学习会话",
]);

/**
 * Mechanical/placeholder session titles that should not surface as a learning
 * event on their own. We frame these as "the activity view has no real event
 * text yet"; the parent component may lazily request an LLM summary for them.
 * A short title that is only digits or greeting words ("你好", "123", "问候")
 * is treated as placeholder content, not a learning event.
 */
export function isPlaceholderSessionTitle(title: string | null | undefined) {
  const value = title?.trim();
  if (!value) return true;
  if (DEFAULT_TITLES.has(value)) return true;
  return /^[\d\s你好问候打招呼哈嗨嗨嗨嗨嗨]+$/.test(value) && value.length <= 6;
}

/** Choose the label text a day's activity row should display. */
function sessionActivityText(session: Pick<SessionSummary, "activity_summary" | "title">) {
  // Prefer the LLM-generated learning-event description; fall back to the
  // (possibly mechanical) title when no summary exists yet.
  return session.activity_summary?.trim() || session.title;
}

function formatDayKey(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function activityIcon(kind: ActivityKind) {
  if (kind === "completed") return CheckCircle2;
  if (kind === "planned") return CalendarClock;
  return MessageSquare;
}

export function ActivityHeatmap({
  variant = "rail",
}: {
  variant?: "rail" | "panel" | "report";
}) {
  const { workspaceId } = useAuth();
  const actions = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "actions"),
    queryFn: listActions,
  });
  const queryClient = useQueryClient();
  const scrollRef = useRef<HTMLDivElement>(null);
  // In-flight session IDs so we never request the same summary twice
  // (component re-renders, day selection changes, resonance queries).
  const inflightSummaries = useRef<Set<string>>(new Set());

  const { today, weeks, monthLabels } = useMemo(() => {
    const todayDate = new Date();
    todayDate.setHours(0, 0, 0, 0);
    const firstMonday = new Date(todayDate);
    firstMonday.setDate(
      todayDate.getDate() -
        ((todayDate.getDay() + 6) % 7) -
        (HEATMAP_WEEKS - 1) * 7,
    );
    const allWeeks: Date[][] = Array.from(
      { length: HEATMAP_WEEKS },
      (_, weekIndex) =>
        Array.from({ length: 7 }, (_, dayIndex) => {
          const date = new Date(firstMonday);
          date.setDate(firstMonday.getDate() + weekIndex * 7 + dayIndex);
          return date;
        }),
    );
    const labels: Array<{ weekIndex: number; label: string }> = [];
    allWeeks.forEach((week, weekIndex) => {
      const monthOfWeek = week[0].getMonth();
      if (
        weekIndex === 0 ||
        monthOfWeek !== allWeeks[weekIndex - 1][0].getMonth()
      )
        labels.push({ weekIndex, label: `${monthOfWeek + 1}月` });
    });
    if (labels.length > 1 && labels[1].weekIndex - labels[0].weekIndex < 3)
      labels.shift();
    return { today: todayDate, weeks: allWeeks, monthLabels: labels };
  }, []);

  const [selectedKey, setSelectedKey] = useState(() => variant === "panel" ? "" : formatDayKey(today));
  const range = useMemo(() => {
    const end = new Date(today); end.setDate(end.getDate() + 1);
    return { start: weeks[0][0].toISOString(), end: end.toISOString() };
  }, [today, weeks]);
  const days = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'sessions', 'activity-days', range),
    queryFn: ({ signal }) => getSessionActivityDays(range.start, range.end, { signal }), staleTime: 30_000,
  });
  const selectedRange = useMemo(() => {
    if (!selectedKey) return {};
    const [year, month, day] = selectedKey.split('-').map(Number);
    return { created_after: new Date(year, month - 1, day).toISOString(), created_before: new Date(year, month - 1, day + 1).toISOString() };
  }, [selectedKey]);
  const dayQueryKey = workspaceQueryKey(workspaceId, 'sessions', 'activity-day', selectedKey);
  const daySessions = useInfiniteQuery({
    queryKey: dayQueryKey,
    queryFn: ({ pageParam, signal }) => listSessionPage({ ...selectedRange, roots_only: false, include_archived: true }, pageParam, { signal }),
    initialPageParam: {} as SessionPageParam,
    getNextPageParam: page => page.has_more && page.next_cursor ? { cursor: page.next_cursor, direction: 'next' as const } : undefined,
    getPreviousPageParam: page => page.has_previous && page.previous_cursor ? { cursor: page.previous_cursor, direction: 'previous' as const } : undefined,
    enabled: Boolean(selectedKey),
    maxPages: 5, staleTime: 30_000,
  });
  const sessions = { data: useMemo(() => daySessions.data?.pages.flatMap(page => page.items), [daySessions.data]) };

  // Lazily enrich mechanical placeholder titles into real learning-event
  // descriptions. Only sessions shown on the currently-selected day and only
  // those without a summary request the (billable) LLM call. Provider
  // unavailable degrades silently — the row keeps showing the title.
  useEffect(() => {
    const list = sessions.data;
    if (!selectedKey || !list) return;
    const pending = list.filter(
      (session) =>
        formatDayKey(new Date(session.created_at)) === selectedKey &&
        !session.activity_summary &&
        !inflightSummaries.current.has(session.id),
    );
    if (!pending.length) return;
    for (const session of pending) inflightSummaries.current.add(session.id);
    for (const session of pending) {
      void renderSessionActivitySummary(session.id, {
        expected_title: session.title,
      })
        .then((updated) => {
          queryClient.setQueryData<InfiniteData<SessionPage>>(dayQueryKey, current => current ? ({
            ...current, pages: current.pages.map(page => ({ ...page, items: page.items.map(item => item.id === updated.id ? { ...item, activity_summary: updated.activity_summary } : item) })),
          }) : current);
        })
        .catch(() => {
          // Degrade gracefully; the existing title-based label stays.
        });
    }
  }, [queryClient, selectedKey, sessions.data, workspaceId]);

  const dayActivity = useMemo(() => {
    const result: Record<string, DayActivityItem[]> = {};
    const add = (value: string | null, text: string, kind: ActivityKind) => {
      if (!value) return;
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return;
      const key = formatDayKey(date);
      result[key] = [...(result[key] ?? []), { text, kind }];
    };
    sessions.data?.forEach((session) =>
      add(session.created_at, sessionActivityText(session), "session"),
    );
    actions.data?.forEach((action) => {
      if (action.completed_at)
        add(action.completed_at, action.title, "completed");
      else if (action.due_at) add(action.due_at, action.title, "planned");
    });
    return result;
  }, [actions.data, sessions.data]);

  const dayCount = (key: string) => (dayActivity[key]?.filter(item => item.kind !== 'session').length ?? 0) + (days.data?.[key] ?? 0);
  const { totalCount, streak } = useMemo(() => {
    let total = 0;
    weeks.forEach((week) =>
      week.forEach((date) => {
        if (date <= today)
          total += dayCount(formatDayKey(date));
      }),
    );
    let streakDays = 0;
    const cursor = new Date(today);
    if (!dayCount(formatDayKey(cursor)))
      cursor.setDate(cursor.getDate() - 1);
    while (dayCount(formatDayKey(cursor))) {
      streakDays += 1;
      cursor.setDate(cursor.getDate() - 1);
    }
    return { totalCount: total, streak: streakDays };
  }, [dayActivity, days.data, today, weeks]);

  const overview = useMemo(() => {
    const months: Array<{ key: string; label: string; count: number }> = [];
    for (let offset = 11; offset >= 0; offset--) {
      const date = new Date(
        today.getFullYear(),
        today.getMonth() - offset,
        1,
      );
      months.push({
        key: `${date.getFullYear()}-${date.getMonth()}`,
        label: `${date.getMonth() + 1}月`,
        count: 0,
      });
    }
    const byMonth = new Map(months.map((month) => [month.key, month]));
    const rangeStart = weeks[0][0];
    let sessionCount = 0;
    let completedCount = 0;
    let plannedCount = 0;
    Object.entries(dayActivity).forEach(([key, items]) => {
      const [year, month, day] = key.split("-").map(Number);
      const date = new Date(year, month - 1, day);
      if (date < rangeStart || date > today) return;
      const bucket = byMonth.get(`${year}-${month - 1}`);
      if (bucket) bucket.count += items.filter(item => item.kind !== 'session').length;
      items.forEach((item) => {
        if (item.kind === "completed") completedCount += 1;
        else if (item.kind === "planned") plannedCount += 1;

      });
    });
    for (const [key, count] of Object.entries(days.data ?? {})) {
      const [year, month, day] = key.split('-').map(Number);
      const date = new Date(year, month - 1, day);
      if (date < rangeStart || date > today) continue;
      sessionCount += count;
      const bucket = byMonth.get(`${year}-${month - 1}`);
      if (bucket) bucket.count += count;
    }
    const typeTotal = sessionCount + completedCount + plannedCount;
    const percent = (count: number) =>
      typeTotal ? Math.round((count / typeTotal) * 100) : 0;
    return {
      months,
      maxMonthly: Math.max(1, ...months.map((month) => month.count)),
      types: [
        { label: "创建会话", count: sessionCount, pct: percent(sessionCount) },
        { label: "完成行动", count: completedCount, pct: percent(completedCount) },
        { label: "计划行动", count: plannedCount, pct: percent(plannedCount) },
      ],
    };
  }, [dayActivity, days.data, today, weeks]);

  const [cellSize, setCellSize] = useState(MIN_CELL);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const compute = () => {
      const fitted = Math.floor(
        (el.clientWidth - (HEATMAP_WEEKS - 1) * HEATMAP_GAP) / HEATMAP_WEEKS,
      );
      setCellSize(Math.min(MAX_CELL, Math.max(MIN_CELL, fitted)));
    };
    compute();
    const observer = new ResizeObserver(compute);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, []);

  const [expandedDayKey, setExpandedDayKey] = useState<string | null>(null);

  const activities = dayActivity[selectedKey] ?? [];
  const dayExpanded = expandedDayKey === selectedKey;
  const visibleActivities = dayExpanded
    ? activities
    : activities.slice(0, DAY_VISIBLE_LIMIT);
  const hiddenCount = Math.max(0, dayCount(selectedKey) - visibleActivities.length);
  const showDayToggle = activities.length > DAY_VISIBLE_LIMIT || daySessions.hasNextPage;
  const moreDayPages = (direction: 'next' | 'previous') => direction === 'next' ? daySessions.fetchNextPage({ cancelRefetch: false }) : daySessions.fetchPreviousPage({ cancelRefetch: false });
  const dayToggleButton = showDayToggle || daySessions.isPending || daySessions.isError ? (
    <button
      className="mt-4 w-full rounded-lg border py-2 text-xs font-medium text-foreground transition-colors hover:bg-muted/60"
      disabled={daySessions.isFetching}
      onClick={() => {
        if (daySessions.isError) { void daySessions.refetch(); return; }
        if (!dayExpanded) setExpandedDayKey(selectedKey);
        else if (daySessions.hasNextPage) void moreDayPages('next');
        else if (daySessions.hasPreviousPage) void moreDayPages('previous');
        else setExpandedDayKey(null);
      }}
      type="button"
    >
      {daySessions.isError ? '活动加载失败，点击重试' : daySessions.isFetching ? '加载中…' : dayExpanded && daySessions.hasNextPage ? '加载更多活动' : dayExpanded && daySessions.hasPreviousPage ? '加载较新的活动' : dayExpanded ? '收起活动' : `显示更多活动（还有 ${hiddenCount} 条）`}
    </button>
  ) : null;
  const [selectedYear, selectedMonth, selectedDay] = selectedKey
    .split("-")
    .map(Number);
  const selectedHeading =
    selectedYear === today.getFullYear()
      ? `${selectedMonth}月${selectedDay}日`
      : `${selectedYear}年${selectedMonth}月${selectedDay}日`;
  const summary = days.isPending ? '正在读取学习活动…' : days.isError ? '学习活动统计暂不可用' : `过去一年 ${totalCount} 次学习活动`;

  const heatmapGrid = (
    <div
      className="activity-heatmap"
      style={{ "--heat-cell": `${cellSize}px` } as CSSProperties}
    >
      <div aria-hidden="true" className="activity-heatmap__weekdays">
        <span>一</span>
        <span />
        <span>三</span>
        <span />
        <span>五</span>
        <span />
        <span>日</span>
      </div>
      <div className="activity-heatmap__scroll" ref={scrollRef}>
        <div
          className="activity-heatmap__months"
          style={{
            gridTemplateColumns: `repeat(${weeks.length}, var(--heat-cell))`,
          }}
        >
          {monthLabels.map(({ weekIndex, label }) => (
            <span
              key={`${weekIndex}-${label}`}
              style={{ gridColumnStart: weekIndex + 1 }}
            >
              {label}
            </span>
          ))}
        </div>
        <div className="activity-heatmap__grid">
          {weeks.map((week) => (
            <div className="activity-heatmap__week" key={formatDayKey(week[0])}>
              {week.map((date) => {
                const key = formatDayKey(date);
                if (date > today)
                  return (
                    <span
                      aria-hidden="true"
                      className="activity-heatmap__cell is-future"
                      key={key}
                    />
                  );
                const count = dayCount(key);
                const dateLabel = `${date.getMonth() + 1}月${date.getDate()}日`;
                return (
                  <button
                    aria-label={`${dateLabel}：${count} 次活动`}
                    aria-pressed={selectedKey === key}
                    className={`activity-heatmap__cell is-level-${Math.min(4, count)}`}
                    key={key}
                    onClick={() => {
                      setExpandedDayKey(null);
                      setSelectedKey(variant === "panel" && selectedKey === key ? "" : key);
                    }}
                    title={`${dateLabel} · ${count > 0 ? `${count} 次活动` : "没有活动"}`}
                    type="button"
                  />
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );

  const legend = (
    <span className="activity-heatmap__legend">
      少
      {[0, 1, 2, 3, 4].map((level) => (
        <i className={`is-level-${level}`} key={level} />
      ))}
      多
    </span>
  );

  if (variant !== "rail") {
    return (
      <div className="activity-heatmap-panel">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-xs text-muted-foreground">
          {!days.isPending && !days.isError && !actions.isPending && !actions.isError ? <span><strong className="mr-1 text-base font-semibold tabular-nums text-foreground">{totalCount.toLocaleString("zh-CN")}</strong>次活动 · 过去一年</span> :
            <span>{days.isError || actions.isError ? "学习活动统计暂不完整" : "正在读取学习活动…"}</span>}
          {!days.isPending && !days.isError && !actions.isPending && !actions.isError && <span>连续活跃 {streak} 天</span>}
        </div>
        <div className="mt-2">{heatmapGrid}</div>
        <div className="activity-heatmap__meta mt-2">
          <span className="activity-heatmap__streak">
            点击方块查看当天的学习活动
          </span>
          {legend}
        </div>

        {variant === "report" && <div className="mt-6 grid gap-x-10 gap-y-6 border-t pt-5 lg:grid-cols-2">
          <div className="min-w-0">
            <p className="text-xs font-medium text-muted-foreground">
              月度活动 · 近 12 个月
            </p>
            <div className="mt-3 flex h-16 items-end gap-1 border-b">
              {overview.months.map((month) => (
                <div
                  className="flex h-full min-w-0 flex-1 items-end justify-center"
                  key={month.key}
                  title={`${month.label} · ${month.count} 次活动`}
                >
                  <div
                    className="w-full max-w-[18px] rounded-t-[4px]"
                    style={
                      month.count > 0
                        ? {
                            height: `${Math.max(
                              8,
                              (month.count / overview.maxMonthly) * 100,
                            )}%`,
                            background: "var(--heat-2)",
                          }
                        : { height: "3px", background: "var(--heat-0)" }
                    }
                  />
                </div>
              ))}
            </div>
            <div className="mt-1 flex gap-1">
              {overview.months.map((month) => (
                <span
                  className="min-w-0 flex-1 text-center text-[9px] text-muted-foreground"
                  key={month.key}
                >
                  {month.label}
                </span>
              ))}
            </div>
          </div>

          <div className="min-w-0">
            <p className="text-xs font-medium text-muted-foreground">
              活动构成 · 过去一年
            </p>
            <div className="mt-3 space-y-3">
              {overview.types.map((type) => (
                <div key={type.label}>
                  <div className="flex items-baseline justify-between gap-3 text-xs">
                    <span>{type.label}</span>
                    <span className="tabular-nums text-muted-foreground">
                      {type.count} 次 · {type.pct}%
                    </span>
                  </div>
                  <div
                    className="mt-1.5 h-2 overflow-hidden rounded-full"
                    style={{ background: "var(--heat-0)" }}
                  >
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${type.pct}%`,
                        background: "var(--heat-2)",
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>}

        {selectedKey && <div className="mt-4 border-t pt-4" aria-live="polite">
          <p className="text-sm">
            <strong>{selectedHeading}</strong> 的活动
          </p>
          {activities.length ? (
            <>
              <ul className="ml-2 mt-4 space-y-4 border-l pl-6">
                {visibleActivities.map((activity, index) => {
                  const Icon = activityIcon(activity.kind);
                  return (
                    <li
                      className="relative text-sm leading-5 text-muted-foreground"
                      key={`${activity.kind}-${index}-${activity.text}`}
                    >
                      <span className="absolute -left-8 top-0 grid size-4 place-items-center rounded-full bg-background">
                        <Icon className="size-3.5" />
                      </span>
                      <span className="min-w-0 break-words">{activity.text}</span>
                    </li>
                  );
                })}
              </ul>
              {dayToggleButton}
            </>
          ) : (
            <p className="mt-3 text-sm leading-6 text-muted-foreground">
              {daySessions.isPending ? '正在读取当天活动…' : daySessions.isError ? '当天活动暂时无法读取。' : '当天没有记录的学习活动。'}
              {dayToggleButton}
            </p>
          )}
        </div>}
      </div>
    );
  }

  return (
    <>
      <div className="activity-rail__title">
        <div>
          <p className="text-sm font-semibold">学习活动</p>
          <p>{summary}</p>
        </div>
        <CalendarDays className="size-4" />
      </div>
      {heatmapGrid}
      <div className="activity-heatmap__meta">
        <span className="activity-heatmap__streak">
          {streak > 0 ? `连续学习 ${streak} 天` : "今天还没有学习记录"}
        </span>
        {legend}
      </div>
      <div className="activity-rail__details">
        <p>
          <strong>{selectedHeading}</strong> 的活动
        </p>
        <ul>
          {(visibleActivities.length
            ? visibleActivities
            : [{ text: daySessions.isPending ? '正在读取当天活动…' : daySessions.isError ? '当天活动暂时无法读取。' : '当天没有记录的学习活动', kind: "session" as const }]
          ).map((activity, index) => (
            <li key={`${activity.kind}-${index}-${activity.text}`}>
              {activity.text}
            </li>
          ))}
        </ul>
        {dayToggleButton}
      </div>
    </>
  );
}
