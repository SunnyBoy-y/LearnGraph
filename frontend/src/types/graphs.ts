import type { IsoDateTime } from './common'

export interface GraphNode {
  id: string
  graph_id: string
  workspace_id: string
  label: string
  description: string
  node_type: string
  external_concept_id: string | null
  target_weight: number
  teaching_strategy?: string
  mastery_stars: number
  achievement_score?: number | null
  retrieval_state: string
  evidence_state: string
  attention_state: string
  /** 该节点的学习页（教材 / 互动实验 / 闯关测评）是否已经生成。 */
  has_learning_page?: boolean
}

export interface GraphEdge {
  id: string
  graph_id: string
  workspace_id: string
  source_node_id: string
  target_node_id: string
  relation: string
}

export interface GraphSummary {
  id: string
  goal_id: string
  workspace_id: string
  title: string
  status: string
  revision: number
  published_at: IsoDateTime | null
  cover_svg?: string | null
  /** 该图谱有 AI 封面正在后台生成（书架显示「生成中」角标）。 */
  cover_ai_active?: boolean
}

export type GraphCoverMode = 'generated' | 'template' | 'image' | 'svg'

export interface GraphCoverTemplate {
  id: 'ancient' | 'literature' | 'history' | 'science' | 'chemistry' | 'paper' | 'midnight' | 'sunrise'
  name: string
  cover_svg: string
}

export interface GraphCoverView {
  graph_id: string
  title: string
  graph_revision: number
  node_count: number
  cover_svg: string
  templates: GraphCoverTemplate[]
  used_default: boolean
}

/**
 * AI cover generation. `svg` = the text model draws a vector cover (no
 * image-model spend); `image` = the image provider draws a raster cover.
 */
export type GraphCoverEngine = 'svg' | 'image'

/** `fallback` means the drafting model was unavailable and a template text was
 * provided instead — it is still editable, it is just not model-authored. */
export type GraphCoverDraftSource = 'model' | 'fallback' | 'user_edited'

export interface GraphCoverDraftRequest {
  engine: GraphCoverEngine
  hint?: string
  provider_id?: string
  model_id?: string
}

export interface GraphCoverDraftView {
  engine: GraphCoverEngine
  prompt: string
  prompt_source: Exclude<GraphCoverDraftSource, 'user_edited'>
}

export interface GraphCoverAIRequest {
  engine: GraphCoverEngine
  prompt: string
  prompt_source?: GraphCoverDraftSource
  provider_id?: string
  model_id?: string
}

export type GraphCoverAIStatus =
  | 'idle'
  | 'queued'
  | 'running'
  | 'ready'
  | 'failed'
  | 'cancelled'

export interface GraphCoverAIJobView {
  id: string | null
  graph_id: string | null
  engine: GraphCoverEngine | null
  status: GraphCoverAIStatus
  prompt: string
  prompt_source: string
  provider_id: string | null
  model_id: string | null
  file_id: string | null
  error: string | null
  active: boolean
  created_at: string | null
  updated_at: string | null
}

export interface Graph extends GraphSummary {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface UpdateGraphNodeRequest {
  expected_revision?: number | null
  label?: string | null
  description?: string | null
  attention_state?: string | null
  target_weight?: number | null
}

export interface MultiNodeStudyRequest {
  node_ids: string[]
}

export interface MultiNodeStudyResponse {
  graph_revision: number
  selected_edges: Array<{ edge_id: string; source_node_id: string; target_node_id: string; relation: string }>
  shared_prerequisites: Array<{ node_id: string; label: string; target_node_ids: string[]; edge_ids: string[] }>
  context_basis: 'graph_structure_only'
  source_materials_queried: false
  related: boolean
  relationship: 'related' | 'weakly_related' | 'unrelated'
  rationale: string
  roles: Record<string, string>
  next_actions: string[]
  study_outline: string
  comparison_points: string[]
  exercise_prompt: string | null
  provider: string
}

export type NodeMergeAction = 'merge' | 'related' | 'do_not_merge'

export interface NodeMergePreviewRequest {
  source_node_id: string
  target_node_id: string
}

export interface NodeMergePreview extends NodeMergePreviewRequest {
  recommendation: 'merge' | 'review' | 'related' | 'do_not_merge'
  decision: 'same' | 'related_not_same' | 'different' | 'insufficient'
  can_auto_merge: boolean
  requires_review: boolean
  rationale: string
  evidence: Record<string, unknown>
  provider: string
}

export interface NodeMergeDecisionRequest extends NodeMergePreviewRequest {
  action: NodeMergeAction
  rationale?: string
  user_confirmed?: boolean
}

export interface NodeMerge {
  id: string
  workspace_id: string
  source_node_id: string
  target_node_id: string
  status: string
  decision_source: string
  rationale: string
  evidence: Record<string, unknown>
  snapshot: Record<string, unknown>
  reverted_at: IsoDateTime | null
  created_at: IsoDateTime
}

export interface GraphRevision {
  id: string
  graph_id: string
  revision: number
  change_type: string
  resource_id: string
  before: Record<string, unknown>
  after: Record<string, unknown>
  actor_id: string
  created_at: IsoDateTime
}
