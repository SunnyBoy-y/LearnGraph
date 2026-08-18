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
 * Agent-produced artifacts (images, html files, magic cards, charts…) that must
 * render in the answer body where the final answer references them — never
 * hidden inside the collapsed thinking chain. They keep their stream position
 * ahead of the final-answer text.
 */
export const ARTIFACT_PART_TYPES = new Set<MessagePart["type"]>([
  "image",
  "sandbox",
  "sandbox_artifact",
  "subapp_artifact",
  "magic_card",
  "component",
  "chart",
]);

/** Layout hint carried on an artifact part by the model's [[artifact:...|layout=]] reference. */
export const ARTIFACT_LAYOUTS = new Set(["inline", "block", "side"]);

export function artifactLayout(part: MessagePart): string | undefined {
  const layout = part.data?.layout;
  return typeof layout === "string" && ARTIFACT_LAYOUTS.has(layout)
    ? layout
    : undefined;
}

/** Artifact explicitly placed side-by-side with its introducing text segment. */
export function isSideLayoutArtifactPart(part: MessagePart): boolean {
  return isArtifactPart(part) && artifactLayout(part) === "side";
}

/** Artifact placed inline as a compact card inside the text flow. */
export function isInlineLayoutArtifactPart(part: MessagePart): boolean {
  return isArtifactPart(part) && artifactLayout(part) === "inline";
}

export function isArtifactPart(part: MessagePart): boolean {
  return ARTIFACT_PART_TYPES.has(part.type);
}

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

/** Graph review card that is deferred to the end of the answer body. */
export function isGraphUpdateProposalPart(part: MessagePart): boolean {
  return (
    part.type === "component" &&
    part.data?.component_type === "graph_update_proposal"
  );
}

/**
 * Chain-internal presentation order: parts keep their stream position so each
 * "思考过程" block stays next to the tool round it belongs to (a reasoning part
 * that arrives after a tool call renders right below that tool row, never
 * hoisted above the whole process). `processParts` is already in stream order,
 * so this is a defensive copy — no grouping.
 */
export function orderChainParts(parts: MessagePart[]): MessagePart[] {
  return [...parts];
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

/** Text part explicitly marked by the backend as the final answer start. */
export function isFinalAnswerPart(part: MessagePart): boolean {
  return part.type === "text" && part.data?.kind === "final_answer";
}

/**
 * Frontend-transient final-answer boundary delivered by the `answer.started`
 * SSE event. Not part of the REST payload; after history load the boundary is
 * re-derived from part `data.kind === "final_answer"` marks instead.
 */
export interface FinalAnswerBoundaryInfo {
  finalPartId?: string;
  boundarySequence?: number;
  thinkingDurationMs?: number;
}

/**
 * Index (into an `orderedMessageParts` array) of the first part that starts the
 * final answer, or -1 when no boundary is known yet. Prefers the live
 * `answer.started` boundary, then the durable part mark.
 */
export function findFinalAnswerBoundaryIndex(
  ordered: MessagePart[],
  boundary?: FinalAnswerBoundaryInfo,
): number {
  if (boundary) {
    if (boundary.finalPartId) {
      const byId = ordered.findIndex((part) => part.id === boundary.finalPartId);
      if (byId >= 0) return byId;
    }
    if (typeof boundary.boundarySequence === "number") {
      const bySequence = ordered.findIndex(
        (part) => part.sequence === boundary.boundarySequence,
      );
      if (bySequence >= 0) return bySequence;
    }
  }
  return ordered.findIndex(isFinalAnswerPart);
}

/** Number of settled tool calls in the chain (for the collapsed summary chip). */
export function toolCallCount(parts: MessagePart[]): number {
  return parts.filter(
    (part) =>
      part.type === "tool_call" &&
      (part.status === "completed" || part.status === "failed"),
  ).length;
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

export interface DisplayGroupOptions {
  /** Live final-answer boundary from the `answer.started` SSE event. */
  boundary?: FinalAnswerBoundaryInfo;
  /** True while the message is still streaming: without a boundary every
   *  process part (including unclassified text that may later become
   *  plan_narration) renders inside the chain in raw stream order. */
  live?: boolean;
}

/**
 * Text part with no content yet (the shared streaming placeholder or an
 * unpopulated draft). Renders nothing, never anchors anything.
 */
function isContentlessTextPart(part: MessagePart): boolean {
  return (
    part.type === "text" &&
    !part.content?.trim() &&
    !part.content_delta?.trim()
  );
}

/** Parts that never participate in adjacency / grouping decisions. */
function isInvisiblePart(part: MessagePart): boolean {
  return (
    isPlaceholderAcknowledgement(part) ||
    isHostAgentBoilerplate(part) ||
    isContentlessTextPart(part)
  );
}

/**
 * Whether a pre-answer text part (plan narration / plain narration) "anchors"
 * an artifact and therefore belongs in the answer body next to it.
 *
 * The rule is pure ordinal adjacency: ignoring process parts (tools, reasoning,
 * agent steps, status rows) and invisible parts, the nearest text-class
 * neighbor of this text part is an artifact part (image, sandbox artifact,
 * magic card, component, chart…). Narration that introduces an artifact
 * ("下面先看整体结构：") or explains one ("从图中可以看到…") stays in the
 * answer body; pure process narration ("我先查一下文档…" with no visible
 * result) stays inside the collapsed thinking chain.
 *
 * Deterministic: the same parts array always yields the same classification,
 * so live streaming and a page refresh never disagree.
 */
export function isArtifactAnchoringText(
  ordered: MessagePart[],
  index: number,
): boolean {
  if (ordered[index]?.type !== "text") return false;
  // Messages without any artifact have nothing to anchor; keep every
  // pre-answer narration in the chain (status quo for plain Q&A).
  if (!ordered.some(isArtifactPart)) return false;
  for (let offset = index - 1; offset >= 0; offset -= 1) {
    const neighbor = ordered[offset];
    if (isInvisiblePart(neighbor)) continue;
    if (isChainPart(neighbor)) continue;
    if (isArtifactPart(neighbor)) return true;
    // Nearest text-class neighbor is not an artifact: backward side does not
    // anchor. Keep scanning forward — the text may still introduce the
    // artifact that follows it.
    break;
  }
  for (let offset = index + 1; offset < ordered.length; offset += 1) {
    const neighbor = ordered[offset];
    if (isInvisiblePart(neighbor)) continue;
    if (isChainPart(neighbor)) continue;
    return isArtifactPart(neighbor);
  }
  return false;
}

/**
 * Split an assistant message for rendering (unified rule, replaces the old
 * boundary-cut + hoisting logic):
 *
 * - chain (fold): reasoning / tools / agent steps / status rows, PLUS
 *   pre-answer narration that does NOT anchor an artifact (smart filter);
 * - answer body: final-answer text, artifacts, and narration that anchors
 *   artifacts — all in pure ordinal stream order, so images / HTML cards /
 *   charts render exactly where the model's words reference them.
 *
 * Legacy messages (no `kind` marks and no live boundary) keep the old
 * type-based partition so history renders unchanged.
 */
export function groupPartsForDisplay(
  parts: MessagePart[],
  options?: DisplayGroupOptions,
): DisplaySegment[] {
  const ordered = orderedMessageParts(parts);
  const boundaryIndex = findFinalAnswerBoundaryIndex(ordered, options?.boundary);
  // New-protocol messages carry a live boundary, durable `kind` marks on
  // text parts, or the streaming flag (unclassified stream text waits inside
  // the chain until the boundary/kind arrives). Old history has none of these
  // and keeps the legacy type-based partition.
  const hasProtocolMarks =
    options?.boundary !== undefined ||
    options?.live === true ||
    ordered.some(
      (part) => part.type === "text" && typeof part.data?.kind === "string",
    );

  const processParts: MessagePart[] = [];
  const answerParts: MessagePart[] = [];
  const deferredGraphProposals: MessagePart[] = [];
  for (let index = 0; index < ordered.length; index += 1) {
    const part = ordered[index];
    if (isPlaceholderAcknowledgement(part) || isHostAgentBoilerplate(part)) {
      continue;
    }
    // Graph review cards stay at the end of the answer body so mid-turn tool
    // emission does not interrupt narration; actions are also locked while
    // the assistant message is still streaming.
    if (isGraphUpdateProposalPart(part)) {
      deferredGraphProposals.push(part);
      continue;
    }
    // Reasoning + tools + agent steps always fold into the thinking chain,
    // no matter where in the stream they were emitted (trailing reasoning
    // summaries included).
    if (isChainPart(part)) {
      processParts.push(part);
      continue;
    }

    if (part.type === "text") {
      // Empty streaming placeholder: render nothing (stays invisible inside
      // the chain) until real content arrives.
      if (isContentlessTextPart(part)) {
        processParts.push(part);
        continue;
      }
      if (!hasProtocolMarks) {
        // Legacy message: keep every non-empty text in the body in stream
        // order (historical behavior; artifacts already interleave by
        // ordinal).
        answerParts.push(part);
        continue;
      }
      // Final answer: from the live boundary or the durable kind mark.
      const isFinalAnswer =
        isFinalAnswerPart(part) ||
        (boundaryIndex >= 0 && index >= boundaryIndex);
      if (isFinalAnswer) {
        answerParts.push(part);
        continue;
      }
      // Pre-answer narration leaves the chain only when it anchors an
      // artifact; process narration stays folded.
      if (isArtifactAnchoringText(ordered, index)) {
        answerParts.push(part);
        continue;
      }
      processParts.push(part);
      continue;
    }

    // Everything else (artifacts, sources, quiz, attachments…) belongs to the
    // answer body at its ordinal position.
    answerParts.push(part);
  }

  answerParts.push(...deferredGraphProposals);
  const segments: DisplaySegment[] = [];
  if (processParts.length) {
    segments.push({ kind: "chain", parts: orderChainParts(processParts) });
  }
  if (answerParts.length) {
    segments.push({ kind: "parts", parts: answerParts });
  }
  return segments;
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

/**
 * Granular activity chips shown next to the collapsed chain title while
 * processing, e.g. ["正在思考", "正在调用工具", "search xxx"]. Derived from
 * the newest live parts only — no backend change needed.
 */
export function currentActivityChips(
  status: string,
  parts: MessagePart[],
): string[] {
  if (status !== "streaming") return [];
  const ordered = orderedMessageParts(parts);
  const chips: string[] = [];

  const activeReasoning = [...ordered]
    .reverse()
    .find(
      (part) =>
        (part.type === "reasoning_summary" ||
          part.type === "reasoning_content") &&
        (part.status === "streaming" || part.status === "pending"),
    );
  if (activeReasoning) chips.push("正在思考");

  const activeTools = [...ordered]
    .reverse()
    .filter(
      (part) =>
        part.type === "tool_call" &&
        (part.status === "streaming" || part.status === "pending"),
    )
    .slice(0, 2);
  for (const tool of activeTools) {
    const toolName =
      typeof tool.data?.tool_name === "string" ? tool.data.tool_name : "";
    const title =
      typeof tool.data?.title === "string" ? tool.data.title.trim() : "";
    if (title) {
      chips.push(title);
      continue;
    }
    if (toolName === "search_web" || /search|检索|搜索/i.test(toolName)) {
      const input = tool.data?.input as { query?: unknown } | undefined;
      const query =
        typeof input?.query === "string" ? input.query.trim() : "";
      chips.push(query ? `search ${query}` : "正在搜索");
      continue;
    }
    chips.push(toolName ? `tool · ${toolName}` : "正在调用工具");
  }

  if (!activeReasoning && chips.length === 0) {
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
      chips.push(phase ? `沙箱 · ${phase}` : "正在执行沙箱");
    }
  }

  if (chips.length === 0) {
    const latestPlan = [...ordered]
      .reverse()
      .find(
        (part) => isPlanNarrationPart(part) && Boolean(part.content?.trim()),
      );
    if (latestPlan?.content?.trim()) {
      const text = latestPlan.content.trim().replace(/\s+/g, " ");
      chips.push(text.length > 24 ? `${text.slice(0, 24)}…` : text);
    }
  }

  return [...new Set(chips)].slice(0, 3);
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
  // Frozen thinking duration (excludes final-answer generation) preferred —
  // it is the number the user sees on the collapsed chain header.
  const thinkingMs = providerTrace.thinking_duration_ms;
  if (
    typeof thinkingMs === "number" &&
    Number.isFinite(thinkingMs) &&
    thinkingMs >= 0
  ) {
    return Math.max(0, Math.floor(thinkingMs / 1_000));
  }

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
