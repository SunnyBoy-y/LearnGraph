import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { CalendarDays, ExternalLink, TrendingDown, TrendingUp } from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { PracticeTrendPoint } from "@/types/practice";
import { graphNodeHref, percent } from "./practice-format";

export function GraphNodeLink({
  workspaceId,
  graphId,
  nodeId,
  label,
  className,
}: {
  workspaceId: string;
  graphId?: string | null;
  nodeId?: string;
  label: string;
  className?: string;
}) {
  const href = graphNodeHref(workspaceId, graphId, nodeId);
  if (!href) {
    return (
      <span className="text-xs text-muted-foreground">未关联图谱</span>
    );
  }
  return (
    <Link
      className={cn(
        "inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline",
        className,
      )}
      to={href}
    >
      {label}
      <ExternalLink className="size-3" />
    </Link>
  );
}

export function RecentResultDots({
  results,
  className,
}: {
  results: boolean[];
  className?: string;
}) {
  if (!results.length) {
    return <span className="text-xs text-muted-foreground">暂无作答记录</span>;
  }
  return (
    <span
      aria-label={`最近 ${results.length} 次作答：${results
        .map((item) => (item ? "正确" : "错误"))
        .join("、")}`}
      className={cn("inline-flex items-center gap-1", className)}
    >
      {results.map((item, index) => (
        <span
          className={cn(
            "grid size-4 place-items-center rounded-full text-[10px] font-semibold text-white",
            item ? "bg-primary" : "bg-destructive",
          )}
          key={index}
        >
          {item ? "✓" : "✕"}
        </span>
      ))}
      <span className="sr-only">
        {results.map((item) => (item ? "✓" : "✕")).join("")}
      </span>
    </span>
  );
}

type ChartPoint = {
  date: string;
  label: string;
  firstTry: number | null;
  final: number | null;
  answered: number;
};

export function PracticeTrendChart({
  points,
  height = 220,
  emptyLabel = "还没有练习记录，完成一次练习后这里会出现趋势。",
}: {
  points: PracticeTrendPoint[];
  height?: number;
  emptyLabel?: string;
}) {
  const data = useMemo<ChartPoint[]>(
    () =>
      points.map((point) => ({
        date: point.date,
        label: point.date.slice(5),
        firstTry:
          point.first_try_accuracy === null ||
          point.first_try_accuracy === undefined
            ? null
            : Math.round(point.first_try_accuracy * 100),
        final:
          point.final_accuracy === null || point.final_accuracy === undefined
            ? null
            : Math.round(point.final_accuracy * 100),
        answered: point.answered,
      })),
    [points],
  );
  const hasData = data.some(
    (point) => point.firstTry !== null || point.final !== null,
  );
  if (!hasData) {
    return (
      <p className="py-10 text-center text-sm text-muted-foreground">{emptyLabel}</p>
    );
  }
  const summary = data
    .filter((point) => point.firstTry !== null || point.final !== null)
    .map(
      (point) =>
        `${point.label}：首次 ${point.firstTry ?? "—"}%，最终 ${point.final ?? "—"}%（${point.answered} 题）`,
    )
    .join("；");
  return (
    <div>
      <div style={{ height }} className="w-full">
        <ResponsiveContainer height="100%" width="100%">
          <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
            <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
            <XAxis
              axisLine={false}
              dataKey="label"
              fontSize={11}
              stroke="var(--muted-foreground)"
              tickLine={false}
            />
            <YAxis
              axisLine={false}
              domain={[0, 100]}
              fontSize={11}
              stroke="var(--muted-foreground)"
              tickFormatter={(value) => `${value}%`}
              tickLine={false}
            />
            <Tooltip
              contentStyle={{
                background: "var(--card)",
                border: "1px solid var(--border)",
                borderRadius: 12,
                fontSize: 12,
              }}
              formatter={(value, name) => [
                `${value}%`,
                name === "firstTry" ? "首次正确率" : "最终正确率",
              ]}
              labelFormatter={(label) => `${label}`}
            />
            <Line
              connectNulls={false}
              dataKey="firstTry"
              dot={{ r: 3 }}
              name="firstTry"
              stroke="var(--primary)"
              strokeWidth={2}
              type="monotone"
            />
            <Line
              connectNulls={false}
              dataKey="final"
              dot={{ r: 3 }}
              name="final"
              stroke="#60a5fa"
              strokeWidth={2}
              strokeDasharray="4 3"
              type="monotone"
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-0.5 w-4 rounded bg-primary" />
          首次正确率
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-0.5 w-4 rounded bg-blue-400" />
          最终正确率
        </span>
        <span>没有练习的日期显示为空档，不按 0% 计算</span>
      </div>
      <p className="sr-only">趋势文字摘要：{summary}</p>
    </div>
  );
}

export function PracticeDeltaBadge({ delta }: { delta: number | null | undefined }) {
  if (delta === null || delta === undefined) {
    return <Badge variant="outline">暂无对比</Badge>;
  }
  const positive = delta >= 0;
  const Icon = positive ? TrendingUp : TrendingDown;
  return (
    <Badge variant="outline" className="gap-1">
      <Icon
        className={cn("size-3", positive ? "text-primary" : "text-destructive")}
      />
      {positive ? "+" : ""}
      {Math.round(delta * 100)}%
    </Badge>
  );
}

export function PracticeCalendarDialog({
  points,
  workspaceId,
}: {
  points: PracticeTrendPoint[];
  workspaceId: string;
}) {
  const [open, setOpen] = useState(false);
  const days = useMemo(
    () => [...points].slice(-30).reverse(),
    [points],
  );
  const totalAnswered = days.reduce((sum, day) => sum + day.answered, 0);
  return (
    <>
      <Button onClick={() => setOpen(true)} size="sm" variant="outline">
        <CalendarDays className="size-4" />
        学习日历
      </Button>
      <Dialog onOpenChange={setOpen} open={open}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>学习日历</DialogTitle>
            <DialogDescription>
              最近 30 天的练习活动，数据来自真实的练习会话与作答记录。
            </DialogDescription>
          </DialogHeader>
          {totalAnswered === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              最近 30 天还没有练习记录。
            </p>
          ) : (
            <div className="max-h-[60vh] overflow-y-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs text-muted-foreground">
                    <th className="py-2 font-medium">日期</th>
                    <th className="py-2 font-medium">练习题数</th>
                    <th className="py-2 font-medium">时长</th>
                    <th className="py-2 font-medium">首次正确率</th>
                  </tr>
                </thead>
                <tbody>
                  {days.map((day) => (
                    <tr className="border-b last:border-0" key={day.date}>
                      <td className="py-2">{day.date}</td>
                      <td className="py-2 tabular-nums">{day.answered}</td>
                      <td className="py-2 tabular-nums">
                        {day.minutes ? `${day.minutes} 分钟` : "—"}
                      </td>
                      <td className="py-2 tabular-nums">
                        {percent(day.first_try_accuracy)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="text-xs text-muted-foreground">
            工作区 {workspaceId.slice(0, 8)} · 只统计已完成的作答
          </p>
        </DialogContent>
      </Dialog>
    </>
  );
}

/**
 * 模型侧失败码（后端 ``AppError.code``）：换模型 / 改 Provider 默认模型后重试
 * 就可能成功，所以 UI 要给出直达设置的出口；其它错误码换模型没有用。
 */
export const PRACTICE_MODEL_ERROR_CODES = new Set([
  "remote_model_required",
  "remote_model_rejected_request",
]);

/**
 * 模型侧错误（没有可用模型 / 选中的模型被 Provider 拒绝）的统一出口。
 *
 * 这里必须跳到「工作区设置」页的「功能模型」卡片：那才是有「练习出题与判分模型」
 * 这个可改选项的地方。Provider 页只能改 Provider 自己的默认模型，把人送错页面
 * 等于让人继续猜。
 */
export function PracticeModelSettingLink({
  workspaceId,
  label = "去设置出题模型",
  className,
}: {
  workspaceId: string;
  label?: string;
  className?: string;
}) {
  return (
    <Button asChild className={className} size="sm" variant="outline">
      <Link to={`/w/${workspaceId}/settings/workspace`}>{label}</Link>
    </Button>
  );
}
