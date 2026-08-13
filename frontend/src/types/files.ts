import type { IsoDateTime } from './common'

export interface FileRecord {
  id: string
  workspace_id: string
  original_name: string
  mime_type: string
  size_bytes: number
  sha256: string
  storage_status: string
  parse_capability: string
  parse_status: string
  parser_name: string | null
  parser_version: string | null
  error_message: string | null
  created_at: IsoDateTime
}

export interface FileStorageSummary {
  file_count: number
  total_bytes: number
}

export interface FileBatchDeleteImpact {
  resource_type: 'file_batch'
  resource_id: string
  title: string
  confirmation_text: string
  file_ids: string[]
  impacts: Array<{ resource_type: string; count: number; action: string }>
}

export interface FileBatchDeleteResult {
  status: 'deleted'
  deleted_file_ids: string[]
  deleted_count: number
  impacts: FileBatchDeleteImpact['impacts']
}

export interface AudioTranscription {
  id: string
  workspace_id: string
  file_id: string
  provider_id: string
  model_id: string
  language: string | null
  status: 'queued' | 'running' | 'completed' | 'failed'
  transcript: string
  duration_seconds: number | null
  provider_request_id: string | null
  provider_trace: Record<string, unknown>
  error_code: string | null
  error_message: string | null
  created_at: IsoDateTime
  updated_at: IsoDateTime
  completed_at: IsoDateTime | null
}

export interface FileParserCapability {
  capability_id: string
  mode: 'built_in' | 'optional' | 'isolated'
  extensions: string[]
  available: boolean
  parser_name: string | null
  reason: string
}

export interface FileTextChunk {
  id: string
  workspace_id: string
  file_id: string
  document_revision_id: string | null
  ordinal: number
  locator: string
  locator_json: Record<string, unknown>
  section_path: string[]
  token_count: number
  content: string
  content_hash: string
  created_at: IsoDateTime
}

export interface DocumentRevision {
  id: string
  workspace_id: string
  file_id: string
  revision_no: number
  source_sha256: string
  size_bytes: number
  mime_detected: string
  processor_id: string | null
  processor_version: string | null
  config_hash: string
  status: string
  quality_report: Record<string, unknown>
  artifact_manifest: Record<string, unknown>
  created_by: string
  completed_at: IsoDateTime | null
  error_code: string | null
  created_at: IsoDateTime
  updated_at: IsoDateTime
}

export type DocumentJobStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted'

export interface DocumentJob {
  id: string
  workspace_id: string
  file_id: string
  document_revision_id: string | null
  job_type: string
  status: DocumentJobStatus
  stage: string
  progress: number
  parameters: Record<string, unknown>
  created_by: string
  started_at: IsoDateTime | null
  completed_at: IsoDateTime | null
  error_code: string | null
  error_message: string | null
  created_at: IsoDateTime
  updated_at: IsoDateTime
}

export interface DocumentJobEvent {
  id: string
  workspace_id: string
  job_id: string
  sequence: number
  event_type: string
  payload: Record<string, unknown>
  created_at: IsoDateTime
}

export interface DocumentCollectionItem {
  id: string
  workspace_id: string
  collection_id: string
  file_id: string
  document_revision_id: string | null
  added_by: string
  created_at: IsoDateTime
}

export interface DocumentCollection {
  id: string
  workspace_id: string
  name: string
  description: string
  project_id: string | null
  goal_id: string | null
  graph_id: string | null
  created_by: string
  items: DocumentCollectionItem[]
  created_at: IsoDateTime
  updated_at: IsoDateTime
}

export type DocumentQueryScope = 'selection' | 'page' | 'section' | 'file' | 'files'

export interface DocumentQueryPreviewRequest {
  query: string
  file_ids?: string[]
  collection_ids?: string[]
  scope: DocumentQueryScope
  locator?: Record<string, unknown>
  selected_text?: string
  selected_text_hash?: string
  max_results?: number
}

export interface DocumentQueryHit {
  rank: number
  score: number
  chunk_id: string
  file_id: string
  document_revision_id: string | null
  filename: string
  locator: string
  locator_json: Record<string, unknown>
  section_path: string[]
  quote: string
  content_hash: string
}

export interface DocumentQueryPreview {
  trace_id: string
  strategy: string
  scope: DocumentQueryScope
  hits: DocumentQueryHit[]
  warnings: string[]
}

export interface FileReference {
  id: string
  workspace_id: string
  file_id: string
  target_type: string
  target_id: string
  relation: string
  locator: string
  metadata_json: Record<string, unknown>
  created_at: IsoDateTime
}

/** One durable file tied to a chat session (unified file-area view). */
export interface SessionFile {
  file_id: string | null
  filename: string
  mime_type: string
  size_bytes: number
  origin:
    | 'user_attachment'
    | 'generated_image'
    | 'external_download'
    | 'agent_workspace_file'
    | 'session_workspace'
    | string
  relation: string | null
  path: string | null
  source: string | null
  message_id: string | null
  is_image: boolean
  storage_status: string | null
  prompt_summary: string | null
  created_at: string | null
}
