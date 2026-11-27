import {
  isFailedArtifactPart,
  isPlanNarrationPart,
  isReasoningTextPart,
  orderChainParts,
} from "@/features/chat/chat-message-parts";
import { webActivityLabel } from "@/lib/web-activity";
import type { MessagePart } from "@/types/sessions";

export type ProcessStageStatus = "running" | "completed" | "failed";

export interface ProcessStage {
  /** Stable identifier across streaming deltas within a message. */
  id: string;
  /** Zero-based stage order in the process timeline. */
  index: number;
  /** Concise stage title derived from reasoning, commentary, or tool intent. */
  title: string;
  /** Compact call/action summary shown while the stage is collapsed at Level-2. */
  summary: string | null;
  /** Execution status of this stage. */
  status: ProcessStageStatus;
  /** True when any part in this stage failed (or the turn failed on this stage). */
  hasFailure: boolean;
  /** True when any part inside this stage is actively streaming or pending. */
  hasRunningPart: boolean;
  /** True when this is the active tail stage before the final answer starts. */
  isCurrent: boolean;
  /** Default automatic expand state for Level-2 (ignoring manual user overrides). */
  autoOpen: boolean;
  /** Ordered parts rendered when this stage is expanded in-place. */
  parts: MessagePart[];
  /** Number of tool_call parts belonging to this stage. */
  toolCount: number;
}

const TOOL_INTENT_LABELS: Record<string, string> = {
  get_current_time: "获取当前时间",
  search_web: "联网搜索",
  native_web_search: "联网搜索",
  sandbox_search_web: "联网搜索",
  search_papers: "搜索论文",
  read_paper: "读取论文",
  fetch_web_page: "读取网页",
  native_web_read: "读取网页",
  browser_read_page: "读取网页",
  sandbox_fetch: "读取网页",
  parallel_web_research: "并行调研",
  search_images: "搜索图片",
  generate_image: "生成图片",
  sandbox_publish_image: "生成图片",
  download_external_image: "下载图片",
  download_github_source: "下载代码仓库",
  lg_skill_read: "加载技能",
  sandbox_skill_read: "加载技能",
  sandbox_skill_list: "查看技能列表",
  lg_skill_run_script: "运行技能脚本",
  resolve_learning_context: "读取学习上下文",
  canvas_get_render_contract: "获取渲染规范",
  canvas_emit_trusted_component: "生成交互组件",
  canvas_emit_magic_card: "生成交互卡片",
  canvas_render: "生成交互练习",
  ui_stream: "生成界面内容",
  artifact_publish_card: "发布交互卡片",
  sandbox_publish_web_app: "发布网页产物",
  sandbox_publish_file: "发布文件产物",
  list_session_files: "查看文件列表",
  sandbox_list_files: "查看文件列表",
  read_session_file: "读取文件",
  sandbox_read_file: "读取文件",
  sandbox_read_result: "读取执行结果",
  sandbox_write_file: "写入文件",
  sandbox_edit_file: "编辑文件",
  sandbox_append_file: "追加文件",
  sandbox_apply_patch: "应用代码变更",
  sandbox_grep: "检索文件内容",
  sandbox_exec: "执行命令",
  sandbox_run: "执行命令",
  sandbox_bash: "执行命令",
  sandbox_subagent: "委派子任务",
  sandbox_subagent_status: "查看子任务状态",
  sandbox_subagent_wait: "等待子任务",
  read_memories: "读取记忆",
  update_memories: "更新记忆",
  propose_memory_draft: "整理记忆",
  search_conversation_history: "检索历史对话",
  query_memory_timeline: "检索记忆时间线",
  read_conversation_segment: "读取对话片段",
  search_session_fragments: "检索会话片段",
  lg_graph_create: "创建知识图谱",
  lg_graph_propose_change: "生成图谱提案",
  lg_goal_read: "读取学习目标",
  lg_goal_create: "创建学习目标",
  lg_goal_confirm: "确认学习目标",
  lg_goal_ask: "确认目标细节",
  lg_goal_ask_batch: "确认目标细节",
  lg_goal_edit_draft: "修改目标草稿",
  search_stickers: "搜索表情",
  send_sticker: "发送表情",
};

function isContentlessChainTextPart(part: MessagePart): boolean {
  if (part.type !== "text") return false;
  return !part.content?.trim() && !part.content_delta?.trim();
}

function isPartFailed(part: MessagePart): boolean {
  if (part.status === "failed") return true;
  if (part.type === "subagent_task") {
    const phase = String(
      part.data?.status ?? part.data?.phase ?? "",
    ).toLowerCase();
    if (phase === "failed" || phase === "timed_out") return true;
  }
  return false;
}

function isActivityPart(part: MessagePart): boolean {
  return (
    part.type === "tool_call" ||
    part.type === "skill_trigger" ||
    part.type === "subagent_task" ||
    part.type === "sandbox_status" ||
    part.type === "graph_context" ||
    part.type === "graph_progress" ||
    part.type === "agent_step" ||
    isFailedArtifactPart(part)
  );
}

function cleanSnippet(raw: string, maxChars: number): string {
  const collapsed = raw
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[`*_~]/g, "")
    .replace(/^#+\s*/gm, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!collapsed) return "";
  if (collapsed.length <= maxChars) return collapsed;
  return `${collapsed.slice(0, maxChars).trimEnd()}…`;
}

function extractCommentaryTitle(part: MessagePart): string {
  const raw = (part.content ?? part.content_delta ?? "").trim();
  if (!raw) return "";
  const firstLine =
    raw
      .split(/\r?\n/)
      .map((line) => line.trim())
      .find(Boolean) ?? "";
  if (!firstLine) return "";
  const cleaned = cleanSnippet(firstLine, 60);
  // Stop at the first sentence delimiter if it keeps a meaningful phrase.
  const sentenceMatch = cleaned.match(/^(.{6,30}?)[。！？；!?;]/);
  if (sentenceMatch?.[1]) {
    return sentenceMatch[1].trim();
  }
  const clauseMatch = cleaned.match(/^(.{8,28}?)[，,]/);
  if (clauseMatch?.[1] && cleaned.length > 32) {
    return clauseMatch[1].trim();
  }
  return cleanSnippet(cleaned, 30);
}

function extractReasoningTitle(
  part: MessagePart,
  options: { requireExplicitHeading: boolean },
): string {
  const raw = (part.content ?? part.content_delta ?? "").trim();
  if (!raw) return "";
  const firstLine =
    raw
      .split(/\r?\n/)
      .map((line) => line.trim())
      .find(Boolean) ?? "";
  if (!firstLine) return "";

  const isMarkdownHeading =
    /^#{1,6}\s+\S/.test(firstLine) ||
    /^\*\*[^*]+\*\*$/.test(firstLine) ||
    /^__[^_]+__$/.test(firstLine);

  if (options.requireExplicitHeading && !isMarkdownHeading) {
    return "";
  }
  const cleaned = cleanSnippet(firstLine, 34);
  if (!cleaned) return "";
  return cleaned;
}

export function describeToolIntent(part: MessagePart): string {
  if (isFailedArtifactPart(part)) {
    return part.type === "image" ? "图片生成异常" : "产物生成异常";
  }

  switch (part.type) {
    case "tool_call": {
      const rawWeb = part.data?.web_activity;
      if (rawWeb && typeof rawWeb === "object" && !Array.isArray(rawWeb)) {
        const op = (rawWeb as Record<string, unknown>).operation;
        if (op === "search") return "联网搜索";
        if (op === "fetch" || op === "browser") return "读取网页";
        const label = webActivityLabel(part);
        if (label) return label;
      }

      const toolName =
        typeof part.data?.tool_name === "string" ? part.data.tool_name.trim() : "";
      const customTitle =
        typeof part.data?.title === "string" ? part.data.title.trim() : "";

      if (customTitle && customTitle !== toolName) {
        return TOOL_INTENT_LABELS[customTitle] ?? customTitle;
      }
      if (toolName && TOOL_INTENT_LABELS[toolName]) {
        return TOOL_INTENT_LABELS[toolName];
      }
      if (toolName) {
        if (/search|检索|搜索/i.test(toolName)) return "搜索资料";
        if (/read|fetch|paper|browser/i.test(toolName)) return "读取资料";
        if (/write|edit|patch|create/i.test(toolName)) return "写入变更";
        return toolName;
      }
      return customTitle || "工具调用";
    }
    case "skill_trigger": {
      const skillName =
        typeof part.data?.skill_name === "string" && part.data.skill_name.trim()
          ? part.data.skill_name.trim()
          : typeof part.data?.skill_key === "string" && part.data.skill_key.trim()
            ? part.data.skill_key.trim()
            : "";
      return skillName ? `技能 ${skillName}` : "加载技能";
    }
    case "subagent_task": {
      const title =
        typeof part.data?.title === "string" && part.data.title.trim()
          ? part.data.title.trim()
          : "";
      return title || "子代理任务";
    }
    case "sandbox_status":
      return part.data?.auth_required === true ? "沙箱授权" : "沙箱执行";
    case "graph_context":
      return "读取图谱上下文";
    case "graph_progress": {
      const content = (part.content ?? "").trim();
      return content ? cleanSnippet(content, 24) : "生成图谱提案";
    }
    case "agent_step": {
      const title =
        (typeof part.data?.title === "string" && part.data.title.trim()) ||
        (typeof part.data?.label === "string" && part.data.label.trim()) ||
        (part.content ?? "").trim();
      return title ? cleanSnippet(title, 24) : "智能体步骤";
    }
    default:
      return "执行操作";
  }
}

function extractToolHint(part: MessagePart): string | null {
  if (part.type === "tool_call") {
    const input =
      part.data?.input &&
      typeof part.data.input === "object" &&
      !Array.isArray(part.data.input)
        ? (part.data.input as Record<string, unknown>)
        : null;
    const query = typeof input?.query === "string" ? input.query.trim() : "";
    if (query) return cleanSnippet(query, 28);
    const url = typeof input?.url === "string" ? input.url.trim() : "";
    if (url) return cleanSnippet(url, 32);
  }
  if (part.type === "subagent_task") {
    const progress =
      typeof part.data?.progress_summary === "string"
        ? part.data.progress_summary.trim()
        : "";
    if (progress) return cleanSnippet(progress, 32);
  }
  return null;
}

function mergeActivityLabels(
  parts: MessagePart[],
): Array<{ label: string; count: number; samplePart: MessagePart }> {
  const merged: Array<{ label: string; count: number; samplePart: MessagePart }> = [];
  for (const part of parts) {
    if (!isActivityPart(part)) continue;
    const label = describeToolIntent(part);
    const previous = merged[merged.length - 1];
    if (previous && previous.label === label) {
      previous.count += 1;
    } else {
      merged.push({ label, count: 1, samplePart: part });
    }
  }
  return merged;
}

function resolveStageTitleAndSummary(
  parts: MessagePart[],
  commentary: MessagePart | null,
  index: number,
  totalStages: number,
): { title: string; summary: string | null } {
  const reasoningParts = parts.filter(isReasoningTextPart);
  const summaryHeading =
    reasoningParts
      .map((p) =>
        extractReasoningTitle(p, {
          requireExplicitHeading: p.type !== "reasoning_summary",
        }),
      )
      .find(Boolean) || "";

  const commentaryTitle = commentary ? extractCommentaryTitle(commentary) : "";
  const plainReasoningHeading =
    reasoningParts
      .map((p) => extractReasoningTitle(p, { requireExplicitHeading: false }))
      .find(Boolean) || "";

  const mergedActivities = mergeActivityLabels(parts);
  const activityParts = parts.filter(isActivityPart);

  let title = "";
  let titleSource: "reasoning" | "commentary" | "tool" | "fallback" = "fallback";

  if (summaryHeading) {
    title = summaryHeading;
    titleSource = "reasoning";
  } else if (commentaryTitle) {
    title = commentaryTitle;
    titleSource = "commentary";
  } else if (plainReasoningHeading) {
    title = plainReasoningHeading;
    titleSource = "reasoning";
  } else if (mergedActivities.length === 1) {
    title = mergedActivities[0].label;
    titleSource = "tool";
  } else if (mergedActivities.length > 1) {
    if (activityParts.every(isFailedArtifactPart)) {
      title = totalStages === 1 ? "生成异常" : `执行阶段 ${index + 1}`;
    } else {
      title = totalStages === 1 ? "执行过程" : `执行阶段 ${index + 1}`;
    }
    titleSource = "fallback";
  } else {
    title = totalStages === 1 ? "思考过程" : `执行阶段 ${index + 1}`;
    titleSource = "fallback";
  }

  let summary: string | null = null;
  if (mergedActivities.length > 0) {
    if (
      mergedActivities.length === 1 &&
      titleSource === "tool" &&
      mergedActivities[0].label === title
    ) {
      if (mergedActivities[0].count > 1) {
        summary = `共 ${mergedActivities[0].count} 次调用`;
      } else {
        summary = extractToolHint(mergedActivities[0].samplePart);
      }
    } else {
      const formatted = mergedActivities
        .slice(0, 3)
        .map((item) => (item.count > 1 ? `${item.label} ${item.count} 次` : item.label));
      if (mergedActivities.length > 3) {
        formatted.push(`等 ${activityParts.length} 项`);
      }
      summary = formatted.join(" · ");
    }
  } else if (commentary && titleSource === "reasoning") {
    const snippet = cleanSnippet(
      commentary.content ?? commentary.content_delta ?? "",
      44,
    );
    if (snippet && snippet !== title) summary = snippet;
  } else if (reasoningParts.length > 0 && titleSource === "commentary") {
    const snippet = plainReasoningHeading;
    summary = snippet && snippet !== title ? snippet : "包含思考过程";
  } else if (reasoningParts.length > 0 && title !== "思考过程") {
    summary = "包含思考过程";
  }

  return { title, summary };
}

/**
 * Group ordered thinking-chain parts into semantic execution stages.
 *
 * Rules:
 * - Commentary (`plan_narration`) blocks split stages; leading reasoning before
 *   the first commentary/activity attaches to Stage 0 instead of an empty stage.
 * - New reasoning summaries and tool calls attach to the current stage.
 * - Stage titles reuse existing reasoning summaries, commentary, or tool intents
 *   without issuing any LLM call.
 */
export function buildProcessStages(
  chainParts: MessagePart[],
  options?: {
    messageStatus?: string;
    finalAnswerStarted?: boolean;
  },
): ProcessStage[] {
  const ordered = orderChainParts(chainParts);
  const buckets: Array<{
    parts: MessagePart[];
    commentary: MessagePart | null;
    hasActivity: boolean;
  }> = [];

  let current: {
    parts: MessagePart[];
    commentary: MessagePart | null;
    hasActivity: boolean;
  } | null = null;

  for (const part of ordered) {
    if (isContentlessChainTextPart(part)) {
      continue;
    }

    if (isPlanNarrationPart(part)) {
      if (!current) {
        current = { parts: [part], commentary: part, hasActivity: false };
      } else if (!current.commentary && !current.hasActivity) {
        current.commentary = part;
        current.parts.push(part);
      } else {
        buckets.push(current);
        current = { parts: [part], commentary: part, hasActivity: false };
      }
      continue;
    }

    const active = isActivityPart(part);
    if (!current) {
      current = { parts: [part], commentary: null, hasActivity: active };
    } else {
      current.parts.push(part);
      if (active) current.hasActivity = true;
    }
  }

  if (current) {
    buckets.push(current);
  }

  const isStreaming = options?.messageStatus === "streaming";
  const isMessageFailed = options?.messageStatus === "failed";
  const finalAnswerStarted = options?.finalAnswerStarted === true;

  return buckets.map((bucket, index) => {
    const isLast = index === buckets.length - 1;
    const hasFailure =
      bucket.parts.some(isPartFailed) || (isMessageFailed && isLast);
    const hasRunningPart =
      isStreaming &&
      bucket.parts.some(
        (part) => part.status === "streaming" || part.status === "pending",
      );
    const isCurrent = isStreaming && !finalAnswerStarted && isLast;
    const status: ProcessStageStatus = hasFailure
      ? "failed"
      : hasRunningPart || isCurrent
        ? "running"
        : "completed";
    const autoOpen = hasFailure || hasRunningPart || isCurrent;
    const { title, summary } = resolveStageTitleAndSummary(
      bucket.parts,
      bucket.commentary,
      index,
      buckets.length,
    );
    const toolCount = bucket.parts.filter(
      (part) => part.type === "tool_call",
    ).length;

    return {
      id: `stage-${index}`,
      index,
      title,
      summary,
      status,
      hasFailure,
      hasRunningPart,
      isCurrent,
      autoOpen,
      parts: bucket.parts,
      toolCount,
    };
  });
}
