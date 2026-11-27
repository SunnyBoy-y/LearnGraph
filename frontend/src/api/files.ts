import type { ActionResponse } from "@/types/common";
import {
  adaptiveChunkUploadThreshold,
  adaptivePollInterval,
  adaptiveUploadChunkSize,
  computeJitteredBackoffMs,
  networkQualityStore,
} from "@/lib/network-quality";
import { createUuid } from "@/lib/uuid";
import { authStore } from "./auth-store";
import type {
  DocumentJob,
  DocumentJobStatus,
  AudioTranscription,
  DocumentJobEvent,
  DocumentQueryPreview,
  DocumentQueryPreviewRequest,
  DocumentRevision,
  FileBatchDeleteImpact,
  FileBatchDeleteResult,
  FileParserCapability,
  FileRecord,
  FileReference,
  FileStorageSummary,
  SessionFile,
  FileTextChunk,
} from "@/types/files";
import type { DeleteImpact } from "@/types/workflow";

import { apiClient, ApiError } from "./client";
import type { ApiRequestOptions } from "./client";

export function listFiles(options?: {
  q?: string;
  limit?: number;
}): Promise<FileRecord[]> {
  const params = new URLSearchParams();
  if (options?.q?.trim()) params.set("q", options.q.trim());
  if (options?.limit != null) params.set("limit", String(options.limit));
  const query = params.toString();
  return apiClient.get<FileRecord[]>(query ? `/files?${query}` : "/files");
}

export function getFile(fileId: string, options: ApiRequestOptions = {}): Promise<FileRecord> {
  return apiClient.get<FileRecord>(`/files/${encodeURIComponent(fileId)}`, options);
}

export function readFileRange(fileId: string, start: number, end: number, options: ApiRequestOptions = {}, officePreview = false) {
  return apiClient.getByteRange(`/files/${encodeURIComponent(fileId)}/${officePreview ? "preview.pdf" : "content"}`, start, end, options);
}

export async function fileStreamUrl(fileId: string, options: ApiRequestOptions = {}) {
  const { ticket } = await apiClient.get<{ ticket: string }>(`/files/${encodeURIComponent(fileId)}/stream-ticket`, options);
  return `${apiClient.baseUrl.replace(/\/$/, "")}/files/${encodeURIComponent(fileId)}/stream?ticket=${encodeURIComponent(ticket)}`;
}

/** Exact name + SHA-256 match for chat upload reuse (no re-store). */
export function lookupFile(options: {
  name: string;
  sha256: string;
}): Promise<FileRecord> {
  const params = new URLSearchParams({
    name: options.name,
    sha256: options.sha256,
  });
  return apiClient.get<FileRecord>(`/files/lookup?${params.toString()}`);
}

export function getFileStorageSummary(): Promise<FileStorageSummary> {
  return apiClient.get<FileStorageSummary>("/files/storage-summary");
}

export function listFileParserCapabilities(): Promise<FileParserCapability[]> {
  return apiClient.get<FileParserCapability[]>("/files/parser-capabilities");
}

export interface UploadProgress {
  loadedBytes: number;
  totalBytes: number;
  percent: number;
  chunkIndex?: number;
  totalChunks?: number;
  resumed?: boolean;
}

export interface ChunkUploadSession {
  upload_id: string;
  filename: string;
  total_size: number;
  chunk_size: number;
  total_chunks: number;
  uploaded_chunks: number[];
  already_exists: boolean;
  existing_file: FileRecord | null;
}

interface ChunkUploadPartResponse {
  upload_id: string;
  chunk_index: number;
  size_bytes: number;
  uploaded_chunks: number[];
}

const RESUME_STORAGE_PREFIX = "learngraph:chunk-upload:v1:";
const MAX_CHUNK_RETRIES = 4;

function resumeStorageKey(file: File, source?: string): string {
  const workspaceId = authStore.getWorkspaceId() ?? "default";
  return `${RESUME_STORAGE_PREFIX}${workspaceId}:${source ?? "upload"}:${file.name}:${file.size}:${file.lastModified}`;
}

function readSavedUploadId(key: string): string | null {
  try {
    if (typeof window === "undefined") return null;
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { uploadId?: unknown; savedAt?: unknown };
    if (
      typeof parsed.uploadId !== "string" ||
      !parsed.uploadId ||
      typeof parsed.savedAt !== "number" ||
      Date.now() - parsed.savedAt > 23 * 3600 * 1000
    ) {
      window.localStorage.removeItem(key);
      return null;
    }
    return parsed.uploadId;
  } catch {
    return null;
  }
}

function saveUploadId(key: string, uploadId: string): void {
  try {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(
      key,
      JSON.stringify({ uploadId, savedAt: Date.now() }),
    );
  } catch {
    // Private mode / quota errors never block uploading.
  }
}

function clearSavedUploadId(key: string): void {
  try {
    if (typeof window === "undefined") return;
    window.localStorage.removeItem(key);
  } catch {
    // Ignore storage cleanup failures.
  }
}

function isTransientNetworkFailure(error: unknown): boolean {
  if (error instanceof ApiError) {
    return error.status === 0 || error.status === 502 || error.status === 503 || error.status === 504;
  }
  return !(error instanceof DOMException && error.name === "AbortError");
}

function waitWithSignal(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
      return;
    }
    const finish = () => {
      clearTimeout(timer);
      if (typeof window !== "undefined") window.removeEventListener("online", finish);
      signal?.removeEventListener("abort", onAbort);
      resolve();
    };
    const onAbort = () => {
      clearTimeout(timer);
      if (typeof window !== "undefined") window.removeEventListener("online", finish);
      reject(signal?.reason ?? new DOMException("Aborted", "AbortError"));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
    if (typeof window !== "undefined" && navigator.onLine === false) {
      window.addEventListener("online", finish, { once: true });
    }
    const timer = setTimeout(finish, ms);
  });
}

async function uploadFileChunked(
  file: File,
  options: ApiRequestOptions & {
    source?: "library_upload" | "chat_attachment";
    onProgress?: (progress: UploadProgress) => void;
    chunkSize?: number;
  } = {},
): Promise<FileRecord> {
  const { source, onProgress, chunkSize: explicitChunkSize, ...requestOptions } = options;
  const storageKey = resumeStorageKey(file, source);
  let session: ChunkUploadSession | null = null;
  let resumed = false;

  const savedId = readSavedUploadId(storageKey);
  if (savedId) {
    try {
      const existing = await apiClient.get<ChunkUploadSession>(
        `/files/uploads/${encodeURIComponent(savedId)}`,
        requestOptions,
      );
      if (existing.total_size === file.size && existing.filename === (file.name || "upload.bin")) {
        session = existing;
        resumed = existing.uploaded_chunks.length > 0;
      }
    } catch {
      clearSavedUploadId(storageKey);
    }
  }

  if (!session) {
    const chunkSize = explicitChunkSize ?? adaptiveUploadChunkSize();
    session = await apiClient.post<ChunkUploadSession>(
      "/files/uploads/init",
      {
        filename: file.name || "upload.bin",
        total_size: file.size,
        chunk_size: chunkSize,
        content_type: file.type || "application/octet-stream",
        source: source ?? "upload",
      },
      requestOptions,
    );
    if (session.already_exists && session.existing_file) {
      onProgress?.({
        loadedBytes: file.size,
        totalBytes: file.size,
        percent: 100,
        totalChunks: session.total_chunks,
        resumed: true,
      });
      return session.existing_file;
    }
    saveUploadId(storageKey, session.upload_id);
  }

  const uploadedSet = new Set<number>(session.uploaded_chunks);
  const computeLoadedBytes = () => {
    let bytes = 0;
    for (const idx of uploadedSet) {
      const start = idx * session.chunk_size;
      const end = Math.min(file.size, start + session.chunk_size);
      bytes += Math.max(0, end - start);
    }
    return bytes;
  };

  onProgress?.({
    loadedBytes: computeLoadedBytes(),
    totalBytes: file.size,
    percent: Math.round((computeLoadedBytes() / Math.max(1, file.size)) * 100),
    totalChunks: session.total_chunks,
    resumed,
  });

  for (let chunkIndex = 0; chunkIndex < session.total_chunks; chunkIndex += 1) {
    if (uploadedSet.has(chunkIndex)) continue;
    if (requestOptions.signal?.aborted) {
      throw requestOptions.signal.reason ?? new DOMException("Aborted", "AbortError");
    }
    const start = chunkIndex * session.chunk_size;
    const end = Math.min(file.size, start + session.chunk_size);
    const slice = file.slice(start, end);

    let chunkAttempt = 0;
    for (;;) {
      try {
        await apiClient.putRaw<ChunkUploadPartResponse>(
          `/files/uploads/${encodeURIComponent(session.upload_id)}/chunks/${chunkIndex}`,
          slice,
          {
            ...requestOptions,
            headers: {
              ...(requestOptions.headers instanceof Headers
                ? Object.fromEntries(requestOptions.headers.entries())
                : (requestOptions.headers as Record<string, string> | undefined)),
              "Content-Type": "application/octet-stream",
              "Idempotency-Key": `chunk-${session.upload_id}-${chunkIndex}`,
            },
          },
        );
        uploadedSet.add(chunkIndex);
        const loadedBytes = computeLoadedBytes();
        onProgress?.({
          loadedBytes,
          totalBytes: file.size,
          percent: Math.min(99, Math.round((loadedBytes / Math.max(1, file.size)) * 100)),
          chunkIndex,
          totalChunks: session.total_chunks,
          resumed,
        });
        break;
      } catch (error) {
        if (requestOptions.signal?.aborted || !isTransientNetworkFailure(error) || chunkAttempt >= MAX_CHUNK_RETRIES) {
          throw error;
        }
        chunkAttempt += 1;
        const delay = computeJitteredBackoffMs(800, chunkAttempt - 1, 10_000);
        await waitWithSignal(delay, requestOptions.signal);
      }
    }
  }

  const record = await apiClient.post<FileRecord>(
    `/files/uploads/${encodeURIComponent(session.upload_id)}/complete`,
    undefined,
    {
      ...requestOptions,
      headers: {
        ...(requestOptions.headers instanceof Headers
          ? Object.fromEntries(requestOptions.headers.entries())
          : (requestOptions.headers as Record<string, string> | undefined)),
        "Idempotency-Key": `chunk-complete-${session.upload_id}`,
      },
    },
  );
  clearSavedUploadId(storageKey);
  onProgress?.({
    loadedBytes: file.size,
    totalBytes: file.size,
    percent: 100,
    totalChunks: session.total_chunks,
    resumed,
  });
  return record;
}

export async function uploadFile(
  file: File,
  options: ApiRequestOptions & {
    source?: "library_upload" | "chat_attachment";
    onProgress?: (progress: UploadProgress) => void;
    chunkSize?: number;
    forceChunked?: boolean;
  } = {},
): Promise<FileRecord> {
  const { source, onProgress, chunkSize, forceChunked, ...requestOptions } = options;
  if (forceChunked || file.size >= adaptiveChunkUploadThreshold()) {
    return uploadFileChunked(file, { source, onProgress, chunkSize, ...requestOptions });
  }
  onProgress?.({ loadedBytes: 0, totalBytes: file.size, percent: 0 });
  const formData = new FormData();
  formData.set("file", file);
  const record = await apiClient.upload<FileRecord>(
    source ? `/files?source=${source}` : "/files",
    formData,
    requestOptions,
  );
  onProgress?.({ loadedBytes: file.size, totalBytes: file.size, percent: 100 });
  return record;
}

export function parseFile(fileId: string): Promise<DocumentJob> {
  // B1-8: parse is queued (202 + DocumentJob); poll pollParseJob for the result.
  return apiClient.post<DocumentJob>(
    `/files/${encodeURIComponent(fileId)}/parse`,
    undefined,
    { headers: { 'Idempotency-Key': `parse-${createUuid()}` } },
  );
}

const TERMINAL_DOCUMENT_JOB_STATUSES = new Set<DocumentJobStatus>([
  'completed',
  'failed',
  'cancelled',
  'interrupted',
]);

function isDocumentJobTerminal(status?: DocumentJobStatus): boolean {
  return status !== undefined && TERMINAL_DOCUMENT_JOB_STATUSES.has(status);
}

/**
 * Poll a parse document job until it reaches a terminal state.
 * Adapts poll spacing on weak networks and tolerates transient network errors
 * with jittered backoff instead of aborting on the first packet drop.
 */
export async function pollParseJob(
  job: DocumentJob,
  options: { intervalMs?: number; timeoutMs?: number; signal?: AbortSignal; maxConsecutiveErrors?: number } = {},
): Promise<DocumentJob> {
  const baseIntervalMs = options.intervalMs ?? 800;
  const timeoutMs = options.timeoutMs ?? 120_000;
  const maxConsecutiveErrors = options.maxConsecutiveErrors ?? 5;
  const deadline = Date.now() + timeoutMs;
  let current = job;
  let consecutiveErrors = 0;

  while (!isDocumentJobTerminal(current.status)) {
    if (options.signal?.aborted) {
      throw options.signal.reason ?? new DOMException("Aborted", "AbortError");
    }
    if (Date.now() > deadline) {
      throw new Error('解析任务超时，请稍后在解析页面查看进度');
    }
    const adaptive = adaptivePollInterval(baseIntervalMs, {
      pauseWhenOffline: false,
      pauseWhenHidden: false,
    });
    const waitMs =
      consecutiveErrors > 0
        ? computeJitteredBackoffMs(baseIntervalMs, consecutiveErrors, 10_000)
        : typeof adaptive === "number"
          ? adaptive
          : baseIntervalMs * (networkQualityStore.getSnapshot().tier === "good" ? 1 : 3);
    await waitWithSignal(waitMs, options.signal);
    try {
      current = await getDocumentJob(job.id);
      consecutiveErrors = 0;
    } catch (error) {
      if (options.signal?.aborted || !isTransientNetworkFailure(error)) {
        throw error;
      }
      consecutiveErrors += 1;
      if (consecutiveErrors > maxConsecutiveErrors) {
        throw error;
      }
    }
  }
  if (current.status === 'failed') {
    throw new Error(current.error_message || current.error_code || '解析任务失败');
  }
  if (current.status !== 'completed') {
    throw new Error(`解析任务未完成（${current.status}）`);
  }
  return current;
}

export function createDocumentJob(
  fileId: string,
  idempotencyKey: string,
): Promise<DocumentJob> {
  return apiClient.post<DocumentJob, { job_type: 'parse_index' }>(
    `/files/${encodeURIComponent(fileId)}/document-jobs`,
    { job_type: 'parse_index' },
    { headers: { 'Idempotency-Key': idempotencyKey } },
  );
}

export function getDocumentJob(jobId: string): Promise<DocumentJob> {
  return apiClient.get<DocumentJob>(
    `/document-jobs/${encodeURIComponent(jobId)}`,
  );
}

export function listDocumentJobEvents(jobId: string): Promise<DocumentJobEvent[]> {
  return apiClient.get<DocumentJobEvent[]>(
    `/document-jobs/${encodeURIComponent(jobId)}/events`,
  );
}

export function retryDocumentJob(jobId: string): Promise<DocumentJob> {
  return apiClient.post<DocumentJob>(
    `/document-jobs/${encodeURIComponent(jobId)}/retry`,
  );
}

export function cancelDocumentJob(jobId: string): Promise<DocumentJob> {
  return apiClient.post<DocumentJob>(
    `/document-jobs/${encodeURIComponent(jobId)}/cancel`,
  );
}

export function listDocumentRevisions(fileId: string): Promise<DocumentRevision[]> {
  return apiClient.get<DocumentRevision[]>(
    `/files/${encodeURIComponent(fileId)}/document-revisions`,
  );
}

export function previewDocumentQuery(
  payload: DocumentQueryPreviewRequest,
): Promise<DocumentQueryPreview> {
  return apiClient.post<DocumentQueryPreview, DocumentQueryPreviewRequest>(
    '/document-query/preview',
    payload,
  );
}

export function listFileChunks(fileId: string): Promise<FileTextChunk[]> {
  return apiClient.get<FileTextChunk[]>(
    `/files/${encodeURIComponent(fileId)}/chunks`,
  );
}

export function linkFileToGraph(fileId: string, graphId: string): Promise<FileReference> {
  return apiClient.post<FileReference>(`/files/${encodeURIComponent(fileId)}/references`, {
    target_type: "graph", target_id: graphId, relation: "learning_material",
  });
}

export function listFileReferences(fileId: string): Promise<FileReference[]> {
  return apiClient.get<FileReference[]>(
    `/files/${encodeURIComponent(fileId)}/references`,
  );
}

export function downloadFile(fileId: string, options: ApiRequestOptions = {}, maxBytes?: number): Promise<Blob> {
  return apiClient.getBlob(`/files/${encodeURIComponent(fileId)}/content`, options, maxBytes)
}

/**
 * Blob-based readers need the complete container, including its trailing
 * directory/xref. A byte prefix is not a valid preview of a large document.
 * Keep this entry point for callers that also have file-size metadata.
 */
export function downloadFileForPreview(
  fileId: string,
  _fileSizeBytes: number,
  options: ApiRequestOptions = {},
): Promise<Blob> {
  return downloadFile(fileId, options);
}

export function listAudioTranscriptions(fileId: string): Promise<AudioTranscription[]> {
  return apiClient.get<AudioTranscription[]>(
    `/files/${encodeURIComponent(fileId)}/transcriptions`,
  )
}

export function transcribeAudioFile(
  fileId: string,
  payload: { provider_id?: string; model_id?: string; language?: string },
): Promise<AudioTranscription> {
  return apiClient.post<AudioTranscription, typeof payload>(
    `/files/${encodeURIComponent(fileId)}/transcriptions`,
    payload,
    { headers: { 'Idempotency-Key': `audio-transcription-${createUuid()}` } },
  )
}

export function getFileDeleteImpact(fileId: string): Promise<DeleteImpact> {
  return apiClient.get<DeleteImpact>(
    `/files/${encodeURIComponent(fileId)}/delete-impact`,
  );
}

export function deleteFileConfirmed(
  fileId: string,
  confirmationText: string,
): Promise<ActionResponse> {
  return apiClient.post<ActionResponse, { confirmation_text: string }>(
    `/files/${encodeURIComponent(fileId)}/delete`,
    { confirmation_text: confirmationText },
  );
}

export function getFileBatchDeleteImpact(
  fileIds: string[],
): Promise<FileBatchDeleteImpact> {
  return apiClient.post<FileBatchDeleteImpact, { file_ids: string[] }>(
    "/files/batch-delete-impact",
    { file_ids: fileIds },
  );
}

export function deleteFilesBatch(
  fileIds: string[],
  confirmationText: string,
): Promise<FileBatchDeleteResult> {
  return apiClient.post<
    FileBatchDeleteResult,
    { file_ids: string[]; confirmation_text: string }
  >("/files/batch-delete", {
    file_ids: fileIds,
    confirmation_text: confirmationText,
  });
}

/** List every durable file tied to a chat session (unified file-area view). */
export function listSessionFiles(sessionId: string): Promise<SessionFile[]> {
  return apiClient.get<SessionFile[]>(
    `/sessions/${encodeURIComponent(sessionId)}/files`,
  );
}
