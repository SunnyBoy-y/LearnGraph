import type { ActionResponse } from "@/types/common";
import { createUuid } from "@/lib/uuid";
import type {
  DocumentJob,
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

export function parseFile(fileId: string): Promise<FileRecord> {
  return apiClient.post<FileRecord>(
    `/files/${encodeURIComponent(fileId)}/parse`,
  );
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

export function downloadFile(fileId: string): Promise<Blob> {
  return apiClient.getBlob(`/files/${encodeURIComponent(fileId)}/content`)
}

/** Max bytes to load into browser memory for in-app preview (16 MiB). */
const PREVIEW_MAX_BYTES = 16 * 1024 * 1024;

/**
 * Download a file for preview, capping at PREVIEW_MAX_BYTES via Range header
 * when the file is large. This prevents large files (e.g. 200 MiB PDFs) from
 * exhausting browser memory on mobile / low-end devices.
 */
export function downloadFileForPreview(
  fileId: string,
  fileSizeBytes: number,
): Promise<Blob> {
  if (fileSizeBytes <= PREVIEW_MAX_BYTES) {
    return downloadFile(fileId);
  }
  return apiClient.getBlobRange(
    `/files/${encodeURIComponent(fileId)}/content`,
    0,
    PREVIEW_MAX_BYTES - 1,
  );
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
