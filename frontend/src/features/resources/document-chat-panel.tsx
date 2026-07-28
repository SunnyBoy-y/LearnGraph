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
  Search,
  Square,
  X,
  Image as ImageIcon,
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
import { ThinkingChain } from "@/components/chat/thinking-chain";
import {
  groupPartsForDisplay,
  thinkingDurationSeconds,
} from "@/features/chat/chat-message-parts";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { discoverProviderModels, listProviders } from "@/api/providers";
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
  setSessionComposerPrefs,
  getSessionComposerPrefs,
  type ThinkingMode,
} from "@/lib/session-composer-prefs";
import { isDeepSeekProvider, isModelProviderType } from "@/types/providers";
import {
  cancelSessionMessage,
  createSession,
  listSessionMessages,
  listSessions,
  streamSessionMessage,
} from "@/api/sessions";
import { lookupFile, uploadFile } from "@/api/files";
import { ApiError } from "@/api/client";
import { hashFileSha256 } from "@/lib/file-hash";
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
  chunk_id: string;
  locator: Record<string, unknown>;
  locator_label: string;
  selected_text: string;
}


interface DocumentChatPanelProps {
  autoSubmitSeed?: boolean;
  embeddedImages?: Array<{ id: string; blob: Blob; filename: string; locator: Record<string, unknown> }>;
  file: FileRecord;
  onRemoveEmbeddedImage?: (id: string) => void;
  onClearSelection: () => void;
  onSessionChange: (sessionId: string) => void;
  questionSeed?: { id: string; text: string };
  selection: PendingDocumentSelection | null;
  sessionId: string;
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
    ? [{ id: `${message.id}-text`, type: "text", status: "completed", content: message.content }]
    : [];
}


export function DocumentChatPanel({
  autoSubmitSeed = false,
  embeddedImages = [],
  file,
  onRemoveEmbeddedImage,
  onClearSelection,
  onSessionChange,
  questionSeed,
  selection,
  sessionId,
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
  const [thinkingMode, setThinkingMode] = useState<ThinkingMode>("off");
  const [modelSearch, setModelSearch] = useState("");

  const providers = useQuery({ queryKey: ["providers"], queryFn: listProviders });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: listSessions });
  const history = useQuery({
    queryKey: ["messages", sessionId],
    queryFn: () => listSessionMessages(sessionId),
    enabled: Boolean(sessionId),
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
      queryKey: ["provider-models", provider.id],
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
  const activeSession = sessions.data?.find((item) => item.id === sessionId);
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
    setThinkingMode(stored.responseMode === "fast" ? "off" : stored.thinkingMode);
  }, [sessionId]);

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
      if (current === "off") {
        return thinkingRequired && thinkingModes.length
          ? (thinkingModes.includes("medium") ? "medium" : thinkingModes[0])
          : current;
      }
      return thinkingModes.includes(current)
        ? current
        : (thinkingModes[0] ?? "off");
    });
  }, [thinkingModes, thinkingRequired]);

  function persistComposerPrefs(
    targetSessionId: string,
    overrides: {
      providerId?: string;
      modelId?: string;
      thinkingMode?: ThinkingMode;
    } = {},
  ) {
    if (!targetSessionId) return;
    const nextThinking = overrides.thinkingMode ?? thinkingMode;
    setSessionComposerPrefs(targetSessionId, {
      providerId: overrides.providerId ?? activeProvider?.id,
      modelId: overrides.modelId ?? selectedModel?.id,
      responseMode: nextThinking === "off" ? "fast" : "thinking",
      thinkingMode: nextThinking,
    });
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

  async function send(event?: FormEvent, contentOverride?: string) {
    event?.preventDefault();
    const content = contentOverride?.trim() || draft.trim();
    if (!content || status === "submitted" || status === "streaming") return;
    if (!activeProvider) {
      toast.error("没有可用的真实模型 Provider，无法发送文档问题。");
      return;
    }
    const fileIsImage = file.mime_type.toLowerCase().startsWith("image/");
    const needsIndex =
      !fileIsImage &&
      file.parse_status !== "indexed" &&
      !embeddedImages.length;
    if (needsIndex && !selection) {
      toast.error("请先完成文档索引，再把原文证据发送到学习对话。");
      return;
    }
    if (file.parse_status !== "indexed" && selection) {
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
      queryClient.setQueryData<Session[]>(["sessions"], (current) => [
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
        try {
          record = await lookupFile({ name: image.filename, sha256: digest });
        } catch (error) {
          if (!(error instanceof ApiError && error.status === 404)) throw error;
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
        ...(fileIsImage || file.parse_status === "indexed" ? [file.id] : []),
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
      // Only send an explicit intensity when the model declares reasoning support.
      thinking_mode: thinkingModes.length ? thinkingMode : undefined,
      document_selection: documentSelection,
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
      await queryClient.invalidateQueries({ queryKey: ["messages", targetSessionId] });
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
      queryClient.setQueryData<Session[]>(["sessions"], (current) => [
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

  return (
    <section className="flex h-full min-h-[36rem] flex-col bg-background" aria-label="文档学习对话">
      <header className="flex h-12 flex-none items-center gap-2 border-b px-3">
        <MessageSquareText className="size-4" />
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-xs font-semibold">文档学习对话</h2>
          <p className="truncate text-[10px] text-muted-foreground">
            {activeProvider ? `${activeProvider.display_name} · 持久会话` : "真实模型未配置"}
          </p>
        </div>
        <DropdownMenu onOpenChange={(open) => { if (!open) setModelSearch(""); }}>
          <DropdownMenuTrigger asChild>
            <Button
              aria-label="选择模型与思考力度"
              className="max-w-36 gap-1 px-2"
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
                if (sessionId) persistComposerPrefs(sessionId, { thinkingMode: mode });
              }}
              value={thinkingModes.length ? thinkingMode : "off"}
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
              <strong className="truncate">{selection.locator_label}</strong>
              <span className="text-muted-foreground">已校验后发送</span>
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
              className="min-h-64 px-5"
              description="选中原文后直接提问，回答、引用和 Provider Trace 会保存到正式 Session。"
              icon={<MessageSquareText className="size-5" />}
              title="围绕原文件继续学习"
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
            return (
              <AiMessage from={message.role === "user" ? "user" : "assistant"} key={message.id}>
                <MessageContent className={message.role === "assistant" ? "w-full gap-2" : undefined}>
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
                        {segment.parts.map(renderPart)}
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
            disabled={!activeProvider || sessionClosed || unavailableSession}
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
                disabled={!draft.trim() || !activeProvider || sessionClosed || unavailableSession || Boolean(embeddedImages.length)}
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
