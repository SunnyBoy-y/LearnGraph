import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  ArrowUp,
  Bot,
  CalendarDays,
  Check,
  ChevronLeft,
  ChevronDown,
  ChevronRight,
  Copy,
  FilePlus2,
  FileText,
  GitBranch,
  GitCompareArrows,
  LayoutDashboard,
  ImageIcon,
  LoaderCircle,
  MessageSquareQuote,
  Mic,
  Network,
  Pencil,
  RefreshCcw,
  Search,
  Sparkles,
  Square,
  Target,
  X,
} from "lucide-react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { SandboxAuthDialog, type SandboxAuthRequest } from "@/components/chat/sandbox-auth-dialog";

import { toast } from "sonner";

import {
  ApiError,
  autoTitleSession,
  branchSession,
  cancelSessionMessage,
  closeSession,
  confirmCompositeDraft,
  confirmGraphChangeSet,
  createSession,
  createCompositeDraft,
  discoverProviderModels,
  generateSessionSuggestedPrompts,
  getAgentSandboxReadiness,
  getMessageSnapshot,
  getSessionSuggestedPrompts,
  listMessageVersions,
  listSessionMessageEvents,
  listProviders,
  listGraphs,
  listMemories,
  listSessionMessages,
  listSettings,
  listSessions,
  listFiles,
  lookupFile,
  parseFile,
  rejectGraphChangeSet,
  retrySessionMessage,
  streamSessionMessage,
  updateSession,
  uploadFile,
  listAudioTranscriptions,
  transcribeAudioFile,
} from "@/api";
import { hashFileSha256 } from "@/lib/file-hash";
import {
  classifyNonAgentAttachment,
  fastThinkingAcceptAttribute,
  isAudioNameOrMime,
  isImageNameOrMime,
  isSpecialBinaryName,
} from "@/lib/chat-attachment-policy";
import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import {
  Message as AiMessage,
  MessageAction,
  MessageActions,
  MessageContent,
} from "@/components/ai-elements/message";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  PromptInput,
  PromptInputActionAddAttachments,
  PromptInputActionMenu,
  PromptInputActionMenuContent,
  PromptInputActionMenuItem,
  PromptInputActionMenuTrigger,
  PromptInputBody,
  PromptInputAttachments,
  PromptInputButton,
  PromptInputSubmit,
  PromptInputTextarea,
  type PromptInputMessage,
} from "@/components/ai-elements/prompt-input";
import { ChatStreamPartRenderer } from "@/components/chat/chat-stream-part-renderer";
import type { TrustedComponentAction } from "@/components/chat/trusted-component-renderer";
import {
  locateSelectionInContent,
  selectionToolbarPoint,
} from "@/features/chat/text-selection";
import {
  markSessionGenerationFinished,
  markSessionRunning,
  markSessionTouched,
  markSessionViewed,
} from "@/lib/session-activity";
import {
  abortSessionStream,
  clearSessionStream,
  getSessionStream,
  isSessionStreaming,
  registerSessionStream,
  setSessionStreamMessageId,
} from "@/lib/session-streams";
import {
  PageFrame,
  PageIntro,
  SectionHeading,
  StatePill,
  Surface,
} from "@/components/shared/page-elements";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { InputGroupAddon } from "@/components/ui/input-group";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { FileRecord } from "@/types/files";
import {
  isDeepSeekProvider,
  type Provider,
  type ProviderModel,
} from "@/types/providers";
import {
  clearDraftSessionId,
  getDraftSessionId,
  isDefaultDraftTitle,
  setDraftSessionId,
} from "@/lib/draft-session";
import {
  defaultComposerPrefs,
  getSessionComposerPrefs,
  inheritSessionComposerPrefs,
  isDefaultComposerPrefs,
  prefsFromModelSnapshot,
  setSessionComposerPrefs,
  type GenerationMode,
  type ResponseMode,
  type SearchRoute,
  type ThinkingMode,
} from "@/lib/session-composer-prefs";
import { areChatSuggestedPromptsEnabled, readChatFeatureModelSetting, CHAT_AUTO_TITLE_MODEL_SETTING_KEY, CHAT_SUGGESTED_PROMPTS_MODEL_SETTING_KEY } from "@/lib/workspace-settings";
import { shouldShowSuggestedPromptError } from "@/lib/suggested-prompts";
import { cn } from "@/lib/utils";
import {
  groupPartsForDisplay,
  orderedMessageParts,
  shouldShowThinkingPlaceholder,
  thinkingDurationSeconds,
} from "@/features/chat/chat-message-parts";
import { ThinkingChain } from "@/components/chat/thinking-chain";
import {
  GoalSetupConversation,
  useGoalSetupFlow,
} from "@/features/goals/goal-chat-flow";
import type { Goal } from "@/types/goals";
import type { Graph } from "@/types/graphs";
import type {
  Message,
  MessageCreateRequest,
  MessagePart,
  MessageRetryRequest,
  MessageSelectionContext,
  Session,
  SessionMessageStreamData,
  SuggestedPrompt,
} from "@/types/sessions";

type ChatStatus = "ready" | "submitted" | "streaming" | "error";
type GraphAction = NonNullable<MessageCreateRequest["graph_action"]>;
type LearningNodeContext = {
  graphId: string;
  nodeId?: string;
  nodeIds?: string[];
  nodeLabel?: string;
};

type TextSelectionMenu = MessageSelectionContext & {
  /** False when the browser selection could not be mapped onto durable content. */
  contentMatched: boolean;
  left: number;
  occurrenceIndex: number;
  top: number;
};

type ComposerCommandId =
  | "goal"
  | "image"
  | "search"
  | "agentic"
  | "graph"
  | "research"
  | "roadmap"
  | "schedule"
  | "progress"
  | "practice"
  | "usage";

const LEARNING_NODE_CONTEXT_STORAGE_KEY = "learngraph:active-learning-node";
const TERMINAL_MESSAGE_STATUSES = ["completed", "failed", "cancelled"] as const;
const IN_FLIGHT_MESSAGE_STATUSES = ["pending", "streaming"] as const;

/** Sidebar/top-bar title for sessions created by edit/branch. */
function branchSessionTitle(sourceTitle: string | null | undefined): string {
  const base = (sourceTitle ?? "").trim() || "未命名会话";
  const stripped = base.replace(/^(分支\.)+/, "").trim() || "未命名会话";
  return `分支.${stripped}`;
}

/** Default composer draft when a learning node becomes the active context. */
function learningNodeComposerDraft(nodeLabel: string): string {
  return `什么是 ${nodeLabel}？`;
}

/**
 * True when the composer still holds an auto-generated node draft (empty, or
 * the previous “什么是 X？” fill). User-edited text is left alone on node switch.
 */
function isLearningNodeComposerDraft(
  text: string,
  previousNodeLabel?: string,
): boolean {
  const trimmed = text.trim();
  if (!trimmed) return true;
  if (previousNodeLabel && trimmed === learningNodeComposerDraft(previousNodeLabel))
    return true;
  // Also treat any “什么是 …？” single-line draft as replaceable node fill,
  // so switching nodes updates the prompt even if the previous label is unknown.
  return /^什么是\s+.+？$/.test(trimmed) && !trimmed.includes("\n");
}

async function confirmedSessionMessages(
  sessionId: string,
  messageId: string,
  statuses: readonly string[],
  minimumVersion = 0,
) {
  const persisted = await listSessionMessages(sessionId);
  const message = persisted.find((item) => item.id === messageId);
  if (
    !message ||
    !statuses.includes(message.status) ||
    message.version < minimumVersion
  )
    throw new Error("持久消息尚未同步到预期终态。");
  return persisted;
}

const thinkingLabels: Record<ThinkingMode, string> = {
  off: "关闭",
  low: "低",
  medium: "中",
  high: "高",
  xhigh: "极高",
};

function capabilityThinkingModes(value: unknown): ThinkingMode[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is ThinkingMode =>
      typeof item === "string" && item in thinkingLabels,
  );
}

type BrowserSpeechRecognitionResult = {
  0: { transcript: string };
  isFinal: boolean;
};
type BrowserSpeechRecognitionEvent = {
  resultIndex: number;
  results: ArrayLike<BrowserSpeechRecognitionResult>;
};
type BrowserSpeechRecognitionErrorEvent = { error?: string };
type BrowserSpeechRecognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: BrowserSpeechRecognitionEvent) => void) | null;
  onerror: ((event: BrowserSpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
};
type BrowserSpeechRecognitionConstructor = new () => BrowserSpeechRecognition;

function readLearningNodeContext(): LearningNodeContext | undefined {
  try {
    const raw = window.sessionStorage.getItem(
      LEARNING_NODE_CONTEXT_STORAGE_KEY,
    );
    const value = raw ? (JSON.parse(raw) as LearningNodeContext) : undefined;
    return value && typeof value.graphId === "string" ? value : undefined;
  } catch {
    return undefined;
  }
}

function storeLearningNodeContext(context: LearningNodeContext) {
  try {
    window.sessionStorage.setItem(
      LEARNING_NODE_CONTEXT_STORAGE_KEY,
      JSON.stringify(context),
    );
  } catch {
    // Current-page state still carries the selected learning node.
  }
}

function clearLearningNodeContext() {
  try {
    window.sessionStorage.removeItem(LEARNING_NODE_CONTEXT_STORAGE_KEY);
  } catch {
    // A new session can still clear its in-memory context.
  }
}

function appendPart(
  parts: MessagePart[],
  incoming: MessagePart,
): MessagePart[] {
  const visibleParts =
    incoming.type === "image"
      ? parts.filter((part) => part.data?.optimistic !== true)
      : parts;
  const index = visibleParts.findIndex((part) => part.id === incoming.id);
  // Prefer an explicit full content replace (part.replaced / part.completed)
  // over delta-append so agent-tool-round rewinds and timeout retries work.
  const nextContent =
    typeof incoming.content === "string"
      ? incoming.content
      : `${visibleParts[index]?.content ?? ""}${incoming.content_delta ?? ""}`;
  if (index === -1)
    return [
      ...visibleParts,
      {
        ...incoming,
        content: nextContent,
        sequence:
          typeof incoming.sequence === "number"
            ? incoming.sequence
            : visibleParts.length,
      },
    ];
  return visibleParts.map((part, partIndex) =>
    partIndex === index
      ? {
          ...part,
          ...incoming,
          data: { ...(part.data ?? {}), ...(incoming.data ?? {}) },
          content: nextContent,
          sequence:
            typeof incoming.sequence === "number"
              ? incoming.sequence
              : (part.sequence ?? partIndex),
        }
      : part,
  );
}

function isMessagePart(value: unknown): value is MessagePart {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as MessagePart).id === "string" &&
    typeof (value as MessagePart).type === "string" &&
    typeof (value as MessagePart).status === "string"
  );
}

function streamData(value: SessionMessageStreamData): Record<string, unknown> {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : {};
}

function streamEventType(data: Record<string, unknown>): string {
  return typeof data.type === "string"
    ? data.type
    : typeof data.event === "string"
      ? data.event
      : "";
}

function providerCapabilityString(provider: Provider | undefined, key: string) {
  const value = provider?.capabilities[key];
  return typeof value === "string" ? value.trim() : "";
}

function providerModelOptions(
  provider: Provider | undefined,
  discovered: ProviderModel[] | undefined,
  defaultModelCapability = "default_model",
) {
  if (!provider) return [];
  const persistedIds = Array.isArray(provider.capabilities.discovered_model_ids)
    ? provider.capabilities.discovered_model_ids.filter(
        (item): item is string => typeof item === "string" && Boolean(item.trim()),
      )
    : [];
  const persistedCapabilities =
    provider.capabilities.models &&
    typeof provider.capabilities.models === "object" &&
    !Array.isArray(provider.capabilities.models)
      ? (provider.capabilities.models as Record<string, ProviderModel["capabilities"]>)
      : {};
  const persistedModels: ProviderModel[] = persistedIds.map((id) => ({
    id,
    roles: ["llm"],
    streaming: true,
    remote: true,
    enabled: true,
    capabilities: persistedCapabilities[id],
  }));
  const byId = new Map(
    (discovered ?? persistedModels).map((model) => [model.id, model]),
  );
  const configured = providerCapabilityString(provider, defaultModelCapability);
  if (configured && !byId.has(configured))
    byId.set(configured, {
      id: configured,
      roles: ["llm"],
      streaming: true,
      remote: true,
      enabled: true,
    });
  const rawStates = provider.capabilities.model_states;
  const states =
    rawStates && typeof rawStates === "object" && !Array.isArray(rawStates)
      ? (rawStates as Record<string, unknown>)
      : {};
  return [...byId.values()].filter(
    (model) => model.enabled !== false && states[model.id] !== false,
  );
}

function fuzzyModelMatch(value: string, query: string): boolean {
  const normalizedValue = value.toLocaleLowerCase();
  const normalizedQuery = query.trim().toLocaleLowerCase();
  if (!normalizedQuery) return true;
  if (normalizedValue.includes(normalizedQuery)) return true;
  let queryIndex = 0;
  for (const character of normalizedValue) {
    if (character === normalizedQuery[queryIndex]) queryIndex += 1;
    if (queryIndex === normalizedQuery.length) return true;
  }
  return false;
}

function modelChoiceValue(providerId: string, modelId: string): string {
  return `${encodeURIComponent(providerId)}|${encodeURIComponent(modelId)}`;
}

function parseModelChoiceValue(
  value: string,
): { providerId: string; modelId: string } | null {
  const separator = value.indexOf("|");
  if (separator < 1) return null;
  try {
    return {
      providerId: decodeURIComponent(value.slice(0, separator)),
      modelId: decodeURIComponent(value.slice(separator + 1)),
    };
  } catch {
    return null;
  }
}

function modelProtocolLabel(providerType: string): string {
  if (providerType === "openai_responses") return "Responses";
  if (providerType === "anthropic_messages") return "Anthropic Messages";
  return "Compatible Chat";
}

const STREAM_EVENTS_PER_FRAME = 3;
const STREAM_DELTA_CHARS = 28;
const DEFAULT_STREAM_RECONNECTS = 5;
const STREAM_RECONNECT_DELAYS_MS = [500, 1_000, 2_000, 4_000, 8_000] as const;

type StreamConnectionNotice = {
  phase: "reconnecting" | "failed";
  attempt: number;
  maxAttempts: number;
  detail: string;
};

function streamErrorDetail(error: unknown): string {
  if (error instanceof ApiError) {
    return `HTTP ${error.status} · ${error.code} · ${error.message}`;
  }
  if (error instanceof Error) return error.message;
  return "连接意外中断";
}

function streamEventFailure(data: Record<string, unknown>): string {
  const payload =
    typeof data.payload === "object" && data.payload !== null
      ? (data.payload as Record<string, unknown>)
      : {};
  const error =
    typeof payload.error === "object" && payload.error !== null
      ? (payload.error as Record<string, unknown>)
      : typeof data.error === "object" && data.error !== null
        ? (data.error as Record<string, unknown>)
        : {};
  const code = typeof error.code === "string" ? error.code : "provider_error";
  const message =
    typeof error.message === "string" ? error.message : "模型提供商返回失败";
  const status =
    typeof error.status === "number"
      ? `HTTP ${error.status} · `
      : typeof error.status_code === "number"
        ? `HTTP ${error.status_code} · `
        : "";
  return `${status}${code} · ${message}`;
}

async function waitForStreamReconnect(
  reconnectAttempt: number,
  signal: AbortSignal,
): Promise<void> {
  if (signal.aborted) throw signal.reason;
  const delay =
    STREAM_RECONNECT_DELAYS_MS[
      Math.min(reconnectAttempt, STREAM_RECONNECT_DELAYS_MS.length - 1)
    ];
  await new Promise<void>((resolve, reject) => {
    let timer: number | undefined;
    const cleanup = () => {
      if (timer !== undefined) window.clearTimeout(timer);
      window.removeEventListener("online", finish);
      signal.removeEventListener("abort", abort);
    };
    const finish = () => {
      cleanup();
      resolve();
    };
    const abort = () => {
      cleanup();
      reject(signal.reason);
    };
    signal.addEventListener("abort", abort, { once: true });
    if (!window.navigator.onLine) {
      window.addEventListener("online", finish, { once: true });
      return;
    }
    timer = window.setTimeout(finish, delay);
  });
}

function StreamConnectionFeedback({
  notice,
}: {
  notice: StreamConnectionNotice;
}) {
  const reconnecting = notice.phase === "reconnecting";
  return (
    <div
      aria-live={reconnecting ? "polite" : "assertive"}
      className={`chat-stream-connection chat-stream-connection--${notice.phase}`}
      role={reconnecting ? "status" : "alert"}
    >
      <LoaderCircle
        aria-hidden="true"
        className={reconnecting ? "animate-spin" : undefined}
      />
      <div>
        <strong>
          {reconnecting
            ? notice.attempt > 0
              ? `连接中断，正在重连（${notice.attempt}/${notice.maxAttempts}）`
              : "正在续接生成任务"
            : "连接或模型服务异常"}
        </strong>
        <span>{notice.detail}</span>
      </div>
    </div>
  );
}

function expandStreamUpdate(data: Record<string, unknown>) {
  const part = isMessagePart(data.part) ? data.part : undefined;
  const eventType = streamEventType(data);
  const delta = part?.content_delta;
  if (
    !part ||
    typeof delta !== "string" ||
    !["part.delta", "message.part.delta"].includes(eventType)
  )
    return [data];
  const characters = Array.from(delta);
  if (characters.length <= STREAM_DELTA_CHARS) return [data];
  const updates: Record<string, unknown>[] = [];
  for (let index = 0; index < characters.length; index += STREAM_DELTA_CHARS) {
    updates.push({
      ...data,
      part: {
        ...part,
        content: undefined,
        content_delta: characters
          .slice(index, index + STREAM_DELTA_CHARS)
          .join(""),
      },
    });
  }
  return updates;
}

function applyStreamUpdates(
  message: Message,
  updates: Record<string, unknown>[],
): Message {
  return updates.reduce<Message>((current, data) => {
    const eventType = streamEventType(data);
    if (eventType === "message.completed")
      return {
        ...current,
        status: "completed",
        provider_trace: (data.provider_trace ?? {}) as Record<string, unknown>,
      };
    if (eventType === "message.failed" || eventType === "message.cancelled")
      return {
        ...current,
        status: eventType === "message.cancelled" ? "cancelled" : "failed",
        parts: current.parts.map((part) =>
          part.data?.optimistic === true
            ? { ...part, status: "failed" as const }
            : part,
        ),
      };
    if (isMessagePart(data.part))
      return {
        ...current,
        parts: appendPart(current.parts, data.part),
      };
    return current;
  }, message);
}

function createAnimationFrameQueue<T>(onBatch: (batch: T[]) => void) {
  let pending: T[] = [];
  let frameId: number | null = null;
  let scheduledWithAnimationFrame = false;
  let drainResolvers: Array<() => void> = [];

  const resolveDrains = () => {
    if (pending.length || frameId !== null) return;
    const resolvers = drainResolvers;
    drainResolvers = [];
    resolvers.forEach((resolve) => resolve());
  };
  const schedule = () => {
    if (frameId !== null) return;
    const run = () => {
      frameId = null;
      const batchSize =
        pending.length > 90
          ? STREAM_EVENTS_PER_FRAME * 4
          : pending.length > 30
            ? STREAM_EVENTS_PER_FRAME * 2
            : STREAM_EVENTS_PER_FRAME;
      const batch = pending.splice(0, batchSize);
      if (batch.length) onBatch(batch);
      if (pending.length) schedule();
      else resolveDrains();
    };
    scheduledWithAnimationFrame =
      document.visibilityState === "visible" &&
      typeof window.requestAnimationFrame === "function";
    frameId = scheduledWithAnimationFrame
      ? window.requestAnimationFrame(run)
      : window.setTimeout(run, 16);
  };

  return {
    push(item: T) {
      pending.push(item);
      schedule();
    },
    drain() {
      if (!pending.length && frameId === null) return Promise.resolve();
      return new Promise<void>((resolve) => drainResolvers.push(resolve));
    },
    clear() {
      pending = [];
      if (frameId !== null) {
        if (scheduledWithAnimationFrame)
          window.cancelAnimationFrame(frameId);
        else window.clearTimeout(frameId);
        frameId = null;
      }
      resolveDrains();
    },
  };
}

function readTextSelection(): TextSelectionMenu | null {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed)
    return null;
  const selectedText = selection.toString().trim().slice(0, 500);
  if (selectedText.length < 3) return null;
  const range = selection.getRangeAt(0);
  const startElement =
    range.startContainer instanceof HTMLElement
      ? range.startContainer
      : range.startContainer.parentElement;
  const endElement =
    range.endContainer instanceof HTMLElement
      ? range.endContainer
      : range.endContainer.parentElement;
  const selectableStart = startElement?.closest<HTMLElement>(
    "[data-message-selectable-text]",
  );
  const selectableEnd = endElement?.closest<HTMLElement>(
    "[data-message-selectable-text]",
  );
  // Cross-message selections stay unsupported; multi-block text inside one
  // selectable root (tables, lists, multiple markdown paragraphs) is fine.
  if (!selectableStart || selectableStart !== selectableEnd) return null;
  const messageElement = selectableStart.closest<HTMLElement>("[data-message-id]");
  const sourceMessageId = messageElement?.dataset.messageId;
  if (
    !messageElement ||
    !sourceMessageId ||
    sourceMessageId.startsWith("temp") ||
    sourceMessageId === "welcome-local" ||
    messageElement.dataset.selectionDisabled === "true"
  )
    return null;
  const contentElement = selectableStart;
  let prefix = "";
  let suffix = "";
  let occurrenceIndex = 0;
  try {
    const before = range.cloneRange();
    before.selectNodeContents(contentElement);
    before.setEnd(range.startContainer, range.startOffset);
    prefix = before.toString().slice(-500);
    const precedingText = before.toString();
    // Count prior exact occurrences of the *raw* browser selection; multi-line
    // selections rarely collide exactly, so this is only a soft preference.
    let precedingOffset = 0;
    while (precedingOffset <= precedingText.length - selectedText.length) {
      const occurrence = precedingText.indexOf(selectedText, precedingOffset);
      if (occurrence < 0) break;
      occurrenceIndex += 1;
      precedingOffset = occurrence + Math.max(1, selectedText.length);
    }
    const after = range.cloneRange();
    after.selectNodeContents(contentElement);
    after.setStart(range.endContainer, range.endOffset);
    suffix = after.toString().slice(0, 500);
  } catch {
    const allText = contentElement.textContent ?? "";
    const selectedIndex = allText.indexOf(selectedText);
    if (selectedIndex >= 0) {
      prefix = allText.slice(Math.max(0, selectedIndex - 500), selectedIndex);
      suffix = allText.slice(
        selectedIndex + selectedText.length,
        selectedIndex + selectedText.length + 500,
      );
    }
  }
  const point = selectionToolbarPoint(range);
  if (!point) return null;
  return {
    source_message_id: sourceMessageId,
    selected_text: selectedText,
    prefix,
    suffix,
    occurrenceIndex,
    contentMatched: false,
    left: point.left,
    top: point.top,
  };
}

function selectionRequestContext(
  selection: TextSelectionMenu,
): MessageSelectionContext | null {
  if (!selection.contentMatched) return null;
  return {
    source_message_id: selection.source_message_id,
    selected_text: selection.selected_text,
    prefix: selection.prefix,
    suffix: selection.suffix,
  };
}

type MessageVersionNavigation = {
  currentIndex: number;
  total: number;
  onNext: () => void;
  onPrevious: () => void;
};

function MessageVersionNavigator({
  navigation,
}: {
  navigation?: MessageVersionNavigation;
}) {
  if (!navigation || navigation.total <= 1) return null;
  return (
    <MessageActions className="min-h-7 justify-end text-xs text-muted-foreground">
      <MessageAction
        disabled={navigation.currentIndex === 0}
        label="上一版本"
        onClick={navigation.onPrevious}
        tooltip="上一版本"
      >
        <ChevronLeft className="size-3.5" />
      </MessageAction>
      <span aria-live="polite" className="min-w-8 text-center tabular-nums">
        {navigation.currentIndex + 1}/{navigation.total}
      </span>
      <MessageAction
        disabled={navigation.currentIndex === navigation.total - 1}
        label="下一版本"
        onClick={navigation.onNext}
        tooltip="下一版本"
      >
        <ChevronRight className="size-3.5" />
      </MessageAction>
    </MessageActions>
  );
}

function UserMessage({
  message,
  editing,
  editValue,
  disabled,
  versionNavigation,
  onCancelEdit,
  onEditValueChange,
  onSaveEdit,
  onStartEdit,
}: {
  message: Message;
  editing: boolean;
  editValue: string;
  disabled: boolean;
  versionNavigation?: MessageVersionNavigation;
  onCancelEdit: () => void;
  onEditValueChange: (value: string) => void;
  onSaveEdit: () => void;
  onStartEdit: () => void;
}) {
  const copyMessage = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      toast.success("已复制消息");
    } catch {
      toast.error("无法复制消息");
    }
  };

  return (
    <AiMessage className="relative" data-message-id={message.id} from="user">
      <MessageContent
        className={editing ? "w-full sm:max-w-xl" : undefined}
        data-message-content
      >
        {editing ? (
          <div className="space-y-2">
            <Textarea
              aria-label="编辑消息"
              autoFocus
              className="min-h-24 bg-background/80 text-sm"
              disabled={disabled}
              onChange={(event) => onEditValueChange(event.currentTarget.value)}
              onKeyDown={(event) => {
                if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                  event.preventDefault();
                  onSaveEdit();
                }
              }}
              value={editValue}
            />
            <div className="flex justify-end gap-2">
              <Button
                disabled={disabled}
                onClick={onCancelEdit}
                size="xs"
                type="button"
                variant="ghost"
              >
                取消
              </Button>
              <Button
                disabled={disabled || !editValue.trim()}
                onClick={onSaveEdit}
                size="xs"
                type="button"
              >
                <Check className="size-3.5" />
                保存并重新回答
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            {message.parts
              .filter((part) =>
                ["attachment", "document_selection", "selection_quote"].includes(
                  part.type,
                ),
              )
              .map((part) => <ChatStreamPartRenderer key={part.id} part={part} />)}
            <p
              className="whitespace-pre-wrap leading-6"
              data-message-selectable-text
            >
              {message.content}
            </p>
          </div>
        )}
      </MessageContent>
      {!editing ? (
        <MessageActions className="min-h-8 justify-end opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
          <MessageAction
            label="复制消息"
            onClick={() => void copyMessage()}
            tooltip="复制消息"
          >
            <Copy className="size-3.5" />
          </MessageAction>
          <MessageAction
            disabled={disabled}
            label="编辑消息"
            onClick={onStartEdit}
            tooltip="编辑消息"
          >
            <Pencil className="size-3.5" />
          </MessageAction>
        </MessageActions>
      ) : null}
      <MessageVersionNavigator navigation={versionNavigation} />
    </AiMessage>
  );
}

function AssistantMessage({
  message,
  sessionId,
  onRetry,
  onBranch,
  onComponentAction,
  retryDisabled = false,
  retryDisabledReason,
  branchDisabled = false,
  branchDisabledReason,
}: {
  message: Message;
  sessionId: string;
  onRetry: () => void;
  onBranch: () => void;
  onComponentAction: (action: TrustedComponentAction) => void | Promise<void>;
  retryDisabled?: boolean;
  retryDisabledReason?: string;
  branchDisabled?: boolean;
  branchDisabledReason?: string;
}) {
  const persisted =
    !message.id.startsWith("temp") && message.id !== "welcome-local";
  const versions = useQuery({
    queryKey: ["message-versions", sessionId, message.id],
    queryFn: () => listMessageVersions(sessionId, message.id),
    enabled: persisted,
  });
  const [selectedVersionId, setSelectedVersionId] = useState<
    string | undefined
  >();
  useEffect(() => {
    setSelectedVersionId(undefined);
  }, [message.id, message.version]);
  const snapshot = useQuery({
    queryKey: ["message-snapshot", sessionId, message.id, selectedVersionId],
    queryFn: () => getMessageSnapshot(sessionId, message.id, selectedVersionId),
    enabled: persisted && Boolean(selectedVersionId),
  });
  const shown = snapshot.data ?? message;
  const orderedParts = orderedMessageParts(shown.parts);
  // Thinking chain (reasoning + tools) is always rendered above the final body.
  const displaySegments = groupPartsForDisplay(shown.parts);
  const fullText = shown.parts
    .filter((part) => part.type === "text" || part.type === "acknowledgement")
    .map((part) => part.content ?? "")
    .filter((content) => content && content.trim() !== "正在思考")
    .join("\n");
  // Empty stream before any chain/answer part arrives.
  const isThinkingPlaceholder = shouldShowThinkingPlaceholder(
    shown.status,
    orderedParts,
  );
  const renderPart = (part: MessagePart) => (
    <ChatStreamPartRenderer
      key={part.id}
      onAction={onComponentAction}
      part={part}
      siblingParts={orderedParts}
      streaming={
        shown.status === "streaming" &&
        (part.status === "streaming" || part.status === "pending")
      }
    />
  );
  return (
    <AiMessage
      className="max-w-none"
      data-message-id={message.id}
      data-selection-disabled={selectedVersionId ? "true" : undefined}
      from="assistant"
    >
      {versions.data && versions.data.length > 1 ? (
        <div className="mb-2 flex items-center gap-1 text-[10px] text-muted-foreground">
          版本：
          {versions.data.map((version) => (
            <Button
              key={version.id}
              onClick={() => setSelectedVersionId(version.id)}
              size="xs"
              variant={
                selectedVersionId === version.id ||
                (!selectedVersionId && version.version === message.version)
                  ? "secondary"
                  : "ghost"
              }
            >
              v{version.version}
            </Button>
          ))}
        </div>
      ) : null}
      <MessageContent className="w-full gap-1" data-message-content>
        {isThinkingPlaceholder ? (
          <div className="message-thinking" role="status" aria-live="polite">
            <span className="message-thinking__dot" />
            <span>正在思考</span>
          </div>
        ) : null}
        {displaySegments.map((segment, index) =>
          segment.kind === "chain" ? (
            <ThinkingChain
              key={`chain-${message.id}-${index}`}
              chainParts={segment.parts}
              completedDurationSec={thinkingDurationSeconds(
                shown.provider_trace,
              )}
              messageStatus={shown.status}
              startedAt={
                typeof shown.provider_trace.generation_started_at === "string"
                  ? shown.provider_trace.generation_started_at
                  : shown.created_at
              }
            >
              {segment.parts.map(renderPart)}
            </ThinkingChain>
          ) : (
            <div
              className="message-answer-segment"
              key={`parts-${message.id}-${index}`}
            >
              {segment.parts.map(renderPart)}
            </div>
          ),
        )}
      </MessageContent>
      <MessageActions className="opacity-60 transition-opacity focus-within:opacity-100 hover:opacity-100">
        <MessageAction
          label="复制全文"
          onClick={() =>
           void navigator.clipboard
              .writeText(fullText)
              .then(() => toast.success("已复制回答"))
              .catch(() => toast.error("无法复制回答"))
          }
          tooltip="复制全文"
        >
          <Copy className="size-3.5" />
        </MessageAction>
        <MessageAction
          disabled={retryDisabled}
          label="重试并保留版本"
          onClick={onRetry}
          tooltip={retryDisabledReason ?? "重试并保留版本"}
        >
          <RefreshCcw className="size-3.5" />
        </MessageAction>
        <MessageAction
          disabled={branchDisabled}
          label="从此创建分支"
          onClick={onBranch}
          tooltip={branchDisabledReason ?? "从此创建分支"}
        >
          <GitBranch className="size-3.5" />
        </MessageAction>
        {sessionId ? (
          <Badge className="ml-1 font-mono text-[10px]" variant="secondary">
            {message.id} · v{shown.version}
          </Badge>
        ) : null}
      </MessageActions>
    </AiMessage>
  );
}

function EmptySessionPrompts({
  prompts,
  onSelect,
  disabled,
  isError,
  isPending,
  isUnavailable,
  onConfigureProvider,
  onRetry,
}: {
  prompts: SuggestedPrompt[];
  onSelect: (content: string) => void;
  disabled: boolean;
  isError: boolean;
  isPending: boolean;
  isUnavailable: boolean;
  onConfigureProvider: () => void;
  onRetry: () => void;
}) {
  return (
    <ConversationEmptyState className="chat-empty-state">
      <div className="chat-empty-state__content">
        <p className="chat-empty-state__eyebrow">新会话</p>
        <h2>从一个问题开始</h2>
        <SuggestedPromptContent
          disabled={disabled}
          isError={isError}
          isPending={isPending}
          isUnavailable={isUnavailable}
          onConfigureProvider={onConfigureProvider}
          onRetry={onRetry}
          onSelect={onSelect}
          prompts={prompts}
        />
      </div>
    </ConversationEmptyState>
  );
}

/** 空会话默认问题：优先来自记忆，否则使用中文通用开场提示。 */
function buildMemoryBackedEmptyPrompts(
  memories: Array<{ id: string; title: string; content: string | null }>,
): SuggestedPrompt[] {
  const active = memories.filter((item) => (item.title || item.content)?.trim());
  if (!active.length) {
    return [
      {
        id: "empty-zh-1",
        content: "我现在最应该先学什么？",
      },
      {
        id: "empty-zh-2",
        content: "请根据我当前的学习目标，帮我梳理可学的主题。",
      },
    ];
  }
  return active.slice(0, 2).map((item, index) => {
    const title = (item.title || "").trim();
    const snippet = (item.content || "").trim().replace(/\s+/g, " ").slice(0, 48);
    const topic = title || snippet || "这段记忆";
    return {
      id: `memory-prompt-${item.id || index}`,
      content:
        index === 0
          ? `结合我记住的「${topic}」，接下来该怎么学？`
          : `请基于「${topic}」帮我回顾关键点，并给出下一步练习。`,
    };
  });
}

function SuggestedPromptContent({
  prompts,
  onSelect,
  disabled,
  isError,
  isPending,
  isUnavailable,
  onConfigureProvider,
  onRetry,
}: {
  prompts: SuggestedPrompt[];
  onSelect: (content: string) => void;
  disabled: boolean;
  isError: boolean;
  isPending: boolean;
  isUnavailable: boolean;
  onConfigureProvider: () => void;
  onRetry: () => void;
}) {
  // 即便模型/Provider 不可用，也展示记忆或中文默认提示，避免空白或英文占位。
  if (!isPending && !isError && !isUnavailable && !prompts.length) return null;

  return (
    <div
      aria-busy={isPending}
      aria-live="polite"
      className="chat-suggestions"
    >
      {isPending && !prompts.length ? (
        <div className="chat-suggestions__status">
          <LoaderCircle
            aria-hidden="true"
            className="chat-suggestions__spinner size-3.5 shrink-0"
          />
          <span>正在生成问题提示…</span>
        </div>
      ) : isUnavailable && !prompts.length ? (
        <div className="chat-suggestions__status chat-suggestions__status--unavailable">
          <span>需配置真实模型 Provider</span>
          <Button onClick={onConfigureProvider} size="xs" variant="outline">
            前往设置
          </Button>
        </div>
      ) : isError && !prompts.length ? (
        <div className="chat-suggestions__status chat-suggestions__status--error">
          <span>问题提示生成失败</span>
          <Button onClick={onRetry} size="xs" variant="ghost">
            <RefreshCcw aria-hidden="true" className="size-3" />
            重试
          </Button>
        </div>
      ) : (
        <div aria-label="推荐问题" className="chat-empty-state__prompts">
          {prompts.map((prompt) => (
            <button
              className="chat-empty-state__prompt"
              disabled={disabled}
              key={prompt.id}
              onClick={() => onSelect(prompt.content)}
              type="button"
            >
              {prompt.content}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function FollowUpPrompts({
  prompts,
  onSelect,
  isError,
  isPending,
  isUnavailable,
  onConfigureProvider,
  onRetry,
}: {
  prompts: SuggestedPrompt[];
  onSelect: (content: string) => void;
  isError: boolean;
  isPending: boolean;
  isUnavailable: boolean;
  onConfigureProvider: () => void;
  onRetry: () => void;
}) {
  if (!prompts.length && !isError && !isPending && !isUnavailable) return null;

  return (
    <section aria-label="对话问题提示" className="chat-follow-up-prompts">
      <div className="chat-follow-up-prompts__label">
        <Sparkles aria-hidden="true" className="size-3.5" />
        <span>接下来可以问</span>
      </div>
      <SuggestedPromptContent
        disabled={isPending}
        isError={isError}
        isPending={isPending}
        isUnavailable={isUnavailable}
        onConfigureProvider={onConfigureProvider}
        onRetry={onRetry}
        onSelect={onSelect}
        prompts={prompts}
      />
    </section>
  );
}

function ConversationContextBar({
  goalBound,
  graphTitle,
  learningNode,
  onClearLearningNode,
}: {
  goalBound: boolean;
  graphTitle?: string;
  learningNode?: LearningNodeContext;
  onClearLearningNode: () => void;
}) {
  if (!goalBound && !graphTitle && !learningNode) return null;

  return (
    <section aria-label="本轮对话上下文" className="chat-context-bar">
      <span className="chat-context-bar__label">上下文</span>
      {goalBound ? <span className="chat-context-bar__item">已绑定目标</span> : null}
      {graphTitle ? (
        <span className="chat-context-bar__item" title={graphTitle}>
          图谱 · {graphTitle}
        </span>
      ) : null}
      {learningNode ? (
        <span className="chat-context-bar__item chat-context-bar__item--node">
          节点 · {learningNode.nodeLabel ?? "已选择学习节点"}
          <button
            aria-label="移除当前学习节点上下文"
            onClick={onClearLearningNode}
            title="本轮后续消息不再绑定此节点"
            type="button"
          >
            <X aria-hidden="true" />
          </button>
        </span>
      ) : null}
    </section>
  );
}

function ConversationQuickActions({
  agentActive,
  agentDisabled,
  attachDisabled,
  goalActive,
  goalDisabled,
  graphActive,
  graphDisabled,
  imageActive,
  imageDisabled,
  onAttach,
  onGoal,
  onGraph,
  onImage,
  onPractice,
  onSearch,
  onAgent,
  practiceDisabled,
  searchActive,
  searchDisabled,
}: {
  agentActive: boolean;
  agentDisabled: boolean;
  attachDisabled: boolean;
  goalActive: boolean;
  goalDisabled: boolean;
  graphActive: boolean;
  graphDisabled: boolean;
  imageActive: boolean;
  imageDisabled: boolean;
  onAttach: () => void;
  onGoal: () => void;
  onGraph: () => void;
  onImage: () => void;
  onPractice: () => void;
  onSearch: () => void;
  onAgent: () => void;
  practiceDisabled: boolean;
  searchActive: boolean;
  searchDisabled: boolean;
}) {
  return (
    <section aria-label="对话工作台功能" className="chat-workbench-toolbar">
      <span className="chat-workbench-toolbar__label">本轮能力</span>
      <div className="chat-workbench-toolbar__actions">
        <button
          aria-label="添加资料到本轮对话"
          className="chat-workbench-toolbar__action"
          disabled={attachDisabled}
          onClick={onAttach}
          title="添加文件或图片，发送时按文件权限和解析状态处理"
          type="button"
        >
          <FilePlus2 aria-hidden="true" />
          资料
        </button>
        <button
          aria-pressed={goalActive}
          className="chat-workbench-toolbar__action"
          disabled={goalDisabled}
          onClick={onGoal}
          title={goalActive ? "退出目标设定" : "在当前对话中设定学习目标"}
          type="button"
        >
          <Target aria-hidden="true" />
          {goalActive ? "目标中" : "目标"}
        </button>
        <button
          aria-pressed={searchActive}
          className="chat-workbench-toolbar__action"
          disabled={searchDisabled}
          onClick={onSearch}
          title={
            searchActive
              ? "关闭本轮联网搜索"
              : searchDisabled
                ? "请先启用 SearchProvider，或确认模型托管联网能力"
                : "下一条消息使用已授权的联网搜索"
          }
          type="button"
        >
          <Search aria-hidden="true" />
          {searchActive ? "联网中" : "联网"}
        </button>
        <button
          aria-pressed={agentActive}
          className="chat-workbench-toolbar__action"
          disabled={agentDisabled}
          onClick={onAgent}
          title={agentActive ? "切回普通对话模式" : "允许本轮调用已授权工具"}
          type="button"
        >
          <Bot aria-hidden="true" />
          智能体
        </button>
        <button
          aria-pressed={graphActive}
          className="chat-workbench-toolbar__action"
          disabled={graphDisabled}
          onClick={onGraph}
          title={
            graphActive
              ? "取消图谱变更：下一条消息将不再生成增量提案"
              : "图谱变更：围绕当前节点细化/增补子节点（去重后生成需审核的变更提案）"
          }
          type="button"
        >
          <Network aria-hidden="true" />
          {graphActive ? "图谱变更" : "图谱"}
        </button>
        <button
          aria-pressed={imageActive}
          className="chat-workbench-toolbar__action"
          disabled={imageDisabled}
          onClick={onImage}
          title={imageActive ? "退出绘图模式" : "使用已配置的图片生成模型"}
          type="button"
        >
          <ImageIcon aria-hidden="true" />
          {imageActive ? "绘图中" : "绘图"}
        </button>
        <button
          className="chat-workbench-toolbar__action"
          disabled={practiceDisabled}
          onClick={onPractice}
          title="基于当前目标、节点和资料，在对话框中预填练习请求"
          type="button"
        >
          <Sparkles aria-hidden="true" />
          生成练习
        </button>
      </div>
    </section>
  );
}

export function ChatCanvasPage() {
  const { workspaceId = "", sessionId = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const goalMode = new URLSearchParams(location.search).get("mode") === "goal";
  const conversationResetKey = `${workspaceId}:${sessionId}`;
  const mentionMenuId = useId();
  const [localMessages, setLocalMessages] = useState<Message[]>([]);
  const [status, setStatus] = useState<ChatStatus>("ready");
  const [streamConnectionNotice, setStreamConnectionNotice] =
    useState<StreamConnectionNotice | null>(null);
  const [selectionMenu, setSelectionMenu] = useState<TextSelectionMenu | null>(null);
  const [longPaste, setLongPaste] = useState<string | null>(null);
  const [pendingFiles, setPendingFiles] = useState<FileRecord[]>([]);
  const [composerText, setComposerText] = useState("");
  const [graphAction, setGraphAction] = useState<GraphAction>("none");
  const initialComposerPrefs = useMemo(
    () =>
      sessionId && sessionId !== "new"
        ? getSessionComposerPrefs(sessionId)
        : defaultComposerPrefs(),
    // Only seed from storage on first mount of this session route.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [conversationResetKey],
  );
  const [selectedProviderId, setSelectedProviderId] = useState(
    initialComposerPrefs.providerId ?? "",
  );
  const [selectedModelId, setSelectedModelId] = useState(
    initialComposerPrefs.modelId ?? "",
  );
  const [selectedImageProviderId, setSelectedImageProviderId] = useState(
    initialComposerPrefs.imageProviderId ?? "",
  );
  const [selectedImageModelId, setSelectedImageModelId] = useState(
    initialComposerPrefs.imageModelId ?? "",
  );
  const [responseMode, setResponseMode] = useState<ResponseMode>(
    initialComposerPrefs.responseMode,
  );
  const [thinkingMode, setThinkingMode] = useState<ThinkingMode>(
    initialComposerPrefs.thinkingMode,
  );
  const [searchRoute, setSearchRoute] = useState<SearchRoute>(
    initialComposerPrefs.searchRoute,
  );
  const [generationMode, setGenerationMode] = useState<GenerationMode>(
    initialComposerPrefs.generationMode,
  );
  useEffect(() => {
    setStreamConnectionNotice(null);
  }, [conversationResetKey]);
  const [modelSearch, setModelSearch] = useState("");
  const [composerInstanceKey, setComposerInstanceKey] =
    useState(conversationResetKey);
  const resumeInFlightRef = useRef<string | null>(null);
  const [rejectProposalId, setRejectProposalId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [closeDialogOpen, setCloseDialogOpen] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [editingMessageContent, setEditingMessageContent] = useState("");
  const [dismissedMention, setDismissedMention] = useState("");
  const [mentionIndex, setMentionIndex] = useState(0);
  const [retryTarget, setRetryTarget] = useState<{
    messageId: string;
    sourceSessionId: string;
  } | null>(null);
  const [retryProviderId, setRetryProviderId] = useState("");
  const [retryModelId, setRetryModelId] = useState("");
  const [retryResponseMode, setRetryResponseMode] =
    useState<ResponseMode>("thinking");
  const [retryThinkingMode, setRetryThinkingMode] =
    useState<ThinkingMode>("medium");
  const [retryWebSearch, setRetryWebSearch] = useState(false);
  const [retryAllowedDomains, setRetryAllowedDomains] = useState("");
  const [sandboxAuthRequest, setSandboxAuthRequest] = useState<SandboxAuthRequest | null>(null);
  const initialLearningNode = useRef<LearningNodeContext | undefined>(
    readLearningNodeContext(),
  );
  const [learningNode, setLearningNode] = useState<
    LearningNodeContext | undefined
  >(initialLearningNode.current);
  const learningNodeRef = useRef<LearningNodeContext | undefined>(
    initialLearningNode.current,
  );
  const abortRef = useRef<AbortController | null>(null);
  const activeCancellationRef = useRef<Promise<void> | null>(null);
  const activeMessageId = useRef<string | null>(null);
  const activeStreamSessionId = useRef<string | null>(null);
  /** Always the session currently on screen — background streams must not mutate UI. */
  const viewingSessionIdRef = useRef(sessionId);
  viewingSessionIdRef.current = sessionId;
  const latestOperationId = useRef(0);
  const optimisticSessionId = useRef<string | null>(null);
  const retryExpectedVersionRef = useRef(0);
  const composerTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const openFileDialogRef = useRef<() => void>(() => undefined);
  const pendingHandled = useRef(false);
  const draftSessionCreationRef = useRef<{
    locationKey: string;
    promise: Promise<Session>;
  } | null>(null);
  const preserveDraftForSessionRef = useRef<string | null>(null);
  const speechRecognitionRef = useRef<BrowserSpeechRecognition | null>(null);
  const registerFileDialog = useCallback((openFileDialog: () => void) => {
    openFileDialogRef.current = openFileDialog;
  }, []);


  useEffect(() => {
    if (!selectionMenu) return;
    const dismiss = () => setSelectionMenu(null);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") dismiss();
    };
    window.addEventListener("resize", dismiss);
    window.addEventListener("scroll", dismiss, true);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("resize", dismiss);
      window.removeEventListener("scroll", dismiss, true);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [selectionMenu]);

  useEffect(() => {
    if (!goalMode) return;
    setGraphAction("none");
    setSearchRoute("disabled");
    setGenerationMode("text");
  }, [goalMode]);

  const history = useQuery({
    queryKey: ["messages", sessionId],
    queryFn: () => listSessionMessages(sessionId),
    enabled: sessionId !== "new",
    // Incomplete streams need a fresh read after refresh so we can resume.
    refetchOnMount: "always",
  });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: listSessions });

  // Restore per-session composer prefs whenever the active session changes.
  useEffect(() => {
    if (!sessionId || sessionId === "new") {
      const defaults = defaultComposerPrefs();
      setResponseMode(defaults.responseMode);
      setThinkingMode(defaults.thinkingMode);
      setSearchRoute(defaults.searchRoute);
      setGenerationMode(defaults.generationMode);
      return;
    }
    const stored = getSessionComposerPrefs(sessionId);
    const session = sessions.data?.find((item) => item.id === sessionId);
    const fromSnapshot = prefsFromModelSnapshot(
      session?.model_snapshot as Record<string, unknown> | undefined,
    );
    // Local storage wins; snapshot only fills gaps on first visit.
    const merged = {
      ...stored,
      ...(!stored.providerId && fromSnapshot.providerId
        ? { providerId: fromSnapshot.providerId }
        : {}),
      ...(!stored.modelId && fromSnapshot.modelId
        ? { modelId: fromSnapshot.modelId }
        : {}),
      // If prefs are still product defaults and snapshot has a different mode,
      // adopt the durable snapshot (e.g. first open after refresh).
      ...(isDefaultComposerPrefs(stored) &&
      fromSnapshot.responseMode &&
      fromSnapshot.responseMode !== stored.responseMode
        ? {
            responseMode: fromSnapshot.responseMode,
            thinkingMode: fromSnapshot.thinkingMode ?? stored.thinkingMode,
          }
        : {}),
    };
    setResponseMode(merged.responseMode);
    setThinkingMode(merged.thinkingMode);
    setSearchRoute(merged.searchRoute);
    setGenerationMode(merged.generationMode);
    setSelectedProviderId(merged.providerId ?? "");
    setSelectedModelId(merged.modelId ?? "");
    setSelectedImageProviderId(merged.imageProviderId ?? "");
    setSelectedImageModelId(merged.imageModelId ?? "");
  }, [sessionId, sessions.data]);

  // Clear the in-flight resume latch when switching sessions so a new session
  // can auto-resume independently. Do NOT abort background streams — concurrent
  // sessions keep generating until the user stops that session explicitly.
  useEffect(() => {
    resumeInFlightRef.current = null;
    if (sessionId && sessionId !== "new") {
      markSessionViewed(sessionId);
    }
  }, [sessionId]);

  // Persist composer mode changes against the active session id.
  useEffect(() => {
    if (!sessionId || sessionId === "new") return;
    setSessionComposerPrefs(sessionId, {
      responseMode,
      thinkingMode,
      searchRoute,
      generationMode,
      providerId: selectedProviderId || undefined,
      modelId: selectedModelId || undefined,
      imageProviderId: selectedImageProviderId || undefined,
      imageModelId: selectedImageModelId || undefined,
    });
  }, [
    generationMode,
    responseMode,
    searchRoute,
    selectedModelId,
    selectedProviderId,
    selectedImageModelId,
    selectedImageProviderId,
    sessionId,
    thinkingMode,
  ]);
  const settings = useQuery({ queryKey: ["settings"], queryFn: listSettings });
  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: listProviders,
  });
  const graphs = useQuery({ queryKey: ["graphs"], queryFn: listGraphs });
  // 空会话提示优先参考工作区记忆；无记忆时使用中文默认问题。
  const emptySessionMemories = useQuery({
    queryKey: ["memories", "empty-session-prompts"],
    queryFn: () => listMemories({ state: "active", zone: "hot" }),
    staleTime: 60_000,
  });
  const memoryFallbackPrompts = useMemo(
    () =>
      buildMemoryBackedEmptyPrompts(
        (emptySessionMemories.data ?? []).map((item) => ({
          id: item.id,
          title: item.title,
          content: item.content,
        })),
      ),
    [emptySessionMemories.data],
  );
  const modelProviders = useMemo(
    () =>
      (providers.data ?? []).filter(
        (provider) =>
          provider.enabled &&
          provider.remote_capability &&
          [
            "openai_responses",
            "openai_compatible_chat",
            "deepseek_chat",
            "anthropic_messages",
          ].includes(provider.provider_type),
      ),
    [providers.data],
  );
  const activeModelProvider = useMemo(
    () =>
      modelProviders.find((provider) => provider.id === selectedProviderId) ??
      modelProviders[0],
    [modelProviders, selectedProviderId],
  );
  const imageProviders = useMemo(
    () =>
      (providers.data ?? []).filter(
        (provider) =>
          provider.enabled &&
          provider.remote_capability &&
          (provider.provider_type === "openai_images" ||
            providerCapabilityString(provider, "provider_role") ===
              "image_generation") &&
          Boolean(
            providerCapabilityString(
              provider,
              "default_image_generation_model_id",
            ),
          ),
      ),
    [providers.data],
  );
  const activeImageProvider = useMemo(
    () =>
      imageProviders.find(
        (provider) => provider.id === selectedImageProviderId,
      ) ?? imageProviders[0],
    [imageProviders, selectedImageProviderId],
  );
  const asrAvailable = useMemo(
    () =>
      (providers.data ?? []).some((provider) => {
        if (!provider.enabled || !provider.remote_capability) return false;
        const role = providerCapabilityString(provider, "provider_role");
        const isTranscription =
          role === "transcription" ||
          provider.provider_type === "openai_compatible_transcription";
        if (!isTranscription) return false;
        return Boolean(
          providerCapabilityString(provider, "default_transcription_model_id"),
        );
      }),
    [providers.data],
  );
  const composerFileAccept = useMemo(() => {
    if (responseMode === "agentic") return undefined;
    return fastThinkingAcceptAttribute(asrAvailable);
  }, [asrAvailable, responseMode]);
  const imageProviderModelQueries = useQueries({
    queries: imageProviders.map((provider) => ({
      queryKey: ["provider-models", provider.id],
      queryFn: () => discoverProviderModels(provider.id),
      retry: false,
    })),
  });
  const activeImageProviderIndex = imageProviders.findIndex(
    (provider) => provider.id === activeImageProvider?.id,
  );
  const discoveredImageModels =
    activeImageProviderIndex >= 0
      ? imageProviderModelQueries[activeImageProviderIndex]
      : undefined;
  const imageModelOptions = useMemo(
    () =>
      providerModelOptions(
        activeImageProvider,
        discoveredImageModels?.data?.models,
        "default_image_generation_model_id",
      ),
    [activeImageProvider, discoveredImageModels?.data?.models],
  );
  const availableImageModelChoices = useMemo(
    () =>
      imageProviders.flatMap((provider, index) =>
        providerModelOptions(
          provider,
          imageProviderModelQueries[index]?.data?.models,
          "default_image_generation_model_id",
        ).map((model) => ({ provider, model })),
      ),
    [imageProviderModelQueries, imageProviders],
  );
  const filteredAvailableImageModelChoices = useMemo(
    () =>
      availableImageModelChoices.filter(({ provider, model }) =>
        fuzzyModelMatch(
          `${model.id} ${provider.display_name} ${modelProtocolLabel(provider.provider_type)}`,
          modelSearch,
        ),
      ),
    [availableImageModelChoices, modelSearch],
  );
  const selectedImageModel =
    imageModelOptions.find((model) => model.id === selectedImageModelId) ??
    imageModelOptions[0];
  const defaultImageModelId = providerCapabilityString(
    activeImageProvider,
    "default_image_generation_model_id",
  );
  const providerModelQueries = useQueries({
    queries: modelProviders.map((provider) => ({
      queryKey: ["provider-models", provider.id],
      queryFn: () => discoverProviderModels(provider.id),
      retry: false,
    })),
  });
  const activeModelProviderIndex = modelProviders.findIndex(
    (provider) => provider.id === activeModelProvider?.id,
  );
  const discoveredModels =
    activeModelProviderIndex >= 0
      ? providerModelQueries[activeModelProviderIndex]
      : undefined;
  const modelOptions = useMemo(
    () => providerModelOptions(activeModelProvider, discoveredModels?.data?.models),
    [activeModelProvider, discoveredModels?.data?.models],
  );
  const availableModelChoices = useMemo(
    () =>
      modelProviders.flatMap((provider, index) =>
        providerModelOptions(
          provider,
          providerModelQueries[index]?.data?.models,
        ).map((model) => ({ provider, model })),
      ),
    [modelProviders, providerModelQueries],
  );
  const filteredAvailableModelChoices = useMemo(
    () =>
      availableModelChoices.filter(({ provider, model }) =>
        fuzzyModelMatch(
          `${model.id} ${provider.display_name} ${modelProtocolLabel(provider.provider_type)}`,
          modelSearch,
        ),
      ),
    [availableModelChoices, modelSearch],
  );
  const selectedModel = modelOptions.find(
    (model) => model.id === selectedModelId,
  );
  const selectedModelSupportsImageInput =
    selectedModel
      ? selectedModel.capabilities?.supports_image_input === true
      : activeModelProvider?.capabilities.supports_image_input === true;
  const retryProvider = modelProviders.find(
    (provider) => provider.id === retryProviderId,
  );
  const retryDiscoveredModels = useQuery({
    queryKey: ["provider-models", retryProviderId],
    queryFn: () => discoverProviderModels(retryProviderId),
    enabled: Boolean(retryTarget && retryProviderId),
    retry: false,
  });
  const retryModelOptions = useMemo(
    () =>
      providerModelOptions(retryProvider, retryDiscoveredModels.data?.models),
    [retryDiscoveredModels.data?.models, retryProvider],
  );
  const retrySelectedModel = retryModelOptions.find(
    (model) => model.id === retryModelId,
  );
  const retrySupportsAgentMode = Boolean(
    retryProvider &&
      [
        "openai_responses",
        "openai_compatible_chat",
        "deepseek_chat",
        "anthropic_messages",
      ].includes(retryProvider.provider_type) &&
      retryProvider.capabilities.supports_agent_tools !== false &&
      retrySelectedModel?.remote,
  );
  const activeProviderSupportsStructuredAgent = Boolean(
    activeModelProvider &&
      [
        "openai_responses",
        "openai_compatible_chat",
        "deepseek_chat",
        "anthropic_messages",
      ].includes(activeModelProvider.provider_type) &&
      activeModelProvider.capabilities.supports_agent_tools !== false,
  );
  const activeProviderIsDeepSeek = Boolean(
    activeModelProvider && isDeepSeekProvider(activeModelProvider),
  );
  const hasAuthorizedAgentSearchProvider = Boolean(
    providers.data?.some(
      (provider) =>
        provider.enabled &&
        provider.remote_capability &&
        [
          "anysearch",
          "searxng",
          "tavily",
          "exa",
          "brave_search",
          "firecrawl_search",
        ].includes(provider.provider_type),
    ),
  );
  const thinkingModes = useMemo(
    () =>
      capabilityThinkingModes(
        selectedModel?.capabilities?.reasoning_efforts ??
          activeModelProvider?.capabilities.reasoning_efforts ??
          (activeProviderIsDeepSeek
            ? ["low", "medium", "high", "xhigh"]
            : undefined),
      ),
    [
      activeProviderIsDeepSeek,
      activeModelProvider?.capabilities.reasoning_efforts,
      selectedModel?.capabilities?.reasoning_efforts,
    ],
  );
  const retryThinkingModes = useMemo(
    () =>
      capabilityThinkingModes(
        retrySelectedModel?.capabilities?.reasoning_efforts ??
          retryProvider?.capabilities.reasoning_efforts ??
          (retryProvider && isDeepSeekProvider(retryProvider)
            ? ["low", "medium", "high", "xhigh"]
            : undefined),
      ),
    [
      retryProvider,
      retrySelectedModel?.capabilities?.reasoning_efforts,
    ],
  );
  useEffect(() => {
    setSelectedProviderId((current) =>
      modelProviders.some((provider) => provider.id === current)
        ? current
        : (modelProviders[0]?.id ?? ""),
    );
  }, [modelProviders]);
  useEffect(() => {
    setSelectedImageProviderId((current) =>
      imageProviders.some((provider) => provider.id === current)
        ? current
        : (imageProviders[0]?.id ?? ""),
    );
  }, [imageProviders]);
  useEffect(() => {
    setSelectedImageModelId((current) =>
      imageModelOptions.some((model) => model.id === current)
        ? current
        : (imageModelOptions.find((model) => model.id === defaultImageModelId)
            ?.id ??
          imageModelOptions[0]?.id ??
          ""),
    );
  }, [defaultImageModelId, imageModelOptions]);
  useEffect(() => {
    setSelectedModelId((current) =>
      modelOptions.some((model) => model.id === current)
        ? current
        : (modelOptions[0]?.id ?? ""),
    );
  }, [modelOptions]);
  useEffect(() => {
    if (!thinkingModes.length) return;
    setThinkingMode((current) =>
      thinkingModes.includes(current) ? current : thinkingModes[0],
    );
  }, [thinkingModes]);
  useEffect(() => {
    setRetryModelId((current) =>
      retryModelOptions.some((model) => model.id === current && model.remote)
        ? current
        : (retryModelOptions.find((model) => model.remote)?.id ?? ""),
    );
  }, [retryModelOptions]);
  useEffect(() => {
    if (retryTarget && retryDiscoveredModels.isPending) return;
    if (!retryThinkingModes.length) {
      setRetryResponseMode("fast");
      setRetryThinkingMode("off");
      return;
    }
    setRetryThinkingMode((current) =>
      retryThinkingModes.includes(current)
        ? current
        : retryThinkingModes.includes("medium")
          ? "medium"
          : retryThinkingModes[0],
    );
  }, [retryDiscoveredModels.isPending, retryTarget, retryThinkingModes]);
  const supportsThinkingMode = thinkingModes.length > 0;
  const supportsAgentMode =
    activeProviderSupportsStructuredAgent &&
    Boolean(selectedModel?.remote);
  const agentModeUnavailableLabel = !activeProviderSupportsStructuredAgent
    ? "（当前接口未声明结构化工具能力）"
    : "";
  // Response mode is an explicit user preference. Capability changes may make
  // the selected mode temporarily unavailable, but must never silently rewrite
  // 智能体 to 思考/极速; the disabled send path explains the unavailable model.
  useEffect(() => {
    if (providers.isPending || discoveredModels?.isPending) return;
    if (
      responseMode === "thinking" &&
      activeModelProvider &&
      !supportsThinkingMode
    ) {
      setResponseMode("fast");
    }
  }, [
    activeModelProvider,
    discoveredModels?.isPending,
    providers.isPending,
    responseMode,
    supportsThinkingMode,
  ]);
  // External SearchProvider is authorized only when enabled + remote_capable.
  // Drop a stale "auto/external/local" preference if the user disables search
  // (or the SearchProvider itself) so agentic send cannot request web_search
  // against an unavailable SearchProvider.
  useEffect(() => {
    if (providers.isPending || providers.isError) return;
    if (searchRoute === "disabled" || searchRoute === "model_native") return;
    if (!hasAuthorizedAgentSearchProvider) {
      setSearchRoute("disabled");
    }
  }, [
    hasAuthorizedAgentSearchProvider,
    providers.isError,
    providers.isPending,
    searchRoute,
  ]);
  const effectiveThinkingMode: ThinkingMode =
    responseMode === "fast" || !supportsThinkingMode ? "off" : thinkingMode;
  const responseModeLabel =
    responseMode === "fast" ? "极速" : responseMode === "agentic" ? "智能体" : "思考";
  const activeGenerationProvider =
    generationMode === "image" ? activeImageProvider : activeModelProvider;
  const activeGenerationModelId =
    generationMode === "image" ? selectedImageModel?.id ?? "" : selectedModel?.id ?? "";
  const messages = useMemo(() => {
    const persisted =
      optimisticSessionId.current === sessionId ? [] : history.data ?? [];
    const retryOverlays = new Map<string, Message>();
    const normalOverlays = new Map<string, Message>();
    const appended: Message[] = [];
    const persistedById = new Map(
      persisted.map((message) => [message.id, message]),
    );
    // Concurrent streams may still push into localMessages; only show the
    // active session so session B's tokens never paint on session C.
    localMessages.forEach((message) => {
      if (
        message.session_id &&
        message.session_id !== sessionId &&
        optimisticSessionId.current !== sessionId
      ) {
        return;
      }
      const target = message.provider_trace?.optimistic_target_message_id;
      if (typeof target === "string") retryOverlays.set(target, message);
      else {
        const persistedId = message.provider_trace
          ?.optimistic_persisted_message_id;
        const confirmed =
          persistedById.get(message.id) ??
          (typeof persistedId === "string"
            ? persistedById.get(persistedId)
            : undefined);
        if (!confirmed)
          appended.push(message);
        else if (
          !TERMINAL_MESSAGE_STATUSES.includes(
            confirmed.status as (typeof TERMINAL_MESSAGE_STATUSES)[number],
          )
        )
          normalOverlays.set(confirmed.id, message);
      }
    });
    return [
      ...persisted.map(
        (message) =>
          retryOverlays.get(message.id) ??
          normalOverlays.get(message.id) ??
          message,
      ),
      ...appended,
    ];
  }, [history.data, localMessages, sessionId]);

  useEffect(() => {
    // Promote sandbox_auth_required status parts into an authorization dialog.
    const parts = messages.flatMap((message) => message.parts ?? []);
    for (const part of parts) {
      if (part.type !== "sandbox_status" || part.data?.auth_required !== true) continue;
      const paths = Array.isArray(part.data?.paths)
        ? part.data.paths.filter((item): item is string => typeof item === "string")
        : [];
      if (!paths.length) continue;
      const chatSessionId =
        (typeof part.data?.chat_session_id === "string" && part.data.chat_session_id) ||
        sessionId;
      if (!chatSessionId || chatSessionId === "new") continue;
      setSandboxAuthRequest((current) => {
        if (current) return current;
        return {
          chatSessionId,
          paths,
          action: typeof part.data?.action === "string" ? part.data.action : "delete_path",
          message:
            typeof part.data?.message_zh === "string" ? part.data.message_zh : undefined,
          sandboxSessionId:
            typeof part.data?.sandbox_session_id === "string"
              ? part.data.sandbox_session_id
              : undefined,
        };
      });
      break;
    }
  }, [messages, sessionId]);

  // After refresh (or remount), detect in-flight assistant messages and resume
  // durable event replay so the user can keep receiving the answer.
  useEffect(() => {
    if (
      sessionId === "new" ||
      !history.isSuccess ||
      status === "streaming" ||
      status === "submitted"
    ) {
      return;
    }
    // A concurrent send for this session is still live — reattach UI controls.
    if (isSessionStreaming(sessionId)) {
      const handle = getSessionStream(sessionId);
      if (handle) {
        abortRef.current = handle.controller;
        activeStreamSessionId.current = sessionId;
        activeMessageId.current = handle.messageId;
      }
      setStatus("streaming");
      return;
    }
    const inFlight = [...(history.data ?? [])]
      .reverse()
      .find(
        (message) =>
          message.role === "assistant" &&
          IN_FLIGHT_MESSAGE_STATUSES.includes(
            message.status as (typeof IN_FLIGHT_MESSAGE_STATUSES)[number],
          ),
      );
    if (!inFlight) return;
    if (resumeInFlightRef.current === inFlight.id) return;
    resumeInFlightRef.current = inFlight.id;

    const controller = new AbortController();
    abortRef.current = controller;
    activeMessageId.current = inFlight.id;
    activeStreamSessionId.current = sessionId;
    registerSessionStream(sessionId, controller, inFlight.id);
    markSessionRunning(sessionId, true);
    setStatus("streaming");
    setStreamConnectionNotice({
      phase: "reconnecting",
      attempt: 0,
      maxAttempts: DEFAULT_STREAM_RECONNECTS,
      detail: "检测到仍在生成的回答，正在从持久事件续接",
    });

    void (async () => {
      latestOperationId.current += 1;
      const streamSessionId = sessionId;
      let completed = false;
      let terminalFailure = "";
      let lastEventId = "";
      const seenEventIds = new Set<string>();
      const isViewing = () => viewingSessionIdRef.current === streamSessionId;
      const frameQueue = createAnimationFrameQueue<Record<string, unknown>>(
        (updates) => {
          setLocalMessages((current) => {
            const existing = current.find((item) => item.id === inFlight.id);
            if (existing) {
              return current.map((message) =>
                message.id === inFlight.id
                  ? applyStreamUpdates(message, updates)
                  : message,
              );
            }
            return [
              ...current,
              applyStreamUpdates(
                {
                  ...inFlight,
                  status: "streaming",
                },
                updates,
              ),
            ];
          });
        },
      );
      const consume = (data: Record<string, unknown>) => {
        const eventId =
          typeof data.event_id === "string" ? data.event_id : "";
        if (eventId && seenEventIds.has(eventId)) return;
        if (eventId) {
          seenEventIds.add(eventId);
          lastEventId = eventId;
        }
        const type = streamEventType(data);
        if (type === "message.completed") completed = true;
        if (type === "message.failed")
          terminalFailure = streamEventFailure(data);
        if (type === "message.cancelled") terminalFailure = "生成已取消。";
        if ((type || isMessagePart(data.part)) && isViewing()) {
          setStatus("streaming");
          setStreamConnectionNotice(null);
        }
        expandStreamUpdate(data).forEach((update) => frameQueue.push(update));
      };
      try {
        let consecutiveFailures = 0;
        while (!controller.signal.aborted) {
          if (controller.signal.aborted) break;
          try {
            const replay = await listSessionMessageEvents(
              streamSessionId,
              inFlight.id,
              {
                afterEventId: lastEventId || undefined,
              },
            );
            replay.forEach((event) =>
              consume(event as Record<string, unknown>),
            );
            consecutiveFailures = 0;
          } catch (error) {
            consecutiveFailures += 1;
            if (isViewing()) {
              setStreamConnectionNotice({
                phase:
                  consecutiveFailures >= DEFAULT_STREAM_RECONNECTS
                    ? "failed"
                    : "reconnecting",
                attempt: consecutiveFailures,
                maxAttempts: DEFAULT_STREAM_RECONNECTS,
                detail: streamErrorDetail(error),
              });
            }
            if (consecutiveFailures >= DEFAULT_STREAM_RECONNECTS) {
              throw error;
            }
          }
          if (completed || terminalFailure) break;
          // Also re-check persisted status in case the generator finished
          // without more events (e.g. process restarted mid-stream).
          try {
            const latest = await listSessionMessages(streamSessionId);
            const refreshed = latest.find((item) => item.id === inFlight.id);
            if (
              refreshed &&
              TERMINAL_MESSAGE_STATUSES.includes(
                refreshed.status as (typeof TERMINAL_MESSAGE_STATUSES)[number],
              )
            ) {
              queryClient.setQueryData(["messages", streamSessionId], latest);
              completed = true;
              break;
            }
          } catch {
            // ignore and keep polling events
          }
          await new Promise((resolve) => window.setTimeout(resolve, 400));
        }
        await frameQueue.drain();
        if (terminalFailure) {
          markSessionRunning(streamSessionId, false);
          if (isViewing()) {
            setStatus("error");
            setStreamConnectionNotice({
              phase: "failed",
              attempt: 0,
              maxAttempts: DEFAULT_STREAM_RECONNECTS,
              detail: terminalFailure,
            });
          } else {
            markSessionGenerationFinished(streamSessionId, { viewing: false });
          }
          return;
        }
        if (completed) {
          await queryClient.invalidateQueries({
            queryKey: ["messages", streamSessionId],
          });
          void queryClient.invalidateQueries({ queryKey: ["sessions"] });
          if (isViewing()) {
            setLocalMessages((current) =>
              current.filter((message) => message.id !== inFlight.id),
            );
            setStatus("ready");
            setStreamConnectionNotice(null);
            markSessionGenerationFinished(streamSessionId, { viewing: true });
          } else {
            markSessionGenerationFinished(streamSessionId, { viewing: false });
          }
          return;
        }
      } catch (error) {
        markSessionRunning(streamSessionId, false);
        if (controller.signal.aborted) {
          if (isViewing()) setStatus("ready");
          return;
        }
        if (isViewing()) {
          setStatus("error");
          setStreamConnectionNotice({
            phase: "failed",
            attempt: DEFAULT_STREAM_RECONNECTS,
            maxAttempts: DEFAULT_STREAM_RECONNECTS,
            detail: streamErrorDetail(error),
          });
        }
      } finally {
        clearSessionStream(streamSessionId, controller);
        if (abortRef.current === controller) abortRef.current = null;
        if (activeMessageId.current === inFlight.id)
          activeMessageId.current = null;
        if (activeStreamSessionId.current === streamSessionId)
          activeStreamSessionId.current = null;
      }
    })();

    // Intentionally no abort on unmount/session switch — concurrent sessions
    // keep replaying until the user stops this session or it finishes.
    return undefined;
  }, [history.data, history.isSuccess, queryClient, sessionId, status]);


  useEffect(() => {
    if (!history.data?.length) return;
    const persistedById = new Map(
      history.data.map((message) => [message.id, message]),
    );
    setLocalMessages((current) => {
      const normalOptimistic = current.filter(
        (message) =>
          typeof message.provider_trace?.optimistic_persisted_message_id ===
          "string",
      );
      const allNormalConfirmed = normalOptimistic.every((message) => {
        const persistedId = message.provider_trace
          ?.optimistic_persisted_message_id as string;
        const persisted = persistedById.get(persistedId);
        return Boolean(
          persisted && TERMINAL_MESSAGE_STATUSES.includes(
            persisted.status as (typeof TERMINAL_MESSAGE_STATUSES)[number],
          ),
        );
      });
      const holdNewSessionOptimistic =
        optimisticSessionId.current === sessionId && !allNormalConfirmed;
      const next = current.filter((message) => {
        const retryTarget = message.provider_trace?.optimistic_target_message_id;
        if (typeof retryTarget === "string") {
          const persisted = persistedById.get(retryTarget);
          return !(
            persisted &&
            persisted.version >= message.version &&
            TERMINAL_MESSAGE_STATUSES.includes(
              persisted.status as (typeof TERMINAL_MESSAGE_STATUSES)[number],
            )
          );
        }
        const persistedId = message.provider_trace
          ?.optimistic_persisted_message_id;
        if (typeof persistedId !== "string" || holdNewSessionOptimistic)
          return true;
        const persisted = persistedById.get(persistedId);
        return !(
          persisted &&
          TERMINAL_MESSAGE_STATUSES.includes(
            persisted.status as (typeof TERMINAL_MESSAGE_STATUSES)[number],
          )
        );
      });
      if (
        optimisticSessionId.current === sessionId &&
        allNormalConfirmed &&
        normalOptimistic.length
      )
        optimisticSessionId.current = null;
      return next.length === current.length ? current : next;
    });
  }, [history.data, sessionId]);
  const currentSession = sessions.data?.find(
    (session) => session.id === sessionId,
  );
  const sessionIsClosed = currentSession?.status === "closed";
  const isEmptySession = messages.length === 0;
  const latestAssistantMessage = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      if (messages[index].role === "assistant") return messages[index];
    }
    return undefined;
  }, [messages]);
  const suggestionAnchor = isEmptySession
    ? null
    : latestAssistantMessage?.status === "completed"
      ? latestAssistantMessage
      : undefined;
  const completedMessageFingerprint = useMemo(
    () =>
      messages
        .filter((message) => message.status === "completed")
        .map(
          (message) =>
            `${message.id}:${message.version}:${message.status}`,
        )
        .join("|"),
    [messages],
  );
  const suggestedPromptsEnabled = areChatSuggestedPromptsEnabled(settings.data);
  const autoTitleModel = useMemo(
    () =>
      readChatFeatureModelSetting(
        settings.data,
        CHAT_AUTO_TITLE_MODEL_SETTING_KEY,
      ),
    [settings.data],
  );
  const suggestedPromptsModel = useMemo(
    () =>
      readChatFeatureModelSetting(
        settings.data,
        CHAT_SUGGESTED_PROMPTS_MODEL_SETTING_KEY,
      ),
    [settings.data],
  );
  const canPrepareSuggestedPrompts = Boolean(
    !goalMode &&
      settings.isSuccess &&
      suggestedPromptsEnabled &&
      sessions.isSuccess &&
      currentSession &&
      history.isSuccess &&
      sessionId !== "new" &&
      !sessionIsClosed &&
      status === "ready" &&
      suggestionAnchor !== undefined,
  );
  const suggestionAnchorVersions = useQuery({
    queryKey: [
      "message-versions",
      suggestionAnchor?.session_id ?? sessionId,
      suggestionAnchor?.id ?? null,
    ],
    queryFn: () =>
      listMessageVersions(suggestionAnchor!.session_id, suggestionAnchor!.id),
    enabled: Boolean(canPrepareSuggestedPrompts && suggestionAnchor),
    retry: false,
  });
  const suggestionAnchorVersion =
    suggestionAnchor === null
      ? null
      : suggestionAnchorVersions.data?.find(
          (version) =>
            version.version === suggestionAnchor?.version &&
            version.status === "completed",
        );
  const expectedSuggestionAnchorId = suggestionAnchor?.id ?? null;
  const expectedSuggestionAnchorVersionId = suggestionAnchorVersion?.id ?? null;
  const suggestionAnchorReady = Boolean(
    suggestionAnchor === null || suggestionAnchorVersion,
  );
  const suggestionAnchorError = Boolean(
    suggestionAnchor &&
      (suggestionAnchorVersions.isError ||
        (suggestionAnchorVersions.isSuccess && !suggestionAnchorVersion)),
  );
  const canReadSuggestedPrompts = Boolean(
    canPrepareSuggestedPrompts && suggestionAnchorReady,
  );
  const suggestionQueryContext = [
    sessionId,
    completedMessageFingerprint,
    suggestionAnchor?.id ?? null,
    suggestionAnchor?.version ?? 0,
    expectedSuggestionAnchorVersionId,
    activeModelProvider?.id ?? null,
    selectedModel?.id ?? null,
  ] as const;
  const persistedSuggestedPrompts = useQuery({
    queryKey: [
      "suggested-prompts",
      "persisted",
      ...suggestionQueryContext,
    ],
    queryFn: async ({ signal }) =>
      (await getSessionSuggestedPrompts(sessionId, { signal })) ?? null,
    enabled: canReadSuggestedPrompts,
    retry: false,
  });
  const persistedSuggestionBatch =
    persistedSuggestedPrompts.data?.session_id === sessionId &&
    persistedSuggestedPrompts.data.anchor_message_id ===
      expectedSuggestionAnchorId &&
    persistedSuggestedPrompts.data.anchor_message_version_id ===
      expectedSuggestionAnchorVersionId
      ? persistedSuggestedPrompts.data
      : undefined;
  const remoteSuggestionProviderAvailable = Boolean(
    activeModelProvider?.id && selectedModel?.id && selectedModel.remote,
  );
  const suggestionProviderPending = Boolean(
    providers.isPending ||
      (activeModelProvider &&
        (discoveredModels?.isPending ||
          (modelOptions.length > 0 && !selectedModel))),
  );
  const suggestionProviderError = Boolean(
    providers.isError ||
      (!remoteSuggestionProviderAvailable && discoveredModels?.isError),
  );
  const canGenerateSuggestedPrompts = Boolean(
    canReadSuggestedPrompts &&
      persistedSuggestedPrompts.isSuccess &&
      !persistedSuggestionBatch &&
      remoteSuggestionProviderAvailable,
  );
  const generatedSuggestedPrompts = useQuery({
    queryKey: [
      "suggested-prompts",
      "generated",
      ...suggestionQueryContext,
      suggestedPromptsModel.provider_id,
      suggestedPromptsModel.model_id,
    ],
    queryFn: ({ signal }) =>
      generateSessionSuggestedPrompts(
        sessionId,
        {
          anchor_message_id: suggestionAnchor?.id ?? null,
          anchor_message_version_id: expectedSuggestionAnchorVersionId,
          provider_id:
            suggestedPromptsModel.provider_id ?? activeModelProvider!.id,
          model_id: suggestedPromptsModel.model_id ?? selectedModel!.id,
          count: 2,
        },
        { signal },
      ),
    enabled: canGenerateSuggestedPrompts,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
  });
  const generatedSuggestionBatch =
    generatedSuggestedPrompts.data?.session_id === sessionId &&
    generatedSuggestedPrompts.data.anchor_message_id ===
      expectedSuggestionAnchorId &&
    generatedSuggestedPrompts.data.anchor_message_version_id ===
      expectedSuggestionAnchorVersionId
      ? generatedSuggestedPrompts.data
      : undefined;
  const promptSuggestions =
    (generatedSuggestionBatch ?? persistedSuggestionBatch)?.prompts ?? [];
  const emptySessionDisplayPrompts =
    promptSuggestions.length > 0
      ? promptSuggestions
      : memoryFallbackPrompts;
  const suggestionAnchorPending = Boolean(
    suggestionAnchor !== null &&
      (suggestionAnchorVersions.isPending || suggestionAnchorVersions.isFetching),
  );
  const persistedSuggestionPending = Boolean(
    canReadSuggestedPrompts &&
      (persistedSuggestedPrompts.isPending ||
        persistedSuggestedPrompts.isFetching ||
        (!persistedSuggestionBatch &&
          !persistedSuggestedPrompts.isError &&
          (suggestionProviderPending ||
            (canGenerateSuggestedPrompts &&
              (generatedSuggestedPrompts.isPending ||
                generatedSuggestedPrompts.isFetching))))),
  );
  const suggestedPromptsPending = Boolean(
    canPrepareSuggestedPrompts &&
      (suggestionAnchorPending || persistedSuggestionPending),
  );
  const suggestedPromptsError = shouldShowSuggestedPromptError({
    canPrepare: canPrepareSuggestedPrompts,
    canRead: canReadSuggestedPrompts,
    anchorError: suggestionAnchorError,
    persistedReadError: persistedSuggestedPrompts.isError,
    hasPersistedBatch: Boolean(persistedSuggestionBatch),
    providerError: suggestionProviderError,
    canGenerate: canGenerateSuggestedPrompts,
    generationError: generatedSuggestedPrompts.isError,
  });
  const suggestedPromptsUnavailable = Boolean(
    canReadSuggestedPrompts &&
      persistedSuggestedPrompts.isSuccess &&
      !persistedSuggestionBatch &&
      !suggestionProviderPending &&
      !suggestionProviderError &&
      !remoteSuggestionProviderAvailable,
  );
  const retrySuggestedPrompts = () => {
    if (suggestionAnchorError) {
      void suggestionAnchorVersions.refetch();
    } else if (persistedSuggestedPrompts.isError) {
      void persistedSuggestedPrompts.refetch();
    } else if (providers.isError) {
      void providers.refetch();
    } else if (
      !remoteSuggestionProviderAvailable &&
      discoveredModels?.isError
    ) {
      void discoveredModels?.refetch();
    } else {
      void generatedSuggestedPrompts.refetch();
    }
  };
  const showSuggestedPromptState =
    canPrepareSuggestedPrompts &&
    (suggestedPromptsPending ||
      suggestedPromptsError ||
      suggestedPromptsUnavailable ||
      promptSuggestions.length > 0);
  const graphTitle = currentSession?.graph_id
    ? graphs.data?.find((graph) => graph.id === currentSession.graph_id)?.title
    : undefined;
  const firstLocalUserMessageId = useMemo(
    () =>
      messages.find(
        (message) =>
          message.session_id === sessionId && message.role === "user",
      )?.id,
    [messages, sessionId],
  );
  const rememberCreatedSession = useCallback(
    (session: Session) => {
      queryClient.setQueryData<Session[]>(["sessions"], (current) => [
        session,
        ...(current ?? []).filter((item) => item.id !== session.id),
      ]);
      window.dispatchEvent(
        new CustomEvent("learngraph:session-created", { detail: { session } }),
      );
    },
    [queryClient],
  );
  useEffect(() => {
    if (sessionId !== "new" || goalMode) return;
    let cancelled = false;

    // Prefer reusing the single unused empty draft so /chat/new never multiplies.
    const existingDraftId = getDraftSessionId();
    if (existingDraftId) {
      const cached = (
        queryClient.getQueryData<Session[]>(["sessions"]) ?? []
      ).find((session) => session.id === existingDraftId);
      if (cached) {
        preserveDraftForSessionRef.current = existingDraftId;
        navigate(`/w/${workspaceId}/chat/${existingDraftId}`, {
          replace: true,
        });
        return;
      }
      const reusePromise = listSessions()
        .then((sessions) => {
          queryClient.setQueryData(["sessions"], sessions);
          return sessions.find((session) => session.id === existingDraftId);
        })
        .then((session) => {
          if (cancelled) return;
          if (session) {
            preserveDraftForSessionRef.current = session.id;
            navigate(`/w/${workspaceId}/chat/${session.id}`, {
              replace: true,
            });
            return;
          }
          clearDraftSessionId(existingDraftId);
          return createSession({ memory_enabled: true, title: "新会话" }).then(
            (created) => {
              if (cancelled) return;
              rememberCreatedSession(created);
              setDraftSessionId(created.id);
              preserveDraftForSessionRef.current = created.id;
              navigate(`/w/${workspaceId}/chat/${created.id}`, {
                replace: true,
              });
            },
          );
        });
      void reusePromise.catch((error: unknown) => {
        if (!cancelled) {
          toast.error(
            error instanceof Error ? error.message : "无法创建新会话",
          );
        }
      });
      return () => {
        cancelled = true;
      };
    }

    let attempt = draftSessionCreationRef.current;
    if (!attempt || attempt.locationKey !== location.key) {
      attempt = {
        locationKey: location.key,
        promise: createSession({ memory_enabled: true, title: "新会话" }),
      };
      draftSessionCreationRef.current = attempt;
    }
    const currentAttempt = attempt;
    void currentAttempt.promise
      .then((session) => {
        if (cancelled) return;
        rememberCreatedSession(session);
        setDraftSessionId(session.id);
        preserveDraftForSessionRef.current = session.id;
        navigate(`/w/${workspaceId}/chat/${session.id}`, { replace: true });
      })
      .catch((error: unknown) => {
        if (draftSessionCreationRef.current === currentAttempt) {
          draftSessionCreationRef.current = null;
        }
        if (!cancelled) {
          toast.error(
            error instanceof Error ? error.message : "无法创建新会话",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [
    goalMode,
    location.key,
    navigate,
    queryClient,
    rememberCreatedSession,
    sessionId,
    workspaceId,
  ]);
  const completeGoalSetup = useCallback(
    async ({ goal, graph }: { goal: Goal; graph: Graph }) => {
      try {
        const canReuseCurrentSession = Boolean(
          sessionId !== "new" &&
            currentSession?.status === "active" &&
            !currentSession.goal_id &&
            !currentSession.graph_id,
        );
        const learningSession = canReuseCurrentSession
          ? await updateSession(sessionId, {
              goal_id: goal.id,
              graph_id: graph.id,
              title: goal.title,
            })
          : await createSession({
              goal_id: goal.id,
              graph_id: graph.id,
              memory_enabled: true,
              title: goal.title,
            });
        if (canReuseCurrentSession) {
          queryClient.setQueryData<Session[]>(["sessions"], (current) =>
            current
              ? current.map((item) =>
                  item.id === learningSession.id ? learningSession : item,
                )
              : [learningSession],
          );
        } else {
          rememberCreatedSession(learningSession);
        }
        setLocalMessages([]);
        setPendingFiles([]);
        setComposerText("");
        navigate(`/w/${workspaceId}/chat/${learningSession.id}`, {
          replace: true,
        });
        toast.success("目标图谱已发布，已进入学习会话。");
      } catch (error) {
        toast.error(
          `图谱已发布，但学习会话创建失败：${
            error instanceof Error ? error.message : "未知错误"
          }`,
        );
        navigate(`/w/${workspaceId}/graphs/${graph.id}`, { replace: true });
      }
    },
    [
      currentSession,
      navigate,
      queryClient,
      rememberCreatedSession,
      sessionId,
      workspaceId,
    ],
  );
  const goalFlow = useGoalSetupFlow({
    enabled: goalMode,
    onPublished: completeGoalSetup,
    scopeKey: conversationResetKey,
  });
  useEffect(() => {
    if (!goalMode) return;
    window.dispatchEvent(
      new CustomEvent("learngraph:goal-graph-preview", {
        detail: { composerText },
      }),
    );
  }, [composerText, goalMode]);
  const closeSessionMutation = useMutation({
    mutationFn: () => {
      if (!currentSession || sessionId === "new") {
        throw new Error("请先创建学习会话后再结束并复盘。");
      }
      return closeSession(currentSession.id);
    },
    onSuccess: async (closedSession) => {
      queryClient.setQueryData<Session[]>(["sessions"], (current) =>
        current
          ? current.map((item) =>
              item.id === closedSession.id ? closedSession : item,
            )
          : [closedSession],
      );
      setCloseDialogOpen(false);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["sessions"] }),
        queryClient.invalidateQueries({
          queryKey: ["messages", closedSession.id],
        }),
        queryClient.invalidateQueries({ queryKey: ["evidence"] }),
        queryClient.invalidateQueries({ queryKey: ["mastery"] }),
        queryClient.invalidateQueries({ queryKey: ["mastery-review-jobs"] }),
        queryClient.invalidateQueries({
          queryKey: ["mastery-session-states"],
        }),
        queryClient.invalidateQueries({ queryKey: ["mastery-schedules"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard", workspaceId] }),
        closedSession.goal_id
          ? queryClient.invalidateQueries({
              queryKey: ["roadmap", closedSession.goal_id],
            })
          : Promise.resolve(),
      ]);
      toast.success("学习会话已结束，复盘已触发。");
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "结束学习失败，请稍后重试。",
      ),
  });
  useEffect(() => {
    window.dispatchEvent(
      new CustomEvent("learngraph:chat-header", {
        detail: {
          canClose:
            sessionId !== "new" &&
            Boolean(currentSession) &&
            !sessionIsClosed &&
            status !== "streaming" &&
            status !== "submitted" &&
            !closeSessionMutation.isPending,
          graphTitle,
          modelConnected: Boolean(activeGenerationProvider),
          sessionClosed: sessionIsClosed,
          sessionTitle: currentSession?.title ?? "新会话",
        },
      }),
    );
  }, [
    activeGenerationProvider,
    closeSessionMutation.isPending,
    currentSession,
    graphTitle,
    sessionId,
    sessionIsClosed,
    status,
  ]);
  useEffect(() => {
    const requestClose = () => {
      if (
        sessionId !== "new" &&
        currentSession &&
        !sessionIsClosed &&
        status !== "streaming" &&
        status !== "submitted" &&
        !closeSessionMutation.isPending
      ) {
        setCloseDialogOpen(true);
      }
    };
    window.addEventListener("learngraph:close-session-requested", requestClose);
    return () =>
      window.removeEventListener(
        "learngraph:close-session-requested",
        requestClose,
      );
  }, [
    closeSessionMutation.isPending,
    currentSession,
    sessionId,
    sessionIsClosed,
    status,
  ]);
  const userVersionNavigation = useCallback(
    (message: Message): MessageVersionNavigation | undefined => {
      const branchParentSessionId = currentSession?.parent_session_id;
      const branchSourceMessageId = currentSession?.source_message_id;
      const isEditedBranchRoot = Boolean(
        branchParentSessionId &&
        branchSourceMessageId &&
        message.session_id === sessionId &&
        message.id === firstLocalUserMessageId,
      );
      const sourceSessionId = isEditedBranchRoot
        ? branchParentSessionId!
        : message.session_id === sessionId
          ? sessionId
          : undefined;
      const sourceMessageId = isEditedBranchRoot
        ? branchSourceMessageId!
        : message.session_id === sessionId
          ? message.id
          : undefined;
      if (!sourceSessionId || !sourceMessageId) return undefined;
      const branchSessionIds = (sessions.data ?? [])
        .filter(
          (session) =>
            session.parent_session_id === sourceSessionId &&
            session.source_message_id === sourceMessageId,
        )
        .sort((left, right) => left.created_at.localeCompare(right.created_at))
        .map((session) => session.id);
      const sessionIds = [sourceSessionId, ...branchSessionIds];
      const currentIndex = sessionIds.indexOf(sessionId);
      if (sessionIds.length <= 1 || currentIndex < 0) return undefined;
      return {
        currentIndex,
        total: sessionIds.length,
        onPrevious: () =>
          navigate(`/w/${workspaceId}/chat/${sessionIds[currentIndex - 1]}`),
        onNext: () =>
          navigate(`/w/${workspaceId}/chat/${sessionIds[currentIndex + 1]}`),
      };
    },
    [
      currentSession?.parent_session_id,
      currentSession?.source_message_id,
      firstLocalUserMessageId,
      navigate,
      sessionId,
      sessions.data,
      workspaceId,
    ],
  );

  const ensureAgentSandboxReady = useCallback(async (): Promise<boolean> => {
    try {
      const readiness = await getAgentSandboxReadiness();
      if (readiness.available) return true;
      toast.error("智能体沙箱不可用", {
        description: [readiness.message, readiness.remediation_steps[0]]
          .filter(Boolean)
          .join(" "),
      });
      return false;
    } catch (error) {
      toast.error("无法检查智能体沙箱状态", {
        description:
          error instanceof Error ? error.message : "请确认后端服务可用后重试。",
      });
      return false;
    }
  }, []);

  const send = useCallback(
    async (
      contentValue: string,
      options: {
        fileIds?: string[];
        graphAction?: GraphAction;
        generationMode?: GenerationMode;
        sandboxPreflighted?: boolean;
        selectionContext?: MessageSelectionContext | null;
      } = {},
    ) => {
      const content = contentValue.trim();
      if (!content) return;
      // Concurrent sessions: only block while THIS session is generating.
      if (isSessionStreaming(sessionId)) return;
      if (
        (status === "streaming" || status === "submitted") &&
        activeStreamSessionId.current === sessionId
      )
        return;
      if (closeSessionMutation.isPending) {
        toast.error("正在结束学习会话，请稍候。");
        return;
      }
      if (sessionIsClosed) {
        toast.error("该学习会话已结束。请创建分支或新会话继续学习。");
        return;
      }
      const requestedGenerationMode = options.generationMode ?? generationMode;
      if (
        requestedGenerationMode === "text" &&
        responseMode === "agentic" &&
        !options.sandboxPreflighted
      ) {
        if (!(await ensureAgentSandboxReady())) return;
      }
      const requestProvider =
        requestedGenerationMode === "image"
          ? activeImageProvider
          : activeModelProvider;
      const requestModelId =
        requestedGenerationMode === "image"
          ? selectedImageModel?.id ?? ""
          : selectedModel?.id ?? "";
      if (!requestProvider || !requestModelId) {
        toast.error(
          requestedGenerationMode === "image"
            ? "当前工作区未启用带默认模型的真实绘图 Provider。"
            : "当前工作区未启用真实模型 Provider，请先到设置中完成配置。",
        );
        return;
      }
      if (requestedGenerationMode === "text" && !selectedModel?.remote) {
        toast.error("当前选择的模型没有可验证的远程能力。");
        return;
      }
      const requestedGraphAction =
        requestedGenerationMode === "image"
          ? "none"
          : options.graphAction ?? graphAction;
      if (
        requestedGraphAction === "propose_create" &&
        !currentSession?.goal_id
      ) {
        toast.error("当前会话尚未绑定已确认的学习目标，不能生成图谱提案。");
        return;
      }
      if (
        requestedGraphAction === "propose_update" &&
        !currentSession?.graph_id
      ) {
        toast.error("当前会话尚未绑定图谱，不能生成增量提案。");
        return;
      }
      const stamp = Date.now();
      const operationId = latestOperationId.current + 1;
      latestOperationId.current = operationId;
      const user: Message = {
        id: `temp-user-${stamp}`,
        workspace_id: workspaceId,
        session_id: sessionId,
        parent_message_id: null,
        role: "user",
        version: 1,
        status: "completed",
        content,
        parts: [
          {
            id: `temp-user-part-${stamp}`,
            type: "text",
            status: "completed",
            content,
          },
        ],
        provider_trace: {},
        created_at: new Date().toISOString(),
      };
      const assistant: Message = {
        id: `temp-assistant-${stamp}`,
        workspace_id: workspaceId,
        session_id: sessionId,
        parent_message_id: user.id,
        role: "assistant",
        version: 1,
        status: "streaming",
        content: "",
        parts:
          requestedGenerationMode === "image"
            ? [
                {
                  id: `temp-image-${stamp}`,
                  type: "image",
                  status: "pending",
                  data: { optimistic: true, title: "正在生成图片" },
                },
              ]
            : [
                {
                  id: `temp-acknowledgement-${stamp}`,
                  type: "acknowledgement",
                  status: "pending",
                  content: "",
                },
              ],
        provider_trace: {},
        created_at: new Date().toISOString(),
      };
      setLocalMessages((current) => [...current, user, assistant]);
      setSelectionMenu(null);
      setStreamConnectionNotice(null);
      setStatus("submitted");
      let targetSessionId = sessionId;
      let targetSession = currentSession;
      if (sessionId === "new") {
        try {
          if (goalMode) {
            targetSession = await createSession({
              memory_enabled: true,
              title: "目标调研",
            });
          } else {
            let draftAttempt = draftSessionCreationRef.current;
            if (!draftAttempt || draftAttempt.locationKey !== location.key) {
              draftAttempt = {
                locationKey: location.key,
                promise: createSession({
                  memory_enabled: true,
                  title: "新会话",
                }),
              };
              draftSessionCreationRef.current = draftAttempt;
            }
            targetSession = await draftAttempt.promise;
          }
          targetSessionId = targetSession.id;
          optimisticSessionId.current = targetSessionId;
          setLocalMessages((current) =>
            current.map((message) =>
              message.id === user.id || message.id === assistant.id
                ? { ...message, session_id: targetSessionId }
                : message,
            ),
          );
          rememberCreatedSession(targetSession);
          navigate(
            `/w/${workspaceId}/chat/${targetSessionId}${goalMode ? "?mode=goal" : ""}`,
            { replace: true },
          );
        } catch (error) {
          setStatus("error");
          setLocalMessages((current) =>
            current.map((message) =>
              message.id === assistant.id
                ? {
                    ...message,
                    status: "failed",
                    parts: [
                      ...message.parts.map((part) => ({
                        ...part,
                        status: "failed" as const,
                      })),
                      {
                        id: `error-${stamp}`,
                        type: "error",
                        status: "failed",
                        content:
                          error instanceof Error
                            ? error.message
                            : "创建会话失败",
                      },
                    ],
                  }
                : message,
            ),
          );
          toast.error(error instanceof Error ? error.message : "创建会话失败");
          return;
        }
      }
      // Goal mode keeps its explicit "目标调研" workflow label.
      const shouldAutoTitle = Boolean(
        targetSession &&
          (targetSession.title === "新会话" ||
            targetSession.title === "新学习会话") &&
          (sessionId === "new" ||
            (history.isSuccess &&
              !messages.some(
                (message) =>
                  message.session_id === targetSessionId &&
                  message.role === "user",
              ))),
      );
      const activeLearningNode =
        requestedGenerationMode === "text" ? learningNodeRef.current : undefined;
      // First user message graduates the empty draft into a normal sidebar session.
      if (
        isDefaultDraftTitle(targetSession?.title) &&
        getDraftSessionId() === targetSessionId
      ) {
        clearDraftSessionId(targetSessionId);
      }
      window.dispatchEvent(
        new CustomEvent("learngraph:session-started", {
          detail: { sessionId: targetSessionId },
        }),
      );
      markSessionTouched(targetSessionId);
      const controller = new AbortController();
      abortRef.current = controller;
      activeStreamSessionId.current = targetSessionId;
      registerSessionStream(targetSessionId, controller, null);
      markSessionRunning(targetSessionId, true);
      const isViewingStream = () =>
        viewingSessionIdRef.current === targetSessionId;
      const selectionContext = options.selectionContext
        ? {
            ...options.selectionContext,
            selected_text: options.selectionContext.selected_text.slice(0, 500),
            prefix: options.selectionContext.prefix.slice(-500),
            suffix: options.selectionContext.suffix.slice(0, 500),
          }
        : undefined;
      const request: MessageCreateRequest = {
        content,
        generation_mode: requestedGenerationMode,
        file_ids:
          requestedGenerationMode === "image" ? [] : options.fileIds ?? [],
        node_ids:
          requestedGenerationMode === "text"
            ? activeLearningNode?.nodeIds?.slice(0, 8) ??
              (activeLearningNode?.nodeId ? [activeLearningNode.nodeId] : [])
            : [],
        provider_id: requestProvider.id,
        model_id: requestModelId,
        thinking_mode:
          requestedGenerationMode === "image" ? "off" : effectiveThinkingMode,
        agent_mode:
          requestedGenerationMode === "text" && responseMode === "agentic",
        goal_mode:
          requestedGenerationMode === "text" &&
          responseMode === "agentic" &&
          goalMode,
        // Agent mode no longer forces web_search just because a SearchProvider
        // exists. Network search follows the explicit searchRoute (and the
        // composer "联网" toggle), while agent tools still run without search.
        search_route:
          requestedGenerationMode === "image"
            ? "disabled"
            : responseMode === "agentic" &&
                !hasAuthorizedAgentSearchProvider &&
                searchRoute !== "model_native"
              ? "disabled"
              : searchRoute === "auto" ||
                  searchRoute === "external" ||
                  searchRoute === "local"
                ? hasAuthorizedAgentSearchProvider
                  ? searchRoute
                  : "disabled"
                : searchRoute,
        web_search:
          requestedGenerationMode === "text" &&
          searchRoute !== "disabled" &&
          (searchRoute === "model_native" || hasAuthorizedAgentSearchProvider),
        graph_action: requestedGraphAction,
        graph_id:
          requestedGraphAction === "propose_update"
            ? targetSession?.graph_id
            : undefined,
        selection_context:
          requestedGenerationMode === "text" ? selectionContext : undefined,
      };
      const idempotencyKey = `chat-${window.crypto.randomUUID()}`;
      const seenEventIds = new Set<string>();
      let lastEventId: string | undefined;
      let completed = false;
      let persistedAssistantId = "";
      let persistedUserId = "";
      let autoTitleRequested = false;
      let autoTitlePromise: Promise<void> | null = null;
      const markPersistedId = (localId: string, persistedId: string) => {
        setLocalMessages((current) =>
          current.map((message) =>
            message.id === localId
              ? {
                  ...message,
                  provider_trace: {
                    ...message.provider_trace,
                    optimistic_persisted_message_id: persistedId,
                  },
                }
              : message,
          ),
        );
      };
      const reconcilePersisted = async (statuses: readonly string[]) => {
        if (!persistedAssistantId)
          throw new Error("服务端未返回持久助手消息标识。");
        const persisted = await confirmedSessionMessages(
          targetSessionId,
          persistedAssistantId,
          statuses,
        );
        queryClient.setQueryData(["messages", targetSessionId], persisted);
        if (isViewingStream()) {
          optimisticSessionId.current = null;
        }
        setLocalMessages((current) =>
          current.filter(
            (message) => message.id !== user.id && message.id !== assistant.id,
          ),
        );
      };
      const retryPersistedReconciliation = () => {
        void (async () => {
          for (const delay of [250, 1_000, 3_000]) {
            await new Promise((resolve) => window.setTimeout(resolve, delay));
            try {
              await reconcilePersisted(TERMINAL_MESSAGE_STATUSES);
              if (isViewingStream()) setStatus("ready");
              return;
            } catch {
              // A later retry or normal query refresh can still reconcile it.
            }
          }
          void queryClient.invalidateQueries({
            queryKey: ["messages", targetSessionId],
          });
        })();
      };
      // Always apply stream tokens into localMessages so returning to this
      // session mid-generation still shows the partial answer.
      const frameQueue = createAnimationFrameQueue<Record<string, unknown>>(
        (updates) =>
          setLocalMessages((current) =>
            current.map((message) =>
              message.id === assistant.id
                ? applyStreamUpdates(message, updates)
                : message,
            ),
          ),
      );
      try {
        for (
          let reconnectAttempt = 0;
          reconnectAttempt <= DEFAULT_STREAM_RECONNECTS;
          reconnectAttempt += 1
        ) {
          let transientError: unknown;
          let terminalFailure = "";
          try {
            for await (const event of streamSessionMessage(targetSessionId, request, {
              signal: controller.signal,
              seenEventIds,
              headers: {
                "Idempotency-Key": idempotencyKey,
                ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
              },
            })) {
              if (event.id) lastEventId = event.id;
              if (isViewingStream()) {
                setStatus("streaming");
                setStreamConnectionNotice(null);
              }
              const data = streamData(event.data);
              const type = streamEventType(data);
              if (
                typeof data.message_id === "string" &&
                !persistedAssistantId
              ) {
                persistedAssistantId = data.message_id;
                if (isViewingStream()) {
                  activeMessageId.current = data.message_id;
                }
                setSessionStreamMessageId(targetSessionId, data.message_id);
                markPersistedId(assistant.id, persistedAssistantId);
              }
              const eventPayload =
                typeof data.payload === "object" && data.payload !== null
                  ? (data.payload as Record<string, unknown>)
                  : {};
              if (
                type === "message.accepted" &&
                typeof eventPayload.user_message_id === "string" &&
                !persistedUserId
              ) {
                persistedUserId = eventPayload.user_message_id;
                markPersistedId(user.id, persistedUserId);
              }
              if (
                type === "message.accepted" &&
                shouldAutoTitle &&
                requestedGenerationMode === "text" &&
                !autoTitleRequested
              ) {
                autoTitleRequested = true;
                const sourceMessageId = eventPayload.user_message_id;
                if (typeof sourceMessageId !== "string" || !sourceMessageId) {
                  toast.error(
                    "会话自动命名失败，服务端未返回首条消息标识。",
                  );
                } else {
                  autoTitlePromise = autoTitleSession(targetSessionId, {
                    source_message_id: sourceMessageId,
                    expected_title: targetSession!.title,
                    provider_id:
                      autoTitleModel.provider_id ?? requestProvider.id,
                    model_id: autoTitleModel.model_id ?? requestModelId,
                  })
                    .then(async (updatedSession) => {
                      queryClient.setQueryData<Session[]>(
                        ["sessions"],
                        (current) => {
                          if (!current) return [updatedSession];
                          if (
                            !current.some(
                              (item) => item.id === updatedSession.id,
                            )
                          ) {
                            return [updatedSession, ...current];
                          }
                          return current.map((item) =>
                            item.id === updatedSession.id
                              ? { ...item, title: updatedSession.title }
                              : item,
                          );
                        },
                      );
                      await queryClient.invalidateQueries({
                        queryKey: ["sessions"],
                      });
                    })
                    .catch(async (error: unknown) => {
                      if (
                        error instanceof ApiError &&
                        error.code === "session_title_changed"
                      ) {
                        await queryClient.invalidateQueries({
                          queryKey: ["sessions"],
                        });
                        return;
                      }
                      toast.error(
                        "会话自动命名失败，已保留当前标题。",
                        {
                          description:
                            error instanceof Error
                              ? error.message
                              : "自动命名服务暂时不可用。",
                        },
                      );
                    });
                }
              }
              if (type === "message.completed") completed = true;
              if (type === "message.failed") {
                const errorPayload =
                  typeof eventPayload.error === "object" &&
                  eventPayload.error !== null
                    ? (eventPayload.error as Record<string, unknown>)
                    : typeof data.error === "object" && data.error !== null
                      ? (data.error as Record<string, unknown>)
                      : null;
                const errorCode =
                  typeof errorPayload?.code === "string"
                    ? errorPayload.code
                    : null;
                const errorMessage =
                  typeof errorPayload?.message === "string"
                    ? errorPayload.message
                    : null;
                if (errorCode === "agent_invocation_limit_reached") {
                  terminalFailure =
                    errorMessage ??
                    "智能体工具调用轮次已达上限，请缩小问题范围或重试。";
                } else if (errorMessage) {
                  terminalFailure = `模型流在服务端失败：${errorMessage}`;
                } else {
                  terminalFailure =
                    "模型流在服务端失败，已保留失败状态与事件记录。";
                }
              }
              if (type === "message.cancelled")
                terminalFailure = "生成已取消。";
              expandStreamUpdate(data).forEach((update) =>
                frameQueue.push(update),
              );
            }
          } catch (error) {
            if (controller.signal.aborted) throw error;
            transientError = error;
          }

          if (completed) break;
          if (terminalFailure) {
            await frameQueue.drain();
            throw new Error(terminalFailure);
          }
          if (reconnectAttempt === DEFAULT_STREAM_RECONNECTS)
            throw transientError instanceof Error
              ? transientError
              : new Error("消息流意外中断，五次恢复均未完成。");
          if (isViewingStream()) {
            setStreamConnectionNotice({
              phase: "reconnecting",
              attempt: reconnectAttempt + 1,
              maxAttempts: DEFAULT_STREAM_RECONNECTS,
              detail: streamErrorDetail(transientError),
            });
          }
          await waitForStreamReconnect(reconnectAttempt, controller.signal);
        }
        await frameQueue.drain();
        if (!completed) throw new Error("消息流结束但没有收到完成事件。");
        if (autoTitlePromise) void autoTitlePromise;
        if (isViewingStream()) setGraphAction("none");
        try {
          await reconcilePersisted(["completed"]);
        } catch {
          if (isViewingStream()) {
            setStatus("ready");
            toast.message("回答已完成，消息记录正在后台同步。");
          }
          markSessionGenerationFinished(targetSessionId, {
            viewing: isViewingStream(),
          });
          retryPersistedReconciliation();
          return;
        }
        if (activeLearningNode?.nodeId) {
          await queryClient.invalidateQueries({
            queryKey: [
              "node-questions",
              activeLearningNode.graphId,
              activeLearningNode.nodeId,
            ],
          });
          // Mastery stars / evidence may have moved; refresh rail graph cards.
          await queryClient.invalidateQueries({
            queryKey: ["graph", activeLearningNode.graphId],
          });
        }
        markSessionGenerationFinished(targetSessionId, {
          viewing: isViewingStream(),
        });
        if (isViewingStream()) setStatus("ready");
        if (isViewingStream()) setStreamConnectionNotice(null);
        void queryClient.invalidateQueries({ queryKey: ["sessions"] });
      } catch (error) {
        if (controller.signal.aborted) {
          const cancellation = activeCancellationRef.current;
          if (cancellation) await cancellation;
          frameQueue.clear();
          if (isViewingStream()) {
            setLocalMessages((current) =>
              current.map((message) =>
                message.id === assistant.id
                  ? {
                      ...message,
                      status: "cancelled",
                      parts: message.parts.map((part) => ({
                        ...part,
                        status: "failed" as const,
                      })),
                    }
                  : message,
              ),
            );
          }
          try {
            await reconcilePersisted(["cancelled"]);
          } catch {
            retryPersistedReconciliation();
          }
          markSessionRunning(targetSessionId, false);
          if (isViewingStream()) setStatus("ready");
        }
        else {
          await frameQueue.drain();
          if (persistedAssistantId) {
            try {
              await reconcilePersisted(TERMINAL_MESSAGE_STATUSES);
              markSessionGenerationFinished(targetSessionId, {
                viewing: isViewingStream(),
              });
              if (isViewingStream()) {
                setStatus("ready");
                setStreamConnectionNotice({
                  phase: "failed",
                  attempt: DEFAULT_STREAM_RECONNECTS,
                  maxAttempts: DEFAULT_STREAM_RECONNECTS,
                  detail: streamErrorDetail(error),
                });
              }
              return;
            } catch {
              // Preserve the streamed failure below until persistence catches up.
            }
          }
          markSessionGenerationFinished(targetSessionId, {
            viewing: isViewingStream(),
          });
          if (isViewingStream()) {
            setStatus("error");
            setStreamConnectionNotice({
              phase: "failed",
              attempt: DEFAULT_STREAM_RECONNECTS,
              maxAttempts: DEFAULT_STREAM_RECONNECTS,
              detail: streamErrorDetail(error),
            });
            setLocalMessages((current) =>
              current.map((message) =>
                message.id === assistant.id
                  ? {
                      ...message,
                      status: "failed",
                      parts: [
                        ...message.parts.map((part) =>
                          part.data?.optimistic === true
                            ? { ...part, status: "failed" as const }
                            : part,
                        ),
                        {
                          id: `error-${stamp}`,
                          type: "error",
                          status: "failed",
                          content:
                            error instanceof Error ? error.message : "消息流失败",
                        },
                      ],
                    }
                  : message,
              ),
            );
          }
          retryPersistedReconciliation();
        }
      } finally {
        clearSessionStream(targetSessionId, controller);
        if (abortRef.current === controller) {
          abortRef.current = null;
          activeMessageId.current = null;
          activeStreamSessionId.current = null;
        }
      }
    },
    [
      queryClient,
      activeModelProvider,
      activeImageProvider,
      autoTitleModel,
      currentSession,
      generationMode,
      graphAction,
      goalMode,
      hasAuthorizedAgentSearchProvider,
      history.isSuccess,
      location.key,
      messages,
      selectedModel?.id,
      selectedModel?.remote,
      selectedImageModel?.id,
      responseMode,
      effectiveThinkingMode,
      ensureAgentSandboxReady,
      searchRoute,
      sessionId,
      navigate,
      rememberCreatedSession,
      closeSessionMutation.isPending,
      sessionIsClosed,
      status,
      workspaceId,
    ],
  );

  const uploadAndIndex = useCallback(
    async (file: File, options?: { agentMode?: boolean }) => {
      const agentMode = options?.agentMode ?? responseMode === "agentic";
      // Fast/thinking: block special binaries early before upload cost.
      if (!agentMode && isSpecialBinaryName(file.name)) {
        throw new Error(
          `「${file.name}」不能在极速/思考模式使用。请切换到智能体模式，或取消该附件。`,
        );
      }
      if (
        !agentMode &&
        isAudioNameOrMime(file.name, file.type) &&
        !asrAvailable
      ) {
        throw new Error(
          `音频「${file.name}」需要已启用的 ASR Provider。请在设置中配置转写，切换智能体，或移除该附件。`,
        );
      }
      // Reuse materials-library files when name + SHA-256 already match.
      try {
        const digest = await hashFileSha256(file);
        const existing = await lookupFile({
          name: file.name,
          sha256: digest,
        });
        await queryClient.invalidateQueries({ queryKey: ["files"] });
        toast.message(`已复用资料库文件「${existing.original_name}」`);
        if (
          !agentMode &&
          isAudioNameOrMime(existing.original_name, existing.mime_type)
        ) {
          const prior = await listAudioTranscriptions(existing.id);
          const completed = prior.find(
            (item) =>
              item.status === "completed" && item.transcript.trim().length > 0,
          );
          if (!completed) {
            toast.message(`正在为「${existing.original_name}」自动转写…`);
            const transcription = await transcribeAudioFile(existing.id, {});
            if (
              transcription.status !== "completed" ||
              !transcription.transcript.trim()
            ) {
              throw new Error(
                `音频「${existing.original_name}」自动转写未完成：${transcription.error_message ?? transcription.status}`,
              );
            }
            toast.success(`「${existing.original_name}」转写完成`);
          }
        }
        return existing;
      } catch (error) {
        if (error instanceof Error && /智能体|ASR|转写|不能在极速/u.test(error.message)) {
          throw error;
        }
        if (!(error instanceof ApiError && error.status === 404)) {
          // Non-404 lookup failures should not block a fresh upload path.
          if (error instanceof ApiError) {
            // fall through to upload
          } else if (!(error instanceof Error && /digest|subtle|crypto/iu.test(error.message))) {
            // crypto unavailable — fall through
          }
        }
      }
      const uploaded = await uploadFile(file);
      // Images are direct multimodal attachments, not text documents.
      if (isImageNameOrMime(uploaded.original_name, uploaded.mime_type)) {
        await queryClient.invalidateQueries({ queryKey: ["files"] });
        return uploaded;
      }
      // Audio: auto-ASR for non-agent; agent keeps source for sandbox.
      if (isAudioNameOrMime(uploaded.original_name, uploaded.mime_type)) {
        await queryClient.invalidateQueries({ queryKey: ["files"] });
        if (!agentMode) {
          toast.message(`正在为「${uploaded.original_name}」自动转写…`);
          const transcription = await transcribeAudioFile(uploaded.id, {});
          if (
            transcription.status !== "completed" ||
            !transcription.transcript.trim()
          ) {
            throw new Error(
              `音频「${uploaded.original_name}」自动转写失败：${transcription.error_message ?? transcription.status}`,
            );
          }
          toast.success(`「${uploaded.original_name}」转写完成，将引用转写文本`);
        }
        return uploaded;
      }
      // Formats that the server stores safely but never indexes in-process
      // (legacy .doc/.ppt/.xls, attachment_only, etc.) must still attach so
      // Agent mode can materialize them into the session workspace inputs/.
      const unindexableCapability =
        uploaded.parse_capability === "isolated_converter_required" ||
        uploaded.parse_capability === "attachment_only";
      if (unindexableCapability) {
        await queryClient.invalidateQueries({ queryKey: ["files"] });
        if (!agentMode) {
          throw new Error(
            `「${uploaded.original_name}」当前无法建立文本索引，极速/思考不能使用。请切换到智能体模式，或移除该附件。`,
          );
        }
        toast.message(
          `“${uploaded.original_name}”已安全存储；当前无法建立文本索引，将作为原始文件附到本轮（智能体可在工作区读取）。`,
        );
        return uploaded;
      }
      try {
        const indexed = await parseFile(uploaded.id);
        await queryClient.invalidateQueries({ queryKey: ["files"] });
        return indexed;
      } catch (error) {
        await queryClient.invalidateQueries({ queryKey: ["files"] });
        // Keep the stored file attachable when parse fails for agent mode.
        if (error instanceof ApiError) {
          if (
            error.code === "file_parse_capability_unavailable" ||
            error.code === "format_not_parseable" ||
            error.status === 409 ||
            error.status === 422
          ) {
            if (!agentMode) {
              throw new Error(
                `“${uploaded.original_name}”解析失败，极速/思考只能引用解析结果：${error.message}。请切换智能体或移除附件。`,
              );
            }
            toast.message(
              `“${uploaded.original_name}”已安全存储，但解析失败：${error.message}。将作为原始文件附到本轮。`,
            );
            return uploaded;
          }
        }
        throw new Error(
          `“${uploaded.original_name}”已安全存储，但解析失败：${error instanceof Error ? error.message : "未知错误"}`,
        );
      }
    },
    [asrAvailable, queryClient, responseMode],
  );

  const convertLongPaste = useMutation({
    mutationFn: (content: string) => {
      const timestamp = new Date().toISOString().replaceAll(/[:.]/g, "-");
      return uploadAndIndex(
        new File([content], `chat-note-${timestamp}.md`, {
          type: "text/markdown",
        }),
      );
    },
    onSuccess: (file) => {
      setPendingFiles((current) => [
        ...current.filter((item) => item.id !== file.id),
        file,
      ]);
      setComposerText((current) =>
        longPaste ? current.replace(longPaste, "").trim() : current,
      );
      setLongPaste(null);
      toast.success(`“${file.original_name}”已解析，将随下一条消息发送`);
    },
    onError: (error) => toast.error(error.message),
  });

  const submitPrompt = useCallback(
    async (message: PromptInputMessage) => {
      try {
        const hasImageMention = /^\s*@绘图(?=\s|$)/u.test(message.text);
        const requestedGenerationMode: GenerationMode =
          hasImageMention || generationMode === "image" ? "image" : "text";
        if (
          requestedGenerationMode === "text" &&
          responseMode === "agentic" &&
          !(await ensureAgentSandboxReady())
        ) {
          return;
        }
        const contentText =
          requestedGenerationMode === "image"
            ? message.text.replace(/^\s*@绘图(?=\s|$)/u, "").trim()
            : message.text.trim();
        if (goalMode && requestedGenerationMode === "text" && !contentText) {
          toast.message(
            "请先补充目标描述。附件可以关联到 Goal，但当前澄清不会读取附件正文。",
          );
          return Promise.reject(new Error("goal_description_required"));
        }
        if (
          requestedGenerationMode === "image" &&
          (!activeImageProvider || !selectedImageModel)
        ) {
          toast.error("请先启用绘图 Provider 并选择已配置的文生图模型。");
          return;
        }
        if (
          requestedGenerationMode === "image" &&
          (message.files.length > 0 || pendingFiles.length > 0)
        )
          toast.message("绘图模式当前只发送文字描述，附件不会进入本次请求。");
        const hasImageAttachment =
          requestedGenerationMode === "text" &&
          (message.files.some((part) =>
            (part.mediaType ?? "").toLowerCase().startsWith("image/"),
          ) ||
            pendingFiles.some((file) =>
              file.mime_type.toLowerCase().startsWith("image/"),
            ));
        if (hasImageAttachment && !selectedModelSupportsImageInput) {
          toast.error(
            "当前模型尚未确认支持图片输入。请在 Provider 设置的模型能力快照中开启并确认该能力后再发送图片。",
          );
          return;
        }
        // D-082: block unsupported pending attachments before upload work.
        if (
          requestedGenerationMode === "text" &&
          responseMode !== "agentic" &&
          pendingFiles.length
        ) {
          for (const file of pendingFiles) {
            const check = classifyNonAgentAttachment({
              name: file.original_name,
              mime: file.mime_type,
              parseCapability: file.parse_capability,
              parseStatus: file.parse_status,
              asrAvailable,
            });
            if (!check.ok) {
              toast.error(check.message);
              return;
            }
          }
        }
        const uploaded = await Promise.all(
          (requestedGenerationMode === "image" ? [] : message.files).map(async (part, index) => {
            if (!part.url)
              throw new Error(
                `附件 ${part.filename ?? index + 1} 缺少可读取内容`,
              );
            const response = await fetch(part.url);
            if (!response.ok)
              throw new Error(`无法读取附件 ${part.filename ?? index + 1}`);
            const blob = await response.blob();
            return uploadAndIndex(
              new File([blob], part.filename ?? `attachment-${index + 1}`, {
                type: part.mediaType || blob.type || "application/octet-stream",
              }),
              { agentMode: responseMode === "agentic" },
            );
          }),
        );
        const fileIds = [
          ...(requestedGenerationMode === "image"
            ? []
            : pendingFiles.map((file) => file.id)),
          ...uploaded.map((file) => file.id),
        ];
        const content =
          contentText ||
          (fileIds.length ? "请阅读并结合附件内容回答。" : "");
        if (!content) {
          if (requestedGenerationMode === "image")
            toast.message("请补充要生成的画面描述。");
          return;
        }
        if (
          goalMode &&
          responseMode !== "agentic" &&
          searchRoute === "disabled"
        ) {
          if (goalFlow.stage !== "capture") {
            toast.message("请先完成上方的目标审核，或开启联网搜索继续调研。");
            return;
          }
          if (fileIds.length) {
            toast.message(
              "附件将作为 Goal 关联资料保存；本轮澄清只依据你的文字描述。",
            );
          }
          await goalFlow.submit(content, fileIds);
          setPendingFiles([]);
          setComposerText("");
        } else {
          const sending = send(content, {
            fileIds,
            generationMode: requestedGenerationMode,
            sandboxPreflighted:
              requestedGenerationMode === "text" && responseMode === "agentic",
          });
          setPendingFiles([]);
          setComposerText("");
          setGenerationMode("text");
          await sending;
        }
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "附件处理失败");
        throw error;
      }
    },
    [
      activeImageProvider,
      asrAvailable,
      ensureAgentSandboxReady,
      generationMode,
      goalFlow,
      goalMode,
      pendingFiles,
      responseMode,
      searchRoute,
      selectedModelSupportsImageInput,
      selectedImageModel,
      send,
      uploadAndIndex,
    ],
  );

  const reviewGraphChange = useMutation({
    mutationFn: ({
      decision,
      proposalId,
      reason,
    }: {
      decision: "confirm" | "reject";
      proposalId: string;
      reason?: string;
    }) =>
      decision === "confirm"
        ? confirmGraphChangeSet(sessionId, proposalId)
        : rejectGraphChangeSet(sessionId, proposalId, reason),
    onSuccess: async (changeSet) => {
      if (changeSet.graph_id) {
        queryClient.setQueryData<Session[]>(["sessions"], (current) =>
          current?.map((item) =>
            item.id === sessionId
              ? { ...item, graph_id: changeSet.graph_id }
              : item,
          ),
        );
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["messages", sessionId] }),
        queryClient.invalidateQueries({
          queryKey: ["graph-change-sets", sessionId],
        }),
        queryClient.invalidateQueries({ queryKey: ["sessions"] }),
        queryClient.invalidateQueries({ queryKey: ["graphs"] }),
        changeSet.graph_id
          ? queryClient.invalidateQueries({
              queryKey: ["graph", changeSet.graph_id],
            })
          : Promise.resolve(),
      ]);
      setLocalMessages([]);
      setRejectProposalId(null);
      setRejectReason("");
      toast.success(
        changeSet.status === "confirmed"
          ? `图谱提案已写入修订 v${changeSet.confirmed_revision}`
          : "图谱提案已拒绝，正式图谱未被修改",
      );
    },
    onError: (error) => toast.error(error.message),
  });

  const handleComponentAction = useCallback(
    (action: TrustedComponentAction) => {
      if (action.componentType === "graph_update_proposal") {
        const proposalId = action.payload.proposal_id;
        if (typeof proposalId !== "string" || !proposalId) {
          toast.error("提案缺少有效标识，未执行任何图谱修改。");
          return;
        }
        if (action.event === "confirm") {
          reviewGraphChange.mutate({ decision: "confirm", proposalId });
        } else if (action.event === "reject") {
          setRejectProposalId(proposalId);
          setRejectReason("");
        }
        return;
      }

      if (action.event !== "submit" && action.event !== "create_plan" && action.event !== "open_plan") {
        return;
      }
      const labels = Array.isArray(action.payload.labels)
        ? action.payload.labels.filter(
            (item): item is string =>
              typeof item === "string" && Boolean(item.trim()),
          )
        : [];
      let answer =
        labels.join("、") ||
        (typeof action.payload.answer === "string"
          ? action.payload.answer
          : typeof action.payload.value === "string"
            ? action.payload.value
            : "");
      if (!answer) {
        if (action.event === "create_plan") {
          const location =
            typeof action.payload.location === "string"
              ? action.payload.location
              : "当前城市";
          const condition =
            typeof action.payload.condition === "string"
              ? action.payload.condition
              : "";
          const temperature =
            typeof action.payload.temperature_c === "number"
              ? `${action.payload.temperature_c}°C`
              : "";
          answer = `请根据${location}的天气（${condition} ${temperature}）生成明日学习计划`;
        } else if (action.event === "open_plan") {
          answer = "请根据卡片中的指标继续分析并给出下一步学习建议";
        } else {
          answer = "跳过该问题";
        }
      } else if (action.event === "submit") {
        answer = `我的回答：${answer}`;
      }
      void send(answer, { graphAction: "none" });
    },
    [reviewGraphChange, send],
  );

  useEffect(() => {
    if (preserveDraftForSessionRef.current === sessionId) {
      preserveDraftForSessionRef.current = null;
      setLocalMessages([]);
      return;
    }
    pendingHandled.current = false;
    if (optimisticSessionId.current !== sessionId) {
      optimisticSessionId.current = null;
    }
    // Keep concurrent-session optimistic rows in localMessages so background
    // streams continue painting; the messages memo only shows this session.
    setEditingMessageId(null);
    setEditingMessageContent("");
    setComposerInstanceKey(conversationResetKey);
    setComposerText("");
    setPendingFiles([]);
    setLongPaste(null);
    setGraphAction("none");
    // Keep product defaults for a fresh /chat/new canvas; existing sessions
    // restore their own prefs via the sessionId effect above.
    if (sessionId === "new") {
      const defaults = defaultComposerPrefs();
      setSearchRoute(defaults.searchRoute);
      setGenerationMode(defaults.generationMode);
    } else {
      // Do not force searchRoute to disabled when switching existing sessions —
      // the prefs restore effect owns that state.
    }
    setSelectionMenu(null);
    if (isSessionStreaming(sessionId)) {
      const handle = getSessionStream(sessionId);
      if (handle) {
        abortRef.current = handle.controller;
        activeStreamSessionId.current = sessionId;
        activeMessageId.current = handle.messageId;
      }
      setStatus("streaming");
    } else {
      // Detach UI control refs from any other session's background stream.
      abortRef.current = null;
      activeStreamSessionId.current = null;
      activeMessageId.current = null;
      setStatus("ready");
    }
    setRejectProposalId(null);
    setRejectReason("");
    setCloseDialogOpen(false);
    learningNodeRef.current = undefined;
    setLearningNode(undefined);
    clearLearningNodeContext();
  }, [conversationResetKey, sessionId]);

  useEffect(() => {
    const routeState = location.state as {
      pendingPrompt?: string;
      pendingFileIds?: string[];
      pendingGraphAction?: GraphAction;
      learningNode?: LearningNodeContext;
    } | null;
    if (routeState?.learningNode?.graphId) {
      learningNodeRef.current = routeState.learningNode;
      setLearningNode(routeState.learningNode);
      storeLearningNodeContext(routeState.learningNode);
    }
    const pendingPrompt = routeState?.pendingPrompt;
    if (
      pendingPrompt &&
      activeModelProvider &&
      !pendingHandled.current &&
      !history.isPending
    ) {
      pendingHandled.current = true;
      navigate(location.pathname, { replace: true });
      void send(pendingPrompt, {
        fileIds: routeState.pendingFileIds ?? [],
        graphAction: routeState.pendingGraphAction,
      });
    }
  }, [
    activeModelProvider,
    history.isPending,
    location.pathname,
    location.state,
    navigate,
    send,
  ]);

  useEffect(() => {
    const listener = (event: Event) => {
      const detail = (
        event as CustomEvent<{
          content?: string;
          autoSend?: boolean;
          graphAction?: GraphAction;
        }>
      ).detail;
      const content = detail?.content ?? "";
      if (!content.trim()) return;
      if (detail?.graphAction && detail.graphAction !== "none") {
        setGraphAction(detail.graphAction);
      }
      if (detail?.autoSend) {
        void send(content, {
          graphAction: detail.graphAction,
        });
        return;
      }
      // Fill composer only when autoSend is not requested.
      window.dispatchEvent(
        new CustomEvent("learngraph:composer-fill", {
          detail: { content },
        }),
      );
      void send(content, {
        graphAction: detail?.graphAction,
      });
    };
    window.addEventListener("learngraph:compose", listener);
    return () => window.removeEventListener("learngraph:compose", listener);
  }, [send]);

  useEffect(() => {
    const selectLearningNode = (event: Event) => {
      const detail = (event as CustomEvent<LearningNodeContext>).detail;
      if (!detail || typeof detail.graphId !== "string") return;
      const previousLabel = learningNodeRef.current?.nodeLabel;
      learningNodeRef.current = detail;
      setLearningNode(detail);
      storeLearningNodeContext(detail);
      // Keep the composer in sync with the selected node: fill when empty, or
      // replace the previous auto draft. Leave real user edits untouched.
      if (detail.nodeLabel) {
        const nextDraft = learningNodeComposerDraft(detail.nodeLabel);
        setComposerText((current) =>
          isLearningNodeComposerDraft(current, previousLabel)
            ? nextDraft
            : current,
        );
        window.requestAnimationFrame(() =>
          composerTextareaRef.current?.focus(),
        );
      }
    };
    window.addEventListener(
      "learngraph:learning-node-selected",
      selectLearningNode,
    );
    return () =>
      window.removeEventListener(
        "learngraph:learning-node-selected",
        selectLearningNode,
      );
  }, []);

  useEffect(() => () => speechRecognitionRef.current?.abort(), []);

  const toggleDictation = useCallback(() => {
    if (isListening) {
      speechRecognitionRef.current?.stop();
      return;
    }

    const speechWindow = window as Window & {
      SpeechRecognition?: BrowserSpeechRecognitionConstructor;
      webkitSpeechRecognition?: BrowserSpeechRecognitionConstructor;
    };
    const SpeechRecognition =
      speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      toast.message("当前浏览器不支持语音转写", {
        description: "可使用支持麦克风权限的 Chrome 或 Edge。",
      });
      return;
    }

    const recognition = new SpeechRecognition();
    const prefix = composerText.trimEnd();
    recognition.lang = navigator.language.startsWith("zh")
      ? "zh-CN"
      : navigator.language || "zh-CN";
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.onresult = (event) => {
      let transcript = "";
      for (
        let index = event.resultIndex;
        index < event.results.length;
        index += 1
      )
        transcript += event.results[index][0]?.transcript ?? "";
      if (transcript.trim())
        setComposerText(
          [prefix, transcript.trim()].filter(Boolean).join(prefix ? " " : ""),
        );
    };
    recognition.onerror = (event) => {
      if (
        event.error === "not-allowed" ||
        event.error === "service-not-allowed"
      )
        toast.error("未获得麦克风权限");
      else if (event.error !== "aborted")
        toast.message("语音转写已停止", {
          description:
            event.error === "no-speech"
              ? "没有检测到语音内容。"
              : "请稍后再试。",
        });
      setIsListening(false);
      speechRecognitionRef.current = null;
    };
    recognition.onend = () => {
      setIsListening(false);
      speechRecognitionRef.current = null;
    };

    speechRecognitionRef.current = recognition;
    try {
      recognition.start();
      setIsListening(true);
    } catch {
      speechRecognitionRef.current = null;
      toast.error("无法启动语音转写");
    }
  }, [composerText, isListening]);

  const branch = useMutation({
    mutationFn: (messageId: string) => {
      const sourceTitle =
        currentSession?.title ??
        sessions.data?.find((item) => item.id === sessionId)?.title;
      return branchSession(sessionId, messageId, {
        title: branchSessionTitle(sourceTitle),
      });
    },
    onSuccess: (session) => {
      inheritSessionComposerPrefs(sessionId, session.id);
      rememberCreatedSession(session);
      navigate(`/w/${workspaceId}/chat/${session.id}`);
    },
    onError: (error) => toast.error(error.message),
  });
  const branchEdit = useMutation<
    Session,
    Error,
    { content: string; messageId: string; sourceSessionId: string }
  >({
    mutationFn: ({ messageId, sourceSessionId }) => {
      const sourceTitle =
        sessions.data?.find((item) => item.id === sourceSessionId)?.title ??
        currentSession?.title;
      return branchSession(sourceSessionId, messageId, {
        title: branchSessionTitle(sourceTitle),
      });
    },
    onSuccess: (session, variables) => {
      inheritSessionComposerPrefs(variables.sourceSessionId, session.id);
      rememberCreatedSession(session);
      setEditingMessageId(null);
      setEditingMessageContent("");
      navigate(`/w/${workspaceId}/chat/${session.id}`, {
        state: { pendingPrompt: variables.content },
      });
    },
    onError: (error) => toast.error(error.message),
  });
  const reconcileRetryHistory = async (
    sourceSessionId: string,
    messageId: string,
    minimumVersion: number,
    statuses: readonly string[],
  ) => {
    const sourceMessages = await confirmedSessionMessages(
      sourceSessionId,
      messageId,
      statuses,
      minimumVersion,
    );
    queryClient.setQueryData(["messages", sourceSessionId], sourceMessages);
    if (sessionId !== sourceSessionId) {
      const currentMessages = await confirmedSessionMessages(
        sessionId,
        messageId,
        statuses,
        minimumVersion,
      );
      queryClient.setQueryData(["messages", sessionId], currentMessages);
    }
  };
  const retry = useMutation<
    { expectedVersion: number; status: "cancelled" | "completed"; tempId: string },
    Error,
    {
      messageId: string;
      sourceSessionId: string;
      payload: MessageRetryRequest;
    }
  >({
    mutationFn: async ({
      messageId,
      sourceSessionId,
      payload,
    }) => {
      setStatus("submitted");
      latestOperationId.current += 1;
      const stamp = Date.now();
      const tempId = `temp-retry-${stamp}`;
      const currentVersion =
        queryClient
          .getQueryData<Message[]>(["messages", sourceSessionId])
          ?.find((message) => message.id === messageId)?.version ?? 0;
      const expectedVersion = currentVersion + 1;
      retryExpectedVersionRef.current = expectedVersion;
      const retryMessage: Message = {
        id: tempId,
        workspace_id: workspaceId,
        session_id: sourceSessionId,
        parent_message_id: messageId,
        role: "assistant",
        version: expectedVersion,
        status: "streaming",
        content: "",
        parts: [],
        provider_trace: { optimistic_target_message_id: messageId },
        created_at: new Date().toISOString(),
      };
      setLocalMessages((current) => [...current, retryMessage]);
      const controller = new AbortController();
      abortRef.current = controller;
      activeMessageId.current = messageId;
      activeStreamSessionId.current = sourceSessionId;
      registerSessionStream(sourceSessionId, controller, messageId);
      markSessionRunning(sourceSessionId, true);
      markSessionTouched(sourceSessionId);
      const isViewingRetry = () =>
        viewingSessionIdRef.current === sourceSessionId;
      let completed = false;
      let terminalFailure = "";
      let lastEventId = "";
      let messageVersionId = "";
      const seenEventIds = new Set<string>();
      const frameQueue = createAnimationFrameQueue<Record<string, unknown>>(
        (updates) =>
          setLocalMessages((current) =>
            current.map((message) =>
              message.id === tempId
                ? applyStreamUpdates(message, updates)
                : message,
            ),
          ),
      );
      const consumeRetryEvent = (data: Record<string, unknown>) => {
        const eventId =
          typeof data.event_id === "string" ? data.event_id : "";
        if (eventId && seenEventIds.has(eventId)) return;
        if (eventId) {
          seenEventIds.add(eventId);
          lastEventId = eventId;
        }
        if (
          typeof data.message_version_id === "string" &&
          data.message_version_id
        )
          messageVersionId = data.message_version_id;
        if (typeof data.message_id === "string") {
          if (isViewingRetry()) activeMessageId.current = data.message_id;
          setSessionStreamMessageId(sourceSessionId, data.message_id);
        }
        const type = streamEventType(data);
        if (type === "message.completed") completed = true;
        if (type === "message.failed")
          terminalFailure = "重试版本生成失败，失败记录已保留。";
        if (type === "message.cancelled") terminalFailure = "生成已取消。";
        if ((type || isMessagePart(data.part)) && isViewingRetry())
          setStatus("streaming");
        expandStreamUpdate(data).forEach((update) => frameQueue.push(update));
      };
      try {
        let streamError: unknown;
        try {
          for await (const event of retrySessionMessage(
            sourceSessionId,
            messageId,
            payload,
            { signal: controller.signal },
          )) {
            consumeRetryEvent(streamData(event.data));
          }
        } catch (error) {
          if (controller.signal.aborted) throw error;
          streamError = error;
        }
        if (!completed && !terminalFailure && streamError && messageVersionId) {
          toast.message("重试连接中断，正在续接持久事件…");
          for (let attempt = 0; attempt < 20; attempt += 1) {
            if (controller.signal.aborted) throw streamError;
            try {
              const replay = await listSessionMessageEvents(
                sourceSessionId,
                messageId,
                {
                  afterEventId: lastEventId || undefined,
                  messageVersionId,
                },
              );
              replay.forEach((event) =>
                consumeRetryEvent(event as Record<string, unknown>),
              );
            } catch {
              // A transient GET failure is retried without starting a new version.
            }
            if (completed || terminalFailure) break;
            await new Promise((resolve) => window.setTimeout(resolve, 250));
          }
        }
        await frameQueue.drain();
        if (terminalFailure) throw new Error(terminalFailure);
        if (!controller.signal.aborted && !completed)
          throw (
            streamError instanceof Error
              ? streamError
              : new Error("重试消息流结束，但没有收到完成事件。")
          );
      } catch (error) {
        if (controller.signal.aborted) {
          const cancellation = activeCancellationRef.current;
          if (cancellation) await cancellation;
          frameQueue.clear();
          if (isViewingRetry()) {
            setLocalMessages((current) =>
              current.map((message) =>
                message.id === tempId
                  ? {
                      ...message,
                      status: "cancelled",
                      parts: message.parts.map((part) => ({
                        ...part,
                        status: "failed" as const,
                      })),
                    }
                  : message,
              ),
            );
          }
        }
        else {
          await frameQueue.drain();
          if (isViewingRetry()) {
            setLocalMessages((current) =>
              current.map((message) =>
                message.id === tempId
                  ? {
                      ...message,
                      status: "failed",
                      parts: [
                        ...message.parts,
                        {
                          id: `retry-error-${stamp}`,
                          type: "error",
                          status: "failed",
                          content:
                            error instanceof Error ? error.message : "重试失败",
                        },
                      ],
                    }
                  : message,
              ),
            );
          }
          throw error;
        }
      } finally {
        clearSessionStream(sourceSessionId, controller);
        if (abortRef.current === controller) {
          abortRef.current = null;
          activeMessageId.current = null;
          activeStreamSessionId.current = null;
        }
      }
      return {
        expectedVersion,
        status: controller.signal.aborted ? "cancelled" : "completed",
        tempId,
      };
    },
    onSuccess: async (result, variables) => {
      markSessionGenerationFinished(variables.sourceSessionId, {
        viewing: viewingSessionIdRef.current === variables.sourceSessionId,
      });
      try {
        await reconcileRetryHistory(
          variables.sourceSessionId,
          variables.messageId,
          result.expectedVersion,
          [result.status],
        );
      } catch {
        retryExpectedVersionRef.current = 0;
        if (viewingSessionIdRef.current === variables.sourceSessionId) {
          setStatus("ready");
          toast.message("新版本已生成，消息记录正在后台同步。");
        }
        void Promise.all([
          queryClient.invalidateQueries({ queryKey: ["messages", sessionId] }),
          queryClient.invalidateQueries({
            queryKey: ["messages", variables.sourceSessionId],
          }),
          queryClient.invalidateQueries({
            queryKey: ["message-versions", variables.sourceSessionId],
          }),
        ]);
        return;
      }
      if (viewingSessionIdRef.current === variables.sourceSessionId) {
        setLocalMessages((current) =>
          current.filter((message) => message.id !== result.tempId),
        );
        setRetryTarget(null);
        setStatus("ready");
      }
      void queryClient.invalidateQueries({
        queryKey: ["message-versions", variables.sourceSessionId],
      });
      retryExpectedVersionRef.current = 0;
    },
    onError: async (error, variables) => {
      markSessionGenerationFinished(variables.sourceSessionId, {
        viewing: viewingSessionIdRef.current === variables.sourceSessionId,
      });
      try {
        await reconcileRetryHistory(
          variables.sourceSessionId,
          variables.messageId,
          retryExpectedVersionRef.current,
          TERMINAL_MESSAGE_STATUSES,
        );
        if (viewingSessionIdRef.current === variables.sourceSessionId) {
          setLocalMessages((current) =>
            current.filter(
              (message) =>
                message.provider_trace?.optimistic_target_message_id !==
                variables.messageId,
            ),
          );
        }
      } catch {
        void Promise.all([
          queryClient.invalidateQueries({
            queryKey: ["messages", variables.sourceSessionId],
          }),
          queryClient.invalidateQueries({ queryKey: ["messages", sessionId] }),
        ]);
      }
      void queryClient.invalidateQueries({
        queryKey: ["message-versions", variables.sourceSessionId],
      });
      retryExpectedVersionRef.current = 0;
      setStatus("ready");
      toast.error(error.message);
    },
  });

  const mentionMatch = composerText.match(/(^|\s)@([^\s@]*)$/u);
  const mentionQuery = mentionMatch?.[2]?.toLocaleLowerCase() ?? "";
  const libraryFileMentions = useQuery({
    queryKey: ["files", "mention", mentionQuery],
    queryFn: () => listFiles({ q: mentionQuery || undefined, limit: 8 }),
    enabled: Boolean(mentionMatch && !goalMode && !sessionIsClosed),
    staleTime: 15_000,
  });
  const goalComposerLocked = Boolean(
    goalMode &&
      responseMode !== "agentic" &&
      searchRoute === "disabled" &&
      goalFlow.stage !== "capture",
  );
  const goalStageLabel =
    goalFlow.stage === "capture"
      ? "描述目标"
      : goalFlow.stage === "clarifying"
        ? "关键澄清"
        : goalFlow.stage === "goal_review" || goalFlow.stage === "graph_building"
          ? "确认 Goal"
          : goalFlow.stage === "graph_review"
            ? "审核图谱"
            : "已完成";

  const enterGoalMode = useCallback(() => {
    setComposerText((current) =>
      current.replace(/(^|\s)@[^\s@]*$/u, "$1").trimEnd(),
    );
    setDismissedMention("");
    setGraphAction("none");
    setSearchRoute("disabled");
    setGenerationMode("text");
    const params = new URLSearchParams(location.search);
    params.set("mode", "goal");
    navigate(`${location.pathname}?${params.toString()}`);
    window.requestAnimationFrame(() => composerTextareaRef.current?.focus());
  }, [location.pathname, location.search, navigate]);

  const leaveGoalMode = useCallback(() => {
    const params = new URLSearchParams(location.search);
    params.delete("mode");
    const query = params.toString();
    navigate(`${location.pathname}${query ? `?${query}` : ""}`, {
      replace: true,
    });
  }, [location.pathname, location.search, navigate]);

  const canUseNetworkSearch = Boolean(
    hasAuthorizedAgentSearchProvider ||
      selectedModel?.capabilities?.hosted_web_search ||
      activeModelProvider?.capabilities.hosted_web_search,
  );
  const retryCanUseNetworkSearch = Boolean(
    hasAuthorizedAgentSearchProvider ||
      retrySelectedModel?.capabilities?.hosted_web_search ||
      retryProvider?.capabilities.hosted_web_search,
  );
  const preferredAgentThinkingMode = useMemo(
    () =>
      (["xhigh", "high", "medium", "low"] as ThinkingMode[]).find((mode) =>
        thinkingModes.includes(mode),
      ) ?? "off",
    [thinkingModes],
  );
  const graphCommandAvailable = Boolean(
    currentSession?.goal_id || currentSession?.graph_id,
  );
  const graphCommandLabel = currentSession?.graph_id
    ? "图谱创建和更新"
    : "图谱创建和更新";
  const focusComposer = useCallback(() => {
    window.requestAnimationFrame(() => composerTextareaRef.current?.focus());
  }, []);
  const enableAgentMode = useCallback(
    ({ enableSearch = false }: { enableSearch?: boolean } = {}) => {
      if (!supportsAgentMode) return;
      setGenerationMode("text");
      setResponseMode("agentic");
      setThinkingMode(preferredAgentThinkingMode);
      if (enableSearch) {
        setSearchRoute(
          hasAuthorizedAgentSearchProvider
            ? "auto"
            : canUseNetworkSearch
              ? "model_native"
              : "disabled",
        );
      }
    },
    [
      canUseNetworkSearch,
      hasAuthorizedAgentSearchProvider,
      preferredAgentThinkingMode,
      supportsAgentMode,
    ],
  );
  const prepareTaskPrompt = useCallback(
    (
      prompt: string,
      {
        agent = false,
        enableSearch = false,
      }: { agent?: boolean; enableSearch?: boolean } = {},
    ) => {
      setComposerText((current) =>
        current.trim() ? current.trimEnd() + "\n\n" + prompt : prompt,
      );
      setGenerationMode("text");
      setGraphAction("none");
      if (agent) enableAgentMode({ enableSearch });
      focusComposer();
    },
    [enableAgentMode, focusComposer],
  );
  const setGraphProposal = useCallback(() => {
    if (!graphCommandAvailable) return;
    prepareTaskPrompt(
      currentSession?.graph_id
        ? "请使用图谱提案工具读取并更新当前目标图谱，生成需要我审核的变更提案，不要直接发布。"
        : "请使用图谱提案工具为当前已确认目标创建候选图谱，生成需要我审核的提案，不要直接发布。",
      { agent: true },
    );
  }, [currentSession?.graph_id, graphCommandAvailable, prepareTaskPrompt]);
  const toggleNetworkSearch = useCallback(() => {
    setGenerationMode("text");
    setSearchRoute((current) => {
      if (current !== "disabled") return "disabled";
      if (!canUseNetworkSearch) return "disabled";
      // Prefer external SearchProvider when authorized; model_native only when
      // the active model explicitly hosts web search and no SearchProvider is on.
      if (hasAuthorizedAgentSearchProvider) return "auto";
      if (
        selectedModel?.capabilities?.hosted_web_search ||
        activeModelProvider?.capabilities.hosted_web_search
      ) {
        return "model_native";
      }
      return "disabled";
    });
    focusComposer();
  }, [
    activeModelProvider?.capabilities.hosted_web_search,
    canUseNetworkSearch,
    focusComposer,
    hasAuthorizedAgentSearchProvider,
    selectedModel?.capabilities?.hosted_web_search,
  ]);
  const toggleAgentMode = useCallback(() => {
    if (responseMode === "agentic") {
      setResponseMode(supportsThinkingMode ? "thinking" : "fast");
      focusComposer();
      return;
    }
    enableAgentMode();
    focusComposer();
  }, [enableAgentMode, focusComposer, responseMode, supportsThinkingMode]);
  const toggleImageMode = useCallback(() => {
    setGenerationMode((current) => (current === "image" ? "text" : "image"));
    setGraphAction("none");
    setSearchRoute("disabled");
    focusComposer();
  }, [focusComposer]);
  const composerCommands = [
    {
      id: "goal" as const,
      label: "设定学习目标",
      description: "进入 Goal 澄清流程",
      keywords: "目标 goal",
      disabled: sessionIsClosed && !goalMode,
      Icon: Target,
    },
    {
      id: "image" as const,
      label: "绘图",
      description: activeImageProvider
        ? `使用 ${selectedImageModel?.id ?? defaultImageModelId}`
        : "需启用绘图 Provider 与默认模型",
      keywords: "绘图 图片 image",
      disabled:
        sessionIsClosed || !activeImageProvider || !selectedImageModel,
      Icon: ImageIcon,
    },
    {
      id: "search" as const,
      label: "网络搜索",
      description: canUseNetworkSearch
        ? hasAuthorizedAgentSearchProvider
          ? "通过已启用的 SearchProvider 检索公开网页"
          : "使用模型托管网页搜索"
        : "请先在 Provider 管理中点「启用」SearchProvider，或确认模型托管联网能力",
      keywords: "网络 搜索 search",
      disabled: sessionIsClosed || !canUseNetworkSearch,
      Icon: Search,
    },
    {
      id: "agentic" as const,
      label: "智能体",
      description: supportsAgentMode
        ? hasAuthorizedAgentSearchProvider
          ? "启用工具循环；联网需打开「联网」开关"
          : "启用工具循环；SearchProvider 探测通过后仍须「启用」才能联网"
        : "需支持结构化工具调用的远程模型",
      keywords: "智能体 agent",
      disabled: sessionIsClosed || !supportsAgentMode,
      Icon: Bot,
    },
    {
      id: "graph" as const,
      label: graphCommandLabel,
      description: currentSession?.graph_id
        ? "围绕当前绑定节点增补/修改子节点，自动去重后生成需审核提案"
        : "下一条消息生成需人工审核的新图谱提案",
      keywords: "图谱 节点 变更 graph",
      disabled: sessionIsClosed || !graphCommandAvailable,
      Icon: Network,
    },
    {
      id: "research" as const,
      label: "联网研究",
      description: "预填并行研究任务，由你确认发送",
      keywords: "研究 调研 research web",
      disabled: sessionIsClosed || !supportsAgentMode || !canUseNetworkSearch,
      Icon: Search,
    },
    {
      id: "roadmap" as const,
      label: "学习路线",
      description: "由智能体读取路线或创建待审核的重规划草稿",
      keywords: "路线 计划 roadmap action",
      disabled: sessionIsClosed || !supportsAgentMode,
      Icon: GitBranch,
    },
    {
      id: "schedule" as const,
      label: "日程规划",
      description: "由智能体读取、创建或调整带时间的学习行动",
      keywords: "日程 计划 安排 calendar schedule action",
      disabled: sessionIsClosed || !supportsAgentMode,
      Icon: CalendarDays,
    },
    {
      id: "progress" as const,
      label: "学习进度",
      description: "读取掌握度、证据与复习状态",
      keywords: "进度 掌握 复习 mastery evidence",
      disabled: sessionIsClosed || !supportsAgentMode,
      Icon: Check,
    },
    {
      id: "practice" as const,
      label: "生成练习",
      description: "预填基于当前上下文的练习请求",
      keywords: "练习 测验 quiz exercise",
      disabled: sessionIsClosed || !activeModelProvider,
      Icon: Sparkles,
    },
    {
      id: "usage" as const,
      label: "用量与预算",
      description: "读取用量、成本和预算状态",
      keywords: "用量 预算 成本 token usage budget",
      disabled: sessionIsClosed || !supportsAgentMode,
      Icon: LayoutDashboard,
    },
  ];
  const mentionCandidates = composerCommands.filter(
    (item) =>
      !mentionQuery ||
      `${item.label} ${item.keywords}`.toLocaleLowerCase().includes(mentionQuery),
  );
  type MentionEntry =
    | { kind: "command"; command: (typeof composerCommands)[number] }
    | { kind: "file"; file: FileRecord };
  const mentionEntries = useMemo<MentionEntry[]>(() => {
    const commands: MentionEntry[] = mentionCandidates.map((command) => ({
      kind: "command",
      command,
    }));
    const files: MentionEntry[] = (libraryFileMentions.data ?? [])
      .filter((file) =>
        !mentionQuery
          ? true
          : file.original_name.toLocaleLowerCase().includes(mentionQuery),
      )
      .slice(0, 8)
      .map((file) => ({ kind: "file", file }));
    return [...commands, ...files];
  }, [libraryFileMentions.data, mentionCandidates, mentionQuery]);
  const showMentionMenu = Boolean(
    !goalMode &&
      mentionMatch &&
      dismissedMention !== composerText &&
      !sessionIsClosed &&
      mentionEntries.length,
  );
  const mentionCandidateSignature = mentionEntries
    .map((item) =>
      item.kind === "command"
        ? `c:${item.command.id}:${item.command.disabled}`
        : `f:${item.file.id}`,
    )
    .join("|");
  const firstEnabledMentionIndex = mentionEntries.findIndex((item) =>
    item.kind === "command" ? !item.command.disabled : true,
  );

  useEffect(() => {
    setMentionIndex(
      firstEnabledMentionIndex >= 0 ? firstEnabledMentionIndex : 0,
    );
  }, [composerText, firstEnabledMentionIndex, mentionCandidateSignature]);

  const attachLibraryFile = useCallback(
    (file: FileRecord) => {
      setComposerText((current) =>
        current.replace(/(^|\s)@[^\s@]*$/u, "$1").trimEnd(),
      );
      setDismissedMention("");
      setPendingFiles((current) =>
        current.some((item) => item.id === file.id)
          ? current
          : [...current, file],
      );
      toast.message(`已引用资料「${file.original_name}」`);
      focusComposer();
    },
    [focusComposer],
  );
  const activateComposerCommand = useCallback(
    (action: ComposerCommandId) => {
      if (action === "goal") {
        enterGoalMode();
        return;
      }
      setComposerText((current) =>
        current.replace(/(^|\s)@[^\s@]*$/u, "$1").trimEnd(),
      );
      setDismissedMention("");
      if (action === "image") {
        setGenerationMode("image");
        setGraphAction("none");
        setSearchRoute("disabled");
        focusComposer();
        return;
      }
      if (action === "search") {
        setGenerationMode("text");
        setSearchRoute("auto");
        focusComposer();
        return;
      }
      if (action === "agentic") {
        enableAgentMode({ enableSearch: hasAuthorizedAgentSearchProvider });
        focusComposer();
        return;
      }
      if (action === "graph") {
        setGraphProposal();
        return;
      }
      if (action === "research") {
        prepareTaskPrompt(
          "请使用已授权的联网搜索与并行研究工具，调研这个问题并给出可追溯来源、不同观点和下一步建议。",
          { agent: true, enableSearch: true },
        );
        return;
      }
      if (action === "roadmap") {
        prepareTaskPrompt(
          "请读取当前学习目标的路线，说明下一步行动、前置阻塞和可执行建议；如需调整，只创建待审核的路线草稿。",
          { agent: true },
        );
        return;
      }
      if (action === "schedule") {
        prepareTaskPrompt(
          "请使用日程规划工具读取当前学习行动，并结合目标期限和可用时间创建或调整具体日程；任何路线重规划只生成待审核草稿。",
          { agent: true },
        );
        return;
      }
      if (action === "progress") {
        prepareTaskPrompt(
          "请读取当前学习节点的掌握度、证据与复习状态，并给出下一步学习建议。",
          { agent: true },
        );
        return;
      }
      if (action === "usage") {
        prepareTaskPrompt(
          "请读取当前工作区的模型用量与预算，分别说明美元和人民币成本以及剩余额度。",
          { agent: true },
        );
        return;
      }
      prepareTaskPrompt(
        "请基于当前学习目标、节点和已附资料生成一道可验证的练习，并先说明覆盖的知识点。",
      );
    },
    [
      enableAgentMode,
      enterGoalMode,
      focusComposer,
      hasAuthorizedAgentSearchProvider,
      prepareTaskPrompt,
      setGraphProposal,
    ],
  );
  const clearSelectedLearningNode = useCallback(() => {
    const previousLabel = learningNodeRef.current?.nodeLabel;
    learningNodeRef.current = undefined;
    setLearningNode(undefined);
    clearLearningNodeContext();
    // Drop the auto-filled “什么是 X？” draft when the user unbinds the node.
    setComposerText((current) =>
      isLearningNodeComposerDraft(current, previousLabel) ? "" : current,
    );
    focusComposer();
  }, [focusComposer]);
  const toggleGoalMode = useCallback(() => {
    if (goalMode) {
      leaveGoalMode();
      return;
    }
    enterGoalMode();
  }, [enterGoalMode, goalMode, leaveGoalMode]);
  const openAttachmentPicker = useCallback(() => {
    openFileDialogRef.current();
  }, []);
  const selectComposerMenuCommand = useCallback(
    (command: ComposerCommandId) => {
      if (command === "goal") {
        toggleGoalMode();
        return;
      }
      if (command === "image") {
        toggleImageMode();
        return;
      }
      if (command === "search") {
        toggleNetworkSearch();
        return;
      }
      if (command === "agentic") {
        toggleAgentMode();
        return;
      }
      activateComposerCommand(command);
    },
    [
      activateComposerCommand,
      toggleAgentMode,
      toggleGoalMode,
      toggleImageMode,
      toggleNetworkSearch,
    ],
  );

  const submitRetry = () => {
    if (!retryTarget || !retryProvider || !retrySelectedModel?.remote) return;
    const useWebSearch = retryWebSearch && retryCanUseNetworkSearch;
    const allowedDomains = [
      ...new Set(
        retryAllowedDomains
          .split(/[\s,，]+/u)
          .map((domain) => domain.trim())
          .filter(Boolean),
      ),
    ];
    const variables = {
      messageId: retryTarget.messageId,
      sourceSessionId: retryTarget.sourceSessionId,
      payload: {
        provider_id: retryProvider.id,
        model_id: retrySelectedModel.id,
        thinking_mode:
          retryResponseMode === "fast" ? "off" : retryThinkingMode,
        agent_mode: retryResponseMode === "agentic",
        search_route: useWebSearch
          ? hasAuthorizedAgentSearchProvider
            ? "auto"
            : "model_native"
          : "disabled",
        web_search: useWebSearch,
        allowed_domains: useWebSearch ? allowedDomains : [],
      },
    } satisfies {
      messageId: string;
      sourceSessionId: string;
      payload: MessageRetryRequest;
    };
    setRetryTarget(null);
    retry.mutate(variables);
  };

  return (
    <div className="chat-canvas-page relative flex h-full min-h-0 flex-col bg-background">
      <Conversation className="min-h-0 flex-1">
        <ConversationContent
          className="mx-auto w-full max-w-4xl gap-7 px-5 py-7 pb-36 sm:px-7"
          onMouseUp={() => {
            const selected = readTextSelection();
            if (!selected) {
              setSelectionMenu(null);
              return;
            }
            const source = messages.find(
              (message) => message.id === selected.source_message_id,
            );
            if (!source) {
              setSelectionMenu(null);
              return;
            }
            // Multi-line markdown selections rarely match `message.content`
            // byte-for-byte. Always show the toolbar; only mark contentMatched
            // when we can re-anchor onto the durable body for selection_context.
            const located = locateSelectionInContent(
              source.content,
              selected.selected_text,
              {
                occurrenceIndex: selected.occurrenceIndex,
                prefixHint: selected.prefix,
                suffixHint: selected.suffix,
              },
            );
            setSelectionMenu({
              ...selected,
              selected_text: located.selected_text,
              prefix: located.prefix,
              suffix: located.suffix,
              contentMatched: located.contentMatched,
            });
          }}
        >
          {isEmptySession && !goalMode ? (
            <EmptySessionPrompts
              disabled={
                history.isPending || status !== "ready" || sessionIsClosed
              }
              isError={
                showSuggestedPromptState &&
                suggestedPromptsError &&
                emptySessionDisplayPrompts.length === 0
              }
              isPending={
                (showSuggestedPromptState && suggestedPromptsPending) ||
                (emptySessionMemories.isPending &&
                  emptySessionDisplayPrompts.length === 0)
              }
              isUnavailable={
                showSuggestedPromptState &&
                suggestedPromptsUnavailable &&
                emptySessionDisplayPrompts.length === 0
              }
              onConfigureProvider={() =>
                navigate(`/w/${workspaceId}/settings/providers`)
              }
              onRetry={retrySuggestedPrompts}
              onSelect={(content) => void send(content)}
              prompts={emptySessionDisplayPrompts}
            />
          ) : (
            <>
              {messages.map((message) => {
                const persisted =
                  !message.id.startsWith("temp") &&
                  message.id !== "welcome-local";
                const imageAnswer = message.parts.some(
                  (part) => part.type === "image",
                );
                if (message.role === "user")
                  return (
                    <UserMessage
                      disabled={
                        status !== "ready" || branchEdit.isPending || !persisted
                      }
                      editing={editingMessageId === message.id}
                      editValue={
                        editingMessageId === message.id
                          ? editingMessageContent
                          : message.content
                      }
                      key={message.id}
                      message={message}
                      onCancelEdit={() => {
                        setEditingMessageId(null);
                        setEditingMessageContent("");
                      }}
                      onEditValueChange={setEditingMessageContent}
                      onSaveEdit={() => {
                        const content = editingMessageContent.trim();
                        if (!content) return;
                        branchEdit.mutate({
                          content,
                          messageId: message.id,
                          sourceSessionId: message.session_id,
                        });
                      }}
                      onStartEdit={() => {
                        setEditingMessageId(message.id);
                        setEditingMessageContent(message.content);
                      }}
                      versionNavigation={userVersionNavigation(message)}
                    />
                  );
                return (
                  <AssistantMessage
                    branchDisabled={
                      !persisted ||
                      message.session_id !== sessionId ||
                      status !== "ready" ||
                      branch.isPending
                    }
                    branchDisabledReason={
                      !persisted
                        ? "回答持久化后才能创建分支"
                        : message.session_id !== sessionId
                          ? "请先切换到该回答所属的会话版本"
                          : status !== "ready" || branch.isPending
                            ? "当前操作完成后才能创建分支"
                            : undefined
                    }
                    key={message.id}
                    message={message}
                    onComponentAction={handleComponentAction}
                    onBranch={() => {
                      if (persisted && message.session_id === sessionId)
                        branch.mutate(message.id);
                    }}
                    onRetry={() => {
                      if (!persisted || imageAnswer) return;
                      setRetryProviderId(activeModelProvider?.id ?? modelProviders[0]?.id ?? "");
                      setRetryModelId(selectedModelId);
                      setRetryResponseMode(
                        responseMode,
                      );
                      setRetryThinkingMode(effectiveThinkingMode);
                      setRetryWebSearch(searchRoute !== "disabled");
                      setRetryAllowedDomains("");
                      setRetryTarget({
                        messageId: message.id,
                        sourceSessionId: message.session_id,
                      });
                    }}
                    retryDisabled={
                      !persisted ||
                      imageAnswer ||
                      sessionIsClosed ||
                      status !== "ready" ||
                      retry.isPending
                    }
                    retryDisabledReason={
                      !persisted
                        ? "回答持久化后才能重试"
                        : imageAnswer
                          ? "绘图回答暂不支持文本模型重试"
                        : sessionIsClosed
                          ? "会话已结束；请创建分支或新会话继续学习"
                          : status !== "ready" || retry.isPending
                            ? "当前操作完成后才能重试"
                            : undefined
                    }
                    sessionId={message.session_id}
                  />
                );
              })}
              {streamConnectionNotice ? (
                <StreamConnectionFeedback notice={streamConnectionNotice} />
              ) : null}
              {goalMode ? (
                <GoalSetupConversation
                  flow={goalFlow}
                  hasConversationMessages={!isEmptySession}
                />
              ) : null}
              {!goalMode && showSuggestedPromptState ? (
                <FollowUpPrompts
                  isError={suggestedPromptsError}
                  isPending={suggestedPromptsPending}
                  isUnavailable={suggestedPromptsUnavailable}
                  onConfigureProvider={() =>
                    navigate(`/w/${workspaceId}/settings/providers`)
                  }
                  onRetry={retrySuggestedPrompts}
                  onSelect={(content) => void send(content)}
                  prompts={promptSuggestions}
                />
              ) : null}
            </>
          )}
        </ConversationContent>
        <ConversationScrollButton />
      </Conversation>

      {selectionMenu ? (
        <div
          aria-label="划词操作"
          className="chat-selection-menu"
          role="toolbar"
          style={{ left: selectionMenu.left, top: selectionMenu.top }}
        >
          <span title={selectionMenu.selected_text}>
            “{selectionMenu.selected_text}”
          </span>
          <Button
            onClick={() => {
              const quote = `> ${selectionMenu.selected_text.replaceAll("\n", "\n> ")}\n\n`;
              setComposerText((current) => `${current}${current ? "\n\n" : ""}${quote}`);
              setSelectionMenu(null);
              window.requestAnimationFrame(() => composerTextareaRef.current?.focus());
            }}
            size="xs"
            variant="ghost"
          >
            <MessageSquareQuote className="size-3.5" />
            引用
          </Button>
          {["解释", "举例"].map((action) => (
            <Button
              disabled={
                selectionMenu.source_message_id.startsWith("temp") ||
                selectionMenu.source_message_id === "welcome-local" ||
                status !== "ready"
              }
              key={action}
              onClick={() => {
                const context = selectionRequestContext(selectionMenu);
                const quote = selectionMenu.selected_text;
                setSelectionMenu(null);
                // Unmatched multi-line selections still fire the prompt; they
                // just omit selection_context so the backend never 409s.
                void send(`请${action}这段内容：${quote}`, {
                  generationMode: "text",
                  selectionContext: context,
                });
              }}
              size="xs"
              variant="ghost"
            >
              {action}
            </Button>
          ))}
          <Button
            aria-label="关闭划词操作"
            onClick={() => setSelectionMenu(null)}
            size="icon-xs"
            variant="ghost"
          >
            <X className="size-3" />
          </Button>
        </div>
      ) : null}

      <div className="chat-composer-dock relative z-10 mx-auto w-full max-w-4xl px-3 pb-3 pt-2.5 sm:px-4">
        <ConversationContextBar
          goalBound={Boolean(currentSession?.goal_id)}
          graphTitle={graphTitle}
          learningNode={learningNode}
          onClearLearningNode={clearSelectedLearningNode}
        />
        <ConversationQuickActions
          agentActive={responseMode === "agentic"}
          agentDisabled={
            sessionIsClosed || goalFlow.busy || !supportsAgentMode
          }
          attachDisabled={sessionIsClosed || goalFlow.busy}
          goalActive={goalMode}
          goalDisabled={goalFlow.busy || (!goalMode && sessionIsClosed)}
          graphActive={graphAction !== "none"}
          graphDisabled={
            sessionIsClosed ||
            goalMode ||
            goalFlow.busy ||
            !graphCommandAvailable
          }
          imageActive={generationMode === "image"}
          imageDisabled={
            sessionIsClosed ||
            goalMode ||
            goalFlow.busy ||
            !activeImageProvider ||
            !selectedImageModel
          }
          onAgent={toggleAgentMode}
          onAttach={openAttachmentPicker}
          onGoal={toggleGoalMode}
          onGraph={setGraphProposal}
          onImage={toggleImageMode}
          onPractice={() => activateComposerCommand("practice")}
          onSearch={toggleNetworkSearch}
          practiceDisabled={
            sessionIsClosed || goalMode || goalFlow.busy || !activeModelProvider
          }
          searchActive={searchRoute !== "disabled"}
          searchDisabled={
            sessionIsClosed ||
            goalFlow.busy ||
            !canUseNetworkSearch
          }
        />
        <SandboxAuthDialog
          onClose={() => setSandboxAuthRequest(null)}
          onGranted={() => {
            void queryClient.invalidateQueries({ queryKey: ["messages", sessionId] });
          }}
          request={sandboxAuthRequest}
        />

      {longPaste ? (
          <div className="mb-2 flex flex-wrap items-center gap-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 shadow-sm dark:border-amber-900 dark:bg-amber-950/45 dark:text-amber-100">
            <FilePlus2 className="size-4" />
            <span className="min-w-0 flex-1">
              <strong>检测到单次粘贴超过 10,000 字符。</strong>
              原文仍保留，可选择转换为私有 Markdown 附件。
            </span>
            <Button
              onClick={() => setLongPaste(null)}
              size="xs"
              variant="outline"
            >
              保留文本
            </Button>
            <Button
              disabled={convertLongPaste.isPending}
              onClick={() => convertLongPaste.mutate(longPaste)}
              size="xs"
            >
              {convertLongPaste.isPending ? "上传并解析中…" : "转换附件"}
            </Button>
          </div>
        ) : null}
        {pendingFiles.length ? (
          <div className="mb-2 flex flex-wrap gap-2" aria-label="待发送附件">
            {pendingFiles.map((file) => (
              <Badge className="gap-1" key={file.id} variant="secondary">
                {file.mime_type.toLowerCase().startsWith("image/") ? (
                  <ImageIcon className="size-3" />
                ) : (
                  <FilePlus2 className="size-3" />
                )}
                <span className="max-w-52 truncate">{file.original_name}</span>
                <button
                  aria-label={`移除附件 ${file.original_name}`}
                  className="ml-1 rounded-sm hover:text-destructive"
                  onClick={() =>
                    setPendingFiles((current) =>
                      current.filter((item) => item.id !== file.id),
                    )
                  }
                  type="button"
                >
                  <X className="size-3" />
                </button>
              </Badge>
            ))}
          </div>
        ) : null}
        {!providers.isPending && !activeModelProvider ? (
          <div className="mb-2 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50/70 p-3 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/35 dark:text-amber-100">
            <span>没有可用的真实模型 Provider，发送已暂停。</span>
            <Button
              onClick={() => navigate(`/w/${workspaceId}/settings/providers`)}
              size="xs"
              variant="outline"
            >
              配置 Provider
            </Button>
          </div>
        ) : null}
        {graphAction !== "none" ? (
          <div className="chat-graph-action" role="status">
            <Network className="size-3.5" />
            <span>
              {graphAction === "propose_create"
                ? "下一条消息将由真实模型生成新图谱提案"
                : "图谱变更已开启：将围绕当前节点细化/增补子节点，并与现有概念去重"}
            </span>
            <Button
              aria-label="取消图谱变更操作"
              onClick={() => setGraphAction("none")}
              size="icon-xs"
              variant="ghost"
            >
              <X className="size-3" />
            </Button>
          </div>
        ) : null}
        {goalMode ? (
          <div className="chat-goal-mode" role="status">
            <Target className="size-3.5" />
            <span>
              <strong>目标设定</strong>
              {goalStageLabel}
            </span>
            <Button
              aria-label="退出目标设定模式"
              disabled={goalFlow.busy}
              onClick={leaveGoalMode}
              size="icon-xs"
              title="退出目标设定"
              variant="ghost"
            >
              <X className="size-3" />
            </Button>
          </div>
        ) : null}
        {!goalMode && generationMode === "image" ? (
          <div className="chat-image-action" role="status">
            <ImageIcon className="size-3.5" />
            <span>
              绘图 · {activeGenerationProvider?.display_name} / {activeGenerationModelId}
            </span>
            <Button
              aria-label="退出绘图模式"
              onClick={() => setGenerationMode("text")}
              size="icon-xs"
              variant="ghost"
            >
              <X className="size-3" />
            </Button>
          </div>
        ) : null}
        {searchRoute !== "disabled" ? (
          <div className="chat-search-action" role="status">
            <Search className="size-3.5" />
            <span>下一条消息将请求真实联网搜索</span>
            <Button
              aria-label="关闭联网搜索"
              onClick={() => setSearchRoute("disabled")}
              size="icon-xs"
              variant="ghost"
            >
              <X className="size-3" />
            </Button>
          </div>
        ) : null}
        <div className="chat-composer-anchor">
        {showMentionMenu ? (
          <div
            aria-label="@ 命令与资料文件"
            className="chat-mention-menu"
            id={mentionMenuId}
            role="listbox"
          >
            {mentionEntries.map((item, index) => {
              if (item.kind === "file") {
                const file = item.file;
                return (
                  <button
                    aria-selected={mentionIndex === index}
                    id={`${mentionMenuId}-file-${file.id}`}
                    key={`file-${file.id}`}
                    onClick={() => attachLibraryFile(file)}
                    onMouseEnter={() => setMentionIndex(index)}
                    onMouseDown={(event) => event.preventDefault()}
                    role="option"
                    type="button"
                  >
                    <span className="chat-mention-menu__icon">
                      <FileText className="size-4" />
                    </span>
                    <span>
                      <strong>@{file.original_name}</strong>
                      <small>
                        资料库复用 · {file.parse_status}
                        {file.mime_type ? ` · ${file.mime_type}` : ""}
                      </small>
                    </span>
                    {mentionIndex === index ? (
                      <span className="chat-mention-menu__key">Enter</span>
                    ) : null}
                  </button>
                );
              }
              const command = item.command;
              return (
                <button
                  aria-selected={mentionIndex === index}
                  disabled={command.disabled}
                  id={`${mentionMenuId}-${command.id}`}
                  key={command.id}
                  onClick={() => activateComposerCommand(command.id)}
                  onMouseEnter={() => setMentionIndex(index)}
                  onMouseDown={(event) => event.preventDefault()}
                  role="option"
                  type="button"
                >
                  <span className="chat-mention-menu__icon">
                    <command.Icon className="size-4" />
                  </span>
                  <span>
                    <strong>@{command.label}</strong>
                    <small>{command.description}</small>
                  </span>
                  {mentionIndex === index && !command.disabled ? (
                    <span className="chat-mention-menu__key">Enter</span>
                  ) : null}
                </button>
              );
            })}
          </div>
        ) : null}
        <PromptInput
          key={composerInstanceKey}
          accept={composerFileAccept}
          className={
            composerText.includes("\n")
              ? "chat-composer is-expanded"
              : "chat-composer"
          }
          multiple
          onError={(error) => {
            if (error.code === "accept") {
              toast.error(
                responseMode === "agentic"
                  ? error.message
                  : "该文件类型不能在极速/思考模式使用。请切换到智能体模式，或选择图片、文档、文本/代码（音频需已配置 ASR）。",
              );
              return;
            }
            toast.error(error.message);
          }}
          onFileDialogReady={registerFileDialog}
          onSubmit={submitPrompt}
        >
          <PromptInputAttachments />
          <InputGroupAddon
            align="inline-start"
            className="chat-composer__start"
          >
            <PromptInputActionMenu>
              <PromptInputActionMenuTrigger
                aria-label="打开对话功能菜单"
                className="chat-composer__add"
                tooltip="打开功能菜单"
              />
              <PromptInputActionMenuContent className="chat-plus-menu">
                <DropdownMenuLabel>上下文</DropdownMenuLabel>
                <PromptInputActionAddAttachments
                  disabled={sessionIsClosed || goalFlow.busy}
                  label="添加资料"
                />
                {(["goal", "graph"] as const).map((id) => {
                  const command = composerCommands.find(
                    (item) => item.id === id,
                  );
                  if (!command) return null;
                  const Icon = command.Icon;
                  return (
                    <PromptInputActionMenuItem
                      disabled={command.disabled || goalFlow.busy}
                      key={command.id}
                      onSelect={() => selectComposerMenuCommand(command.id)}
                    >
                      <Icon className="size-4" />
                      {command.id === "goal" && goalMode
                        ? "退出目标设定"
                        : command.label}
                    </PromptInputActionMenuItem>
                  );
                })}
                <DropdownMenuSeparator />
                <DropdownMenuLabel>模式与工具</DropdownMenuLabel>
                {(["search", "agentic", "image"] as const).map((id) => {
                  const command = composerCommands.find(
                    (item) => item.id === id,
                  );
                  if (!command) return null;
                  const Icon = command.Icon;
                  return (
                    <PromptInputActionMenuItem
                      disabled={command.disabled || goalFlow.busy}
                      key={command.id}
                      onSelect={() => selectComposerMenuCommand(command.id)}
                    >
                      <Icon className="size-4" />
                      {command.id === "search" && searchRoute !== "disabled"
                        ? "关闭网络搜索"
                        : command.label}
                    </PromptInputActionMenuItem>
                  );
                })}
                <DropdownMenuSeparator />
                <DropdownMenuLabel>学习工作流</DropdownMenuLabel>
                {(["research", "roadmap", "schedule", "progress", "practice", "usage"] as const).map(
                  (id) => {
                    const command = composerCommands.find(
                      (item) => item.id === id,
                    );
                    if (!command) return null;
                    const Icon = command.Icon;
                    return (
                      <PromptInputActionMenuItem
                        disabled={command.disabled || goalFlow.busy}
                        key={command.id}
                        onSelect={() => selectComposerMenuCommand(command.id)}
                      >
                        <Icon className="size-4" />
                        {command.label}
                      </PromptInputActionMenuItem>
                    );
                  },
                )}
                <DropdownMenuSeparator />
                <PromptInputActionMenuItem
                  onSelect={() =>
                    navigate(`/w/${workspaceId}/research/tasks/new`, {
                      state: { pendingPrompt: composerText },
                    })
                  }
                >
                  <Sparkles className="size-4" />
                  打开深度研究任务
                </PromptInputActionMenuItem>
              </PromptInputActionMenuContent>
            </PromptInputActionMenu>
          </InputGroupAddon>
          <PromptInputBody>
            <PromptInputTextarea
              aria-activedescendant={
                showMentionMenu
                  ? (() => {
                      const entry = mentionEntries[mentionIndex];
                      if (!entry) return undefined;
                      if (entry.kind === "file") {
                        return `${mentionMenuId}-file-${entry.file.id}`;
                      }
                      if (entry.command.disabled) return undefined;
                      return `${mentionMenuId}-${entry.command.id}`;
                    })()
                  : undefined
              }
              aria-autocomplete="list"
              aria-controls={showMentionMenu ? mentionMenuId : undefined}
              aria-expanded={showMentionMenu}
              aria-label="输入消息"
              className="chat-composer__textarea"
              disabled={
                (goalMode
                  ? !activeModelProvider
                  : !activeModelProvider && !activeImageProvider) ||
                sessionIsClosed ||
                closeSessionMutation.isPending ||
                goalFlow.busy
              }
              onChange={(event) => setComposerText(event.currentTarget.value)}
              onKeyDown={(event) => {
                if (
                  showMentionMenu &&
                  (event.key === "ArrowDown" || event.key === "ArrowUp")
                ) {
                  event.preventDefault();
                  const direction = event.key === "ArrowDown" ? 1 : -1;
                  let nextIndex = mentionIndex;
                  for (
                    let offset = 0;
                    offset < mentionEntries.length;
                    offset += 1
                  ) {
                    nextIndex =
                      (nextIndex + direction + mentionEntries.length) %
                      mentionEntries.length;
                    const entry = mentionEntries[nextIndex];
                    if (
                      entry &&
                      (entry.kind === "file" || !entry.command.disabled)
                    ) {
                      break;
                    }
                  }
                  setMentionIndex(nextIndex);
                } else if (
                  showMentionMenu &&
                  (event.key === "Enter" || event.key === "Tab")
                ) {
                  const entry = mentionEntries[mentionIndex];
                  event.preventDefault();
                  if (!entry) return;
                  if (entry.kind === "file") {
                    attachLibraryFile(entry.file);
                    return;
                  }
                  if (entry.command.disabled) return;
                  activateComposerCommand(entry.command.id);
                } else if (showMentionMenu && event.key === "Escape") {
                  event.preventDefault();
                  setDismissedMention(composerText);
                }
              }}
              onPaste={(event) => {
                const text = event.clipboardData.getData("text");
                if (Array.from(text).length > 10_000) setLongPaste(text);
              }}
              placeholder={
                goalMode
                  ? searchRoute !== "disabled"
                    ? "搜索待确认的信息…"
                    : goalComposerLocked
                      ? "完成审核，或开启搜索"
                      : "描述你的学习目标…"
                  : generationMode === "image"
                    ? "描述你想生成的画面…"
                    : "输入消息，或输入 @ 选择模式 / 文件…"
              }
              ref={composerTextareaRef}
              role="combobox"
              style={{
                height: `${Math.min(210, 52 + Math.max(0, composerText.split("\n").length - 1) * 23)}px`,
              }}
              value={composerText}
            />
          </PromptInputBody>
           <InputGroupAddon align="inline-end" className="chat-composer__end">
             <DropdownMenu
               onOpenChange={(open) => {
                 if (!open) setModelSearch("");
               }}
             >
              <DropdownMenuTrigger asChild>
                <PromptInputButton
                  aria-label={
                    generationMode === "image"
                      ? "选择绘图模型"
                      : "选择响应模式、思考力度和模型"
                  }
                  className="chat-composer__mode"
                  disabled={
                    !activeGenerationProvider ||
                    sessionIsClosed ||
                    closeSessionMutation.isPending ||
                    goalFlow.busy ||
                    status !== "ready"
                  }
                  tooltip={
                    generationMode === "image" ? "绘图模型" : "响应模式与模型"
                  }
                >
                  <span>
                    {generationMode === "image"
                      ? `绘图 · ${selectedImageModel?.id ?? "未选择"}`
                      : `${responseModeLabel} · ${activeModelProvider?.display_name ?? "模型"} / ${selectedModel?.id ?? "未选择"}`}
                  </span>
                  <ChevronDown className="size-3.5" />
                </PromptInputButton>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="chat-model-menu w-60" side="top">
                {generationMode === "image" ? (
                  <>
                    <DropdownMenuLabel>绘图模型</DropdownMenuLabel>
                    <div className="relative px-1 pb-1">
                      <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                      <Input
                        aria-label="搜索绘图模型"
                        className="h-8 pl-8 font-mono text-xs"
                        onChange={(event) => setModelSearch(event.target.value)}
                        onKeyDown={(event) => event.stopPropagation()}
                        onPointerDown={(event) => event.stopPropagation()}
                        placeholder="搜索绘图模型或 Provider…"
                        value={modelSearch}
                      />
                    </div>
                    {filteredAvailableImageModelChoices.length ? (
                      <DropdownMenuRadioGroup
                        onValueChange={(value) => {
                          const choice = parseModelChoiceValue(value);
                          if (!choice) return;
                          setSelectedImageProviderId(choice.providerId);
                          setSelectedImageModelId(choice.modelId);
                        }}
                        value={
                          activeImageProvider && selectedImageModel
                            ? modelChoiceValue(
                                activeImageProvider.id,
                                selectedImageModel.id,
                              )
                            : ""
                        }
                      >
                        {filteredAvailableImageModelChoices.map(
                          ({ provider, model }) => (
                            <DropdownMenuRadioItem
                              key={`${provider.id}:${model.id}`}
                              value={modelChoiceValue(provider.id, model.id)}
                            >
                              <span className="flex min-w-0 flex-col">
                                <span className="truncate font-mono">{model.id}</span>
                                <span className="text-[10px] text-muted-foreground">
                                  {provider.display_name} · 绘图
                                </span>
                              </span>
                            </DropdownMenuRadioItem>
                          ),
                        )}
                      </DropdownMenuRadioGroup>
                    ) : (
                      <DropdownMenuItem disabled>
                        {discoveredImageModels?.isPending
                          ? "正在载入绘图模型…"
                          : "暂无已配置的绘图模型"}
                      </DropdownMenuItem>
                    )}
                  </>
                ) : (
                  <>
                <DropdownMenuLabel>响应模式</DropdownMenuLabel>
                <DropdownMenuRadioGroup
                  onValueChange={(value) => {
                    const nextMode = value as ResponseMode;
                    setGenerationMode("text");
                    setResponseMode(nextMode);
                    if (nextMode === "fast") {
                      setThinkingMode("off");
                    } else if (nextMode === "agentic") {
                      // Prefer medium thinking intensity for agent mode (product default).
                      const preferred = ["medium", "high", "low", "xhigh"].find(
                        (mode) => thinkingModes.includes(mode as ThinkingMode),
                      );
                      setThinkingMode((preferred as ThinkingMode | undefined) ?? thinkingMode);
                      // Only auto-enable search when a SearchProvider is already
                      // enabled; otherwise leave search off so agent tools work
                      // without forcing web_search against an unavailable provider.
                      if (hasAuthorizedAgentSearchProvider) {
                        setSearchRoute((current) =>
                          current === "disabled" ? "auto" : current,
                        );
                      }
                    } else if (thinkingMode === "off") {
                      setThinkingMode(
                        thinkingModes.includes("medium") ? "medium" : thinkingModes[0] ?? "medium",
                      );
                    }
                  }}
                  value={responseMode}
                >
                  <DropdownMenuRadioItem value="fast">极速</DropdownMenuRadioItem>
                  <DropdownMenuRadioItem
                    disabled={!supportsThinkingMode}
                    value="thinking"
                  >
                    思考{supportsThinkingMode ? "" : "（模型未声明推理能力）"}
                  </DropdownMenuRadioItem>
                  <DropdownMenuRadioItem
                    disabled={!supportsAgentMode}
                    value="agentic"
                  >
                    智能体{supportsAgentMode ? "" : agentModeUnavailableLabel}
                  </DropdownMenuRadioItem>
                </DropdownMenuRadioGroup>
                <DropdownMenuSeparator />
                <DropdownMenuSub>
                  <DropdownMenuSubTrigger>
                    思考力度
                    <span className="ml-auto text-xs text-muted-foreground">
                      {thinkingModes.length
                        ? thinkingLabels[thinkingMode]
                        : "当前模型未声明"}
                    </span>
                  </DropdownMenuSubTrigger>
                  <DropdownMenuSubContent>
                    {thinkingModes.length ? (
                      <DropdownMenuRadioGroup
                        onValueChange={(value) => setThinkingMode(value as ThinkingMode)}
                        value={thinkingMode}
                      >
                        {thinkingModes.map((mode) => (
                          <DropdownMenuRadioItem key={mode} value={mode}>
                            {thinkingLabels[mode]}
                          </DropdownMenuRadioItem>
                        ))}
                      </DropdownMenuRadioGroup>
                    ) : (
                      <DropdownMenuItem disabled>由当前服务商决定</DropdownMenuItem>
                    )}
                  </DropdownMenuSubContent>
                </DropdownMenuSub>
                <DropdownMenuSub>
                  <DropdownMenuSubTrigger>模型</DropdownMenuSubTrigger>
                  <DropdownMenuSubContent className="max-h-[min(60vh,32rem)] overflow-y-auto">
                    <div className="relative px-1 pb-1">
                      <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                      <Input
                        aria-label="搜索模型"
                        className="h-8 pl-8 font-mono text-xs"
                        onChange={(event) => setModelSearch(event.target.value)}
                        onKeyDown={(event) => event.stopPropagation()}
                        onPointerDown={(event) => event.stopPropagation()}
                        placeholder="搜索模型名称或 Provider…"
                        value={modelSearch}
                      />
                    </div>
                    {filteredAvailableModelChoices.length ? (
                      <DropdownMenuRadioGroup
                        onValueChange={(value) => {
                          const choice = parseModelChoiceValue(value);
                          if (!choice) return;
                          setSelectedProviderId(choice.providerId);
                          setSelectedModelId(choice.modelId);
                        }}
                        value={
                          activeModelProvider && selectedModelId
                            ? modelChoiceValue(
                                activeModelProvider.id,
                                selectedModelId,
                              )
                            : ""
                        }
                      >
                        {filteredAvailableModelChoices.map(({ provider, model }) => (
                          <DropdownMenuRadioItem
                            key={`${provider.id}:${model.id}`}
                            value={modelChoiceValue(provider.id, model.id)}
                          >
                            <span className="flex min-w-0 flex-col">
                              <span className="truncate font-mono">{model.id}</span>
                              <span className="text-[10px] text-muted-foreground">
                                {provider.display_name} ·{" "}
                                {modelProtocolLabel(provider.provider_type)}
                              </span>
                            </span>
                            {model.capabilities?.supports_image_input
                              ? " · 图片"
                              : ""}
                          </DropdownMenuRadioItem>
                        ))}
                      </DropdownMenuRadioGroup>
                    ) : (
                      <DropdownMenuItem disabled>
                        {discoveredModels?.isPending ? "正在载入模型…" : "暂无可用模型"}
                      </DropdownMenuItem>
                    )}
                  </DropdownMenuSubContent>
                </DropdownMenuSub>
                  </>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
            <PromptInputButton
              aria-label={isListening ? "停止语音输入" : "开始语音输入"}
              aria-pressed={isListening}
              className="chat-composer__microphone"
              disabled={
                !activeGenerationProvider ||
                sessionIsClosed ||
                closeSessionMutation.isPending ||
                goalFlow.busy
              }
              onClick={toggleDictation}
              tooltip={isListening ? "停止语音输入" : "语音输入"}
            >
              <Mic className="size-4" />
            </PromptInputButton>
            <PromptInputSubmit
              aria-label={
                status === "streaming" || status === "submitted" || goalFlow.busy
                  ? "停止生成"
                  : "发送消息"
              }
              className={cn(
                "chat-composer__submit",
                status === "streaming" && "chat-composer__submit--stop",
              )}
              disabled={
                !activeGenerationProvider ||
                !activeGenerationModelId ||
                sessionIsClosed ||
                closeSessionMutation.isPending ||
                goalFlow.busy ||
                goalComposerLocked
              }
              onStop={() => {
                const messageId = activeMessageId.current;
                const streamSessionId = activeStreamSessionId.current;
                const cancellation =
                  messageId && streamSessionId
                    ? cancelSessionMessage(streamSessionId, messageId)
                        .catch((error: Error) => toast.error(error.message))
                        .then(() => undefined)
                    : Promise.resolve();
                activeCancellationRef.current = cancellation;
                void cancellation.finally(() => {
                  if (activeCancellationRef.current === cancellation)
                    activeCancellationRef.current = null;
                });
                // Only abort the currently viewed session's stream.
                abortSessionStream(streamSessionId ?? sessionId);
                abortRef.current?.abort();
                markSessionRunning(streamSessionId ?? sessionId, false);
                setStatus("submitted");
              }}
              status={goalFlow.busy ? "submitted" : status}
            >
              {status === "streaming" && !goalFlow.busy ? (
                <Square className="size-3.5" color="#111" fill="#111" strokeWidth={0} />
              ) : status === "submitted" || goalFlow.busy ? (
                <LoaderCircle className="size-3.5 animate-spin" />
              ) : (
                <ArrowUp className="size-4" />
              )}
            </PromptInputSubmit>
          </InputGroupAddon>
         </PromptInput>
        </div>
      </div>
      <Dialog
        onOpenChange={(open) => {
          if (!open && !retry.isPending) setRetryTarget(null);
        }}
        open={Boolean(retryTarget)}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>重试回答并保留版本</DialogTitle>
            <DialogDescription>
              为这次重试显式选择真实 Provider、模型和调用模式。新回答会保存为可追溯版本，原回答与 Provider Trace 不会被覆盖。
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="retry-provider">模型 Provider</Label>
              <Select
                disabled={retry.isPending || !modelProviders.length}
                onValueChange={(value) => {
                  setRetryProviderId(value);
                  setRetryModelId("");
                }}
                value={retryProviderId}
              >
                <SelectTrigger className="w-full" id="retry-provider">
                  <SelectValue placeholder="选择真实模型 Provider" />
                </SelectTrigger>
                <SelectContent>
                  {modelProviders.map((provider) => (
                    <SelectItem key={provider.id} value={provider.id}>
                      {provider.display_name} · {provider.provider_type}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {!modelProviders.length && !providers.isPending ? (
                <div className="flex items-center justify-between gap-3 text-xs text-amber-700 dark:text-amber-300">
                  <span>当前工作区没有已启用的真实模型 Provider。</span>
                  <Button
                    onClick={() =>
                      navigate(`/w/${workspaceId}/settings/providers`)
                    }
                    size="xs"
                    variant="outline"
                  >
                    前往设置
                  </Button>
                </div>
              ) : null}
            </div>

            <div className="grid gap-2">
              <Label htmlFor="retry-model">模型</Label>
              <Select
                disabled={
                  retry.isPending ||
                  !retryProvider ||
                  retryDiscoveredModels.isPending ||
                  !retryModelOptions.some((model) => model.remote)
                }
                onValueChange={setRetryModelId}
                value={retryModelId}
              >
                <SelectTrigger className="w-full" id="retry-model">
                  <SelectValue
                    placeholder={
                      retryDiscoveredModels.isPending
                        ? "正在载入模型…"
                        : "选择远程模型"
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  {retryModelOptions.map((model) => (
                    <SelectItem
                      disabled={!model.remote}
                      key={model.id}
                      value={model.id}
                    >
                      {model.id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {retryDiscoveredModels.isError ? (
                <div className="flex items-center justify-between gap-3 text-xs text-destructive">
                  <span>模型发现失败，尚未发起重试。</span>
                  <Button
                    disabled={retryDiscoveredModels.isFetching}
                    onClick={() => void retryDiscoveredModels.refetch()}
                    size="xs"
                    variant="ghost"
                  >
                    <RefreshCcw className="size-3" />
                    重试发现
                  </Button>
                </div>
              ) : null}
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="grid gap-2">
                <Label htmlFor="retry-response-mode">响应模式</Label>
                <Select
                  disabled={retry.isPending}
                  onValueChange={(value) => {
                    const mode = value as ResponseMode;
                    setRetryResponseMode(mode);
                    if (mode === "fast") setRetryThinkingMode("off");
                    else if (retryThinkingMode === "off")
                      setRetryThinkingMode(
                        retryThinkingModes.includes("medium")
                          ? "medium"
                          : (retryThinkingModes[0] ?? "off"),
                      );
                  }}
                  value={retryResponseMode}
                >
                  <SelectTrigger className="w-full" id="retry-response-mode">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="fast">极速</SelectItem>
                    <SelectItem
                      disabled={!retryThinkingModes.length}
                      value="thinking"
                    >
                      思考
                    </SelectItem>
                    <SelectItem
                      disabled={!retrySupportsAgentMode}
                      value="agentic"
                    >
                      智能体
                      {retrySupportsAgentMode
                        ? ""
                        : "（所选模型未声明结构化工具能力）"}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="retry-thinking-mode">思考力度</Label>
                <Select
                  disabled={
                    retry.isPending ||
                    retryResponseMode === "fast" ||
                    !retryThinkingModes.length
                  }
                  onValueChange={(value) =>
                    setRetryThinkingMode(value as ThinkingMode)
                  }
                  value={retryThinkingMode}
                >
                  <SelectTrigger className="w-full" id="retry-thinking-mode">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {retryThinkingModes.map((mode) => (
                      <SelectItem key={mode} value={mode}>
                        {thinkingLabels[mode]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="grid gap-3 rounded-lg border p-3">
              <div className="flex items-start gap-3">
                <Checkbox
                  checked={retryWebSearch}
                  disabled={retry.isPending || !retryCanUseNetworkSearch}
                  id="retry-web-search"
                  onCheckedChange={(checked) =>
                    setRetryWebSearch(checked === true)
                  }
                />
                <Label
                  className="grid cursor-pointer gap-1 font-normal"
                  htmlFor="retry-web-search"
                >
                  <span className="font-medium">启用联网搜索</span>
                  <span className="text-xs text-muted-foreground">
                    {retryCanUseNetworkSearch
                      ? "由后端按模型能力与已授权 Search Provider 选择真实搜索路由。"
                      : "当前模型与工作区没有可用的真实搜索能力。"}
                  </span>
                </Label>
              </div>
              {retryWebSearch && retryCanUseNetworkSearch ? (
                <div className="grid gap-2">
                  <Label htmlFor="retry-allowed-domains">限定来源域名（可选）</Label>
                  <Input
                    disabled={retry.isPending}
                    id="retry-allowed-domains"
                    onChange={(event) =>
                      setRetryAllowedDomains(event.currentTarget.value)
                    }
                    placeholder="例如：docs.python.org, react.dev"
                    value={retryAllowedDomains}
                  />
                  <p className="text-xs text-muted-foreground">
                    使用逗号或空格分隔；只允许这些域名及其子域的结果。
                  </p>
                </div>
              ) : null}
            </div>
          </div>
          <DialogFooter>
            <Button
              disabled={retry.isPending}
              onClick={() => setRetryTarget(null)}
              variant="outline"
            >
              取消
            </Button>
            <Button
              disabled={
                retry.isPending ||
                !retryTarget ||
                !retryProvider ||
                !retrySelectedModel?.remote ||
                (retryResponseMode === "thinking" &&
                  !retryThinkingModes.includes(retryThinkingMode)) ||
                (retryResponseMode === "agentic" && !retrySupportsAgentMode)
              }
              onClick={submitRetry}
            >
              {retry.isPending ? (
                <LoaderCircle className="size-4 animate-spin" />
              ) : (
                <RefreshCcw className="size-4" />
              )}
              {retry.isPending ? "正在生成新版本…" : "开始重试"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        onOpenChange={(open) => {
          if (!closeSessionMutation.isPending) setCloseDialogOpen(open);
        }}
        open={closeDialogOpen}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>结束本次学习并复盘？</DialogTitle>
            <DialogDescription>
              结束后将停止向这个会话提交新消息，并立即按同一掌握度规则复盘已关联的学习证据。既有消息、版本、附件和证据会保留；如需继续探索，可从现有回答创建分支或新会话。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              disabled={closeSessionMutation.isPending}
              onClick={() => setCloseDialogOpen(false)}
              variant="outline"
            >
              继续学习
            </Button>
            <Button
              disabled={closeSessionMutation.isPending}
              onClick={() => closeSessionMutation.mutate()}
            >
              {closeSessionMutation.isPending ? (
                <LoaderCircle className="size-4 animate-spin" />
              ) : (
                <Check className="size-4" />
              )}
              确认结束并复盘
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        onOpenChange={(open) => {
          if (!open && !reviewGraphChange.isPending) {
            setRejectProposalId(null);
            setRejectReason("");
          }
        }}
        open={Boolean(rejectProposalId)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>拒绝这份图谱提案？</DialogTitle>
            <DialogDescription>
              拒绝只会结束本次待审提案，不会修改正式图谱。理由可选，便于后续对话继续调整。
            </DialogDescription>
          </DialogHeader>
          <Textarea
            aria-label="拒绝图谱提案的理由"
            disabled={reviewGraphChange.isPending}
            onChange={(event) => setRejectReason(event.currentTarget.value)}
            placeholder="例如：节点粒度太粗，请拆分后重新生成（可不填）"
            value={rejectReason}
          />
          <DialogFooter>
            <Button
              disabled={reviewGraphChange.isPending}
              onClick={() => {
                setRejectProposalId(null);
                setRejectReason("");
              }}
              variant="outline"
            >
              取消
            </Button>
            <Button
              disabled={!rejectProposalId || reviewGraphChange.isPending}
              onClick={() => {
                if (!rejectProposalId) return;
                reviewGraphChange.mutate({
                  decision: "reject",
                  proposalId: rejectProposalId,
                  reason: rejectReason.trim(),
                });
              }}
              variant="destructive"
            >
              {reviewGraphChange.isPending ? "正在拒绝…" : "确认拒绝"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export function VersionsPage() {
  const { sessionId = "" } = useParams();
  const queryClient = useQueryClient();
  const [messageId, setMessageId] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [draftId, setDraftId] = useState("");
  const [draftContent, setDraftContent] = useState("");
  const messages = useQuery({
    queryKey: ["messages", sessionId],
    queryFn: () => listSessionMessages(sessionId),
    enabled: Boolean(sessionId),
  });
  const assistants = (messages.data ?? []).filter(
    (item) => item.role === "assistant",
  );
  const targetId = messageId || assistants.at(-1)?.id || "";
  const versions = useQuery({
    queryKey: ["message-versions", sessionId, targetId],
    queryFn: () => listMessageVersions(sessionId, targetId),
    enabled: Boolean(targetId),
  });
  const create = useMutation({
    mutationFn: () => createCompositeDraft(targetId, selected),
    onSuccess: (draft) => {
      setDraftId(draft.id);
      setDraftContent(draft.content);
    },
    onError: (error) => toast.error(error.message),
  });
  const confirm = useMutation({
    mutationFn: () => confirmCompositeDraft(draftId),
    onSuccess: () => {
      toast.success("合并版本已保存并设为当前版本");
      setDraftId("");
      setSelected([]);
      void queryClient.invalidateQueries({ queryKey: ["messages", sessionId] });
      void versions.refetch();
    },
    onError: (error) => toast.error(error.message),
  });
  return (
    <PageFrame>
      <PageIntro
        description="重试、换模型和创建分支都产生新版本或新 Session，原回答与 Provider Trace 不会被覆盖。"
        eyebrow="Session history"
        title="会话分支与版本对比"
      />
      <Surface className="p-5">
        <SectionHeading title="选择助手消息" />
        <Select
          onValueChange={(value) => {
            setMessageId(value);
            setSelected([]);
          }}
          value={targetId}
        >
          <SelectTrigger className="mt-4">
            <SelectValue placeholder="选择消息" />
          </SelectTrigger>
          <SelectContent>
            {assistants.map((item) => (
              <SelectItem key={item.id} value={item.id}>
                {item.content.slice(0, 80) || item.id}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Surface>
      <Surface className="p-5">
        <SectionHeading
          action={
            <Button
              disabled={selected.length < 2 || create.isPending}
              onClick={() => create.mutate()}
              size="sm"
            >
              <GitCompareArrows className="size-4" />
              合并所选版本
            </Button>
          }
          title="真实版本"
        />
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          {versions.data?.map((version) => (
            <label className="rounded-xl border p-4" key={version.id}>
              <div className="flex items-center gap-3">
                <input
                  checked={selected.includes(version.id)}
                  onChange={(event) =>
                    setSelected((current) =>
                      event.target.checked
                        ? [...current, version.id]
                        : current.filter((id) => id !== version.id),
                    )
                  }
                  type="checkbox"
                />
                <p className="text-sm font-semibold">v{version.version}</p>
                <StatePill label={version.status} status={version.status} />
              </div>
              <div className="mt-4 font-mono text-[11px] text-muted-foreground">
                {String(version.provider_trace.provider_id ?? "unknown")} ·{" "}
                {version.id}
              </div>
            </label>
          ))}
        </div>
        {!versions.data?.length ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            该消息还没有可比较版本。
          </p>
        ) : null}
      </Surface>
      <Dialog
        onOpenChange={(open) => {
          if (!open) setDraftId("");
        }}
        open={Boolean(draftId)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认合并后的新版本</DialogTitle>
            <DialogDescription>
              来源版本会永久保留；确认后新版本自动成为当前展示版本。
            </DialogDescription>
          </DialogHeader>
          <Textarea className="min-h-64" readOnly value={draftContent} />
          <div className="flex justify-end gap-2">
            <Button onClick={() => setDraftId("")} variant="outline">
              取消
            </Button>
            <Button
              disabled={confirm.isPending}
              onClick={() => confirm.mutate()}
            >
              {confirm.isPending ? "保存中…" : "确认并设为当前版本"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </PageFrame>
  );
}
