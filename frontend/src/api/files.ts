import type { ActionResponse } from "@/types/common";
import { createUuid } from "@/lib/uuid";
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

import { apiClient } from "./client";
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

export function uploadFile(
  file: File,
  options: ApiRequestOptions = {},
): Promise<FileRecord> {
  const formData = new FormData();
  formData.set("file", file);
  return apiClient.upload<FileRecord>("/files", formData, options);
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
 * Poll a parse document job until it reaches a terminal state (800 ms interval).
 * Resolves with the terminal job; throws on failure/cancellation/timeout.
 */
export async function pollParseJob(
  job: DocumentJob,
  options: { intervalMs?: number; timeoutMs?: number } = {},
): Promise<DocumentJob> {
  const intervalMs = options.intervalMs ?? 800;
  const timeoutMs = options.timeoutMs ?? 120_000;
  const deadline = Date.now() + timeoutMs;
  let current = job;
  while (!isDocumentJobTerminal(current.status)) {
    if (Date.now() > deadline) {
      throw new Error('解析任务超时，请稍后在解析页面查看进度');
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
    current = await getDocumentJob(job.id);
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
