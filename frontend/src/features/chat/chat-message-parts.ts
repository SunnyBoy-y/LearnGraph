import type { UnknownRecord } from "@/types/common";
import type { MessagePart } from "@/types/sessions";

/**
 * Multi-step agent process that belongs inside the outer "正在思考" collapsible.
 * Includes model reasoning text so the user can expand one fold to read thoughts.
 */
export const CHAIN_PART_TYPES = new Set<MessagePart["type"]>([
  "reasoning_summary",
  "reasoning_content",
  "agent_step",
  "tool_call",
  "graph_context",
  "sandbox_status",
  "skill_trigger",
  "graph_progress",
]);

/** Reasoning-only subset (still chain parts; used for activity labels). */
export const REASONING_TEXT_PART_TYPES = new Set<MessagePart["type"]>([
  "reasoning_summary",
  "reasoning_content",
]);

/**
 * Stable presentation order.
 *
 * Prefer process (thinking chain) above answer content when sequence is
 * absent. With sequence, keep stream order so tools stay chronologically honest.
 */
function partGroup(part: MessagePart) {
  if (part.type === "acknowledgement") return -1;
  if (isChainPart(part)) return 0;
  if (part.type === "text") return 1;
  return 2;
}

function partSequence(part: MessagePart, index: number) {
  return typeof part.sequence === "number" && Number.isFinite(part.sequence)
    ? part.sequence
    : index;
}

export function isChainPart(part: MessagePart): boolean {
  // Reasoning + tools + agent steps fold into the outer "正在思考" chain.
  // Model answer text / cards stay in the main stream below the fold.
  return CHAIN_PART_TYPES.has(part.type);
}

/**
 * Agent tool result that needs an explicit budget confirmation card.
 * The tool row itself still belongs in the thinking chain; the approval UI is
 * hoisted outside so the user can act without expanding the fold.
 */
export function isDeepResearchApprovalPart(part: MessagePart): boolean {
  if (part.type !== "tool_call") return false;
  if (part.data?.tool_name !== "start_deep_research") return false;
  const output = part.data?.output;
  if (!output || typeof output !== "object" || Array.isArray(output)) return false;
  return (output as { user_approval_required?: unknown }).user_approval_required === true;
}

export function isReasoningTextPart(part: MessagePart): boolean {
  return REASONING_TEXT_PART_TYPES.has(part.type);
}

/**
 * Host placeholder acknowledgement that should not render as a fake plan.
 * Real opening narration comes from the model as `text` deltas.
 */
export function isPlaceholderAcknowledgement(part: MessagePart): boolean {
  if (part.type !== "acknowledgement") return false;
  const text = (part.content ?? "").trim();
  if (!text) return true;
  return text === "正在思考" || text === "Thinking..." || text === "Thinking";
}

/**
 * Fixed host boilerplate that must never appear in the thinking chain UI.
 * Tool rows already surface real tool names; these strings add noise only.
 */
export function isHostAgentBoilerplate(part: MessagePart): boolean {
  if (part.type !== "agent_step") return false;
  const text = (part.content ?? "").trim();
  if (!text) return true;
  return (
    text === "智能体正在执行已授权的工具调用。" ||
    text === "智能体步骤已完成" ||
    text === "正在执行智能体步骤"
  );
}

/** Model-authored intermediate narration promoted before tool rounds. */
export function isPlanNarrationPart(part: MessagePart): boolean {
  return part.type === "text" && part.data?.kind === "plan_narration";
}

export function orderedMessageParts(parts: MessagePart[]) {
  const withIndex = parts.map((part, index) => ({ part, index }));
  const hasAnySequence = withIndex.some(
    ({ part }) =>
      typeof part.sequence === "number" && Number.isFinite(part.sequence),
  );

  return withIndex
    .sort((left, right) => {
      if (hasAnySequence) {
        const leftSequence = partSequence(left.part, left.index);
        const rightSequence = partSequence(right.part, right.index);
        if (leftSequence !== rightSequence) return leftSequence - rightSequence;
        return left.index - right.index;
      }
      const groupDifference = partGroup(left.part) - partGroup(right.part);
      if (groupDifference) return groupDifference;
      return left.index - right.index;
    })
    .map(({ part }) => part);
}

/**
 * Split an assistant message for ChatGPT-style rendering:
 * - chainParts: reasoning / tools / agent steps (outer collapsible, always top)
 * - answerParts: all visible text / cards / sources in chronological sequence
 *
 * Thinking is always above the answer body. Answer texts keep stream order so
 * opening narration A then final answer C renders as A → C (not C → A).
 */
export function partitionMessageParts(parts: MessagePart[]): {
  chainParts: MessagePart[];
  answerParts: MessagePart[];
} {
  const ordered = orderedMessageParts(parts);
  const chainParts: MessagePart[] = [];
  const answerParts: MessagePart[] = [];
  const deferredGraphProposals: MessagePart[] = [];
  for (const part of ordered) {
    if (isPlaceholderAcknowledgement(part) || isHostAgentBoilerplate(part)) {
      continue;
    }
    if (isChainPart(part)) {
      chainParts.push(part);
      continue;
    }
    // Graph review cards stay at the end of the answer body so mid-turn tool
    // emission does not interrupt narration; actions are also locked while
    // the assistant message is still streaming.
    if (
      part.type === "component" &&
      part.data?.component_type === "graph_update_proposal"
    ) {
      deferredGraphProposals.push(part);
      continue;
    }
    answerParts.push(part);
  }
  answerParts.push(...deferredGraphProposals);
  return { chainParts, answerParts };
}

/**
 * Group ordered parts into: thinking chain (top) → chronological answer body.
 */
export type DisplaySegment =
  | { kind: "parts"; parts: MessagePart[] }
  | { kind: "chain"; parts: MessagePart[] };

export function groupPartsForDisplay(parts: MessagePart[]): DisplaySegment[] {
  const { chainParts, answerParts } = partitionMessageParts(parts);
  const segments: DisplaySegment[] = [];
  // Thinking always sits at the top of the assistant turn.
  if (chainParts.length) {
    segments.push({ kind: "chain", parts: chainParts });
  }
  if (answerParts.length) {
    segments.push({ kind: "parts", parts: answerParts });
  }
  return segments;
}

function hasVisiblePartContent(part: MessagePart) {
  if (part.type === "acknowledgement") {
    if (isPlaceholderAcknowledgement(part)) return false;
    return Boolean(part.content?.trim());
  }
  if (isHostAgentBoilerplate(part)) return false;
  if (part.type === "image") return true;
  if (
    part.type === "sandbox" ||
    part.type === "sandbox_artifact" ||
    part.type === "sandbox_status" ||
    part.type === "magic_card" ||
    part.type === "component"
  ) {
    return true;
  }
  if (part.content?.trim() || part.content_delta?.trim()) return true;
  return ["agent_step", "tool_call", "graph_context", "skill_trigger"].includes(
    part.type,
  );
}

export function shouldShowThinkingPlaceholder(
  status: string,
  parts: MessagePart[],
) {
  if (status !== "streaming") return false;
  // Outer ThinkingChain owns the progress cue when chain parts exist.
  if (parts.some(isChainPart)) return false;
  // Model text / reasoning already streaming — no host placeholder.
  if (
    parts.some(
      (part) =>
        (part.type === "text" || isReasoningTextPart(part)) &&
        Boolean(part.content?.trim() || part.content_delta?.trim()),
    )
  ) {
    return false;
  }
  return !parts.some(hasVisiblePartContent);
}

/**
 * Live status line while streaming and the chain is collapsed —
 * e.g. "正在搜索 stock.eastmoney.com" or the latest mid-step plan sentence.
 */
export function currentActivityLabel(
  status: string,
  parts: MessagePart[],
): string | null {
  if (status !== "streaming") return null;
  const ordered = orderedMessageParts(parts);

  const activeTool = [...ordered]
    .reverse()
    .find(
      (part) =>
        part.type === "tool_call" &&
        (part.status === "streaming" || part.status === "pending"),
    );
  if (activeTool) {
    const toolName =
      typeof activeTool.data?.tool_name === "string"
        ? activeTool.data.tool_name
        : "";
    const title =
      typeof activeTool.data?.title === "string" ? activeTool.data.title : "";
    if (title.trim()) return title.trim();
    if (toolName === "search_web" || /search|检索|搜索/i.test(toolName)) {
      const input = activeTool.data?.input as
        | { query?: unknown }
        | undefined;
      const query =
        typeof input?.query === "string"
          ? input.query.trim()
          : "";
      return query ? `正在搜索 ${query}` : "正在搜索";
    }
    if (toolName) return `正在调用 ${toolName}`;
    return "正在执行工具";
  }

  // Prefer the latest mid-step plan sentence (round > 0) while waiting between tools.
  const latestPlan = [...ordered]
    .reverse()
    .find((part) => {
      if (!isPlanNarrationPart(part) || !part.content?.trim()) return false;
      const round = part.data?.agent_tool_round;
      if (typeof round !== "number" || round <= 0) return false;
      return (
        part.status === "streaming" ||
        part.status === "pending" ||
        part.status === "completed"
      );
    });
  if (latestPlan?.content?.trim()) {
    const text = latestPlan.content.trim().replace(/\s+/g, " ");
    return text.length > 48 ? `${text.slice(0, 48)}…` : text;
  }

  const activeStep = [...ordered]
    .reverse()
    .find(
      (part) =>
        part.type === "agent_step" &&
        (part.status === "streaming" || part.status === "pending"),
    );
  if (activeStep) {
    const title =
      (typeof activeStep.data?.title === "string" &&
        activeStep.data.title.trim()) ||
      (typeof activeStep.data?.label === "string" &&
        activeStep.data.label.trim()) ||
      activeStep.content?.trim();
    return title || "正在执行智能体步骤";
  }

  const activeReasoning = [...ordered]
    .reverse()
    .find(
      (part) =>
        (part.type === "reasoning_summary" ||
          part.type === "reasoning_content") &&
        (part.status === "streaming" || part.status === "pending"),
    );
  if (activeReasoning) {
    return activeReasoning.type === "reasoning_summary"
      ? "正在生成推理摘要"
      : "正在思考";
  }

  const activeSandbox = [...ordered]
    .reverse()
    .find(
      (part) =>
        part.type === "sandbox_status" &&
        (part.status === "streaming" || part.status === "pending"),
    );
  if (activeSandbox) {
    const phase =
      typeof activeSandbox.data?.phase === "string"
        ? activeSandbox.data.phase
        : "";
    return phase ? `沙箱 · ${phase}` : "正在执行沙箱";
  }

  return null;
}

export function formatThinkingDuration(seconds: number | undefined): string {
  if (seconds === undefined || !Number.isFinite(seconds) || seconds < 0) {
    return "几秒";
  }
  const wholeSeconds = Math.floor(seconds);
  if (wholeSeconds < 60) return `${wholeSeconds}s`;

  const units = [
    { label: "d", seconds: 86_400 },
    { label: "h", seconds: 3_600 },
    { label: "m", seconds: 60 },
    { label: "s", seconds: 1 },
  ] as const;
  let remaining = wholeSeconds;
  const segments: string[] = [];
  for (const unit of units) {
    const value = Math.floor(remaining / unit.seconds);
    if (value > 0) {
      segments.push(`${value}${unit.label}`);
      remaining %= unit.seconds;
    }
  }
  return segments.join(" ");
}

export function thinkingDurationSeconds(
  providerTrace: UnknownRecord,
): number | undefined {
  const milliseconds = providerTrace.generation_duration_ms;
  if (
    typeof milliseconds === "number" &&
    Number.isFinite(milliseconds) &&
    milliseconds >= 0
  ) {
    return Math.max(0, Math.floor(milliseconds / 1_000));
  }

  const startedAt = providerTrace.generation_started_at;
  const completedAt = providerTrace.generation_completed_at;
  if (typeof startedAt !== "string" || typeof completedAt !== "string") {
    return undefined;
  }
  const startedMs = Date.parse(startedAt);
  const completedMs = Date.parse(completedAt);
  if (
    !Number.isFinite(startedMs) ||
    !Number.isFinite(completedMs) ||
    completedMs < startedMs
  ) {
    return undefined;
  }
  return Math.max(0, Math.floor((completedMs - startedMs) / 1_000));
}
