import {
  useEffect,
  useMemo,
  useState,
  type MouseEvent as ReactMouseEvent,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { createUuid } from "@/lib/uuid";
import {
  ArrowLeft,
  BookOpenText,
  FileQuestion,
  Focus,
  LoaderCircle,
  MessageSquareText,
  Network,
  PanelLeft,
  PanelRight,
  RefreshCw,
  Search,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";

import {
  cancelDocumentJob,
  createDocumentJob,
  downloadFile,
  downloadFileForPreview,
  getDocumentJob,
  listDocumentJobEvents,
  listDocumentRevisions,
  listFileChunks,
  listFiles,
  listAudioTranscriptions,
  previewDocumentQuery,
  retryDocumentJob,
  transcribeAudioFile,
} from "@/api/files";
import { listProviders } from "@/api/providers";
import { ApiError } from "@/api/client";
import { listGoals } from "@/api/goals";
import { createSession } from "@/api/sessions";
import { createConceptBranch } from "@/api/sessions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  DocumentChatPanel,
  type PendingDocumentSelection,
} from "@/features/resources/document-chat-panel";
import {
  FilePreviewCanvas,
} from "@/components/resources/file-preview";
import { requiresLearningIndex, resolveFilePreviewKind } from "@/lib/file-preview";
import {
  isRealtimeTranscriptionModel,
  providerCapabilityString,
} from "@/lib/model-choices";
import { ConceptBranchWorkspace } from "@/features/resources/concept-branch-workspace";
import { cn } from "@/lib/utils";
import type {
  DocumentJobEvent,
  DocumentJobStatus,
  DocumentQueryHit,
  DocumentQueryScope,
  FileTextChunk,
} from "@/types/files";


async function sha256(value: string) {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}


function pageForChunk(chunk?: FileTextChunk) {
  const page = chunk?.locator_json.page;
  return typeof page === "number" ? page : undefined;
}


function normalizedSelection(value: string) {
  return value.replace(/\s+/g, " ").trim();
}


function chunkOutlineLabel(chunk: FileTextChunk) {
  const section = chunk.section_path.at(-1)?.trim();
  if (section) return section;
  const content = normalizedSelection(chunk.content);
  return content || chunk.locator;
}


function overlapLength(haystack: string, needle: string) {
  if (!needle) return 0;
  if (haystack.includes(needle)) return needle.length;
  // Longest common substring length (dynamic programming).  Used only as a
  // fallback for cross-chunk selections, so the O(n*m) cost is acceptable on
  // the selection text against a single chunk's content.
  const n = haystack.length;
  const m = needle.length;
  let best = 0;
  let prev = new Array(m + 1).fill(0);
  for (let i = 1; i <= n; i += 1) {
    const curr = new Array(m + 1).fill(0);
    for (let j = 1; j <= m; j += 1) {
      if (haystack[i - 1] === needle[j - 1]) {
        curr[j] = prev[j - 1] + 1;
        if (curr[j] > best) best = curr[j];
      }
    }
    prev = curr;
  }
  return best;
}

function chunkForSelection(
  text: string,
  chunks: FileTextChunk[],
  page?: number,
) {
  const normalized = normalizedSelection(text);
  const candidates = page
    ? chunks.filter((chunk) => pageForChunk(chunk) === page)
    : chunks;
  const exact =
    candidates.find((chunk) => chunk.content.includes(text)) ??
    candidates.find((chunk) => normalizedSelection(chunk.content).includes(normalized)) ??
    chunks.find((chunk) => chunk.content.includes(text)) ??
    chunks.find((chunk) => normalizedSelection(chunk.content).includes(normalized));
  if (exact) return exact;
  // Cross-chunk / duplicate-text fallback: pick the chunk with the longest
  // overlap with the selection so we anchor on the best single match.  Returns
  // undefined when there is no overlap at all — the caller then degrades to
  // whole-file context with the selection attached as an unverified hint.
  const pool = candidates.length ? candidates : chunks;
  let bestChunk: FileTextChunk | undefined;
  let bestScore = 0;
  for (const chunk of pool) {
    const score = overlapLength(normalizedSelection(chunk.content), normalized);
    if (score > bestScore) {
      bestScore = score;
      bestChunk = chunk;
    }
  }
  return bestScore > 0 ? bestChunk : undefined;
}


const terminalDocumentJobStatuses = new Set<DocumentJobStatus>([
  "completed",
  "failed",
  "cancelled",
  "interrupted",
]);
const retryableDocumentJobStatuses = new Set<DocumentJobStatus>([
  "failed",
  "cancelled",
  "interrupted",
]);


function isDocumentJobTerminal(status?: DocumentJobStatus) {
  return status !== undefined && terminalDocumentJobStatuses.has(status);
}


function documentJobStatusLabel(status: DocumentJobStatus) {
  switch (status) {
    case "queued":
      return "等待执行";
    case "running":
      return "处理中";
    case "completed":
      return "已完成";
    case "failed":
      return "失败";
    case "cancelled":
      return "已取消";
    case "interrupted":
      return "已中断";
  }
}


function documentJobEventSummary(event: DocumentJobEvent) {
  const stage = typeof event.payload.stage === "string" ? event.payload.stage : undefined;
  const progress = typeof event.payload.progress === "number" ? event.payload.progress : undefined;
  const errorCode = typeof event.payload.error_code === "string" ? event.payload.error_code : undefined;
  const fields = [stage, progress === undefined ? undefined : `${progress}%`, errorCode].filter(
    (value): value is string => Boolean(value),
  );
  return fields.length ? fields.join(" · ") : "已持久化";
}


function formatEventTime(value: string) {
  const time = new Date(value);
  return Number.isNaN(time.valueOf())
    ? value
    : time.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}


function ChunkText({
  chunks,
  activeChunkId,
  onSelect,
}: {
  chunks: FileTextChunk[];
  activeChunkId?: string;
  onSelect: (chunk: FileTextChunk, text: string) => void;
}) {
  function capture(chunk: FileTextChunk, event: ReactMouseEvent<HTMLElement>) {
    const selection = window.getSelection()?.toString().trim() ?? "";
    if (selection && event.currentTarget.contains(window.getSelection()?.anchorNode ?? null)) {
      onSelect(chunk, selection);
    }
  }
  return (
    <div className="mx-auto max-w-3xl px-8 py-10 sm:px-12">
      {chunks.map((chunk) => (
        <article
          className={cn(
            "scroll-mt-24 border-l-2 border-transparent py-3 pl-5 text-[15px] leading-8 transition-colors",
            activeChunkId === chunk.id && "border-primary bg-primary/5",
          )}
          data-chunk-id={chunk.id}
          data-locator={chunk.locator}
          id={`chunk-${chunk.id}`}
          key={chunk.id}
          onMouseUp={(event) => capture(chunk, event)}
        >
          <div className="whitespace-pre-wrap">{chunk.content}</div>
        </article>
      ))}
    </div>
  );
}


export function DocumentLearningPage() {
  const { fileId = "", workspaceId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const activeChunkId = searchParams.get("chunk") ?? undefined;
  const jobId = searchParams.get("job") ?? "";
  const chatSessionId = searchParams.get("chat") ?? "";
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<DocumentQueryScope>("file");
  const [selected, setSelected] = useState<{ chunk: FileTextChunk | null; text: string } | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [goalId, setGoalId] = useState("");
  const [scopeFileIds, setScopeFileIds] = useState<string[]>([fileId]);
  const [jobEventsExpanded, setJobEventsExpanded] = useState(false);
  const [outlineOpen, setOutlineOpen] = useState(false);
  const [rightPanel, setRightPanel] = useState<"chat" | "evidence" | "graph">("chat");
  const [mobilePane, setMobilePane] = useState<"outline" | "reader" | "chat">("reader");
  const [chatQuestion, setChatQuestion] = useState<{ id: string; text: string }>();
  const [selectionMenu, setSelectionMenu] = useState<{ left: number; top: number } | null>(null);
  const [embeddedImages, setEmbeddedImages] = useState<Array<{ id: string; blob: Blob; filename: string; locator: Record<string, unknown> }>>([]);
  const [conceptBranches, setConceptBranches] = useState<string[]>([]);

  function setActiveJobId(nextJobId: string) {
    const next = new URLSearchParams(searchParams);
    if (nextJobId) {
      next.set("job", nextJobId);
    } else {
      next.delete("job");
    }
    setSearchParams(next);
  }

  function setChatSessionId(nextSessionId: string) {
    const next = new URLSearchParams(searchParams);
    if (nextSessionId) {
      next.set("chat", nextSessionId);
    } else {
      next.delete("chat");
    }
    setSearchParams(next, { replace: true });
  }

  useEffect(() => {
    setScopeFileIds([fileId]);
  }, [fileId]);

  const files = useQuery({ queryKey: ["files"], queryFn: () => listFiles() });
  const file = files.data?.find((item) => item.id === fileId);
  const chunks = useQuery({
    queryKey: ["file-chunks", fileId],
    queryFn: () => listFileChunks(fileId),
    enabled: Boolean(fileId),
  });
  const revisions = useQuery({
    queryKey: ["document-revisions", fileId],
    queryFn: () => listDocumentRevisions(fileId),
    enabled: Boolean(fileId),
  });
  const goals = useQuery({ queryKey: ["goals"], queryFn: listGoals });
  const providers = useQuery({ queryKey: ["providers"], queryFn: listProviders });
  const blob = useQuery({
    queryKey: ["file-content", fileId],
    queryFn: () =>
      file?.size_bytes
        ? downloadFileForPreview(fileId, file.size_bytes)
        : downloadFile(fileId),
    enabled: Boolean(fileId && file),
  });
  const job = useQuery({
    queryKey: ["document-job", jobId],
    queryFn: () => getDocumentJob(jobId),
    enabled: Boolean(jobId),
    refetchInterval: (state) =>
      isDocumentJobTerminal(state.state.data?.status)
        ? false
        : 800,
  });
  const jobEvents = useQuery({
    queryKey: ["document-job-events", jobId],
    queryFn: () => listDocumentJobEvents(jobId),
    enabled: Boolean(jobId && job.data?.file_id === fileId),
    refetchInterval: isDocumentJobTerminal(job.data?.status) ? false : 800,
  });
  const transcriptions = useQuery({
    queryKey: ["audio-transcriptions", fileId],
    queryFn: () => listAudioTranscriptions(fileId),
    enabled: Boolean(fileId && (file?.mime_type.startsWith("audio/") || /\.(mp3|m4a|wav|webm|ogg|flac|aac)$/iu.test(file?.original_name ?? ""))),
    refetchInterval: (query) =>
      query.state.data?.[0]?.status === "running" ? 1_000 : false,
  });
  const transcriptionProviders = (providers.data ?? []).filter((provider) =>
    provider.enabled &&
    provider.remote_capability &&
    provider.provider_type === "openai_compatible_transcription",
  );
  const storedTranscriptionProvider = transcriptionProviders.find((provider) => {
    const modelId = providerCapabilityString(
      provider,
      "default_transcription_model_id",
    );
    return Boolean(modelId && !isRealtimeTranscriptionModel(modelId));
  });
  const realtimeOnlyTranscription =
    transcriptionProviders.length > 0 && !storedTranscriptionProvider;

  useEffect(() => {
    if (!jobId || job.data?.status === undefined) return;
    setJobEventsExpanded(!isDocumentJobTerminal(job.data.status));
  }, [job.data?.status, jobId]);

  useEffect(() => {
    if (job.data?.status !== "completed" || job.data.file_id !== fileId) return;
    void queryClient.invalidateQueries({ queryKey: ["files"] });
    void queryClient.invalidateQueries({ queryKey: ["file-chunks", fileId] });
    void queryClient.invalidateQueries({ queryKey: ["document-revisions", fileId] });
    void queryClient.invalidateQueries({ queryKey: ["document-job-events", jobId] });
    // Re-indexing invalidates the stored chunk_id anchor; clear the selection
    // so a stale chunk_id is never sent (the user must re-select for the new
    // revision, which now degrades gracefully instead of erroring).
    setSelected(null);
    setSelectionMenu(null);
  }, [fileId, job.data?.file_id, job.data?.status, jobId, queryClient]);

  const activeChunk = chunks.data?.find((item) => item.id === activeChunkId);
  useEffect(() => {
    const page = pageForChunk(activeChunk);
    if (page) setPageNumber(page);
    if (activeChunkId) {
      requestAnimationFrame(() =>
        document.getElementById(`chunk-${activeChunkId}`)?.scrollIntoView({ block: "center" }),
      );
    }
  }, [activeChunk, activeChunkId]);

  const currentPageChunks = useMemo(() => {
    if (!file?.mime_type.includes("pdf")) return chunks.data ?? [];
    return (chunks.data ?? []).filter((item) => pageForChunk(item) === pageNumber);
  }, [chunks.data, file?.mime_type, pageNumber]);

  const pendingSelection = useMemo<PendingDocumentSelection | null>(() => {
    if (!selected || !file) return null;
    const revisionId =
      selected.chunk?.document_revision_id ?? revisions.data?.[0]?.id;
    if (!revisionId) return null;
    return {
      file_id: file.id,
      document_revision_id: revisionId,
      chunk_id: selected.chunk?.id ?? null,
      locator: selected.chunk?.locator_json ?? {},
      locator_label: selected.chunk?.locator ?? "",
      selected_text: selected.text,
    };
  }, [file, revisions.data, selected]);

  function selectDocumentText(
    text: string,
    locatorHint?: Record<string, unknown>,
    firstLineRect?: DOMRect,
    knownChunk?: FileTextChunk,
  ) {
    const normalized = normalizedSelection(text);
    if (!normalized) return;
    if (Array.from(text).length > 50_000) {
      toast.error("选区超过 50,000 字符，请缩小范围后再询问。");
      return;
    }
    const hintedPage =
      typeof locatorHint?.page === "number" ? locatorHint.page : undefined;
    const chunk = knownChunk ?? chunkForSelection(text, chunks.data ?? [], hintedPage);
    const fallbackRevisionId = revisions.data?.[0]?.id;
    const revisionId = chunk?.document_revision_id ?? fallbackRevisionId;
    if (!revisionId) {
      if (
        file &&
        requiresLearningIndex(
          file.original_name,
          file.mime_type,
          file.parse_capability,
        )
      ) {
        toast.error("该选区尚未绑定可验证的文档 Revision，请先建立索引。");
      }
      return;
    }
    if (!chunk) {
      // Cross-chunk / stale-index selection: degrade to whole-file context
      // with the selection text attached as an unverified hint instead of
      // rejecting the user's request.
      toast.warning("选区未能精确定位到分块，将以整文件上下文 + 选区提示发送。");
    }
    setSelected({ chunk: chunk ?? null, text });
    setSelectionMenu(
      firstLineRect
        ? { left: Math.max(8, firstLineRect.left + firstLineRect.width / 2), top: Math.max(8, firstLineRect.top - 10) }
        : null,
    );
  }

  async function openConceptBranch() {
    if (!pendingSelection || !file) return;
    let parentSessionId = chatSessionId;
    if (!parentSessionId) {
      const created = await createSession({
        title: `阅读 ${file.original_name}`,
        memory_enabled: false,
      });
      parentSessionId = created.id;
      setChatSessionId(created.id);
    }
    const branch = await createConceptBranch(parentSessionId, {
      title: pendingSelection.selected_text.slice(0, 80),
      document_selection: {
        file_id: pendingSelection.file_id,
        document_revision_id: pendingSelection.document_revision_id,
        chunk_id: pendingSelection.chunk_id,
        locator: pendingSelection.locator,
        selected_text: pendingSelection.selected_text,
        selected_text_hash: await sha256(pendingSelection.selected_text),
      },
      selected_sentence: pendingSelection.selected_text,
      surrounding_text: selected?.chunk?.content.slice(0, 8_000) ?? "",
      source_title: file.original_name,
    });
    await queryClient.invalidateQueries({ queryKey: ["sessions"] });
    setConceptBranches((current) => [...current.filter((id) => id !== branch.id), branch.id]);
    setSelectionMenu(null);
  }

  const transcription = useMutation({
    mutationFn: () => {
      if (!storedTranscriptionProvider) {
        throw new Error(
          realtimeOnlyTranscription
            ? "当前 ASR 模型仅支持实时听写。文件转写需在 Provider 管理中选择非 realtime 模型。"
            : "没有可用的文件转写 ASR Provider，请先在 Provider 管理中配置。",
        );
      }
      return transcribeAudioFile(fileId, {
        provider_id: storedTranscriptionProvider.id,
        model_id: providerCapabilityString(
          storedTranscriptionProvider,
          "default_transcription_model_id",
        ),
      });
    },
    onSuccess: () => {
      toast.success("音频转写已完成");
    },
    onError: (error) => {
      if (
        error instanceof ApiError &&
        error.code === "stored_transcription_model_required"
      ) {
        toast.error("当前 ASR 模型仅支持实时听写", {
          description: "请在 Provider 管理中选择支持文件转写的非 realtime 模型。",
        });
        return;
      }
      toast.error(error.message);
    },
    onSettled: () =>
      queryClient.invalidateQueries({
        queryKey: ["audio-transcriptions", fileId],
      }),
  });

  const preview = useMutation({
    mutationFn: async () => {
      const anchor = activeChunk ?? currentPageChunks[0] ?? chunks.data?.[0];
      const effectiveScope = selected ? "selection" : scope;
      const effectiveFileIds = effectiveScope === "files" ? scopeFileIds : [fileId];
      return previewDocumentQuery({
        query: query.trim(),
        file_ids: effectiveFileIds,
        scope: effectiveScope,
        locator: selected?.chunk
          ? { chunk_id: selected.chunk.id }
          : effectiveScope === "page"
            ? { page: pageNumber }
            : effectiveScope === "section"
              ? { chunk_id: anchor?.id, section_path: anchor?.section_path ?? [] }
              : {},
        selected_text: selected?.text,
        selected_text_hash: selected ? await sha256(selected.text) : undefined,
        max_results: 8,
      });
    },
    onError: (error) => toast.error(error.message),
  });
  const startJob = useMutation({
    mutationFn: () => createDocumentJob(fileId, `document-${createUuid()}`),
    onSuccess: (created) => {
      setActiveJobId(created.id);
      queryClient.setQueryData(["document-job", created.id], created);
      toast.success("文档处理任务已创建");
    },
    onError: (error) => toast.error(error.message),
  });
  const retryJob = useMutation({
    mutationFn: () => retryDocumentJob(jobId),
    onSuccess: (retried) => {
      setActiveJobId(retried.id);
      queryClient.setQueryData(["document-job", retried.id], retried);
      void queryClient.invalidateQueries({ queryKey: ["document-job-events", retried.id] });
      toast.success("已从失败阶段重新排队");
    },
    onError: (error) => toast.error(error.message),
  });
  const cancelJob = useMutation({
    mutationFn: () => cancelDocumentJob(jobId),
    onSuccess: (cancelled) => {
      setActiveJobId(cancelled.id);
      queryClient.setQueryData(["document-job", cancelled.id], cancelled);
      void queryClient.invalidateQueries({ queryKey: ["document-job-events", cancelled.id] });
      if (cancelled.status === "cancelled") {
        toast.success("文档处理任务已取消");
      } else {
        toast.message(`任务当前状态为${documentJobStatusLabel(cancelled.status)}`);
      }
    },
    onError: (error) => toast.error(error.message),
  });
  const graphSession = useMutation({
    mutationFn: () =>
      createSession({
        title: `从 ${file?.original_name ?? "文档"} 构建图谱`,
        goal_id: goalId,
        memory_enabled: true,
      }),
    onSuccess: (session) =>
      navigate(`/w/${workspaceId}/chat/${session.id}`, {
        state: {
          pendingPrompt: `请基于文档《${file?.original_name ?? ""}》和已确认目标，提取需要学习的概念、前置关系与证据，生成候选目标图谱供我审核。`,
          pendingFileIds: scopeFileIds,
          pendingGraphAction: "propose_create",
        },
      }),
    onError: (error) => toast.error(error.message),
  });

  function focusHit(hit: DocumentQueryHit) {
    const next = new URLSearchParams(searchParams);
    next.set("chunk", hit.chunk_id);
    setSearchParams(next);
    const page = hit.locator_json.page;
    if (typeof page === "number") setPageNumber(page);
  }

  if (files.isPending || chunks.isPending) {
    return <div className="grid min-h-[70svh] place-items-center"><LoaderCircle className="size-5 animate-spin" /></div>;
  }
  if (!file) {
    return <div className="grid min-h-[70svh] place-items-center text-sm text-muted-foreground">文档不存在或无权访问。</div>;
  }

  const previewKind = resolveFilePreviewKind(file.original_name, file.mime_type);
  const isAudio = previewKind === "audio";
  const indexRequired = requiresLearningIndex(
    file.original_name,
    file.mime_type,
    file.parse_capability,
  );
  const hasOriginalPreview = previewKind !== "unsupported";
  // 仅 PDF / DOCX / DOC 支持证据检索与图谱构建；其余格式隐藏这两个标签页。
  const supportsEvidenceAndGraph =
    /\.(pdf|docx|doc)$/i.test(file.original_name) ||
    [
      "application/pdf",
      "application/msword",
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ].includes(file.mime_type);
  // HTML 页面在沙箱 iframe 中整体预览，证据检索与图谱构建不适用。
  const chatOnlyPanel = previewKind === "html" || !supportsEvidenceAndGraph;
  const effectiveRightPanel = chatOnlyPanel ? "chat" : rightPanel;
  const confirmedGoals = (goals.data ?? []).filter((item) =>
    ["confirmed", "candidate_ready", "approved"].includes(item.status),
  );
  const documentJob = job.data;
  const jobBelongsToFile = !documentJob || documentJob.file_id === fileId;
  const jobIsActive = Boolean(
    documentJob && jobBelongsToFile && !isDocumentJobTerminal(documentJob.status),
  );
  const jobCanRetry = Boolean(
    documentJob &&
      jobBelongsToFile &&
      retryableDocumentJobStatuses.has(documentJob.status),
  );
  const originalUnavailable = blob.isError && !blob.data;

  return (
    <div className="min-h-[calc(100svh-3.5rem)] bg-background lg:min-h-svh">
      <header className="sticky top-14 z-20 flex flex-wrap items-center gap-3 border-b bg-background/95 px-4 py-3 backdrop-blur lg:top-0">
        <Button aria-label="返回资料库" onClick={() => navigate(`/w/${workspaceId}/sources`)} size="icon-sm" variant="ghost">
          <ArrowLeft />
        </Button>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-sm font-semibold">{file.original_name}</h1>
          <p className="text-[11px] text-muted-foreground">
            {revisions.data?.[0]
              ? `版本 ${revisions.data[0].revision_no} · ${chunks.data?.length ?? 0} 个可定位内容块`
              : "尚未建立文档版本"}
          </p>
        </div>
        <Button
          aria-pressed={outlineOpen}
          className="hidden 2xl:inline-flex"
          onClick={() => setOutlineOpen((current) => !current)}
          size="sm"
          variant="ghost"
        >
          <PanelLeft className="size-3.5" />
          {outlineOpen ? "隐藏目录" : "显示目录"}
        </Button>
        {file.parse_status !== "indexed" && !jobId && indexRequired ? (
          <Button disabled={startJob.isPending} onClick={() => startJob.mutate()} size="sm">
            {startJob.isPending ? <LoaderCircle className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
            建立索引
          </Button>
        ) : null}
      </header>

      {jobId && (!documentJob || jobIsActive) ? (
        <div className="border-b bg-muted/20 px-5 py-2">
          <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-2 text-xs">
            <span className="font-medium">持久任务</span>
            {documentJob ? <Badge variant="outline">{documentJobStatusLabel(documentJob.status)}</Badge> : null}
            <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-muted-foreground" title={jobId}>{jobId}</span>
            <Button
              aria-label="刷新文档处理任务状态和事件"
              disabled={job.isFetching || jobEvents.isFetching}
              onClick={() => {
                void job.refetch();
                void jobEvents.refetch();
              }}
              size="icon-xs"
              variant="ghost"
            >
              <RefreshCw className={job.isFetching || jobEvents.isFetching ? "size-3 animate-spin" : "size-3"} />
            </Button>
            {jobIsActive ? (
              <Button
                disabled={cancelJob.isPending}
                onClick={() => cancelJob.mutate()}
                size="xs"
                variant="outline"
              >
                {cancelJob.isPending ? <LoaderCircle className="size-3 animate-spin" /> : null}
                取消任务
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}

      {job.isError ? (
        <div className="border-b border-destructive/20 bg-destructive/5 px-5 py-3" role="alert">
          <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3 text-xs">
            <span className="font-semibold text-destructive">无法读取持久任务</span>
            <span className="min-w-0 flex-1 text-muted-foreground">{job.error.message}</span>
            <Button disabled={job.isFetching} onClick={() => void job.refetch()} size="sm" variant="outline">重新读取</Button>
            <Button onClick={() => setActiveJobId("")} size="sm" variant="ghost">清除任务引用</Button>
          </div>
        </div>
      ) : null}

      {documentJob && !jobBelongsToFile ? (
        <div className="border-b border-destructive/20 bg-destructive/5 px-5 py-3" role="alert">
          <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3 text-xs">
            <span className="font-semibold text-destructive">任务不属于当前文件</span>
            <span className="min-w-0 flex-1 text-muted-foreground">
              已阻止读取事件、取消和重试；请清除 URL 中的任务引用。
            </span>
            <Button onClick={() => setActiveJobId("")} size="sm" variant="outline">
              清除任务引用
            </Button>
          </div>
        </div>
      ) : null}

      {documentJob && jobIsActive ? (
        <div className="border-b bg-primary/5 px-5 py-2">
          <div className="mx-auto flex max-w-5xl items-center gap-3 text-xs">
            <span className="min-w-0 flex-1 truncate font-medium">正在建立索引 · {documentJob.stage}</span>
            <span className="tabular-nums text-muted-foreground">{documentJob.progress}%</span>
          </div>
        </div>
      ) : null}
      {documentJob && jobCanRetry ? (
        <div className="border-b border-destructive/20 bg-destructive/5 px-5 py-3" role="alert">
          <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3 text-xs">
            <span className="font-semibold text-destructive">
              {documentJob.status === "cancelled" ? "索引已取消" : documentJob.status === "interrupted" ? "索引已中断" : "索引失败"}
            </span>
            <span className="min-w-0 flex-1 text-muted-foreground">
              {documentJob.error_code && documentJob.error_message
                ? `${documentJob.error_code} · ${documentJob.error_message}`
                : "任务没有被标记为成功；可从持久状态重新排队。"}
            </span>
            <Button
              disabled={retryJob.isPending}
              onClick={() => retryJob.mutate()}
              size="sm"
              variant="outline"
            >
              {retryJob.isPending ? <LoaderCircle className="size-4 animate-spin" /> : null}
              重试
            </Button>
          </div>
        </div>
      ) : null}
      {jobId && jobBelongsToFile && (jobIsActive || jobCanRetry) ? (
        <details
          className="border-b bg-background px-5 py-2"
          onToggle={(event) => setJobEventsExpanded(event.currentTarget.open)}
          open={jobEventsExpanded}
        >
          <summary className="mx-auto flex max-w-5xl cursor-pointer list-none items-center gap-2 text-xs font-medium">
            持久事件
            {documentJob ? <Badge variant="outline">{documentJobStatusLabel(documentJob.status)}</Badge> : null}
            <span className="font-mono text-[10px] text-muted-foreground">{jobEvents.data?.length ?? 0}</span>
          </summary>
          <div className="mx-auto mt-2 max-w-5xl divide-y rounded-md border text-[11px]">
            {jobEvents.isPending ? (
              <div className="flex items-center gap-2 p-3 text-muted-foreground"><LoaderCircle className="size-3 animate-spin" />读取服务端事件…</div>
            ) : null}
            {jobEvents.isError ? (
              <div className="flex flex-wrap items-center gap-2 p-3 text-destructive" role="alert">
                <span className="min-w-0 flex-1">无法读取持久事件：{jobEvents.error.message}</span>
                <Button disabled={jobEvents.isFetching} onClick={() => void jobEvents.refetch()} size="xs" variant="outline">重新读取</Button>
              </div>
            ) : null}
            {jobEvents.data?.map((event) => (
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 p-3" key={event.id}>
                <span className="w-8 font-mono text-muted-foreground">#{event.sequence}</span>
                <span className="min-w-28 font-medium">{event.event_type}</span>
                <span className="min-w-0 flex-1 text-muted-foreground">{documentJobEventSummary(event)}</span>
                <time className="font-mono text-[10px] text-muted-foreground" dateTime={event.created_at}>{formatEventTime(event.created_at)}</time>
              </div>
            ))}
            {jobEvents.isSuccess && !jobEvents.data.length ? (
              <p className="p-3 text-muted-foreground">服务端尚未写入事件。</p>
            ) : null}
          </div>
        </details>
      ) : null}

      <div className="sticky top-[7.1rem] z-20 grid grid-cols-3 border-b bg-background p-1 xl:hidden">
        <Button
          aria-pressed={mobilePane === "outline"}
          onClick={() => setMobilePane("outline")}
          size="sm"
          variant={mobilePane === "outline" ? "secondary" : "ghost"}
        >
          <PanelLeft className="size-3.5" />定位
        </Button>
        <Button
          aria-pressed={mobilePane === "reader"}
          onClick={() => setMobilePane("reader")}
          size="sm"
          variant={mobilePane === "reader" ? "secondary" : "ghost"}
        >
          <BookOpenText className="size-3.5" />原文
        </Button>
        <Button
          aria-pressed={mobilePane === "chat"}
          onClick={() => setMobilePane("chat")}
          size="sm"
          variant={mobilePane === "chat" ? "secondary" : "ghost"}
        >
          <PanelRight className="size-3.5" />对话
        </Button>
      </div>

      <div
        className={cn(
          "grid min-h-[calc(100svh-7rem)] xl:min-h-[calc(100svh-4.25rem)] xl:grid-cols-[minmax(0,1fr)_23rem]",
          outlineOpen && "2xl:grid-cols-[13rem_minmax(0,1fr)_23rem]",
        )}
      >
        <aside
          className={cn(
            "border-r bg-muted/15",
            mobilePane === "outline" ? "block" : "hidden",
            outlineOpen ? "2xl:block" : "2xl:hidden",
            "xl:hidden",
          )}
        >
          <div className="sticky top-[7.5rem] max-h-[calc(100svh-7.5rem)] overflow-auto p-4 2xl:top-[4.25rem] 2xl:max-h-[calc(100svh-4.25rem)]">
            <div className="mb-3 flex items-center gap-2 text-xs font-semibold"><BookOpenText className="size-4" />文档定位</div>
            <nav aria-label="文档内容定位" className="space-y-0.5">
              {(chunks.data ?? []).map((chunk) => (
                <button
                  className={cn(
                    "block w-full truncate rounded-md px-2 py-2 text-left text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
                    activeChunkId === chunk.id && "bg-primary/10 text-primary",
                  )}
                  key={chunk.id}
                  onClick={() => focusHit({
                    rank: chunk.ordinal,
                    score: 0,
                    chunk_id: chunk.id,
                    file_id: chunk.file_id,
                    document_revision_id: chunk.document_revision_id,
                    filename: file.original_name,
                    locator: chunk.locator,
                    locator_json: chunk.locator_json,
                    section_path: chunk.section_path,
                    quote: chunk.content,
                    content_hash: chunk.content_hash,
                  })}
                  title={`${chunkOutlineLabel(chunk)} · ${chunk.locator}`}
                  type="button"
                >
                  <span className="mr-2 font-mono text-[10px]">{String(chunk.ordinal).padStart(2, "0")}</span>
                  {chunkOutlineLabel(chunk)}
                </button>
              ))}
            </nav>
            {!chunks.data?.length && indexRequired ? (
              <p className="mt-3 text-[11px] leading-5 text-muted-foreground">
                原文件仍可预览；建立索引后这里会显示可验证定位。
              </p>
            ) : null}
          </div>
        </aside>

        <main className={cn("min-w-0 bg-card xl:block", mobilePane === "reader" ? "block" : "hidden")}>
          {blob.isPending && hasOriginalPreview ? (
            <div className="grid min-h-[36rem] place-items-center text-sm text-muted-foreground">
              <div className="flex items-center gap-2">
                <LoaderCircle className="size-4 animate-spin" />
                正在读取原文件…
              </div>
            </div>
          ) : null}
          {originalUnavailable ? (
            <div className="flex flex-wrap items-center gap-3 border-b border-destructive/20 bg-destructive/5 px-5 py-3 text-xs" role="alert">
              <div className="min-w-0 flex-1">
                <p className="font-semibold text-destructive">原文件暂时无法加载</p>
                <p className="mt-0.5 truncate text-muted-foreground" title={blob.error.message}>
                  {chunks.data?.length ? "已切换到可定位的索引文本。" : blob.error.message}
                </p>
              </div>
              <Button disabled={blob.isFetching} onClick={() => void blob.refetch()} size="sm" variant="outline">
                {blob.isFetching ? <LoaderCircle className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
                重试原文件
              </Button>
            </div>
          ) : null}
          {blob.data && hasOriginalPreview ? (
            <FilePreviewCanvas
              audioDetails={isAudio ? (
              <section className="audio-transcript">
                <div className="audio-transcript__header">
                  <div><p>ASR 文稿</p><span>远程 Provider 结果会持久保存</span></div>
                  <Button
                    disabled={
                      transcription.isPending ||
                      transcriptions.data?.[0]?.status === "running" ||
                      !storedTranscriptionProvider
                    }
                    onClick={() => transcription.mutate()}
                    size="sm"
                    title={
                      realtimeOnlyTranscription
                        ? "当前模型仅支持实时听写；文件转写需选择非 realtime 模型"
                        : storedTranscriptionProvider
                          ? undefined
                          : "请先配置文件转写 ASR Provider"
                    }
                  >
                    {transcription.isPending || transcriptions.data?.[0]?.status === "running" ? (
                      <LoaderCircle className="size-4 animate-spin" />
                    ) : (
                      <Sparkles className="size-4" />
                    )}
                    {transcription.isPending || transcriptions.data?.[0]?.status === "running"
                      ? "正在转写"
                      : transcriptions.data?.[0]?.status === "completed"
                        ? "重新转写"
                        : transcriptions.data?.[0]?.status === "failed"
                          ? "重试转写"
                          : "开始转写"}
                  </Button>
                </div>
                {transcriptions.isPending ? (
                  <p className="audio-transcript__empty">正在读取转写记录…</p>
                ) : transcriptions.isError ? (
                  <div className="audio-transcript__error" role="alert">
                    <p>无法读取转写记录：{transcriptions.error.message}</p>
                    <Button
                      disabled={transcriptions.isFetching}
                      onClick={() => void transcriptions.refetch()}
                      size="xs"
                      variant="outline"
                    >
                      重新读取
                    </Button>
                  </div>
                ) : realtimeOnlyTranscription ? (
                  <p className="audio-transcript__error">
                    当前 ASR 模型仅支持实时听写。请在 Provider 管理中选择支持文件转写的非 realtime 模型。
                  </p>
                ) : !storedTranscriptionProvider ? (
                  <p className="audio-transcript__error">
                    尚未配置可用的文件转写 ASR Provider。
                  </p>
                ) : transcriptions.data?.[0]?.status === "completed" ? (
                  <div className="audio-transcript__text">{transcriptions.data[0].transcript}</div>
                ) : transcriptions.data?.[0]?.status === "failed" ? (
                  <p className="audio-transcript__error">
                    {transcriptions.data[0].error_code
                      ? `${transcriptions.data[0].error_code} · `
                      : ""}
                    {transcriptions.data[0].error_message ?? "音频转写失败"}
                  </p>
                ) : transcriptions.data?.[0]?.status === "running" ? (
                  <p className="audio-transcript__empty">服务端正在转写，结果完成后会自动刷新。</p>
                ) : (
                  <p className="audio-transcript__empty">尚无转写文稿。</p>
                )}
              </section>
              ) : undefined}
              blob={blob.data}
              filename={file.original_name}
              imageActions={previewKind === "image" ? (
              <div className="flex flex-wrap items-center justify-center gap-2">
                <Button
                  onClick={() =>
                    setEmbeddedImages((current) => [
                      ...current,
                      {
                        id: createUuid(),
                        blob: blob.data!,
                        filename: file.original_name,
                        locator: { file_id: file.id, kind: "primary_image" },
                      },
                    ])
                  }
                  size="sm"
                  type="button"
                >
                  <Sparkles className="size-3.5" />
                  发送给模型解析
                </Button>
                <p className="text-xs text-muted-foreground">
                  将本图加入右侧对话附件；发送时由原生多模态或识图 Provider 处理
                </p>
              </div>
              ) : undefined}
              mimeType={file.mime_type}
              onEmbeddedImage={(image) => setEmbeddedImages((current) => [
                ...current,
                { id: createUuid(), ...image },
              ])}
              onPdfPageChange={setPageNumber}
              onTextSelection={selectDocumentText}
              pdfPage={pageNumber}
            />
          ) : !hasOriginalPreview ? (
            <ChunkText
              activeChunkId={activeChunkId}
              chunks={chunks.data ?? []}
              onSelect={(chunk, text) => selectDocumentText(text, chunk.locator_json, undefined, chunk)}
            />
          ) : null}
          {originalUnavailable && hasOriginalPreview && chunks.data?.length ? (
            <ChunkText
              activeChunkId={activeChunkId}
              chunks={chunks.data}
              onSelect={(chunk, text) => selectDocumentText(text, chunk.locator_json, undefined, chunk)}
            />
          ) : null}
          {originalUnavailable && hasOriginalPreview && !chunks.data?.length ? (
            <div className="grid min-h-[28rem] place-items-center px-8 text-center text-sm text-muted-foreground">
              {indexRequired
                ? "原文件读取失败，且尚无可用的索引文本。请重试或重新建立索引。"
                : "原文件读取失败，请重试。"}
            </div>
          ) : null}
          {!chunks.data?.length && !hasOriginalPreview ? (
            <div className="grid min-h-[36rem] place-items-center p-8 text-center text-sm text-muted-foreground">
              <div>
                <FileQuestion className="mx-auto mb-3 size-7" />
                {indexRequired
                  ? "先建立索引，才能阅读可定位文本并进行文档问答。"
                  : "该文件暂不支持在学习页中预览。"}
              </div>
            </div>
          ) : null}
        </main>

        <aside className={cn("border-l bg-background xl:block", mobilePane === "chat" ? "block" : "hidden")}>
          <div
            className={cn(
              "sticky top-[7.5rem] flex min-h-[36rem] flex-col xl:top-[4.25rem]",
              jobId && jobBelongsToFile
                ? "h-[calc(100svh-10.5rem)] xl:h-[calc(100svh-6.75rem)]"
                : "h-[calc(100svh-8.25rem)] xl:h-[calc(100svh-4.25rem)]",
            )}
          >
            {!chatOnlyPanel ? (
              <div className="grid h-10 flex-none grid-cols-3 border-b p-1">
                <Button aria-pressed={effectiveRightPanel === "chat"} onClick={() => setRightPanel("chat")} size="sm" variant={effectiveRightPanel === "chat" ? "secondary" : "ghost"}>
                  <MessageSquareText className="size-3.5" />对话
                </Button>
                <Button aria-pressed={effectiveRightPanel === "evidence"} onClick={() => setRightPanel("evidence")} size="sm" variant={effectiveRightPanel === "evidence" ? "secondary" : "ghost"}>
                  <Search className="size-3.5" />证据
                </Button>
                <Button aria-pressed={effectiveRightPanel === "graph"} onClick={() => setRightPanel("graph")} size="sm" variant={effectiveRightPanel === "graph" ? "secondary" : "ghost"}>
                  <Network className="size-3.5" />图谱
                </Button>
              </div>
            ) : null}

            {effectiveRightPanel === "chat" ? (
              <div className="min-h-0 flex-1">
                <DocumentChatPanel
                  embeddedImages={embeddedImages}
                  file={file}
                  onRemoveEmbeddedImage={(id) => setEmbeddedImages((current) => current.filter((image) => image.id !== id))}
                  onClearSelection={() => setSelected(null)}
                  onSessionChange={setChatSessionId}
                  questionSeed={chatQuestion}
                  selection={pendingSelection}
                  sessionId={chatSessionId}
                  workspaceId={workspaceId}
                />
              </div>
            ) : null}

            {effectiveRightPanel === "evidence" ? (
              <div className="min-h-0 flex-1 overflow-auto p-4">
                <div className="flex items-center gap-2"><Focus className="size-4 text-primary" /><h2 className="text-sm font-semibold">证据检索</h2></div>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">先查看真实 FTS5 命中，再决定是否发送给模型。</p>
                {selected ? (
                  <div className="mt-4 border-l-2 border-primary bg-primary/5 px-3 py-2 text-xs">
                    <div className="flex items-center justify-between gap-2"><strong>已限定选区</strong><button className="text-muted-foreground hover:text-foreground" onClick={() => setSelected(null)} type="button">清除</button></div>
                    <p className="mt-1 line-clamp-3 leading-5 text-muted-foreground">{selected.text}</p>
                  </div>
                ) : null}
                <div className="mt-4 flex gap-2">
                  <select aria-label="问答范围" className="h-8 rounded-md border bg-background px-2 text-xs" onChange={(event) => setScope(event.target.value as DocumentQueryScope)} value={scope}>
                    <option value="file">当前文件</option>
                    <option value="page">当前页</option>
                    <option value="section">当前章节</option>
                    <option value="files">所选资料</option>
                  </select>
                  <Badge className="font-mono" variant="outline">FTS5</Badge>
                </div>
                <details className="mt-3 border-y py-2 text-xs">
                  <summary className="cursor-pointer select-none text-muted-foreground">资料范围 · {scopeFileIds.length} 份已索引文件</summary>
                  <div className="mt-2 max-h-32 space-y-1 overflow-auto">
                    {(files.data ?? []).filter((item) => item.parse_status === "indexed").map((item) => (
                      <label className="flex items-center gap-2 py-1" key={item.id}>
                        <input
                          checked={scopeFileIds.includes(item.id)}
                          disabled={item.id === fileId}
                          onChange={(event) => {
                            setScopeFileIds((current) => event.target.checked ? [...new Set([...current, item.id])] : current.filter((id) => id !== item.id));
                            if (event.target.checked) setScope("files");
                          }}
                          type="checkbox"
                        />
                        <span className="truncate">{item.original_name}</span>
                      </label>
                    ))}
                  </div>
                </details>
                <Textarea className="mt-3 min-h-28 resize-none" onChange={(event) => setQuery(event.target.value)} placeholder="询问定义、原因、对比或章节关系…" value={query} />
                <Button className="mt-2 w-full" disabled={!query.trim() || preview.isPending || file.parse_status !== "indexed"} onClick={() => preview.mutate()}>
                  {preview.isPending ? <LoaderCircle className="size-4 animate-spin" /> : <Search className="size-4" />}预览证据
                </Button>
                {preview.data ? (
                  <section className="mt-5">
                    <div className="flex items-center justify-between"><h3 className="text-xs font-semibold">检索命中</h3><span className="font-mono text-[10px] text-muted-foreground">{preview.data.hits.length} hits</span></div>
                    <div className="mt-2 divide-y border-y">
                      {preview.data.hits.map((hit) => (
                        <button className="block w-full py-3 text-left hover:bg-muted/40" key={hit.chunk_id} onClick={() => focusHit(hit)} type="button">
                          <div className="flex items-center gap-2 text-[10px] text-muted-foreground"><span className="font-mono">#{hit.rank}</span><span>{hit.locator}</span></div>
                          <p className="mt-1 line-clamp-3 text-xs leading-5">{hit.quote}</p>
                        </button>
                      ))}
                    </div>
                    {preview.data.warnings.map((warning) => <p className="mt-2 text-[10px] leading-4 text-amber-700" key={warning}>{warning}</p>)}
                    <Button
                      className="mt-3 w-full"
                      onClick={() => {
                        setChatQuestion({ id: createUuid(), text: query });
                        setRightPanel("chat");
                      }}
                      variant="outline"
                    >
                      <MessageSquareText className="size-4" />在右侧对话中询问
                    </Button>
                  </section>
                ) : null}
              </div>
            ) : null}

            {effectiveRightPanel === "graph" ? (
              <div className="min-h-0 flex-1 overflow-auto p-4">
                <div className="flex items-center gap-2"><Network className="size-4" /><h2 className="text-sm font-semibold">从文档构建图谱</h2></div>
                <p className="mt-1 text-[11px] leading-5 text-muted-foreground">模型只生成带来源的候选变更；确认前不会发布图谱。</p>
                <select aria-label="选择已确认目标" className="mt-4 h-9 w-full rounded-md border bg-background px-2 text-xs" onChange={(event) => setGoalId(event.target.value)} value={goalId}>
                  <option value="">选择已确认 Goal</option>
                  {confirmedGoals.map((goal) => <option key={goal.id} value={goal.id}>{goal.title}</option>)}
                </select>
                <Button className="mt-2 w-full" disabled={!goalId || graphSession.isPending || file.parse_status !== "indexed"} onClick={() => graphSession.mutate()} variant="secondary">
                  {graphSession.isPending ? <LoaderCircle className="size-4 animate-spin" /> : <Sparkles className="size-4" />}生成候选图谱
                </Button>
                <p className="mt-6 border-t pt-4 text-[10px] leading-5 text-muted-foreground">阅读、滚动和检索不会直接提高掌握度。</p>
              </div>
            ) : null}
          </div>
        </aside>
      </div>
      {selectionMenu && pendingSelection ? (
        <div
          className="document-selection-menu"
          style={{ left: selectionMenu.left, top: selectionMenu.top }}
        >
          <Button
            onClick={() => { setRightPanel("chat"); setMobilePane("chat"); setSelectionMenu(null); }}
            size="sm"
            variant="ghost"
          >
            添加到对话框
          </Button>
          <Button onClick={() => void openConceptBranch().catch((error: Error) => toast.error(error.message))} size="sm" variant="ghost">
            单独解释
          </Button>
        </div>
      ) : null}
      {conceptBranches.length ? (
        <ConceptBranchWorkspace
          branchIds={conceptBranches}
          file={file}
          onCloseBranch={(id) => setConceptBranches((current) => current.filter((branchId) => branchId !== id))}
          selection={pendingSelection}
          workspaceId={workspaceId}
        />
      ) : null}
    </div>
  );
}
