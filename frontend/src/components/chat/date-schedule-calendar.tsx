// 日期选择日历（内嵌学习日程）：question_batch / lg_goal_ask 的 date 类型问题
// 使用。月历网格 + 工作区 ActionItem 日程标注；选中日期时展示当天日程条目，
// 用户结合日程选择日期，结果以 YYYY-MM-DD 回填。
import { useEffect, useMemo, useState } from "react";
import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";

import { listActions } from "@/api/workflow";
import type { ActionItem } from "@/types/workflow";

const WEEKDAY_LABELS = ["日", "一", "二", "三", "四", "五", "六"];

function toLocalDateKey(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function parseDueKey(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return null;
  return toLocalDateKey(parsed);
}

function formatLabel(dateKey: string): string {
  const [year, month, day] = dateKey.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  const weekday = WEEKDAY_LABELS[date.getDay()];
  return `${year} 年 ${month} 月 ${day} 日（周${weekday}）`;
}

export function DateScheduleCalendar({
  disabled = false,
  onChange,
  value,
}: {
  disabled?: boolean;
  onChange: (dateKey: string, label: string) => void;
  value?: string;
}) {
  const todayKey = toLocalDateKey(new Date());
  const [viewMonth, setViewMonth] = useState(() => {
    const seeded = value ? new Date(`${value}T00:00:00`) : new Date();
    if (Number.isNaN(seeded.getTime())) return new Date(new Date().getFullYear(), new Date().getMonth(), 1);
    return new Date(seeded.getFullYear(), seeded.getMonth(), 1);
  });
  const [selected, setSelected] = useState<string>(value ?? "");
  const [actions, setActions] = useState<ActionItem[]>([]);

  useEffect(() => {
    let cancelled = false;
    listActions()
      .then((items) => {
        if (!cancelled) setActions(items);
      })
      .catch(() => {
        if (!cancelled) setActions([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const scheduleByDay = useMemo(() => {
    const map = new Map<string, ActionItem[]>();
    for (const item of actions) {
      const key = parseDueKey(item.due_at);
      if (!key) continue;
      const list = map.get(key) ?? [];
      list.push(item);
      map.set(key, list);
    }
    for (const list of map.values()) {
      list.sort((a, b) => a.priority - b.priority || a.position - b.position);
    }
    return map;
  }, [actions]);

  const grid = useMemo(() => {
    const year = viewMonth.getFullYear();
    const month = viewMonth.getMonth();
    const first = new Date(year, month, 1);
    const offset = first.getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const cells: Array<{ key: string; day: number; dateKey: string } | null> =
      [];
    for (let index = 0; index < offset; index += 1) cells.push(null);
    for (let day = 1; day <= daysInMonth; day += 1) {
      const dateKey = toLocalDateKey(new Date(year, month, day));
      cells.push({ key: dateKey, day, dateKey });
    }
    return cells;
  }, [viewMonth]);

  function pick(dateKey: string) {
    if (disabled) return;
    setSelected(dateKey);
    onChange(dateKey, formatLabel(dateKey));
  }

  function shiftMonth(delta: number) {
    setViewMonth(
      (current) => new Date(current.getFullYear(), current.getMonth() + delta, 1),
    );
  }

  const selectedItems = selected ? (scheduleByDay.get(selected) ?? []) : [];

  return (
    <div className="date-schedule-calendar">
      <div className="date-schedule-calendar__head">
        <button
          aria-label="上一个月"
          className="date-schedule-calendar__nav"
          disabled={disabled}
          onClick={() => shiftMonth(-1)}
          type="button"
        >
          <ChevronLeft className="size-3.5" />
        </button>
        <strong>
          {viewMonth.getFullYear()} 年 {viewMonth.getMonth() + 1} 月
        </strong>
        <button
          aria-label="下一个月"
          className="date-schedule-calendar__nav"
          disabled={disabled}
          onClick={() => shiftMonth(1)}
          type="button"
        >
          <ChevronRight className="size-3.5" />
        </button>
      </div>
      <div className="date-schedule-calendar__weekdays">
        {WEEKDAY_LABELS.map((label) => (
          <span key={label}>{label}</span>
        ))}
      </div>
      <div className="date-schedule-calendar__grid">
        {grid.map((cell, index) => {
          if (!cell) return <span className="date-schedule-calendar__blank" key={`blank-${index}`} />;
          const isToday = cell.dateKey === todayKey;
          const isSelected = cell.dateKey === selected;
          const count = scheduleByDay.get(cell.dateKey)?.length ?? 0;
          return (
            <button
              aria-label={formatLabel(cell.dateKey)}
              aria-pressed={isSelected}
              className={
                isSelected
                  ? "date-schedule-calendar__day is-selected"
                  : isToday
                    ? "date-schedule-calendar__day is-today"
                    : "date-schedule-calendar__day"
              }
              disabled={disabled}
              key={cell.key}
              onClick={() => pick(cell.dateKey)}
              type="button"
            >
              <span>{cell.day}</span>
              {count > 0 ? (
                <small className="date-schedule-calendar__mark">{count}</small>
              ) : null}
            </button>
          );
        })}
      </div>
      <div className="date-schedule-calendar__schedule">
        {selected ? (
          selectedItems.length ? (
            <>
              <div className="date-schedule-calendar__schedule-head">
                <CalendarDays className="size-3.5" />
                <strong>{formatLabel(selected)}</strong>
                <span>日程 {selectedItems.length} 项</span>
              </div>
              <ul>
                {selectedItems.slice(0, 6).map((item) => (
                  <li key={item.id}>
                    <span
                      className={`date-schedule-calendar__dot is-${item.status}`}
                    />
                    <span className="date-schedule-calendar__title">
                      {item.title}
                    </span>
                    {item.duration_minutes ? (
                      <small>{item.duration_minutes} 分钟</small>
                    ) : null}
                  </li>
                ))}
                {selectedItems.length > 6 ? (
                  <li className="date-schedule-calendar__more">
                    另有 {selectedItems.length - 6} 项
                  </li>
                ) : null}
              </ul>
            </>
          ) : (
            <p className="date-schedule-calendar__empty">
              {formatLabel(selected)} 暂无日程，可自由安排。
            </p>
          )
        ) : (
          <p className="date-schedule-calendar__empty">
            带数字标记的日期已有学习日程，选择日期后将自动回填。
          </p>
        )}
      </div>
    </div>
  );
}
