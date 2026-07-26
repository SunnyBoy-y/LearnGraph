import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { SessionContextUsage } from "@/types/sessions";

function formatTokenCount(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 10_000) return `${Math.round(tokens / 1_000)}k`;
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}k`;
  return String(tokens);
}

const RING_RADIUS = 7;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

/** 输入框工具栏里的上下文用量小圆环；悬停显示上下文大小与距自动压缩的剩余量。 */
export function ContextUsageRing({
  usage,
  className,
}: {
  usage: SessionContextUsage;
  className?: string;
}) {
  const fraction = Math.min(1, Math.max(0, usage.used_ratio));
  const percent = Math.round(usage.used_ratio * 100);
  const atThreshold = usage.remaining_tokens <= 0;
  const ringColor = atThreshold
    ? "text-red-500"
    : fraction >= 0.7
      ? "text-amber-500"
      : "text-muted-foreground";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          aria-label={`上下文已使用约 ${percent}%`}
          className={cn(
            "flex size-8 shrink-0 cursor-default items-center justify-center rounded-md",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            className,
          )}
          type="button"
        >
          <svg
            aria-hidden="true"
            className={cn("size-[18px] -rotate-90", ringColor)}
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
        </button>
      </TooltipTrigger>
      <TooltipContent side="top">
        <div className="grid gap-1 text-xs">
          <p className="font-medium">上下文用量 {percent}%</p>
          <p>
            上下文大小：约 {formatTokenCount(usage.estimated_tokens)} tokens（
            {usage.message_count} 条消息）
          </p>
          <p>
            {atThreshold
              ? "已达压缩阈值，下次发送将压缩较早的历史"
              : `距自动压缩还剩：约 ${formatTokenCount(usage.remaining_tokens)} tokens`}
          </p>
          <p className="text-muted-foreground">
            压缩阈值 {formatTokenCount(usage.compaction_threshold_tokens)} · 模型窗口{" "}
            {formatTokenCount(usage.context_window_tokens)}（估算值，不含检索与记忆注入）
          </p>
        </div>
      </TooltipContent>
    </Tooltip>
  );
}
