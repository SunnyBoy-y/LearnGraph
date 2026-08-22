import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Bot,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Clock,
  LoaderCircle,
  MessageSquareText,
  UserRound,
  Wrench,
} from "lucide-react";
import { useState, type ReactNode } from "react";

import { listSessionMessagesPage } from "@/api/sessions";
import {
  formatMilliseconds,
  formatTokenCount,
} from "@/features/chat/stream-stats";
import { workspaceQueryKey } from "@/lib/query-keys";
import type { Message, MessagePart } from "@/types/sessions";

/**
 * 轨迹追踪：只读展示当前会话里每条消息的生成轨迹（耗时 / Token / 推理 /
 * 工具与子智能体概览）。数据来自现有 GET /sessions/{id}/messages 的
 * provider_trace 与 parts，不新增后端接口。
 */

type Trace = Record<string, unknown> | undefined;

function traceNumber(trace: Trace, key: string): number | undefined {
  const value = trace?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function traceString(trace: Trace, key: string): string | undefined {
  const value = trace?.[key];
  return typeof value === "string" && value ? value : undefined;
}

/** 拼接若干可选文本片段；全部为空时返回 "–"。 */
function joinFragments(fragments: (string | null | undefined)[]): string {
  const parts = fragments.filter((item): item is string => Boolean(item));
  return parts.length ? parts.join(" · ") : "–";
}

const PART_LABELS: Record<string, string> = {
  reasoning_content: "思考链",
  reasoning_summary: "思考摘要",
  agent_step: "工具步骤",
  tool_call: "工具调用",
  source_list: "资料列表",
  attachment: "附件",
  image: "图片",
  subagent_task: "子智能体",
  graph_context: "图谱上下文",
  sandbox: "沙箱",
  error: "错误",
  user_confirmation: "确认",
};

function partLabel(type: string): string {
  return PART_LABELS[type] ?? type;
}

function partsDetail(parts: MessagePart[]): string {
  return parts
    .map((part) => {
      const body =
        typeof part.content === "string" && part.content.trim()
          ? `：${part.content.trim().slice(0, 60)}`
          : "";
      return `${partLabel(part.type)}${body}`;
    })
    .join(" · ");
}

function MetricLine({
  icon,
  label,
  title,
  children,
}: {
  icon: typeof Clock;
  label: string;
  title?: string;
  children: ReactNode;
}) {
  const Icon = icon;
  return (
    <div className="flex items-center gap-1 text-[10px] leading-4 text-muted-foreground">
      <Icon className="size-3 shrink-0" />
      <span className="shrink-0 text-muted-foreground/90">{label}</span>
      <span className="truncate text-foreground/70" title={title}>
        {children}
      </span>
    </div>
  );
}

function TrajectoryRow({
  message,
  expanded,
  onToggle,
}: {
  message: Message;
  expanded: boolean;
  onToggle: () => void;
}) {
  const trace = (message.provider_trace ?? {}) as Trace;
  const model = traceString(trace, "model_id");
  const thinkingMs = traceNumber(trace, "thinking_duration_ms");
  const generationMs = traceNumber(trace, "generation_duration_ms");
  const inputTokens = traceNumber(trace, "input_tokens");
  const cachedTokens = traceNumber(trace, "cached_input_tokens");
  const outputTokens = traceNumber(trace, "output_tokens");
  const reasoningTokens = traceNumber(trace, "reasoning_tokens");
  const toolRounds = traceNumber(trace, "agent_tool_rounds");
  const toolCalls = traceNumber(trace, "agent_tool_calls");

  const isUser = message.role === "user";
  const preview =
    message.content && message.content.trim()
      ? message.content.trim().slice(0, 60)
      : "（无正文，仅状态）";

  const reasoning = message.parts.filter(
    (part) =>
      part.type === "reasoning_content" || part.type === "reasoning_summary",
  );
  const tools = message.parts.filter(
    (part) => part.type === "tool_call" || part.type === "agent_step",
  );
  const subagents = message.parts.filter((part) => part.type === "subagent_task");
  const failed = message.parts.filter(
    (part) => part.status === "failed" || part.type === "error",
  );

  return (
    <li className="rounded-lg border bg-card p-2.5">
      <div
        className="group flex cursor-pointer items-center gap-2"
        onClick={onToggle}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onToggle();
          }
        }}
        role="button"
        tabIndex={0}
      >
        <div className="grid size-6 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground">
          {isUser ? (
            <UserRound className="size-3.5" />
          ) : failed.length > 0 ? (
            <CircleAlert className="size-3.5 text-destructive" />
          ) : (
            <Bot className="size-3.5" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="text-xs font-medium">{isUser ? "用户" : "助手"}</span>
            {model ? (
              <span className="truncate text-[10px] text-muted-foreground" title={model}>
                {model}
              </span>
            ) : null}
          </div>
          <p className="mt-0.5 truncate text-[10px] leading-4 text-muted-foreground">
            {`${reasoning.length ? `推理 ${reasoning.length} ` : ""}${tools.length ? `工具 ${tools.length}` : ""}${subagents.length ? ` 子智能体 ${subagents.length}` : ""} · ${preview}`}
          </p>
        </div>
        {expanded ? (
          <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
        )}
      </div>

      {expanded ? (
        <div className="mt-2 space-y-1.5 border-t pt-2">
          {failed.length > 0 ? (
            <p className="text-[11px] font-medium text-destructive">
              含 {failed.length} 个失败/错误片段
            </p>
          ) : null}
          <MetricLine icon={Clock} label="耗时">
            {joinFragments([
              thinkingMs !== undefined
                ? `思考 ${formatMilliseconds(thinkingMs)}`
                : null,
              generationMs !== undefined
                ? `生成 ${formatMilliseconds(generationMs)}`
                : null,
            ])}
          </MetricLine>
          <MetricLine icon={Activity} label="Token">
            {joinFragments([
              inputTokens !== undefined
                ? `输入 ${formatTokenCount(inputTokens)}`
                : null,
              cachedTokens !== undefined && cachedTokens > 0
                ? `缓存 ${formatTokenCount(cachedTokens)}`
                : null,
              outputTokens !== undefined
                ? `输出 ${formatTokenCount(outputTokens)}`
                : null,
              reasoningTokens !== undefined && reasoningTokens > 0
                ? `推理 ${formatTokenCount(reasoningTokens)}`
                : null,
            ])}
          </MetricLine>
          {toolRounds !== undefined ||
          toolCalls !== undefined ||
          subagents.length > 0 ||
          reasoning.length > 0 ? (
            <MetricLine icon={Wrench} label="过程">
              {joinFragments([
                toolRounds !== undefined ? `${toolRounds} 轮` : null,
                toolCalls !== undefined ? `${toolCalls} 次调用` : null,
                subagents.length > 0 ? `子智能体 ${subagents.length}` : null,
                reasoning.length > 0 ? `推理 ${reasoning.length} 段` : null,
              ])}
            </MetricLine>
          ) : null}
          {message.parts.length > 0 ? (
            <p className="border-t pt-1.5 text-[10px] leading-4 text-muted-foreground">
              {partsDetail(message.parts)}
            </p>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

export function TrajectoryRail({
  sessionId,
  workspaceId,
}: {
  sessionId?: string;
  workspaceId: string;
}) {
  const enabled = Boolean(sessionId && sessionId !== "new");
  const messages = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "sessions", sessionId, "trajectory"),
    queryFn: () => listSessionMessagesPage(sessionId!),
    enabled,
  });
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  if (!enabled) {
    return (
      <div className="grid h-full place-items-center px-4 py-10 text-center text-xs text-muted-foreground">
        <MessageSquareText className="mb-2 size-5 opacity-60" />
        <p>选择或开始一个会话后显示运行轨迹。</p>
      </div>
    );
  }

  return (
    <div className="h-full min-h-0 overflow-y-auto">
      {messages.isPending ? (
        <div className="flex items-center justify-center gap-2 py-8 text-xs text-muted-foreground">
          <LoaderCircle className="size-3.5 animate-spin" />
          正在加载轨迹…
        </div>
      ) : messages.isError ? (
        <p className="px-4 py-8 text-center text-xs text-destructive" role="alert">
          轨迹加载失败：{messages.error.message}
        </p>
      ) : (messages.data?.items.length ?? 0) === 0 ? (
        <div className="px-4 py-8 text-center text-xs text-muted-foreground">
          <Activity className="mx-auto mb-2 size-5 opacity-60" />
          该会话还没有可展示的轨迹数据。
        </div>
      ) : (
        <ul className="grid gap-1.5 p-2">
          {messages.data.items.map((message) => (
            <TrajectoryRow
              key={message.id}
              message={message}
              expanded={Boolean(expanded[message.id])}
              onToggle={() =>
                setExpanded((current: Record<string, boolean>) => ({
                  ...current,
                  [message.id]: !current[message.id],
                }))
              }
            />
          ))}
        </ul>
      )}
    </div>
  );
}
