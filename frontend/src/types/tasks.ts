export type MemoryTaskStatus =
  | 'planned'
  | 'in_progress'
  | 'blocked'
  | 'paused'
  | 'completed'
  | 'cancelled'
  | 'superseded'

export interface MemoryTaskState {
  task_id: string
  stream_version: number
  title: string
  goal: string
  status: MemoryTaskStatus
  current_stage: string
  completed: Array<Record<string, unknown>>
  pending: Array<Record<string, unknown>>
  constraints: Array<Record<string, unknown>>
  blocked_by: Array<Record<string, unknown>>
  decisions: Array<Record<string, unknown>>
  artifact_refs: Array<Record<string, unknown>>
  related_file_refs: Array<Record<string, unknown>>
  next_action: string
  updated_at: string
}

export interface MemoryTaskCreateRequest {
  task_id?: string
  title: string
  goal?: string
  project_id?: string
  goal_id?: string
  parent_task_id?: string
  idempotency_key: string
}

export interface MemoryTaskUpdateRequest {
  expected_stream_version: number
  status?: MemoryTaskStatus
  current_stage?: string
  completed?: Array<Record<string, unknown>>
  pending?: Array<Record<string, unknown>>
  constraints?: Array<Record<string, unknown>>
  blocked_by?: Array<Record<string, unknown>>
  decisions?: Array<Record<string, unknown>>
  artifact_refs?: Array<Record<string, unknown>>
  related_file_refs?: Array<Record<string, unknown>>
  next_action?: string
  idempotency_key: string
}
