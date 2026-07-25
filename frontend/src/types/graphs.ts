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
  retrieval_state: string
  evidence_state: string
  attention_state: string
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
