import type { IsoDateTime, UnknownRecord } from './common'

export interface GoalAvailability {
  minutes_per_day?: number
  days_per_week?: number
}

export interface GoalPreferences {
  preferred_action_types?: string[]
  session_minutes?: number
}

export interface GoalPlanningUpdate {
  target_weight?: number
  deadline_at?: IsoDateTime | null
  availability?: Partial<GoalAvailability>
  preferences?: Partial<GoalPreferences>
}

export interface GoalClarifyRequest {
  prompt: string
  file_ids?: string[]
  graph_context_ids?: string[]
  /** Optional model selection so the Goal wizard reuses the chat-selected model. */
  provider_id?: string
  model_id?: string
  thinking_mode?: "off" | "low" | "medium" | "high" | "xhigh" | null
}

export interface ClarificationQuestion {
  key: string
  prompt: string
  options: string[]
  required: boolean
  reason?: string
  input_type?: string
  allow_custom?: boolean
  allow_skip?: boolean
  graph_impact?: string
  default_assumption?: string | null
}

export interface Goal {
  id: string
  workspace_id: string
  title: string
  raw_prompt: string
  status: string
  intent: string
  time_limit: string
  desired_outcome: string
  constraints: UnknownRecord
  assumptions: UnknownRecord[]
  target_weight: number
  deadline_at: IsoDateTime | null
  availability: GoalAvailability
  preferences: GoalPreferences
  created_at: IsoDateTime
  updated_at: IsoDateTime
}

export interface GoalClarifyResponse {
  goal: Goal
  questions: ClarificationQuestion[]
  provider: string
  remote_model_used: boolean
}

export interface GoalConfirmRequest {
  title: string
  intent?: string
  time_limit?: string
  target_weight?: number
  deadline_at?: IsoDateTime | null
  availability?: GoalAvailability
  preferences?: GoalPreferences
  desired_outcome?: string
  constraints?: UnknownRecord
  assumptions?: UnknownRecord[]
}

export interface CandidateGraphRequest {
  seed_concepts?: string[]
  provider_id?: string
  model_id?: string
  thinking_mode?: "off" | "low" | "medium" | "high" | "xhigh" | null
}

export interface PublishGoalRequest {
  graph_id: string
  expected_revision: number
}

export interface PublishGoalResponse {
  goal: Goal
  graph_id: string
  graph_revision: number
  status: string
}
