export interface MemoryEpisode {
  episode_id: string
  stream_version: number
  conversation_id: string
  task_id: string | null
  title: string
  summary: string
  decisions: Array<Record<string, unknown>>
  open_questions: Array<Record<string, unknown>>
  constraints: Array<Record<string, unknown>>
  source_message_refs: string[]
  status: string
  boundary_reason: string
}

export interface EpisodeGenerateRequest {
  conversation_id: string
  task_id?: string
  title: string
  summary?: string
  decisions?: Array<Record<string, unknown>>
  open_questions?: Array<Record<string, unknown>>
  constraints?: Array<Record<string, unknown>>
  entities?: Array<Record<string, unknown>>
  source_message_refs?: string[]
  boundary_reason?: string
  idempotency_key: string
}
