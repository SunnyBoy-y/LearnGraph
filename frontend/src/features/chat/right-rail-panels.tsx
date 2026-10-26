import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpRight,
  ChevronDown,
  Check,
  Download,
  FileArchive,
  FileSpreadsheet,
  FileText,
  FolderOpen,
  Image as ImageIcon,
  LoaderCircle,
  MessageCircle,
  Plus,
  RefreshCw,
  Search,
  X,
  Video,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import {
  cancelSessionMessage,
  createSession,
  fileStreamUrl,
  listSessionFiles,
  listSessionMessages,
  listSessions,
  listSettings,
  streamSessionMessage,
} from "@/api";
import {
  Conversation,
  ConversationContent,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import { RailConversationComposer, RailConversationMessage } from "./rail-conversation";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { FilePreviewContent } from "@/components/resources/file-preview-content";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  getSessionComposerPrefs,
} from "@/lib/session-composer-prefs";
import { useRailConversationModel } from "./use-rail-conversation-model";
import {
  bindExplanationSession,
  buildSelectionExplainPrompt,
  ensureSelectionExplanationRecord,
  listSelectionExplanations,
  upsertSelectionExplanation,
  type SelectionExplainAction,
  type SelectionExplanationOpenDetail,
  type SelectionExplanationRecord,
} from "./selection-explanation";
import { createUuid } from "@/lib/uuid";
import { workspaceQueryKey } from "@/lib/query-keys";
import { readChatThinkingChainDefault } from "@/lib/workspace-settings";
import { resolveFilePreviewKind, type FilePreviewKind } from "@/lib/file-preview";
import type { SessionFile } from "@/types/files";
import type {
  Message,
  MessageCreateRequest,
  MessagePart,
  Session,
  SessionMessageStreamData,
} from "@/types/sessions";
import type { TrustedComponentAction } from "@/components/chat/trusted-component-renderer";

function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value >= 10 || index === 0 ? Math.round(value) : value.toFixed(1)} ${units[index]}`;
}

function fileKind(filename: string, mimeType?: string): {
  label: string;
  Icon: typeof FileText;
  kind: FilePreviewKind;
} {
  const kind = resolveFilePreviewKind(filename, mimeType);
  if (kind === "image") return { label: "图片", Icon: ImageIcon, kind };
  if (kind === "video") return { label: "视频", Icon: Video, kind };
  if (kind === "spreadsheet") return { label: "表格", Icon: FileSpreadsheet, kind };
  if (kind === "pdf" || kind === "word" || kind === "rtf" || kind === "powerpoint" || kind === "text" || kind === "html") {
    return { label: kind === "powerpoint" ? "演示文稿" : kind === "html" ? "网页" : "文档", Icon: FileText, kind };
  }
  if (kind === "audio") return { label: "音频", Icon: FileText, kind };
  if (kind === "legacy-office") return { label: "旧版 Office", Icon: FileArchive, kind };
  return { label: "文件", Icon: FileText, kind };
}

function sessionKindLabel(kind: Session["session_kind"]) {
  if (kind === "side") return "侧边对话";
  if (kind === "concept_branch") return "概念分支";
  if (kind === "standalone") return "独立会话";
  return "主会话";
}

function isRailMessagePart(value: unknown): value is MessagePart {
  return Boolean(
    value &&
      typeof value === "object" &&
      typeof (value as MessagePart).id === "string" &&
      typeof (value as MessagePart).type === "string" &&
      typeof (value as MessagePart).status === "string",
  );
}

function appendRailPart(parts: MessagePart[], incoming: MessagePart): MessagePart[] {
  const index = parts.findIndex((part) => part.id === incoming.id);
  if (index < 0) {
    return [
      ...parts,
      { ...incoming, content: incoming.content ?? incoming.content_delta ?? "" },
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

function railStreamData(value: SessionMessageStreamData): Record<string, unknown> {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : {};
}

function railStreamEventType(data: Record<string, unknown>) {
  return typeof data.type === "string"
    ? data.type
    : typeof data.event === "string"
      ? data.event
      : "";
}

function railStreamError(data: Record<string, unknown>): string {
  const eventPayload =
    typeof data.payload === "object" && data.payload !== null
      ? (data.payload as Record<string, unknown>)
      : {};
  const errorPayload =
    typeof eventPayload.error === "object" && eventPayload.error !== null
      ? (eventPayload.error as Record<string, unknown>)
      : typeof data.error === "object" && data.error !== null
        ? (data.error as Record<string, unknown>)
        : null;
  const message =
    typeof errorPayload?.message === "string"
      ? errorPayload.message
      : typeof eventPayload.message === "string"
        ? eventPayload.message
        : typeof data.message === "string"
          ? data.message
          : "";
  return message.trim() || "侧边会话生成失败";
}

type RailStreamHandle = {
  controller: AbortController;
  assistantId: string;
  messageId: string | null;
};

/**
 * A real, writable conversation surface for the right rail. It deliberately
 * uses the same stream endpoint and rich part renderer as the main canvas, so
 * markdown, reasoning/tool steps, citations, charts, cards and file artifacts
 * do not get reduced to a plain-text preview when shown in the split pane.
 */
export function SideConversationRail({
  focusSessionId,
  workspaceId,
  currentSessionId,
  selectionDetail,
  creationParentSessionId,
  onCloseSelection,
}: {
  workspaceId: string;
  currentSessionId?: string;
  focusSessionId?: string;
  selectionDetail?: SelectionExplanationOpenDetail | null;
  creationParentSessionId?: string;
  onCloseSelection?: () => void;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: workspaceQueryKey(workspaceId, "settings"), queryFn: listSettings, staleTime: 30_000 });
  const thinkingChainDefaultOpen = readChatThinkingChainDefault(settings.data);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const streamHandlesRef = useRef<Map<string, RailStreamHandle>>(new Map());
  const mountedRef = useRef(true);
  const [draft, setDraft] = useState("");
  const [selection, setSelection] = useState<SelectionExplanationRecord | null>(() =>
    selectionDetail ? ensureSelectionExplanationRecord(selectionDetail, currentSessionId ?? selectionDetail.parentSessionId) : null,
  );
  const selectionInputRef = useRef({ detail: selectionDetail, parent: currentSessionId });
  const activeSelectionIdRef = useRef(selection?.id);
  activeSelectionIdRef.current = selection?.id;
  const creatingExplanationsRef = useRef(new Map<string, Promise<Session>>());
  const autoSubmittedRef = useRef(new Set<string>());

  useEffect(() => {
    const previous = selectionInputRef.current;
    selectionInputRef.current = { detail: selectionDetail, parent: currentSessionId };
    if (selectionDetail && (previous.detail !== selectionDetail || previous.parent !== currentSessionId)) {
      setSelection(ensureSelectionExplanationRecord(selectionDetail, currentSessionId ?? selectionDetail.parentSessionId));
      setDraft("");
    } else if (previous.parent !== currentSessionId) {
      setSelection(null);
      setDraft("");
    }
  }, [selectionDetail, currentSessionId]);
  const [streamingSessionIds, setStreamingSessionIds] = useState<Set<string>>(
    () => new Set(),
  );
  const sessions = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "sessions"),
    queryFn: listSessions,
    staleTime: 15_000,
  });
  const [query, setQuery] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [selectedSessionId, setSelectedSessionId] = useState<string | undefined>(
    currentSessionId && currentSessionId !== "new" ? currentSessionId : undefined,
  );

  const visibleSessions = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return (sessions.data ?? [])
      .filter((session) => session.status !== "archived")
      .filter((session) => !normalized || session.title.toLocaleLowerCase().includes(normalized))
      .sort((left, right) => right.updated_at.localeCompare(left.updated_at));
  }, [query, sessions.data]);

  useEffect(() => {
    if (!sessions.data?.length) return;
    const availableSessions = sessions.data.filter(
      (session) => session.status !== "archived",
    );
    setSelectedSessionId((current) => {
      if (current && availableSessions.some((session) => session.id === current)) {
        return current;
      }
      if (
        currentSessionId &&
        availableSessions.some((session) => session.id === currentSessionId)
      ) {
        return currentSessionId;
      }
      return availableSessions[0]?.id;
    });
  }, [currentSessionId, sessions.data]);

  useEffect(() => {
    if (focusSessionId) {
      setSelectedSessionId(focusSessionId);
      if (!selectionInputRef.current.detail) setSelection(null);
      setDraft("");
    }
  }, [focusSessionId]);

  const currentSession = sessions.data?.find(
    (session) => session.id === currentSessionId,
  );
  const parentSessionId =
    currentSession?.parent_session_id ??
    (currentSessionId && currentSessionId !== "new"
      ? currentSessionId
      : null);
  const activeSessionId = selection ? selection.explanationSessionId : selectedSessionId;
  const selectedSession = sessions.data?.find((session) => session.id === activeSessionId);
  const { canSend, pending: modelsPending, control: modelControl, persistComposerPrefs, requestPreferences } =
    useRailConversationModel(workspaceId, activeSessionId || currentSessionId || "", activeSessionId, (selectedSession ?? currentSession)?.model_snapshot);
  const messages = useQuery({
    // Keep the same cache key as the main canvas. A message sent from the
    // split pane is therefore immediately available when that session is
    // opened in the full canvas.
    queryKey: workspaceQueryKey(workspaceId, "messages", activeSessionId),
    queryFn: () => listSessionMessages(activeSessionId!, { compact: false }),
    enabled: Boolean(activeSessionId),
    staleTime: 10_000,
    // Switching the common surface must retain an in-flight conversation.
    gcTime: 15 * 60_000,
  });

  const messageKey = useCallback(
    (sessionId: string) => workspaceQueryKey(workspaceId, "messages", sessionId),
    [workspaceId],
  );
  const patchMessages = useCallback(
    (sessionId: string, update: (messages: Message[]) => Message[]) => {
      queryClient.setQueryData<Message[]>(messageKey(sessionId), (current) =>
        update(current ?? []),
      );
    },
    [messageKey, queryClient],
  );
  const setStreaming = useCallback((sessionId: string, active: boolean) => {
    setStreamingSessionIds((current) => {
      const next = new Set(current);
      if (active) next.add(sessionId);
      else next.delete(sessionId);
      return next;
    });
  }, []);

  const createSideSession = useMutation({
    mutationFn: () =>
      createSession({
        title: "侧边对话",
        session_kind: "side",
        parent_session_id: parentSessionId,
      }),
    onSuccess: (session) => {
      queryClient.setQueryData<Session[]>(
        workspaceQueryKey(workspaceId, "sessions"),
        (current) => [
          session,
          ...(current ?? []).filter((item) => item.id !== session.id),
        ],
      );
      queryClient.setQueryData<Message[]>(messageKey(session.id), []);
      setSelection(null);
      onCloseSelection?.();
      setSelectedSessionId(session.id);
      setDraft("");
      requestAnimationFrame(() => composerRef.current?.focus());
      toast.success("已创建侧边会话，可以直接问答");
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "创建侧边对话失败"),
  });

  const ensureExplanationSession = useCallback(async (record: SelectionExplanationRecord): Promise<Session> => {
    const existing = sessions.data?.find((session) => session.id === record.explanationSessionId);
    if (existing) return existing;
    let pending = creatingExplanationsRef.current.get(record.id);
    if (!pending) {
      const titleSeed = record.selectedText.replace(/\s+/gu, " ").slice(0, 24);
      pending = createSession({
        title: `划词解释 · ${titleSeed || "选区"}`,
        memory_enabled: false,
        session_kind: "side",
        parent_session_id: creationParentSessionId ?? parentSessionId,
        project_id: currentSession?.project_id,
      });
      creatingExplanationsRef.current.set(record.id, pending);
    }
    try {
      const session = await pending;
      persistComposerPrefs(session.id);
      const bound = bindExplanationSession(record.parentSessionId, record.id, session.id);
      queryClient.setQueryData<Session[]>(workspaceQueryKey(workspaceId, "sessions"), (current) => [
        session, ...(current ?? []).filter((item) => item.id !== session.id),
      ]);
      if (queryClient.getQueryData(messageKey(session.id)) === undefined) {
        queryClient.setQueryData<Message[]>(messageKey(session.id), []);
      }
      if (activeSelectionIdRef.current === record.id) {
        setSelection(bound ?? { ...record, explanationSessionId: session.id });
        setSelectedSessionId(session.id);
      }
      return session;
    } finally {
      creatingExplanationsRef.current.delete(record.id);
    }
  }, [sessions.data, creationParentSessionId, parentSessionId, currentSession?.project_id, persistComposerPrefs, queryClient, workspaceId, messageKey]);

  const send = useCallback(
    async (event?: FormEvent, contentOverride?: string) => {
      event?.preventDefault();
      const content = (contentOverride ?? draft).trim();
      if (!content || !canSend || selectedSession?.status === "closed") return;
      let targetSession = selectedSession;
      if (!targetSession && selection && !selection.explanationSessionId) {
        try {
          targetSession = await ensureExplanationSession(selection);
        } catch (error) {
          toast.error(error instanceof Error ? error.message : "创建解释会话失败");
          return;
        }
      }
      if (!mountedRef.current || !targetSession || streamHandlesRef.current.has(targetSession.id)) return;
      const sessionId = targetSession.id;
      persistComposerPrefs(sessionId);
      const stamp = Date.now();
      const userId = `rail-user-${stamp}-${createUuid()}`;
      const assistantId = `rail-assistant-${stamp}-${createUuid()}`;
      const user: Message = {
        id: userId,
        workspace_id: workspaceId,
        session_id: sessionId,
        parent_message_id: null,
        role: "user",
        version: 1,
        status: "completed",
        content,
        parts: [
          {
            id: `${userId}-text`,
            type: "text",
            status: "completed",
            content,
          },
        ],
        provider_trace: {},
        created_at: new Date().toISOString(),
      };
      if (selection) {
        user.parts.push({
          id: `${userId}-quote`, type: "selection_quote", status: "completed", content: selection.selectedText,
          data: { source_role: "assistant", source_message_id: selection.sourceMessageId,
            parent_session_id: selection.parentSessionId, action: selection.action },
        });
      }
      const assistant: Message = {
        id: assistantId,
        workspace_id: workspaceId,
        session_id: sessionId,
        parent_message_id: userId,
        role: "assistant",
        version: 1,
        status: "streaming",
        content: "",
        parts: [
          {
            id: `${assistantId}-ack`,
            type: "acknowledgement",
            status: "pending",
            content: "",
          },
        ],
        provider_trace: {},
        created_at: new Date().toISOString(),
      };
      patchMessages(sessionId, (current) => [
        ...current.filter((message) => message.id !== userId && message.id !== assistantId),
        user,
        assistant,
      ]);
      if (contentOverride === undefined) setDraft("");

      const prefs = getSessionComposerPrefs(sessionId);
      const searchRoute = selection ? "disabled" : prefs.searchRoute;
      const request: MessageCreateRequest = {
        content,
        generation_mode: "text",
        ...requestPreferences,
        search_route: searchRoute,
        web_search: searchRoute !== "disabled",
        graph_action: "none",
      };
      const controller = new AbortController();
      const handle: RailStreamHandle = {
        controller,
        assistantId,
        messageId: null,
      };
      streamHandlesRef.current.set(sessionId, handle);
      setStreaming(sessionId, true);
      const seenEventIds = new Set<string>();
      const idempotencyKey = `rail-chat-${createUuid()}`;
      let lastEventId: string | undefined;
      let completed = false;
      let terminalFailure = "";

      const patchAssistant = (update: (message: Message) => Message) => {
        patchMessages(sessionId, (current) =>
          current.map((message) =>
            message.id === assistantId ? update(message) : message,
          ),
        );
      };

      try {
        for (let attempt = 0; attempt < 3 && !completed; attempt += 1) {
          let transientError: unknown;
          try {
            for await (const item of streamSessionMessage(sessionId, request, {
              signal: controller.signal,
              seenEventIds,
              headers: {
                "Idempotency-Key": idempotencyKey,
                ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
              },
            })) {
              if (item.id) lastEventId = item.id;
              const data = railStreamData(item.data);
              const type = railStreamEventType(data);
              if (typeof data.message_id === "string") {
                handle.messageId = data.message_id;
              }
              if (type === "message.completed") {
                completed = true;
                patchAssistant((message) => ({
                  ...message,
                  status: "completed",
                  provider_trace:
                    (data.provider_trace as Record<string, unknown> | undefined) ??
                    message.provider_trace,
                }));
              } else if (type === "message.failed") {
                terminalFailure = railStreamError(data);
                patchAssistant((message) => ({
                  ...message,
                  status: "failed",
                }));
              } else if (type === "message.cancelled" || type === "message.interrupted") {
                terminalFailure = "生成已取消";
                patchAssistant((message) => ({ ...message, status: "failed" }));
              } else if (type === "answer.started") {
                const answerPayload =
                  typeof data.payload === "object" && data.payload !== null
                    ? (data.payload as Record<string, unknown>)
                    : data;
                patchAssistant((message) => ({
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
                }));
              } else if (isRailMessagePart(data.part)) {
                const part = data.part;
                patchAssistant((message) => ({
                  ...message,
                  parts: appendRailPart(message.parts, part),
                  content: appendRailPart(message.parts, part)
                    .filter((item) => item.type === "text")
                    .map((item) => item.content ?? "").join("\n"),
                }));
              }
            }
          } catch (error) {
            if (controller.signal.aborted) throw error;
            transientError = error;
          }
          if (terminalFailure) throw new Error(terminalFailure);
          if (!completed && attempt === 2) {
            throw transientError ?? new Error("侧边会话消息流中断");
          }
        }
        if (!completed) throw new Error("消息流结束但没有收到完成事件");
        await queryClient.invalidateQueries({ queryKey: messageKey(sessionId) });
        void queryClient.invalidateQueries({
          queryKey: workspaceQueryKey(workspaceId, "sessions"),
        });
      } catch (error) {
        if (!controller.signal.aborted) {
          const message = error instanceof Error ? error.message : "侧边会话生成失败";
          patchAssistant((current) => ({
            ...current,
            status: "failed",
            parts: [
              ...current.parts,
              {
                id: `${assistantId}-error`,
                type: "error",
                status: "failed",
                content: message,
              },
            ],
          }));
          toast.error(message);
        }
      } finally {
        streamHandlesRef.current.delete(sessionId);
        setStreaming(sessionId, false);
      }
    },
    [
      draft,
      messageKey,
      patchMessages,
      queryClient,
      selectedSession,
      selection,
      canSend,
      ensureExplanationSession,
      persistComposerPrefs,
      requestPreferences,
      setStreaming,
      workspaceId,
    ],
  );

  const stop = useCallback(async () => {
    if (!activeSessionId) return;
    const handle = streamHandlesRef.current.get(activeSessionId);
    if (!handle) return;
    if (handle.messageId) {
      try {
        await cancelSessionMessage(activeSessionId, handle.messageId);
      } catch {
        // Aborting the client stream below still prevents more UI updates.
      }
    }
    patchMessages(activeSessionId, (current) =>
      current.map((message) =>
        message.id === handle.assistantId && message.status === "streaming"
          ? {
              ...message,
              status: "failed",
              parts: message.parts.map((part) =>
                part.status === "pending" || part.status === "streaming"
                  ? { ...part, status: "failed" }
                  : part,
              ),
            }
          : message,
      ),
    );
    handle.controller.abort();
  }, [patchMessages, activeSessionId]);

  useEffect(() => {
    if (!selection || !canSend) return;
    if (selection.explanationSessionId && (messages.isPending || messages.isError)) return;
    if (messages.data?.length || (activeSessionId && streamingSessionIds.has(activeSessionId))) return;
    const key = `${selection.id}:${selection.action}`;
    if (autoSubmittedRef.current.has(key)) return;
    autoSubmittedRef.current.add(key);
    void send(undefined, buildSelectionExplainPrompt(selection.action, selection.selectedText));
  }, [selection, canSend, messages.isPending, messages.isError, messages.data?.length, activeSessionId, streamingSessionIds, send]);

  function chooseSession(sessionId: string) {
    const record = listSelectionExplanations(currentSessionId).find((item) => item.explanationSessionId === sessionId);
    setSelectedSessionId(sessionId);
    setSelection(record ?? null);
    setDraft("");
    setPickerOpen(false);
    setQuery("");
    onCloseSelection?.();
  }

  function setSelectionAction(action: SelectionExplainAction) {
    if (!selection) return;
    setSelection(upsertSelectionExplanation({ ...selection, action }));
  }

  const handleRailAction = useCallback(
    (action: TrustedComponentAction) => {
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
    },
    [send],
  );

  useEffect(() => {
    mountedRef.current = true;
    const handles = streamHandlesRef.current;
    return () => {
      mountedRef.current = false;
      for (const handle of handles.values()) handle.controller.abort();
      handles.clear();
    };
  }, []);

  return (
    <section aria-label="侧边对话" className="right-rail-panel right-rail-conversation">
      <header className="right-rail-panel__header">
        <div className="right-rail-panel__heading">
          <strong>侧边对话</strong>
        </div>
        <div className="right-rail-panel__header-actions">
          <Button
            aria-label="刷新会话列表"
            disabled={sessions.isFetching}
            onClick={() => void sessions.refetch()}
            size="icon-xs"
            title="刷新会话列表"
            variant="ghost"
          >
            <RefreshCw className={sessions.isFetching ? "size-3.5 animate-spin" : "size-3.5"} />
          </Button>
          <Button
            aria-label="新建侧边对话"
            disabled={sessions.isPending || createSideSession.isPending}
            onClick={() => createSideSession.mutate()}
            size="icon-xs"
            title="新建侧边对话"
            variant="outline"
          >
            {createSideSession.isPending ? <LoaderCircle className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />}
          </Button>
        </div>
      </header>

      <div className="rail-session-picker">
        <Popover open={pickerOpen} onOpenChange={(open) => { setPickerOpen(open); if (!open) setQuery(""); }}>
          <PopoverTrigger asChild>
            <button aria-label="选择分屏会话" className="rail-session-picker__trigger" type="button">
              <MessageCircle className="size-4 shrink-0" />
              <span className="rail-session-picker__copy">
                <strong>{selectedSession?.title || (selection ? "划词解释" : "选择一个会话")}</strong>
                <small>{selectedSession ? `${sessionKindLabel(selectedSession.session_kind)} · ${selectedSession.id === currentSessionId ? "当前主区" : "可继续问答"}` : selection ? "侧边对话 · 划词解释" : "查看或继续一段对话"}</small>
              </span>
              <ChevronDown className="size-3.5 shrink-0" />
            </button>
          </PopoverTrigger>
          <PopoverContent align="start" className="rail-session-picker__popover">
            <div className="right-rail-conversation__search">
              <Search className="size-3.5" />
              <Input aria-label="搜索会话" onChange={(event) => setQuery(event.currentTarget.value)} placeholder="搜索会话…" value={query} />
            </div>
            <div aria-label="分屏会话" className="rail-session-picker__list">
              {visibleSessions.map((session) => (
                <button aria-pressed={session.id === activeSessionId} key={session.id} type="button"
                  onClick={() => chooseSession(session.id)}>
                  <span><strong>{session.title || "新会话"}</strong><small>{sessionKindLabel(session.session_kind)}{session.id === currentSessionId ? " · 当前主区" : ""}</small></span>
                  {session.id === activeSessionId ? <Check className="size-3.5" /> : null}
                </button>
              ))}
              {!visibleSessions.length ? <p className="p-3 text-xs text-muted-foreground">{sessions.isPending ? "正在读取会话…" : "没有匹配的会话"}</p> : null}
            </div>
          </PopoverContent>
        </Popover>
        {selectedSession ? <Button aria-label="在主区打开这个会话" disabled={selectedSession.id === currentSessionId}
          onClick={() => navigate(`/w/${encodeURIComponent(workspaceId)}/chat/${encodeURIComponent(selectedSession.id)}`)}
          size="icon-xs" title={selectedSession.id === currentSessionId ? "这就是当前主区会话" : "在主区打开这个会话"} variant="ghost">
          <ArrowUpRight className="size-3.5" />
        </Button> : null}
      </div>

      {selectedSession || selection ? (
        <div className="right-rail-conversation__active rail-chat-surface">
          {selection ? (
            <div className="rail-selection-context">
              <div className="flex items-center justify-between gap-2">
                <span>选中内容</span>
                <div className="flex items-center gap-1">
                  {(["define", "explain"] as const).map((action) => (
                    <Button key={action} aria-pressed={selection.action === action} className="h-6 px-2 text-[10px]"
                      onClick={() => setSelectionAction(action)} size="xs" variant={selection.action === action ? "secondary" : "ghost"}>
                      {action === "define" ? "定义" : "解释"}
                    </Button>
                  ))}
                  <Button aria-label="关闭划词引用" onClick={() => { setSelection(null); onCloseSelection?.(); }} size="icon-xs" variant="ghost"><X className="size-3.5" /></Button>
                </div>
              </div>
              <p>{selection.selectedText}</p>
            </div>
          ) : null}
          <Conversation className="right-rail-conversation__messages-shell">
            <ConversationContent
              aria-live="polite"
              className="right-rail-conversation__messages"
            >
              {messages.isPending && activeSessionId ? (
                <div className="right-rail-empty"><LoaderCircle className="size-4 animate-spin" />正在读取对话…</div>
              ) : messages.isError ? (
                <div className="right-rail-empty is-error">对话读取失败，请稍后重试。</div>
              ) : messages.data?.length ? (
                messages.data.map((message) => (
                  <RailConversationMessage key={message.id} message={message} onAction={handleRailAction} thinkingChainDefaultOpen={thinkingChainDefaultOpen} />
                ))
              ) : (
                <div className="right-rail-empty"><MessageCircle className="size-4" /><strong>这个会话还没有消息</strong><span>在下方输入问题即可开始侧边问答。</span></div>
              )}
            </ConversationContent>
            <ConversationScrollButton />
          </Conversation>
          <RailConversationComposer
            busy={Boolean(activeSessionId && streamingSessionIds.has(activeSessionId))}
            disabled={!canSend || Boolean(activeSessionId && messages.isPending) || createSideSession.isPending || selectedSession?.status === "closed"}
            inputRef={composerRef} label="侧边会话输入" placeholder="输入消息…" sendLabel="发送侧边会话消息" stopLabel="停止侧边会话生成"
            value={draft} onChange={setDraft} onSubmit={() => void send()} onStop={() => void stop()} controls={modelControl}
          >
            {!modelsPending && !canSend ? <div className="mb-2 flex items-center justify-between gap-2 text-xs text-muted-foreground">
              <span>没有可用的模型，请先配置。</span>
              <Button onClick={() => navigate(`/w/${workspaceId}/settings/providers`)} size="xs" type="button" variant="outline">配置</Button>
            </div> : null}
            {selectedSession?.status === "closed" ? <p className="mb-2 text-xs text-muted-foreground">该会话已结束，请新建侧边对话。</p> : null}
          </RailConversationComposer>
        </div>
      ) : (
        <div className="right-rail-empty right-rail-empty--tall">
          <MessageCircle className="size-5" />
          <strong>从会话列表选择一个分屏</strong>
          <span>你可以保留当前学习页面，同时对照另一段对话。</span>
        </div>
      )}
    </section>
  );
}

/** Inline session-file browser that reuses the same safe document renderer as
 * the full resource preview dialog. The list stays compact; the selected
 * document occupies the rest of the rail and supports PPT/HTML/DOCX/PDF and
 * other formats already understood by FilePreviewCanvas. */
export function SessionFilePreviewRail({
  workspaceId,
  sessionId,
}: {
  workspaceId: string;
  sessionId?: string;
}) {
  const enabled = Boolean(sessionId && sessionId !== "new");
  const files = useQuery({
    queryKey: ["workspaces", workspaceId, "sessions", sessionId, "files"],
    queryFn: () => listSessionFiles(sessionId!),
    enabled,
    staleTime: 10_000,
  });
  const list = useMemo(() => files.data ?? [], [files.data]);
  const [selectedFileKey, setSelectedFileKey] = useState<string | null>(null);
  const selectedFile = useMemo(
    () => list.find((file) => fileKey(file) === selectedFileKey) ?? null,
    [list, selectedFileKey],
  );
  const [downloading, setDownloading] = useState(false);
  useEffect(() => {
    if (!list.length) {
      setSelectedFileKey(null);
      return;
    }
    setSelectedFileKey((current) =>
      current && list.some((file) => fileKey(file) === current)
        ? current
        : fileKey(list[0]),
    );
  }, [list]);

  async function downloadSelected() {
    if (!selectedFile?.file_id || downloading) return;
    setDownloading(true);
    try {
      const url = await fileStreamUrl(selectedFile.file_id);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = selectedFile.filename;
      anchor.click();
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : "下载失败");
    } finally { setDownloading(false); }
  }
  if (!enabled) {
    return (
      <section aria-label="会话文件预览" className="right-rail-panel right-rail-files">
        <div className="right-rail-empty right-rail-empty--tall"><FolderOpen className="size-5" /><strong>新会话还没有文件</strong><span>上传或生成文件后，可在这里直接预览。</span></div>
      </section>
    );
  }

  return (
    <section aria-label="会话文件预览" className="right-rail-panel right-rail-files">
      <header className="right-rail-panel__header">
        <div className="right-rail-panel__heading">
          <span className="right-rail-panel__eyebrow"><FolderOpen className="size-3.5" />文件工作区</span>
          <strong>会话文件</strong>
          <small>{list.length ? `${list.length} 个文件 · 选择后在右栏内预览` : "当前会话还没有关联文件"}</small>
        </div>
        <Button
          aria-label="刷新会话文件"
          disabled={files.isFetching}
          onClick={() => void files.refetch()}
          size="icon-xs"
          title="刷新会话文件"
          variant="ghost"
        >
          <RefreshCw className={files.isFetching ? "size-3.5 animate-spin" : "size-3.5"} />
        </Button>
      </header>
      {files.isPending ? (
        <div className="right-rail-empty right-rail-empty--tall"><LoaderCircle className="size-4 animate-spin" />正在读取文件…</div>
      ) : files.isError ? (
        <div className="right-rail-empty right-rail-empty--tall is-error">会话文件加载失败，请刷新重试。</div>
      ) : !list.length ? (
        <div className="right-rail-empty right-rail-empty--tall"><ImageIcon className="size-5" /><strong>还没有可预览的文件</strong><span>引用资料、生成图片或下载文件后会显示在这里。</span></div>
      ) : (
        <>
          <div className="right-rail-files__list" aria-label="文件列表">
            {list.map((file) => {
              const { Icon, label, kind } = fileKind(file.filename, file.mime_type);
              const active = selectedFileKey === fileKey(file);
              return (
                <button
                  aria-pressed={active}
                  className={active ? "right-rail-file-card is-active" : "right-rail-file-card"}
                  key={fileKey(file)}
                  onClick={() => setSelectedFileKey(fileKey(file))}
                  type="button"
                >
                  <span className="right-rail-file-card__icon"><Icon className="size-3.5" /></span>
                  <span className="right-rail-file-card__copy">
                    <strong title={file.filename}>{file.filename}</strong>
                    <small>{label} · {formatBytes(file.size_bytes)}{kind === "unsupported" ? " · 需下载" : ""}</small>
                  </span>
                  {active ? <Badge variant="secondary">预览</Badge> : null}
                </button>
              );
            })}
          </div>
          {selectedFile ? (
            <div className="right-rail-files__preview">
              <header className="right-rail-files__preview-head">
                <div className="min-w-0"><strong title={selectedFile.filename}>{selectedFile.filename}</strong><span>{fileKind(selectedFile.filename, selectedFile.mime_type).label}</span></div>
                <Button disabled={!selectedFile.file_id || downloading} onClick={() => void downloadSelected()} size="icon-xs" title="下载文件" variant="ghost"><Download className="size-3.5" /></Button>
              </header>
              <div className="right-rail-files__preview-body">
                {!selectedFile.file_id ? (
                  <div className="right-rail-empty"><FolderOpen className="size-4" />该文件只存在于会话工作区，暂时没有可下载内容。</div>
                ) : (
                  <FilePreviewContent
                    fileId={selectedFile.file_id}
                    fileSize={selectedFile.size_bytes}
                    className="right-rail-files__canvas"
                    filename={selectedFile.filename}
                    imageZoom
                    mimeType={selectedFile.mime_type}
                  />
                )}
              </div>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}

function fileKey(file: SessionFile) {
  return file.file_id ?? `${file.path ?? ""}:${file.filename}`;
}
