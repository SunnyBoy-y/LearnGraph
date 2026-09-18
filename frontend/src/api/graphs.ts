import type {
  Graph,
  GraphCoverAIJobView,
  GraphCoverAIRequest,
  GraphCoverDraftRequest,
  GraphCoverDraftView,
  GraphCoverMode,
  GraphCoverView,
  GraphRevision,
  GraphNode,
  GraphSummary,
  MultiNodeStudyRequest,
  MultiNodeStudyResponse,
  NodeMerge,
  NodeMergeDecisionRequest,
  NodeMergePreview,
  NodeMergePreviewRequest,
  UpdateGraphNodeRequest,
} from '@/types/graphs'

import { apiClient } from './client'

export function listGraphs(): Promise<GraphSummary[]> {
  return apiClient.get<GraphSummary[]>('/graphs')
}

export function getGraph(graphId: string): Promise<Graph> {
  return apiClient.get<Graph>(`/graphs/${encodeURIComponent(graphId)}`)
}

export function updateGraphCover(
  graphId: string,
  payload: {
    mode: GraphCoverMode
    template?: 'ancient' | 'literature' | 'history' | 'science' | 'chemistry' | 'paper' | 'midnight' | 'sunrise'
    image_data_url?: string
    svg?: string
  },
): Promise<GraphCoverView> {
  return apiClient.patch<GraphCoverView>(`/graphs/${encodeURIComponent(graphId)}/cover`, payload)
}

export function getGraphCover(graphId: string): Promise<GraphCoverView> {
  return apiClient.get<GraphCoverView>(
    `/graphs/${encodeURIComponent(graphId)}/cover`,
  )
}

/** Phase 1 of the AI cover flow: draft a brief to review before paying for a drawing. */
export function draftAIGraphCover(
  graphId: string,
  payload: GraphCoverDraftRequest,
): Promise<GraphCoverDraftView> {
  return apiClient.post<GraphCoverDraftView, GraphCoverDraftRequest>(
    `/graphs/${encodeURIComponent(graphId)}/cover/ai/draft`,
    payload,
  )
}

/** Phase 2: submit the confirmed brief; the worker draws it in the background. */
export function startAIGraphCover(
  graphId: string,
  payload: GraphCoverAIRequest,
): Promise<GraphCoverAIJobView> {
  return apiClient.post<GraphCoverAIJobView, GraphCoverAIRequest>(
    `/graphs/${encodeURIComponent(graphId)}/cover/ai`,
    payload,
  )
}

export function getAIGraphCoverStatus(
  graphId: string,
): Promise<GraphCoverAIJobView> {
  return apiClient.get<GraphCoverAIJobView>(
    `/graphs/${encodeURIComponent(graphId)}/cover/ai`,
  )
}

export function cancelAIGraphCover(
  graphId: string,
): Promise<GraphCoverAIJobView> {
  return apiClient.post<GraphCoverAIJobView, Record<string, never>>(
    `/graphs/${encodeURIComponent(graphId)}/cover/ai/cancel`,
    {},
  )
}

export function listGraphRevisions(graphId: string): Promise<GraphRevision[]> {
  return apiClient.get<GraphRevision[]>(
    `/graphs/${encodeURIComponent(graphId)}/revisions`,
  )
}

export function listNodeMerges(): Promise<NodeMerge[]> {
  return apiClient.get<NodeMerge[]>('/graphs/merges')
}

export function previewNodeMerge(
  payload: NodeMergePreviewRequest,
): Promise<NodeMergePreview> {
  return apiClient.post<NodeMergePreview, NodeMergePreviewRequest>(
    '/graphs/merges/preview',
    payload,
  )
}

export function decideNodeMerge(
  payload: NodeMergeDecisionRequest,
): Promise<NodeMerge> {
  return apiClient.post<NodeMerge, NodeMergeDecisionRequest>(
    '/graphs/merges',
    payload,
  )
}

export function undoNodeMerge(mergeId: string): Promise<NodeMerge> {
  return apiClient.post<NodeMerge>(
    `/graphs/merges/${encodeURIComponent(mergeId)}/undo`,
  )
}

export const listNodeQuestions = (graphId: string, nodeId: string) => apiClient.get<Array<{ id: string; content: string; created_at: string }>>(`/graphs/${encodeURIComponent(graphId)}/nodes/${encodeURIComponent(nodeId)}/questions`)

export function updateGraphNode(
  graphId: string,
  nodeId: string,
  payload: UpdateGraphNodeRequest,
): Promise<GraphNode> {
  return apiClient.patch<GraphNode, UpdateGraphNodeRequest>(
    `/graphs/${encodeURIComponent(graphId)}/nodes/${encodeURIComponent(nodeId)}`,
    payload,
  )
}

export function retryGraphNode(
  graphId: string,
  nodeId: string,
  expectedRevision: number,
  instruction: string,
): Promise<GraphNode> {
  return apiClient.post<GraphNode, { expected_revision: number; instruction: string }>(
    `/graphs/${encodeURIComponent(graphId)}/nodes/${encodeURIComponent(nodeId)}/retry`,
    { expected_revision: expectedRevision, instruction },
  )
}

export function deleteGraphNode(
  graphId: string,
  nodeId: string,
  expectedRevision: number,
): Promise<{ resource_id: string }> {
  return apiClient.delete<{ resource_id: string }>(
    `/graphs/${encodeURIComponent(graphId)}/nodes/${encodeURIComponent(nodeId)}`,
    { query: { expected_revision: expectedRevision } },
  )
}

export function studyMultipleNodes(
  graphId: string,
  payload: MultiNodeStudyRequest,
): Promise<MultiNodeStudyResponse> {
  return apiClient.post<MultiNodeStudyResponse, MultiNodeStudyRequest>(
    `/graphs/${encodeURIComponent(graphId)}/multi-node-study`,
    payload,
  )
}
