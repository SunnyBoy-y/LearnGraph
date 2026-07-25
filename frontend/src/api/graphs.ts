import type {
  Graph,
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
