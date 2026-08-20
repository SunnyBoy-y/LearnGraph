import { useState } from "react";
import { Check, CircleAlert, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";
import { compactSessionContext } from "@/api/sessions";
import type { CompactContextResult, SessionContextUsage } from "@/types/sessions";

function formatTokenCount(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 10_000) return `${Math.round(tokens / 1_000)}k`;
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}k`;
  return String(tokens);
}

const RING_RADIUS = 7;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

/** 主动压缩上下文的最小用量门槛（与后端 compact_context 门控一致）。 */
const MIN_COMPACT_RATIO = 0.5;

function RingGlyph({
  fraction,
  sizeClass,
  colorClass,
}: {
  fraction: number;
  sizeClass: string;
  colorClass: string;
}) {
  return (
    <svg
      aria-hidden="true"
      className={cn(sizeClass, "-rotate-90", colorClass)}
      viewBox="0 0 18 18"
    >
      <circle
        className="opacity-25"
        cx="9"
        cy="9"
        fill="none"
        r={RING_RADIUS}
        stroke="currentColor"
        strokeWidth="2"
      />
      <circle
        cx="9"
        cy="9"
        fill="none"
        r={RING_RADIUS}
        stroke="currentColor"
        strokeDasharray={RING_CIRCUMFERENCE}
        strokeDashoffset={RING_CIRCUMFERENCE * (1 - fraction)}
        strokeLinecap="round"
        strokeWidth="2"
      />
    </svg>
  );
}

function usageTone(fraction: number, atThreshold: boolean) {
  if (atThreshold || fraction >= 1) {
    return {
      label: "已达阈值，将自动压缩",
      text: "text-red-500",
      bar: "bg-red-500",
      ring: "text-red-500",
    };
  }
  if (fraction >= 0.7) {
    return {
      label: "接近压缩阈值",
      text: "text-amber-500",
      bar: "bg-amber-500",
      ring: "text-amber-500",
    };
  }
  return {
    label: "上下文状态正常",
    text: "text-emerald-600 dark:text-emerald-400",
    bar: "bg-emerald-500",
    ring: "text-muted-foreground",
  };
}

function Stat({
  label,
  value,
  valueClass,
}: {
  label: string;
  value: string;
  valueClass?: string;
}) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] leading-4 text-muted-foreground">{label}</p>
      <p className={cn("truncate text-xs font-medium tabular-nums", valueClass)}>
        {value}
      </p>
    </div>
  );
}

/** 输入框工具栏里的上下文用量小圆环；点击弹出用量详情与「立即压缩」面板。 */
export function ContextUsageRing({
  usage,
  sessionId,
  agentMode = false,
  onCompacted,
  className,
}: {
  usage: SessionContextUsage;
  sessionId: string;
  agentMode?: boolean;
  onCompacted?: () => void;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [compacting, setCompacting] = useState(false);
  const [result, setResult] = useState<CompactContextResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fraction = Math.min(1, Math.max(0, usage.used_ratio));
  const percent = Math.round(usage.used_ratio * 100);
  const atThreshold = usage.remaining_tokens <= 0;
  const tone = usageTone(fraction, atThreshold);
  const compactedCount = usage.compacted_message_count ?? 0;
  const canCompact = !compacting && usage.used_ratio >= MIN_COMPACT_RATIO;

  const handleCompact = async () => {
    if (!canCompact || compacting) return;
    setCompacting(true);
    setError(null);
    setResult(null);
    try {
      const res = await compactSessionContext(sessionId, {
        agent_mode: agentMode,
      });
      setResult(res);
      if (!res.skipped) onCompacted?.();
    } catch (err) {
      setError("压缩失败，请稍后重试");
    } finally {
      setCompacting(false);
    }
  };

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    if (!next) {
      // 关闭面板时清空上次结果，避免与刷新后的用量混淆。
      setResult(null);
      setError(null);
    }
  };

  const freedTokens =
    result && !result.skipped
      ? result.estimated_tokens_before - result.estimated_tokens_after
      : 0;

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <button
          aria-label={`上下文已使用约 ${percent}%，点击查看并压缩`}
          className={cn(
            "group flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-md",
            "transition-colors hover:bg-muted/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            className,
          )}
          type="button"
        >
          <RingGlyph
            colorClass={cn(tone.ring, "transition-colors group-hover:opacity-80")}
            fraction={fraction}
            sizeClass="size-[18px]"
          />
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" side="top" className="w-[21rem]">
        <div className="grid gap-3 p-1">
          {/* 头部：放大圆环 + 状态 */}
          <div className="flex items-center gap-3">
            <div className="relative flex size-11 shrink-0 items-center justify-center rounded-full bg-muted/70 ring-1 ring-foreground/10">
              <RingGlyph
                colorClass={tone.ring}
                fraction={fraction}
                sizeClass="size-7"
              />
              <span className="absolute inset-0 flex items-center justify-center text-[10px] font-semibold tabular-nums">
                {percent}%
              </span>
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                上下文用量
              </p>
              <p className={cn("truncate text-sm font-semibold", tone.text)}>
                {tone.label}
              </p>
            </div>
            {compactedCount > 0 && (
              <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
                <Check className="size-3" />
                已压缩 {compactedCount} 条
              </span>
            )}
          </div>

          {/* 动态色进度条 */}
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
            <div
              className={cn(
                "h-full rounded-full transition-all duration-500 ease-out",
                tone.bar,
              )}
              style={{ width: `${Math.min(100, fraction * 100)}%` }}
            />
          </div>

          {/* 统计网格 */}
          <div className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-lg border bg-muted/40 p-3">
            <Stat
              label="上下文大小"
              value={`约 ${formatTokenCount(usage.estimated_tokens)} tokens`}
            />
            <Stat label="消息数" value={`${usage.message_count} 条`} />
            <Stat
              label="剩余空间"
              value={
                atThreshold
                  ? "已用尽"
                  : `约 ${formatTokenCount(usage.remaining_tokens)}`
              }
              valueClass={atThreshold ? "text-red-500" : undefined}
            />
            <Stat
              label="压缩阈值"
              value={formatTokenCount(usage.compaction_threshold_tokens)}
            />
          </div>

          <p className="text-[11px] leading-relaxed text-muted-foreground">
            模型窗口 {formatTokenCount(usage.context_window_tokens)} tokens
            （估算值，不含检索与记忆注入）
          </p>

          {compactedCount > 0 && (
            <p className="flex items-start gap-1.5 rounded-md bg-emerald-500/10 px-2.5 py-2 text-[11px] leading-relaxed text-emerald-700 dark:text-emerald-300">
              <Check className="mt-0.5 size-3.5 shrink-0" />
              早期消息已转为摘要计入用量，后续回复仍可参考历史要点
            </p>
          )}

          {/* 结果反馈 */}
          {result && !result.skipped && (
            <div className="grid gap-1 rounded-lg border border-emerald-500/25 bg-emerald-500/10 px-3 py-2">
              <p className="flex items-center gap-1.5 text-xs font-medium text-emerald-600 dark:text-emerald-400">
                <Check className="size-3.5" />
                压缩完成
              </p>
              <p className="text-[11px] leading-relaxed text-emerald-700/90 dark:text-emerald-300/90">
                {formatTokenCount(result.estimated_tokens_before)} →{" "}
                {formatTokenCount(result.estimated_tokens_after)} tokens · 释放{" "}
                <span className="font-semibold">
                  {formatTokenCount(freedTokens)}
                </span>{" "}
                tokens（{result.source_message_count} 条早期消息）
              </p>
            </div>
          )}
          {result?.skipped && (
            <div className="rounded-lg bg-muted px-3 py-2 text-xs text-muted-foreground">
              {result.reason === "context_too_small"
                ? "上下文还小，暂无需压缩"
                : "没有可压缩的早期消息"}
            </div>
          )}
          {error && (
            <div className="flex items-center gap-1.5 rounded-lg border border-red-500/25 bg-red-500/10 px-3 py-2 text-xs text-red-500">
              <CircleAlert className="size-3.5 shrink-0" />
              {error}
            </div>
          )}

          {/* 压缩按钮 */}
          <Button
            className="w-full gap-1.5"
            disabled={!canCompact}
            onClick={handleCompact}
            size="sm"
            variant={canCompact ? "default" : "outline"}
          >
            {compacting ? (
              <Spinner className="size-3.5" />
            ) : (
              <Sparkles className="size-3.5" />
            )}
            {compacting ? "正在压缩…" : "立即压缩上下文"}
          </Button>
          {!canCompact && !compacting && (
            <p className="text-center text-[11px] text-muted-foreground">
              用量低于 {Math.round(MIN_COMPACT_RATIO * 100)}%，暂无需压缩
            </p>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
