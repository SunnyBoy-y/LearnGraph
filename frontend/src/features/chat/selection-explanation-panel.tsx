import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  ArrowUp,
  Bot,
  Brain,
  ChevronDown,
  ExternalLink,
  LoaderCircle,
  MessageSquareText,
  Search,
  Sparkles,
  Square,
  Zap,
} from "lucide-react";
import { toast } from "sonner";

import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import {
  Message as AiMessage,
  MessageContent,
} from "@/components/ai-elements/message";
import { MessagePartRenderer } from "@/components/chat/message-part-renderer";
import {
  groupAnswerParts,
  QuestionSetPager,
} from "@/components/chat/question-set-pager";
import { SandboxImageStrip } from "@/components/chat/sandbox-image-artifact";
import { ThinkingChain } from "@/components/chat/thinking-chain";
import {
  groupPartsForDisplay,
  thinkingDurationSeconds,
} from "@/features/chat/chat-message-parts";
import {
  bindExplanationSession,
  buildSelectionExplainPrompt,
  createSelectionExplanationId,
  getSelectionExplanation,
  inferSelectionAction,
  type SelectionExplainAction,
  type SelectionExplanationOpenDetail,
  type SelectionExplanationRecord,
  upsertSelectionExplanation,
} from "@/features/chat/selection-explanation";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { discoverProviderModels, listProviders } from "@/api/providers";
import { getAgentSandboxReadiness } from "@/api/control";
import { workspaceQueryKey } from "@/lib/query-keys";
import {
  cancelSessionMessage,
  createSession,
  listSessionMessages,
  listSessions,
  streamSessionMessage,
} from "@/api/sessions";
import {
  capabilityThinkingModes,
  fuzzyModelMatch,
  modelChoiceValue,
  modelProtocolLabel,
  parseModelChoiceValue,
  providerModelOptions,
  thinkingLabels,
} from "@/lib/model-choices";
import {
  getSessionComposerPrefs,
  setSessionComposerPrefs,
  type ResponseMode,
  type ThinkingMode,
} from "@/lib/session-composer-prefs";
import { createUuid } from "@/lib/uuid";
import { isDeepSeekProvider, isModelProviderType } from "@/types/providers";
import type {
  Message,
  MessageCreateRequest,
  MessagePart,
  Session,
  SessionMessageStreamData,
} from "@/types/sessions";

type ChatStatus = "ready" | "submitted" | "streaming" | "error";

function appendPart(parts: MessagePart[], incoming: MessagePart): MessagePart[] {
  const index = parts.findIndex((part) => part.id === incoming.id);
  if (index === -1) {
    return [
      ...parts,
      {
        ...incoming,
        content: incoming.content ?? incoming.content_delta ?? "",
      },
    ];
  }
  return parts.map((part, partIndex) =>
    partIndex === index
      ? {
          ...part,
          ...incoming,
          content:
            incoming.content ??
            `${part.content ?? ""}${incoming.content_delta ?? ""}`,
        }
      : part,
  );
}

function isMessagePart(value: unknown): value is MessagePart {
  return Boolean(
    value &&
      typeof value === "object" &&
      typeof (value as MessagePart).id === "string" &&
      typeof (value as MessagePart).type === "string" &&
      typeof (value as MessagePart).status === "string",
  );
}

function streamData(value: SessionMessageStreamData): Record<string, unknown> {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : {};
}

function streamEventType(data: Record<string, unknown>) {
  return typeof data.type === "string"
    ? data.type
    : typeof data.event === "string"
      ? data.event
      : "";
}

function messageParts(message: Message): MessagePart[] {
  if (message.parts.length) return message.parts;
  return message.content
    ? [
        {
          id: `${message.id}-text`,
          type: "text",
          status: "completed",
          content: message.content,
        },
      ]
    : [];
}

function responseModeLabel(mode: ResponseMode) {
  return mode === "fast" ? "极速" : mode === "agentic" ? "智能体" : "思考";
}

function ensureRecord(
  detail: SelectionExplanationOpenDetail,
  parentSessionId: string,
): SelectionExplanationRecord {
  if (detail.recordId) {
    const existing = getSelectionExplanation(parentSessionId, detail.recordId);
    if (existing) return existing;
  }
  const action =
    detail.action ?? inferSelectionAction(detail.selectedText);
  const record: SelectionExplanationRecord = {
    id: detail.recordId ?? createSelectionExplanationId(),
    parentSessionId,
    sourceMessageId: detail.sourceMessageId,
    selectedText: detail.selectedText.trim(),
    prefix: detail.prefix ?? "",
    suffix: detail.suffix ?? "",
    contentMatched: detail.contentMatched ?? false,
    action,
    explanationSessionId: detail.explanationSessionId,
    createdAt: new Date().toISOString(),
  };
  return upsertSelectionExplanation(record);
}

export type SelectionExplanationPanelProps = {
  detail: SelectionExplanationOpenDetail;
  parentSessionId: string;
  creationParentSessionId?: string;
  workspaceId: string;
};

export function SelectionExplanationPanel({
  detail,
  parentSessionId,
  creationParentSessionId = parentSessionId,
  workspaceId,
}: SelectionExplanationPanelProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [record, setRecord] = useState<SelectionExplanationRecord>(() =>
    ensureRecord(detail, parentSessionId),
  );
  const [sessionId, setSessionId] = useState(record.explanationSessionId ?? "");
  const [draft, setDraft] = useState("");
  const [localMessages, setLocalMessages] = useState<Message[]>([]);
  const [status, setStatus] = useState<ChatStatus>("ready");
  const abortRef = useRef<AbortController | null>(null);
  const activeMessageId = useRef<string | null>(null);
  const activeStreamSessionId = useRef("");
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const createdSessionRef = useRef("");
  const autoSubmittedRef = useRef("");
  const sendRef = useRef<(content: string) => Promise<void>>(async () => undefined);

  const initialPrefs = getSessionComposerPrefs(
    record.explanationSessionId || parentSessionId,
  );
  const [selectedProviderId, setSelectedProviderId] = useState(
    () => initialPrefs.providerId ?? "",
  );
  const [selectedModelId, setSelectedModelId] = useState(
    () => initialPrefs.modelId ?? "",
  );
  const [responseMode, setResponseMode] = useState<ResponseMode>(
    () => initialPrefs.responseMode,
  );
  const [thinkingMode, setThinkingMode] = useState<ThinkingMode>(
    () => initialPrefs.thinkingMode,
  );
  const [modelSearch, setModelSearch] = useState("");

  const providers = useQuery({ queryKey: workspaceQueryKey(workspaceId, "providers"), queryFn: listProviders });
  const sessions = useQuery({ queryKey: workspaceQueryKey(workspaceId, "sessions"), queryFn: listSessions });
  const history = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "messages", sessionId),
    queryFn: () => listSessionMessages(sessionId, { limit: 50 }),
    enabled: Boolean(sessionId),
    gcTime: 15_000,
  });

  const modelProviders = useMemo(
    () =>
      (providers.data ?? []).filter(
        (provider) =>
          provider.enabled &&
          provider.remote_capability &&
          isModelProviderType(provider.provider_type),
      ),
    [providers.data],
  );
  const providerModelQueries = useQueries({
    queries: modelProviders.map((provider) => ({
      queryKey: workspaceQueryKey(workspaceId, "provider-models", provider.id),
      queryFn: () => discoverProviderModels(provider.id),
      retry: false,
    })),
  });
  const activeProvider =
    modelProviders.find((provider) => provider.id === selectedProviderId) ??
    modelProviders[0];
  const activeProviderIndex = modelProviders.findIndex(
    (provider) => provider.id === activeProvider?.id,
  );
  const discoveredModels =
    activeProviderIndex >= 0
      ? providerModelQueries[activeProviderIndex]
      : undefined;
  const modelOptions = useMemo(
    () => providerModelOptions(activeProvider, discoveredModels?.data?.models),
    [activeProvider, discoveredModels?.data?.models],
  );
  const selectedModel =
    modelOptions.find((model) => model.id === selectedModelId) ?? modelOptions[0];
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
  const filteredModelChoices = useMemo(
    () =>
      availableModelChoices.filter(({ provider, model }) =>
        fuzzyModelMatch(
          `${model.id} ${provider.display_name} ${modelProtocolLabel(provider.provider_type)}`,
          modelSearch,
        ),
      ),
    [availableModelChoices, modelSearch],
  );
  const thinkingModes = useMemo(
    () =>
      capabilityThinkingModes(
        selectedModel?.capabilities?.reasoning_efforts ??
          activeProvider?.capabilities.reasoning_efforts ??
          (activeProvider && isDeepSeekProvider(activeProvider)
            ? ["low", "medium", "high", "xhigh"]
            : undefined),
      ),
    [activeProvider, selectedModel?.capabilities?.reasoning_efforts],
  );
  const thinkingRequired =
    selectedModel?.capabilities?.thinking_required === true;
  const supportsThinkingMode = thinkingModes.length > 0;
  const effectiveThinkingMode =
    responseMode === "fast" && !thinkingRequired
      ? "off"
      : thinkingModes.includes(thinkingMode)
        ? thinkingMode
        : (thinkingModes[0] ?? "off");
  const activeSession = sessions.data?.find((item) => item.id === sessionId);
  const messages = useMemo(
    () => [...(history.data ?? []), ...localMessages],
    [history.data, localMessages],
  );

  // Rebind when the parent opens a different selection record.
  // NOTE: autoSubmittedRef is intentionally NOT reset here. The auto-send guard
  // key includes record.id and action, so a genuinely new record will always get
  // its own key (no match → fires). Resetting here causes a double-send in React
  // StrictMode because the second mount cycle clears the guard right before the
  // auto-send effect re-runs.
  useEffect(() => {
    const next = ensureRecord(detail, parentSessionId);
    setRecord(next);
    setSessionId(next.explanationSessionId ?? "");
    setLocalMessages([]);
    setStatus("ready");
    setDraft("");
    const prefs = getSessionComposerPrefs(
      next.explanationSessionId || parentSessionId,
    );
    if (prefs.providerId) setSelectedProviderId(prefs.providerId);
    if (prefs.modelId) setSelectedModelId(prefs.modelId);
    setResponseMode(prefs.responseMode);
    setThinkingMode(prefs.thinkingMode);
  }, [
    detail.recordId,
    detail.sourceMessageId,
    detail.selectedText,
    detail.action,
    detail.explanationSessionId,
    parentSessionId,
  ]);

  useEffect(() => {
    setSelectedProviderId((current) =>
      modelProviders.some((provider) => provider.id === current)
        ? current
        : (modelProviders[0]?.id ?? ""),
    );
  }, [modelProviders]);

  useEffect(() => {
    setSelectedModelId((current) =>
      modelOptions.some((model) => model.id === current)
        ? current
        : (modelOptions[0]?.id ?? ""),
    );
  }, [modelOptions]);

  useEffect(() => {
    setThinkingMode((current) => {
      if (responseMode === "fast" && !thinkingRequired) return "off";
      if (!thinkingModes.length) return current === "off" ? "off" : current;
      if (current === "off" && thinkingRequired) {
        return thinkingModes.includes("medium") ? "medium" : thinkingModes[0]!;
      }
      return thinkingModes.includes(current)
        ? current
        : (thinkingModes[0] ?? "off");
    });
  }, [responseMode, thinkingModes, thinkingRequired]);

  function persistComposerPrefs(
    targetSessionId: string,
    overrides: {
      providerId?: string;
      modelId?: string;
      responseMode?: ResponseMode;
      thinkingMode?: ThinkingMode;
    } = {},
  ) {
    if (!targetSessionId) return;
    setSessionComposerPrefs(targetSessionId, {
      providerId: overrides.providerId ?? activeProvider?.id,
      modelId: overrides.modelId ?? selectedModel?.id,
      responseMode: overrides.responseMode ?? responseMode,
      thinkingMode: overrides.thinkingMode ?? thinkingMode,
    });
  }

  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  async function ensureExplanationSession(): Promise<string> {
    if (sessionId) return sessionId;
    const titleSeed = record.selectedText.replace(/\s+/gu, " ").slice(0, 24);
    const created = await createSession({
      title: `划词解释 · ${titleSeed || "选区"}`,
      memory_enabled: false,
      parent_session_id: creationParentSessionId,
      session_kind: "side",
      project_id: sessions.data?.find((item) => item.id === parentSessionId)
        ?.project_id,
    });
    createdSessionRef.current = created.id;
    setSessionId(created.id);
    const bound = bindExplanationSession(parentSessionId, record.id, created.id);
    if (bound) setRecord(bound);
    queryClient.setQueryData<Session[]>(workspaceQueryKey(workspaceId, "sessions"), (current) => [
      created,
      ...(current ?? []).filter((item) => item.id !== created.id),
    ]);
    persistComposerPrefs(created.id);
    return created.id;
  }

  async function send(event?: FormEvent, contentOverride?: string) {
    event?.preventDefault();
    const content = contentOverride?.trim() || draft.trim();
    if (!content || status === "submitted" || status === "streaming") return;
    if (!activeProvider || !selectedModel?.id) {
      toast.error("没有可用的真实模型 Provider，无法发送划词解释。");
      return;
    }
    if (responseMode === "agentic") {
      try {
        const readiness = await getAgentSandboxReadiness();
        if (!readiness.available) {
          toast.message("沙箱工具暂不可用", {
            description: [
              readiness.message,
              "划词解释仍可继续；需要代码/文件沙箱时请稍后再试。",
              readiness.remediation_steps[0],
            ]
              .filter(Boolean)
              .join(" "),
          });
        }
      } catch (error) {
        toast.message("无法检查智能体沙箱状态", {
          description:
            error instanceof Error
              ? `${error.message} 将继续发送。`
              : "将继续发送；沙箱工具可能不可用。",
        });
      }
    }

    let targetSessionId: string;
    try {
      targetSessionId = await ensureExplanationSession();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "创建解释会话失败");
      return;
    }
    persistComposerPrefs(targetSessionId);

    const stamp = Date.now();
    // Independent explanation sessions do not share the parent timeline, so
    // selection_context (which validates against the *current* session) cannot
    // be sent. Embed the quote as a local part and keep the prompt self-contained.

    const optimisticUser: Message = {
      id: `explain-user-${stamp}`,
      workspace_id: workspaceId,
      session_id: targetSessionId,
      parent_message_id: null,
      role: "user",
      version: 1,
      status: "completed",
      content,
      parts: [
        {
          id: `explain-user-text-${stamp}`,
          type: "text",
          status: "completed",
          content,
        },
        {
          id: `explain-quote-${stamp}`,
          type: "selection_quote",
          status: "completed",
          content: record.selectedText,
          data: {
            source_role: "assistant",
            source_message_id: record.sourceMessageId,
            parent_session_id: parentSessionId,
            action: record.action,
          },
        },
      ],
      provider_trace: {},
      created_at: new Date().toISOString(),
    };
    const optimisticAssistant: Message = {
      id: `explain-assistant-${stamp}`,
      workspace_id: workspaceId,
      session_id: targetSessionId,
      parent_message_id: optimisticUser.id,
      role: "assistant",
      version: 1,
      status: "streaming",
      content: "",
      parts: [],
      provider_trace: {},
      created_at: new Date().toISOString(),
    };
    setLocalMessages((current) => [...current, optimisticUser, optimisticAssistant]);
    setDraft("");
    setStatus("submitted");

    const controller = new AbortController();
    abortRef.current = controller;
    activeStreamSessionId.current = targetSessionId;
    const request: MessageCreateRequest = {
      content,
      provider_id: activeProvider.id,
      model_id: selectedModel.id,
      thinking_mode: effectiveThinkingMode,
      agent_mode: responseMode === "agentic",
      generation_mode: "text",
      search_route: "disabled",
      web_search: false,
      graph_action: "none",
    };
    const idempotencyKey = `selection-explain-${createUuid()}`;
    const seenEventIds = new Set<string>();
    let lastEventId: string | undefined;
    let completed = false;

    try {
      for (let attempt = 0; attempt < 3; attempt += 1) {
        let transientError: unknown;
        let terminalFailure = "";
        try {
          for await (const item of streamSessionMessage(targetSessionId, request, {
            signal: controller.signal,
            seenEventIds,
            headers: {
              "Idempotency-Key": idempotencyKey,
              ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
            },
          })) {
            if (item.id) lastEventId = item.id;
            setStatus("streaming");
            const data = streamData(item.data);
            const type = streamEventType(data);
            if (typeof data.message_id === "string") {
              activeMessageId.current = data.message_id;
            }
            if (type === "message.completed") completed = true;
            if (type === "message.failed") {
              const eventPayload =
                typeof data.payload === "object" && data.payload !== null
                  ? (data.payload as Record<string, unknown>)
                  : {};
              const errorPayload =
                typeof eventPayload.error === "object" &&
                eventPayload.error !== null
                  ? (eventPayload.error as Record<string, unknown>)
                  : typeof data.error === "object" && data.error !== null
                    ? (data.error as Record<string, unknown>)
                    : null;
              const errorMessage =
                typeof errorPayload?.message === "string"
                  ? errorPayload.message
                  : typeof eventPayload.message === "string"
                    ? eventPayload.message
                    : typeof data.message === "string"
                      ? data.message
                      : null;
              terminalFailure = errorMessage
                ? `划词解释失败：${errorMessage}`
                : "划词解释失败";
            }
            if (type === "message.cancelled") terminalFailure = "生成已取消。";
            setLocalMessages((current) =>
              current.map((message) => {
                if (message.id !== optimisticAssistant.id) return message;
                if (type === "message.completed") {
                  return {
                    ...message,
                    id: String(data.message_id ?? message.id),
                    status: "completed",
                    provider_trace: (data.provider_trace ??
                      {}) as Record<string, unknown>,
                  };
                }
                if (type === "message.failed" || type === "message.cancelled") {
                  return {
                    ...message,
                    id: String(data.message_id ?? message.id),
                    status: "failed",
                  };
                }
                return isMessagePart(data.part)
                  ? { ...message, parts: appendPart(message.parts, data.part) }
                  : message;
              }),
            );
          }
        } catch (error) {
          if (controller.signal.aborted) throw error;
          transientError = error;
        }
        if (completed) break;
        if (terminalFailure) throw new Error(terminalFailure);
        if (attempt === 2) throw transientError ?? new Error("消息流中断");
      }
      if (!completed) throw new Error("消息流结束但没有收到完成事件。");
      await queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "messages", targetSessionId),
      });
      void queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "sessions") });
      setLocalMessages([]);
      setStatus("ready");
      activeMessageId.current = null;
    } catch (error) {
      if (controller.signal.aborted) {
        setStatus("ready");
      } else {
        setStatus("error");
        toast.error(error instanceof Error ? error.message : "划词解释失败");
        setLocalMessages((current) =>
          current.map((message) =>
            message.id === optimisticAssistant.id
              ? {
                  ...message,
                  status: "failed",
                  parts: [
                    ...message.parts,
                    {
                      id: `explain-error-${stamp}`,
                      type: "error",
                      status: "failed",
                      content:
                        error instanceof Error ? error.message : "划词解释失败",
                    },
                  ],
                }
              : message,
          ),
        );
      }
    } finally {
      abortRef.current = null;
      activeMessageId.current = null;
      activeStreamSessionId.current = "";
    }
  }
  sendRef.current = (content) => send(undefined, content);

  // Auto-send define/explain on first open of a fresh record.
  useEffect(() => {
    if (!activeProvider || !selectedModel?.id) return;
    if (sessionId && history.isPending) return;
    if (messages.length > 0) return;
    if (status !== "ready") return;
    const autoKey = `${record.id}:${record.action}`;
    if (autoSubmittedRef.current === autoKey) return;
    autoSubmittedRef.current = autoKey;
    const prompt = buildSelectionExplainPrompt(record.action, record.selectedText);
    void sendRef.current(prompt);
  }, [
    activeProvider,
    selectedModel?.id,
    sessionId,
    history.isPending,
    messages.length,
    status,
    record.id,
    record.action,
    record.selectedText,
  ]);

  async function stop() {
    const messageId = activeMessageId.current;
    const streamSessionId = activeStreamSessionId.current || sessionId;
    if (streamSessionId && messageId) {
      await cancelSessionMessage(streamSessionId, messageId).catch((error: Error) =>
        toast.error(error.message),
      );
    }
    abortRef.current?.abort();
    setStatus("ready");
  }

  function setMode(mode: ResponseMode) {
    setResponseMode(mode);
    if (mode === "fast") setThinkingMode("off");
    else if (mode === "thinking" && thinkingMode === "off" && thinkingModes.length) {
      setThinkingMode(
        thinkingModes.includes("medium") ? "medium" : thinkingModes[0]!,
      );
    }
    if (sessionId) {
      persistComposerPrefs(sessionId, {
        responseMode: mode,
        thinkingMode:
          mode === "fast"
            ? "off"
            : thinkingMode === "off" && thinkingModes.length
              ? thinkingModes.includes("medium")
                ? "medium"
                : thinkingModes[0]!
              : thinkingMode,
      });
    }
  }

  function setAction(action: SelectionExplainAction) {
    const next = upsertSelectionExplanation({ ...record, action });
    setRecord(next);
  }

  const busy = status === "submitted" || status === "streaming";
  const sessionClosed = activeSession?.status === "closed";

  return (
    <section
      aria-label="划词解释独立画布"
      className="selection-explanation-panel flex h-full min-h-0 flex-col"
    >
      <div className="selection-explanation-rail__quote selection-explanation-panel__quote">
        <div className="flex items-center justify-between gap-2">
          <span>选中内容</span>
          <div className="flex items-center gap-1">
            <Button
              aria-pressed={record.action === "define"}
              className="h-6 px-2 text-[10px]"
              onClick={() => setAction("define")}
              size="xs"
              type="button"
              variant={record.action === "define" ? "secondary" : "ghost"}
            >
              定义
            </Button>
            <Button
              aria-pressed={record.action === "explain"}
              className="h-6 px-2 text-[10px]"
              onClick={() => setAction("explain")}
              size="xs"
              type="button"
              variant={record.action === "explain" ? "secondary" : "ghost"}
            >
              解释
            </Button>
          </div>
        </div>
        <p>{record.selectedText}</p>
      </div>

      <div className="selection-explanation-panel__toolbar">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              aria-label="选择响应模式"
              className="h-7 gap-1 px-2"
              disabled={busy}
              size="xs"
              variant="outline"
            >
              {responseMode === "fast" ? (
                <Zap className="size-3" />
              ) : responseMode === "agentic" ? (
                <Bot className="size-3" />
              ) : (
                <Brain className="size-3" />
              )}
              <span className="text-[10px]">{responseModeLabel(responseMode)}</span>
              <ChevronDown className="size-3" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-40">
            <DropdownMenuLabel>响应模式</DropdownMenuLabel>
            <DropdownMenuRadioGroup
              onValueChange={(value) => setMode(value as ResponseMode)}
              value={responseMode}
            >
              <DropdownMenuRadioItem value="fast">
                <Zap className="mr-2 size-3.5" />
                极速
              </DropdownMenuRadioItem>
              <DropdownMenuRadioItem value="thinking">
                <Brain className="mr-2 size-3.5" />
                思考
              </DropdownMenuRadioItem>
              <DropdownMenuRadioItem value="agentic">
                <Bot className="mr-2 size-3.5" />
                智能体
              </DropdownMenuRadioItem>
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>

        <DropdownMenu
          onOpenChange={(open) => {
            if (!open) setModelSearch("");
          }}
        >
          <DropdownMenuTrigger asChild>
            <Button
              aria-label="选择模型与思考力度"
              className="max-w-[9.5rem] gap-1 px-2"
              disabled={!modelProviders.length || busy}
              size="xs"
              variant="outline"
            >
              <span className="truncate font-mono text-[10px]">
                {selectedModel?.id ?? "选择模型"}
              </span>
              <ChevronDown className="size-3 flex-none" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-64" collisionPadding={12}>
            <DropdownMenuLabel>思考力度</DropdownMenuLabel>
            <DropdownMenuRadioGroup
              onValueChange={(value) => {
                const mode = value as ThinkingMode;
                setThinkingMode(mode);
                if (mode !== "off" && responseMode === "fast") {
                  setResponseMode("thinking");
                }
                if (sessionId) {
                  persistComposerPrefs(sessionId, {
                    thinkingMode: mode,
                    responseMode:
                      mode === "off" && responseMode !== "agentic"
                        ? "fast"
                        : responseMode === "fast"
                          ? "thinking"
                          : responseMode,
                  });
                }
              }}
              value={
                responseMode === "fast" && !thinkingRequired
                  ? "off"
                  : supportsThinkingMode
                    ? effectiveThinkingMode
                    : "off"
              }
            >
              <DropdownMenuRadioItem disabled={thinkingRequired} value="off">
                关闭{thinkingRequired ? "（该模型仅支持思考）" : ""}
              </DropdownMenuRadioItem>
              {thinkingModes.map((mode) => (
                <DropdownMenuRadioItem key={mode} value={mode}>
                  {thinkingLabels[mode]}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
            {!thinkingModes.length ? (
              <p className="px-2 pb-1 text-[10px] text-muted-foreground">
                当前模型未声明推理能力，按服务商默认执行。
              </p>
            ) : null}
            <DropdownMenuSeparator />
            <DropdownMenuLabel>模型</DropdownMenuLabel>
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
            <div className="max-h-[min(50vh,20rem)] overflow-y-auto">
              {filteredModelChoices.length ? (
                <DropdownMenuRadioGroup
                  onValueChange={(value) => {
                    const choice = parseModelChoiceValue(value);
                    if (!choice) return;
                    setSelectedProviderId(choice.providerId);
                    setSelectedModelId(choice.modelId);
                    if (sessionId) {
                      persistComposerPrefs(sessionId, {
                        providerId: choice.providerId,
                        modelId: choice.modelId,
                      });
                    }
                  }}
                  value={
                    activeProvider && selectedModel
                      ? modelChoiceValue(activeProvider.id, selectedModel.id)
                      : ""
                  }
                >
                  {filteredModelChoices.map(({ provider, model }) => (
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
                    </DropdownMenuRadioItem>
                  ))}
                </DropdownMenuRadioGroup>
              ) : (
                <DropdownMenuItem disabled>
                  {discoveredModels?.isPending ? "正在载入模型…" : "暂无可用模型"}
                </DropdownMenuItem>
              )}
            </div>
          </DropdownMenuContent>
        </DropdownMenu>

        <Button
          aria-label="在完整会话画布中打开"
          className="ml-auto"
          disabled={!sessionId || busy}
          onClick={() => {
            if (!sessionId) return;
            persistComposerPrefs(sessionId);
            navigate(`/w/${workspaceId}/chat/${sessionId}`);
          }}
          size="icon-xs"
          variant="ghost"
        >
          <ExternalLink className="size-3.5" />
        </Button>
      </div>

      <Conversation className="min-h-0 flex-1">
        <ConversationContent className="gap-4 px-3 py-3">
          {history.isPending && sessionId ? (
            <div className="grid min-h-24 place-items-center">
              <LoaderCircle className="size-4 animate-spin" />
            </div>
          ) : null}
          {!messages.length && !history.isPending ? (
            <ConversationEmptyState
              className="min-h-40 px-4"
              description="会自动按「定义 / 解释」方式生成首答，也可继续追问。"
              icon={<Sparkles className="size-4" />}
              title={
                busy
                  ? record.action === "define"
                    ? "正在定义…"
                    : "正在解释…"
                  : "独立解释上下文"
              }
            />
          ) : null}
          {messages.map((message) => {
            const parts = messageParts(message);
            const streaming = message.status === "streaming";
            const renderPart = (part: MessagePart) => (
              <MessagePartRenderer
                key={part.id}
                part={part}
                siblingParts={parts}
                streaming={streaming}
              />
            );
            const renderAnswerParts = (answerParts: MessagePart[]) =>
              groupAnswerParts(answerParts).map((group) => {
                if (group.kind === "question_set") {
                  return (
                    <QuestionSetPager
                      key={`question-set-${group.parts.map((part) => part.id).join("-")}`}
                      questions={group.questions}
                    />
                  );
                }
                if (group.kind === "image_strip") {
                  return (
                    <SandboxImageStrip
                      key={`image-strip-${group.parts[0]?.id ?? "empty"}`}
                      parts={group.parts}
                    />
                  );
                }
                return renderPart(group.part);
              });
            return (
              <AiMessage
                from={message.role === "user" ? "user" : "assistant"}
                key={message.id}
              >
                <MessageContent
                  className={
                    message.role === "assistant" ? "w-full gap-2" : undefined
                  }
                >
                  {groupPartsForDisplay(parts).map((segment, index) =>
                    segment.kind === "chain" ? (
                      <ThinkingChain
                        chainParts={segment.parts}
                        completedDurationSec={thinkingDurationSeconds(
                          message.provider_trace,
                        )}
                        key={`chain-${message.id}-${index}`}
                        messageStatus={message.status}
                        startedAt={
                          typeof message.provider_trace.generation_started_at ===
                          "string"
                            ? message.provider_trace.generation_started_at
                            : message.created_at
                        }
                      >
                        {segment.parts.map(renderPart)}
                      </ThinkingChain>
                    ) : (
                      <div
                        className="message-answer-segment"
                        key={`parts-${message.id}-${index}`}
                      >
                        {renderAnswerParts(segment.parts)}
                      </div>
                    ),
                  )}
                </MessageContent>
              </AiMessage>
            );
          })}
        </ConversationContent>
        <ConversationScrollButton />
      </Conversation>

      <form
        className="selection-explanation-panel__composer"
        onSubmit={(event) => void send(event)}
      >
        {!providers.isPending && !activeProvider ? (
          <div className="mb-2 flex items-center justify-between gap-2 text-[11px] text-amber-800">
            <span>没有可用的真实模型 Provider。</span>
            <Button
              onClick={() => navigate(`/w/${workspaceId}/settings/providers`)}
              size="xs"
              type="button"
              variant="outline"
            >
              配置
            </Button>
          </div>
        ) : null}
        {sessionClosed ? (
          <p className="mb-2 text-[11px] text-muted-foreground">
            该解释会话已结束。关闭后重新划词可再开一条。
          </p>
        ) : null}
        <div className="relative rounded-md border bg-background focus-within:ring-1 focus-within:ring-ring">
          <Textarea
            aria-label="针对这段内容继续提问"
            className="min-h-16 resize-none border-0 pb-10 shadow-none focus-visible:ring-0"
            disabled={!activeProvider || sessionClosed}
            onChange={(event) => setDraft(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void send();
              }
            }}
            placeholder="针对这段内容继续提问…"
            ref={composerRef}
            value={draft}
          />
          <div className="absolute inset-x-2 bottom-2 flex items-center justify-between gap-2">
            <span className="truncate text-[10px] text-muted-foreground">
              <MessageSquareText className="mr-1 inline size-3" />
              {responseModeLabel(responseMode)}
              {activeProvider ? ` · ${activeProvider.display_name}` : ""}
            </span>
            {busy ? (
              <Button
                aria-label="停止生成"
                onClick={() => void stop()}
                size="icon-sm"
                type="button"
                variant="secondary"
              >
                <Square
                  className="size-3.5"
                  color="#111"
                  fill="#111"
                  strokeWidth={0}
                />
              </Button>
            ) : (
              <Button
                aria-label="发送划词追问"
                disabled={!draft.trim() || !activeProvider || sessionClosed}
                size="icon-sm"
                type="submit"
              >
                <ArrowUp className="size-4" />
              </Button>
            )}
          </div>
        </div>
      </form>
    </section>
  );
}
