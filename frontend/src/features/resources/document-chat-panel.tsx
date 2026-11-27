import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { createUuid } from "@/lib/uuid";
import {
  ArrowUp,
  ChevronDown,
  ExternalLink,
  FileText,
  LoaderCircle,
  MessageSquareText,
  Plus,
  Square,
  X,
  Image as ImageIcon,
} from "lucide-react";
import { toast } from '@/lib/notifications';

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
import { ChatStreamPartRenderer } from "@/components/chat/chat-stream-part-renderer";
import {
  groupAnswerParts,
  QuestionSetPager,
} from "@/components/chat/question-set-pager";
import { SandboxImageStrip } from "@/components/chat/sandbox-image-artifact";
import { ThinkingChain } from "@/components/chat/thinking-chain";
import { MessageReading } from "@/components/chat/message-reading";
import { migrateThinkingPreference } from "@/lib/thinking-effort-migration";
import { PartUIStateProvider, patchMessagesUIState } from "@/lib/part-ui-state";
import { GenerationConfigMenu } from "@/components/chat/generation-config";
import { buildModelSelectorGroups } from "@/lib/generation-config";
import { ReasoningSummaryRow } from "@/components/chat/reasoning-summary-row";
import { StreamStatsBadge } from "@/components/chat/stream-stats-badge";
import {
  groupPartsForDisplay,
  isReasoningTextPart,
  thinkingDurationSeconds,
  toolCallCount,
} from "@/features/chat/chat-message-parts";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { discoverProviderModels, listProviders } from "@/api/providers";
import {
  capabilityThinkingModes,
  providerModelOptions,
  modelSupportsAgent,
  thinkingLabels,
} from "@/lib/model-choices";
import {
  setSessionComposerPrefs,
  getSessionComposerPrefs,
  type ResponseMode,
  type SessionComposerPrefs,
  type ThinkingMode,
} from "@/lib/session-composer-prefs";
import { getAgentSandboxReadiness } from "@/api/control";
import { isDeepSeekProvider, isModelProvider } from "@/types/providers";
import {
  cancelSessionMessage,
  createSession,
  listSessionMessages,
  streamSessionMessage,
} from "@/api/sessions";
import { lookupFile, uploadFile } from "@/api/files";
import { ApiError } from "@/api/client";
import { hashFileSha256 } from "@/lib/file-hash";
import { requiresLearningIndex } from "@/lib/file-preview";
import { useSessionDetail } from "@/lib/session-queries";
import { workspaceQueryKey } from "@/lib/query-keys";
import type { FileRecord } from "@/types/files";
import type {
  DocumentSelectionContext,
  Message,
  MessageCreateRequest,
  MessagePart,
  Session,
  SessionMessageStreamData,
} from "@/types/sessions";


type ChatStatus = "ready" | "submitted" | "streaming" | "error";


export interface PendingDocumentSelection {
  file_id: string;
  document_revision_id: string;
  /** Best-effort chunk anchor; null when the selection could not be resolved to a single chunk. */
  chunk_id: string | null;
  locator: Record<string, unknown>;
  locator_label: string;
  selected_text: string;
}


export type DocumentChatPreferences = Pick<
  SessionComposerPrefs,
  "providerId" | "modelId" | "responseMode" | "thinkingMode"
>;

interface DocumentChatPanelProps {
  autoSubmitSeed?: boolean;
  embeddedImages?: Array<{ id: string; blob: Blob; filename: string; locator: Record<string, unknown> }>;
  file: FileRecord;
  onSendToFullChat?: (content: string, preferences: DocumentChatPreferences) => Promise<void> | void;
  onRemoveEmbeddedImage?: (id: string) => void;
  onClearSelection: () => void;
  onSessionChange: (sessionId: string) => void;
  questionSeed?: { id: string; text: string };
  selection: PendingDocumentSelection | null;
  sessionId: string;
  /** 尚未开始对话时的提问建议；按当前文件能力生成。 */
  suggestions?: string[];
  workspaceId: string;
}

function EmbeddedImagePreview({ blob, filename }: { blob: Blob; filename: string }) {
  const [source, setSource] = useState("");
  useEffect(() => {
    const url = URL.createObjectURL(blob);
    setSource(url);
    return () => URL.revokeObjectURL(url);
  }, [blob]);
  return source ? <img alt={filename} className="max-h-[75svh] w-full object-contain" src={source} /> : null;
}


async function sha256(value: string) {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  );
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}


function appendPart(
  parts: MessagePart[],
  incoming: MessagePart,
  replaceData = false,
): MessagePart[] {
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
          data:
            replaceData && incoming.data !== undefined
              ? { ...incoming.data }
              : { ...(part.data ?? {}), ...(incoming.data ?? {}) },
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
    ? [{ id: `${message.id}-text`, type: "text", status: "completed", content: message.content }]
    : [];
}


export function DocumentChatPanel({
  autoSubmitSeed = false,
  embeddedImages = [],
  file,
  onSendToFullChat,
  onRemoveEmbeddedImage,
  onClearSelection,
  onSessionChange,
  questionSeed,
  selection,
  sessionId,
  suggestions = [],
  workspaceId,
}: DocumentChatPanelProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState("");
  const [localMessages, setLocalMessages] = useState<Message[]>([]);
  const [status, setStatus] = useState<ChatStatus>("ready");
  const abortRef = useRef<AbortController | null>(null);
  const activeMessageId = useRef<string | null>(null);
  const activeStreamSessionId = useRef("");
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const createdSessionRef = useRef("");
  const submittedSeedRef = useRef("");
  const sendRef = useRef<(content: string) => Promise<void>>(async () => undefined);
  const [previewImage, setPreviewImage] = useState<(typeof embeddedImages)[number] | null>(null);
  const [selectedProviderId, setSelectedProviderId] = useState(
    () => getSessionComposerPrefs(sessionId).providerId ?? "",
  );
  const [selectedModelId, setSelectedModelId] = useState(
    () => getSessionComposerPrefs(sessionId).modelId ?? "",
  );
  const [responseMode, setResponseMode] = useState<ResponseMode>(
    () => getSessionComposerPrefs(sessionId).responseMode,
  );
  const [thinkingMode, setThinkingMode] = useState<ThinkingMode>(
    () => getSessionComposerPrefs(sessionId).thinkingMode,
  );
  const [modelSearch, setModelSearch] = useState("");
  /** 响应与模型菜单是否展开：展开时胶囊显示「思考强度」，收起时显示当前档位。 */
  const [modeMenuOpen, setModeMenuOpen] = useState(false);

  const providers = useQuery({ queryKey: workspaceQueryKey(workspaceId, "providers"), queryFn: listProviders });
  const sessions = useSessionDetail(workspaceId, sessionId);
  const history = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "messages", sessionId),
    queryFn: () => listSessionMessages(sessionId, { limit: 50, compact: false }),
    enabled: Boolean(sessionId),
    gcTime: 15_000,
  });
  const modelProviders = useMemo(
    () =>
      (providers.data ?? []).filter(
        (provider) =>
          provider.enabled &&
          provider.remote_capability &&
          isModelProvider(provider),
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
  const supportsAgentMode = modelSupportsAgent(activeProvider, selectedModel);
  const effectiveAgentMode = responseMode === "agentic" && supportsAgentMode;
  const modelGroups = useMemo(
    () => buildModelSelectorGroups(modelProviders, providerModelQueries),
    [modelProviders, providerModelQueries],
  );
  const thinkingModes = useMemo(
    () =>
      capabilityThinkingModes(
        selectedModel?.capabilities?.reasoning_efforts ??
          activeProvider?.capabilities.reasoning_efforts ??
          (activeProvider && isDeepSeekProvider(activeProvider)
            ? ["low", "high", "max"]
            : undefined),
      ),
    [activeProvider, selectedModel?.capabilities?.reasoning_efforts],
  );
  const thinkingRequired =
    selectedModel?.capabilities?.thinking_required === true;
  const activeSession = sessions.data;
  const indexRequired = requiresLearningIndex(
    file.original_name,
    file.mime_type,
    file.parse_capability,
  );
  const messages = useMemo(
    () => [...(history.data ?? []), ...localMessages],
    [history.data, localMessages],
  );

  // Adopt the session's persisted composer prefs so panel and full chat page agree.
  useEffect(() => {
    if (!sessionId) return;
    const stored = getSessionComposerPrefs(sessionId);
    if (!stored.providerId && !stored.modelId) return;
    if (stored.providerId) setSelectedProviderId(stored.providerId);
    if (stored.modelId) setSelectedModelId(stored.modelId);
    setResponseMode(stored.responseMode);
    setThinkingMode(stored.thinkingMode);
  }, [sessionId]);

  useEffect(() => {
    if (providers.isPending) return;
    setSelectedProviderId((current) =>
      modelProviders.some((provider) => provider.id === current)
        ? current
        : (modelProviders[0]?.id ?? ""),
    );
  }, [modelProviders, providers.isPending]);

  useEffect(() => {
    if (providers.isPending || discoveredModels?.isPending) return;
    setSelectedModelId((current) =>
      modelOptions.some((model) => model.id === current)
        ? current
        : (modelOptions[0]?.id ?? ""),
    );
  }, [modelOptions, providers.isPending, discoveredModels?.isPending]);

  useEffect(() => {
    if (!activeProvider || providers.isPending || discoveredModels?.isPending) return;
    const migrated = migrateThinkingPreference(sessionId, activeProvider.id, selectedModel?.id, selectedModel?.capabilities, thinkingMode);
    if (migrated !== thinkingMode) { setThinkingMode(migrated); return; }
    setThinkingMode((current) => {
      if (responseMode === "fast" && !thinkingRequired) return "off";
      if (current === "off") {
        return thinkingRequired && thinkingModes.length
          ? (thinkingModes.includes("medium") ? "medium" : thinkingModes[0])
          : current;
      }
      return thinkingModes.includes(current)
        ? current
        : (thinkingModes[0] ?? "off");
    });
  }, [activeProvider, responseMode, thinkingModes, thinkingRequired, providers.isPending, discoveredModels?.isPending, sessionId, selectedModel?.id, selectedModel?.capabilities, thinkingMode]);

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

  /** 极速：清空思考力度；智能体：力度缺失时补一个可用档位。 */
  function setMode(mode: ResponseMode) {
    setResponseMode(mode);
    if (mode === "fast") {
      setThinkingMode("off");
      if (sessionId) {
        persistComposerPrefs(sessionId, { responseMode: "fast", thinkingMode: "off" });
      }
      return;
    }
    const nextThinking =
      thinkingMode === "off" && thinkingModes.length
        ? thinkingModes.includes("medium")
          ? "medium"
          : thinkingModes[0]!
        : thinkingMode;
    setThinkingMode(nextThinking);
    if (sessionId) {
      persistComposerPrefs(sessionId, {
        responseMode: mode,
        thinkingMode: nextThinking,
      });
    }
  }

  function handleThinkingChange(nextThinking: ThinkingMode) {
    setThinkingMode(nextThinking);
    if (sessionId) {
      persistComposerPrefs(sessionId, { thinkingMode: nextThinking });
    }
  }

  useEffect(() => {
    if (!selection) return;
    setDraft((current) =>
      current.trim()
        ? current
        : `请解释这段内容：${selection.selected_text.slice(0, 600)}`,
    );
    requestAnimationFrame(() => composerRef.current?.focus());
  }, [selection]);

  useEffect(() => {
    if (!questionSeed?.text.trim()) return;
    setDraft(questionSeed.text.trim());
    requestAnimationFrame(() => composerRef.current?.focus());
    if (
      autoSubmitSeed &&
      activeProvider &&
      history.isSuccess &&
      submittedSeedRef.current !== questionSeed.id
    ) {
      submittedSeedRef.current = questionSeed.id;
      void sendRef.current(questionSeed.text.trim());
    }
  }, [activeProvider, autoSubmitSeed, history.isSuccess, questionSeed]);

  useEffect(() => {
    if (!sessionId) {
      setLocalMessages([]);
      setStatus("ready");
      return;
    }
    if (createdSessionRef.current === sessionId) {
      createdSessionRef.current = "";
      return;
    }
    setLocalMessages([]);
    setStatus("ready");
  }, [sessionId]);

  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  async function send(
    event?: FormEvent,
    contentOverride?: string,
    options?: {
      uiAction?: MessageCreateRequest["ui_action"];
      analysisOnly?: boolean;
    },
  ) {
    event?.preventDefault();
    const content = contentOverride?.trim() || draft.trim();
    if (!content || status === "submitted" || status === "streaming") return;
    if (onSendToFullChat) {
      setStatus("submitted");
      try {
        await onSendToFullChat(content, {
          providerId: activeProvider?.id,
          modelId: selectedModel?.id,
          responseMode,
          thinkingMode,
        });
      } catch (error) {
        setStatus("error");
        toast.error(error instanceof Error ? error.message : "无法打开完整对话");
      }
      return;
    }
    if (!activeProvider) {
      toast.error("没有可用的真实模型 Provider，无法发送文档问题。");
      return;
    }
    // 智能体模式：沙箱不可用时降级提示，不阻断文档问答本身。
    if (effectiveAgentMode) {
      try {
        const readiness = await getAgentSandboxReadiness();
        if (!readiness.available) {
          toast.message("沙箱工具暂不可用", {
            description:
              "文档问答仍可继续；文件执行与代码沙箱工具会暂时不可用。可在设置中初始化沙箱。",
          });
        }
      } catch {
        toast.message("无法检查智能体沙箱状态", {
          description: "将继续发送；沙箱工具可能不可用。",
        });
      }
    }
    const needsIndex =
      indexRequired &&
      file.parse_status !== "indexed" &&
      !embeddedImages.length;
    if (needsIndex && !selection) {
      toast.error("请先完成文档索引，再把原文证据发送到学习对话。");
      return;
    }
    if (indexRequired && file.parse_status !== "indexed" && selection) {
      toast.error("请先完成文档索引，再发送划词证据。");
      return;
    }

    let targetSessionId = sessionId;
    if (!targetSessionId) {
      const created = await createSession({
        title: `阅读 ${file.original_name}`,
        memory_enabled: false,
      });
      targetSessionId = created.id;
      createdSessionRef.current = created.id;
      onSessionChange(created.id);
      queryClient.setQueryData<Session[]>(workspaceQueryKey(workspaceId, "sessions"), (current) => [
        created,
        ...(current ?? []).filter((item) => item.id !== created.id),
      ]);
    }
    persistComposerPrefs(targetSessionId);

    // Upload / reuse embedded images so the server can route native or vision.
    const imageFileIds: string[] = [];
    for (const image of embeddedImages) {
      try {
        const digest = await hashFileSha256(image.blob);
        let record: FileRecord | null = null;
        if (digest) {
          try {
            record = await lookupFile({ name: image.filename, sha256: digest });
          } catch (error) {
            if (!(error instanceof ApiError && error.status === 404)) throw error;
          }
        }
        if (!record) {
          const asFile = new File([image.blob], image.filename, {
            type: image.blob.type || "image/png",
          });
          record = await uploadFile(asFile);
        }
        imageFileIds.push(record.id);
      } catch (error) {
        toast.error(
          error instanceof Error
            ? `图片附件失败：${error.message}`
            : "图片附件失败",
        );
        return;
      }
    }
    // Clear embedded images after successful upload / reuse.
    for (const image of [...embeddedImages]) {
      onRemoveEmbeddedImage?.(image.id);
    }

    const documentSelection: DocumentSelectionContext | undefined = selection
      ? {
          file_id: selection.file_id,
          document_revision_id: selection.document_revision_id,
          chunk_id: selection.chunk_id,
          locator: selection.locator,
          selected_text: selection.selected_text,
          selected_text_hash: await sha256(selection.selected_text),
        }
      : undefined;
    const stamp = Date.now();
    const optimisticUser: Message = {
      id: `document-user-${stamp}`,
      workspace_id: workspaceId,
      session_id: targetSessionId,
      parent_message_id: null,
      role: "user",
      version: 1,
      status: "completed",
      content,
      parts: [
        {
          id: `document-attachment-${file.id}-${stamp}`,
          type: "attachment",
          status: "completed",
          content: file.original_name,
          data: {
            file_id: file.id,
            filename: file.original_name,
            media_type: file.mime_type,
            mime_type: file.mime_type,
            parse_status: file.parse_status,
          },
        },
        { id: `document-user-text-${stamp}`, type: "text", status: "completed", content },
        ...(documentSelection
          ? [{
              id: `document-selection-${stamp}`,
              type: "document_selection" as const,
              status: "completed" as const,
              content: documentSelection.selected_text,
              data: {
                ...documentSelection,
                filename: file.original_name,
                locator_label: selection?.locator_label,
              },
            }]
          : []),
      ],
      provider_trace: {},
      created_at: new Date().toISOString(),
    };
    const optimisticAssistant: Message = {
      id: `document-assistant-${stamp}`,
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
    const fileIds = Array.from(
      new Set([
        ...(file.id ? [file.id] : []),
        ...imageFileIds,
      ]),
    );
    const request: MessageCreateRequest = {
      content,
      file_ids: fileIds,
      provider_id: activeProvider.id,
      model_id:
        selectedModel?.id ??
        (typeof activeProvider.capabilities.default_model === "string"
          ? activeProvider.capabilities.default_model
          : undefined),
      // Fast mode must be explicit: omitting the field makes the backend use a
      // model's configured default_thinking_mode (often "medium"), which
      // silently turns a 极速 document chat into a thinking request.
      thinking_mode:
        responseMode === "fast" && !thinkingRequired
          ? "off"
          : responseMode !== "fast" && thinkingModes.length
            ? thinkingMode
            : undefined,
      agent_mode: effectiveAgentMode,
      document_selection: documentSelection,
      ...(options?.uiAction ? { ui_action: options.uiAction } : {}),
      ...(options?.analysisOnly ? { analysis_only: true } : {}),
    };
    const idempotencyKey = `document-chat-${createUuid()}`;
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
            if (typeof data.message_id === "string") activeMessageId.current = data.message_id;
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
                  : null;
              terminalFailure = errorMessage
                ? `模型流在服务端失败：${errorMessage}`
                : "模型流在服务端失败。";
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
                    provider_trace: (data.provider_trace ?? {}) as Record<string, unknown>,
                  };
                }
                if (type === "message.failed" || type === "message.cancelled") {
                  return {
                    ...message,
                    id: String(data.message_id ?? message.id),
                    status: type === "message.cancelled" ? "cancelled" : "failed",
                  };
                }
                if (type === "answer.started") {
                  // 后端 SSE envelope 把边界字段放在 payload 里（顶层只提升
                  // part/status/provider_trace），需从 payload 读取。
                  const answerPayload =
                    typeof data.payload === "object" && data.payload !== null
                      ? (data.payload as Record<string, unknown>)
                      : {};
                  return {
                    ...message,
                    finalAnswerStarted: {
                      finalPartId:
                        typeof answerPayload.final_part_id === "string"
                          ? answerPayload.final_part_id
                          : undefined,
                      boundarySequence:
                        typeof answerPayload.boundary_sequence === "number"
                          ? answerPayload.boundary_sequence
                          : undefined,
                      thinkingDurationMs:
                        typeof answerPayload.thinking_duration_ms === "number"
                          ? answerPayload.thinking_duration_ms
                          : undefined,
                    },
                  };
                }
                return isMessagePart(data.part)
                  ? { ...message, parts: appendPart(message.parts, data.part, type === "part.replaced") }
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
      await queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "messages", targetSessionId) });
      setLocalMessages([]);
      setStatus("ready");
      activeMessageId.current = null;
      onClearSelection();
    } catch (error) {
      if (controller.signal.aborted) {
        setStatus("ready");
      } else {
        setStatus("error");
        setLocalMessages((current) =>
          current.map((message) =>
            message.id === optimisticAssistant.id
              ? {
                  ...message,
                  status: "failed",
                  parts: [
                    ...message.parts,
                    {
                      id: `document-error-${stamp}`,
                      type: "error",
                      status: "failed",
                      content: error instanceof Error ? error.message : "文档对话失败",
                    },
                  ],
                }
              : message,
          ),
        );
        toast.error(error instanceof Error ? error.message : "文档对话失败");
      }
    } finally {
      abortRef.current = null;
      activeMessageId.current = null;
      activeStreamSessionId.current = "";
    }
  }
  sendRef.current = (content) => send(undefined, content);

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

  async function expandToFullChat() {
    let targetSessionId = sessionId;
    if (!targetSessionId) {
      const created = await createSession({
        title: `阅读 ${file.original_name}`,
        memory_enabled: false,
      });
      targetSessionId = created.id;
      createdSessionRef.current = created.id;
      onSessionChange(created.id);
      queryClient.setQueryData<Session[]>(workspaceQueryKey(workspaceId, "sessions"), (current) => [
        created,
        ...(current ?? []).filter((item) => item.id !== created.id),
      ]);
    }
    persistComposerPrefs(targetSessionId);
    navigate(`/w/${workspaceId}/chat/${targetSessionId}`);
  }

  const unavailableSession = Boolean(
    sessionId && sessions.isSuccess && !activeSession,
  );
  const sessionClosed = activeSession?.status === "closed";
  const busy = status === "submitted" || status === "streaming";

  const modelMenu = (
    <GenerationConfigMenu
      open={modeMenuOpen}
      onOpenChange={setModeMenuOpen}
      align="end"
      side="bottom"
      layout="inline"
      groups={modelGroups}
      providerId={activeProvider?.id ?? ""}
      modelId={selectedModel?.id ?? ""}
      responseMode={responseMode}
      thinkingMode={thinkingMode}
      thinkingModes={thinkingModes}
      supportsAgent={supportsAgentMode}
      thinkingRequired={thinkingRequired}
      modelError={Boolean(discoveredModels?.isError)}
      onModelChange={(providerId, modelId) => {
        setSelectedProviderId(providerId);
        setSelectedModelId(modelId);
        if (sessionId) {
          persistComposerPrefs(sessionId, { providerId, modelId });
        }
      }}
      onModeChange={setMode}
      onThinkingChange={handleThinkingChange}
      onManageModels={() => {
        setModeMenuOpen(false);
        navigate(`/w/${workspaceId}/settings/providers`);
      }}
      onRefreshModels={() => void discoveredModels?.refetch()}
      modelSearch={modelSearch}
      onModelSearchChange={setModelSearch}
      trigger={
        <button
          aria-label="选择响应模式、思考力度和模型"
          className="chat-mode-chip"
          disabled={!modelProviders.length}
          title={
            selectedModel
              ? `${selectedModel.id} · ${thinkingLabels[thinkingMode]}`
              : "生成配置"
          }
          type="button"
        >
          <span>
            {modeMenuOpen
              ? "生成配置"
              : responseMode === "fast"
                ? "极速"
                : thinkingLabels[thinkingMode]}
          </span>
          <ChevronDown aria-hidden="true" className="size-3.5" />
        </button>
      }
    />
  );

  return (
    <section className="document-chat-panel flex h-full min-h-0 flex-col bg-background" aria-label="文档学习对话">
      <header className="flex h-12 flex-none items-center gap-2 border-b px-3">
        <MessageSquareText className="size-4" />
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-xs font-semibold">文档学习对话</h2>
          <p className="truncate text-[10px] text-muted-foreground">
            {activeProvider ? `${activeProvider.display_name} · 已保存的对话` : "尚未配置对话模型"}
          </p>
        </div>
        {modelMenu}
        <Button
          aria-label="在完整会话画布中打开"
          disabled={busy}
          onClick={() =>
            void expandToFullChat().catch((error: Error) => toast.error(error.message))
          }
          size="icon-xs"
          variant="ghost"
        >
          <ExternalLink className="size-3.5" />
        </Button>
        <Button
          aria-label="新建文档学习对话"
          disabled={busy}
          onClick={() => onSessionChange("")}
          size="icon-xs"
          variant="ghost"
        >
          <Plus className="size-3.5" />
        </Button>
      </header>

      {selection ? (
        <div className="flex flex-none gap-2 border-b bg-muted/35 px-3 py-2 text-[11px]">
          <FileText className="mt-0.5 size-3.5 flex-none text-primary" />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <strong className="truncate">{selection.locator_label || "整文件上下文"}</strong>
              <span className="text-muted-foreground">
                {selection.chunk_id ? "已校验后发送" : "以整文件 + 选区提示发送"}
              </span>
            </div>
            <p className="mt-1 line-clamp-3 leading-4 text-muted-foreground">
              {selection.selected_text}
            </p>
          </div>
          <Button aria-label="清除文档选区" onClick={onClearSelection} size="icon-xs" variant="ghost">
            <X className="size-3" />
          </Button>
        </div>
      ) : null}

      <Conversation className="min-h-0 flex-1">
        <PartUIStateProvider
          onLocalPartUIStateChange={(partId, uiState) =>
            setLocalMessages((current) =>
              patchMessagesUIState(current, partId, uiState),
            )
          }
          sessionId={sessionId}
          workspaceId={workspaceId}
        >
        <ConversationContent className="gap-5 px-3 py-4">
          {history.isPending && sessionId ? (
            <div className="grid min-h-32 place-items-center">
              <LoaderCircle className="size-4 animate-spin" />
            </div>
          ) : null}
          {unavailableSession ? (
            <ConversationEmptyState
              description="该会话已删除、不可访问或不属于当前工作区。"
              icon={<MessageSquareText className="size-5" />}
              title="无法读取会话"
            >
              <Button onClick={() => onSessionChange("")} size="sm" variant="outline">开始新对话</Button>
            </ConversationEmptyState>
          ) : null}
          {!messages.length && !history.isPending && !unavailableSession ? (
            <ConversationEmptyState
              className="min-h-56 px-5"
              description="当前文件已作为对话上下文，直接提问即可。"
              icon={<MessageSquareText className="size-5" />}
              title="围绕这个文件开始学习"
            >
              {suggestions.length ? (
                <div className="mt-3 flex w-full flex-col gap-1.5">
                  {suggestions.map((suggestion) => (
                    <Button
                      className="h-auto justify-start whitespace-normal py-2 text-left text-xs font-normal"
                      disabled={(!activeProvider && !onSendToFullChat) || busy || sessionClosed || unavailableSession}
                      key={suggestion}
                      onClick={() => void send(undefined, suggestion)}
                      variant="outline"
                    >
                      {suggestion}
                    </Button>
                  ))}
                </div>
              ) : null}
            </ConversationEmptyState>
          ) : null}
          {messages.map((message) => {
            const parts = messageParts(message);
            const streaming = message.status === "streaming";
            const messageSettled =
              message.status !== "streaming" && message.status !== "pending";
            const interactive =
              messageSettled && status !== "streaming" && status !== "submitted";
            const handleComponentAction = (action: {
              componentId: string;
              componentType: string;
              event: string;
              payload: Record<string, unknown>;
            }) => {
              if (action.componentType === "code_trainer") {
                if (
                  action.event !== "submit" ||
                  typeof action.payload.prompt !== "string" ||
                  action.payload.prompt.length > 50000
                ) {
                  return;
                }
                return send(undefined, action.payload.prompt, { analysisOnly: true });
              }
              if (action.componentType === "stream_ui") {
                if (
                  action.event !== "explain" &&
                  action.event !== "graph.node.explain" &&
                  action.event !== "document.block.explain"
                ) {
                  return;
                }
                const uiAction = {
                  part_id: action.componentId,
                  revision: Number(action.payload.revision),
                  action: action.event,
                  ...(typeof action.payload.node_id === "string"
                    ? { node_id: action.payload.node_id }
                    : {}),
                  ...(typeof action.payload.block_id === "string"
                    ? { block_id: action.payload.block_id }
                    : {}),
                  ...(action.payload.view_state && typeof action.payload.view_state === "object"
                    ? { view_state: action.payload.view_state as Record<string, unknown> }
                    : {}),
                } satisfies NonNullable<MessageCreateRequest["ui_action"]>;
                const userMessage =
                  typeof action.payload.userMessage === "string" &&
                  action.payload.userMessage.trim()
                    ? action.payload.userMessage.trim()
                    : uiAction.node_id
                      ? `请解释图谱节点「${uiAction.node_id}」及其关联。`
                      : "请解释当前图表，分析其中的主要信息。";
                return send(undefined, userMessage, { uiAction });
              }
              if (action.componentType === "graph_update_proposal") {
                toast.message("图谱变更请在主画布中审核");
                return;
              }
              if (
                action.event !== "submit" &&
                action.event !== "create_plan" &&
                action.event !== "open_plan"
              ) {
                return;
              }
              if (
                action.event === "submit" &&
                typeof action.payload.summaryText === "string" &&
                action.payload.summaryText.trim()
              ) {
                void send(undefined, action.payload.summaryText.trim());
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
              if (!answer && action.componentType === "question_batch") {
                const answers = Array.isArray(action.payload.answers)
                  ? action.payload.answers
                  : [];
                answer = answers
                  .map((item, index) => {
                    const record =
                      item && typeof item === "object"
                        ? (item as Record<string, unknown>)
                        : {};
                    const prompt =
                      typeof record.prompt === "string" ? record.prompt : `问题 ${index + 1}`;
                    const value =
                      typeof record.labels === "string"
                        ? record.labels
                        : typeof record.value === "string"
                          ? record.value
                          : "（跳过）";
                    return `${index + 1}. ${prompt} → ${value}`;
                  })
                  .filter(Boolean)
                  .join("；");
              }
              if (!answer && action.componentType === "goal_draft_editor") {
                const draftPayload =
                  (action.payload.draft as Record<string, unknown> | undefined) ??
                  action.payload;
                answer = `请按我修改后的字段确认目标草稿：${JSON.stringify(draftPayload)}`;
              }
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
              }
              if (action.event === "submit" && action.componentType !== "question_batch") {
                answer = `我的回答：${answer}`;
              }
              void send(undefined, answer);
            };
            const renderPart = (part: MessagePart) => (
              <ChatStreamPartRenderer
                interactive={interactive}
                key={part.id}
                onAction={handleComponentAction}
                part={part}
                settled={messageSettled}
                siblingParts={parts}
                streaming={
                  streaming &&
                  (part.status === "streaming" || part.status === "pending")
                }
              />
            );
            const renderAnswerParts = (answerParts: MessagePart[]) =>
              groupAnswerParts(answerParts).map((group) => {
                if (group.kind === "question_set") {
                  return (
                    <QuestionSetPager
                      interactive={interactive}
                      key={`question-set-${group.parts.map((part) => part.id).join("-")}`}
                      onSubmit={(submission) =>
                        void handleComponentAction({
                          componentId:
                            group.questions[0]?.componentId ?? group.parts[0].id,
                          componentType:
                            group.questions.length > 1
                              ? "question_set"
                              : group.questions[0]?.componentType ?? "question_set",
                          event: "submit",
                          payload: {
                            answer: submission.summaryText,
                            summaryText: submission.summaryText,
                            results: submission.results,
                            labels: submission.results.flatMap((item) => item.labels),
                            graded_count: submission.gradedCount,
                            correct_count: submission.correctCount,
                          },
                        })
                      }
                      questions={group.questions}
                      storageKey={group.parts.map((part) => part.id).join(":")}
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
                if (group.kind === "side_pair") {
                  // Model-authored |layout=side: text left, artifact right.
                  return (
                    <div
                      className="grid min-w-0 gap-3 sm:grid-cols-2 sm:items-start"
                      key={`side-pair-${group.artifact.id}`}
                    >
                      <div className="min-w-0">{renderPart(group.text)}</div>
                      <div className="min-w-0">{renderPart(group.artifact)}</div>
                    </div>
                  );
                }
                return renderPart(group.part);
              });
            const displaySegments = groupPartsForDisplay(parts, {
              boundary: message.finalAnswerStarted,
              live: message.status === "streaming",
            });
            return (
              <AiMessage from={message.role === "user" ? "user" : "assistant"} key={message.id}>
                <MessageContent className={message.role === "assistant" ? "message-reading-surface message-reading-surface--compact w-full gap-2 overflow-visible" : undefined} data-message-content={message.role === "assistant" ? true : undefined}>
                  {message.role === "assistant" ? <MessageReading process={displaySegments.filter(segment => segment.kind === "chain").map(segment => (
                      <ThinkingChain
                        chainParts={segment.parts}
                        completedDurationSec={thinkingDurationSeconds(
                          message.provider_trace,
                        )}
                        finalAnswerStarted={Boolean(
                          message.finalAnswerStarted,
                        )}
                        key={`chain-${message.id}`}
                        messageStatus={message.status}
                        renderPart={(part) =>
                          isReasoningTextPart(part) ? (
                            <ReasoningSummaryRow
                              key={part.id}
                              part={part}
                              streaming={streaming}
                            />
                          ) : (
                            renderPart(part)
                          )
                        }
                        startedAt={
                          typeof message.provider_trace.generation_started_at ===
                          "string"
                            ? message.provider_trace.generation_started_at
                            : message.created_at
                        }
                        thinkingDurationMs={
                          message.finalAnswerStarted?.thinkingDurationMs
                        }
                        toolCallCount={toolCallCount(parts)}
                      />
                  ))}>
                    {displaySegments.filter(segment => segment.kind === "parts").map(segment => (
                      <div className="message-answer-segment answer-flow" key={`parts-${message.id}`}>
                        {renderAnswerParts(segment.parts)}
                      </div>
                    ))}
                  </MessageReading> : renderAnswerParts(parts)}
                </MessageContent>
                {message.role === "assistant" ? (
                  <StreamStatsBadge messageId={message.id} status={message.status} providerTrace={message.provider_trace} />
                ) : null}
              </AiMessage>
            );
          })}
        </ConversationContent>
        </PartUIStateProvider>
        <ConversationScrollButton />
      </Conversation>

      <form className="document-chat-composer flex-none border-t p-3" onSubmit={(event) => void send(event)}>
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
          <p className="mb-2 text-[11px] text-muted-foreground">该会话已结束。新建对话后可继续询问。</p>
        ) : null}
        {embeddedImages.length ? (
          <div className="document-chat-attachments" aria-label="待发送图片">
            {embeddedImages.map((image) => (
              <div className="document-chat-attachment" key={image.id}>
                <button aria-label={`预览 ${image.filename}`} onClick={() => setPreviewImage(image)} type="button">
                  <EmbeddedImagePreview blob={image.blob} filename={image.filename} />
                  <span><strong>{image.filename}</strong><small>源文件图片 · 待发送</small></span>
                </button>
                <Button aria-label={`移除 ${image.filename}`} onClick={() => onRemoveEmbeddedImage?.(image.id)} size="icon-xs" type="button" variant="ghost"><X /></Button>
              </div>
            ))}
          </div>
        ) : null}
        <div className="relative rounded-md border bg-background focus-within:ring-1 focus-within:ring-ring">
          <Textarea
            aria-label="询问当前文档"
            className="min-h-20 resize-none border-0 pb-11 shadow-none focus-visible:ring-0"
            disabled={
              onSendToFullChat
                ? false
                : !activeProvider || sessionClosed || unavailableSession
            }
            onChange={(event) => setDraft(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void send();
              }
            }}
            placeholder="询问选区、公式、表格或当前文档…"
            ref={composerRef}
            value={draft}
          />
          <div className="absolute inset-x-2 bottom-2 flex items-center justify-between gap-2">
            <span className="truncate text-[10px] text-muted-foreground">
              {selection ? selection.locator_label : file.original_name}
            </span>
            {status === "submitted" || status === "streaming" ? (
              <Button aria-label="停止生成" onClick={() => void stop()} size="icon-sm" type="button" variant="secondary">
                <Square className="size-3.5" color="#111" fill="#111" strokeWidth={0} />
              </Button>
            ) : (
              <Button
                aria-label="发送文档问题"
                disabled={
                  !draft.trim() ||
                  (!onSendToFullChat &&
                    (!activeProvider || sessionClosed || unavailableSession))
                }
                size="icon-sm"
                type="submit"
              >
                {status === "error" ? <ArrowUp className="size-4" /> : <ArrowUp className="size-4" />}
              </Button>
            )}
          </div>
        </div>
      </form>
      <Dialog onOpenChange={(open) => { if (!open) setPreviewImage(null); }} open={Boolean(previewImage)}>
        <DialogContent className="max-w-4xl">
          <DialogTitle className="truncate text-sm"><ImageIcon className="mr-2 inline size-4" />{previewImage?.filename}</DialogTitle>
          {previewImage ? <EmbeddedImagePreview blob={previewImage.blob} filename={previewImage.filename} /> : null}
        </DialogContent>
      </Dialog>
    </section>
  );
}
