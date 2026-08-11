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

/** Streaming candidate-graph generation: mode selects the generation budget. */
export interface CandidateGraphStreamRequest extends CandidateGraphRequest {
  mode?: "fast" | "thinking"
}

export interface GraphStreamNode {
  id: string
  label: string
  description: string
  node_type: string
  target_weight: number
  teaching_strategy?: string
}

export interface GraphStreamEdge {
  id: string
  source_node_id: string
  target_node_id: string
  relation: string
}

/** Stage 1 — the single root preview is persisted and emitted immediately. */
export interface GraphStreamRootEvent {
  graph_id: string
  title: string
  root: GraphStreamNode | null
}

/** Stage 2 — one incremental batch of level-1 children + edges. */
export interface GraphStreamNodesEvent {
  nodes: GraphStreamNode[]
  edges: GraphStreamEdge[]
}

/** Final stage — the full candidate snapshot (review is ready). */
export interface GraphStreamCompleteEvent {
  graph_id: string
  title: string
  revision: number
  status: string
  nodes: GraphStreamNode[]
  edges: GraphStreamEdge[]
}

export interface GraphStreamErrorEvent {
  code: string
  message: string
}

export type GraphStreamEvent =
  | { event: "graph.root"; data: GraphStreamRootEvent }
  | { event: "graph.nodes_added"; data: GraphStreamNodesEvent }
  | { event: "graph.complete"; data: GraphStreamCompleteEvent }
  | { event: "graph.error"; data: GraphStreamErrorEvent }

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
