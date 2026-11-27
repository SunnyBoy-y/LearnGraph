import { apiClient } from "./client";

export interface QueuedTurnRecord {
  id: string;
  workspace_id: string;
  session_id: string;
  created_by: string;
  client_request_id: string;
  position: number;
  status: "queued" | "dispatching" | "dispatched" | "cancelled" | string;
  content: string;
  payload: Record<string, unknown>;
  dispatched_message_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface QueuedTurnCreatePayload {
  client_request_id: string;
  content: string;
  mode?: "append" | "interrupt";
  payload?: Record<string, unknown>;
}

export function listQueuedTurns(
  sessionId: string,
): Promise<{ items: QueuedTurnRecord[] }> {
  return apiClient.get<{ items: QueuedTurnRecord[] }>(
    `/sessions/${encodeURIComponent(sessionId)}/queued-turns`,
  );
}

export function enqueueQueuedTurn(
  sessionId: string,
  body: QueuedTurnCreatePayload,
): Promise<QueuedTurnRecord> {
  return apiClient.post<QueuedTurnRecord, QueuedTurnCreatePayload>(
    `/sessions/${encodeURIComponent(sessionId)}/queued-turns`,
    body,
  );
}

export function updateQueuedTurn(
  sessionId: string,
  turnId: string,
  body: { content?: string; payload?: Record<string, unknown> },
): Promise<QueuedTurnRecord> {
  return apiClient.patch<
    QueuedTurnRecord,
    { content?: string; payload?: Record<string, unknown> }
  >(
    `/sessions/${encodeURIComponent(sessionId)}/queued-turns/${encodeURIComponent(turnId)}`,
    body,
  );
}

export function cancelQueuedTurn(
  sessionId: string,
  turnId: string,
): Promise<QueuedTurnRecord> {
  return apiClient.delete<QueuedTurnRecord>(
    `/sessions/${encodeURIComponent(sessionId)}/queued-turns/${encodeURIComponent(turnId)}`,
  );
}

export function promoteQueuedTurn(
  sessionId: string,
  turnId: string,
  options: { cancelActive?: boolean } = {},
): Promise<{ turn: QueuedTurnRecord; cancelled_message_id: string | null }> {
  return apiClient.post<
    { turn: QueuedTurnRecord; cancelled_message_id: string | null },
    { cancel_active: boolean }
  >(
    `/sessions/${encodeURIComponent(sessionId)}/queued-turns/${encodeURIComponent(turnId)}/promote`,
    { cancel_active: Boolean(options.cancelActive) },
  );
}

export function claimQueuedTurn(
  sessionId: string,
  turnId: string,
): Promise<QueuedTurnRecord> {
  return apiClient.post<QueuedTurnRecord, Record<string, never>>(
    `/sessions/${encodeURIComponent(sessionId)}/queued-turns/${encodeURIComponent(turnId)}/claim`,
    {},
  );
}

export function completeQueuedTurn(
  sessionId: string,
  turnId: string,
  dispatchedMessageId?: string,
): Promise<QueuedTurnRecord> {
  return apiClient.post<
    QueuedTurnRecord,
    { dispatched_message_id?: string }
  >(
    `/sessions/${encodeURIComponent(sessionId)}/queued-turns/${encodeURIComponent(turnId)}/complete`,
    dispatchedMessageId ? { dispatched_message_id: dispatchedMessageId } : {},
  );
}

export function requeueQueuedTurn(
  sessionId: string,
  turnId: string,
): Promise<QueuedTurnRecord> {
  return apiClient.post<QueuedTurnRecord, Record<string, never>>(
    `/sessions/${encodeURIComponent(sessionId)}/queued-turns/${encodeURIComponent(turnId)}/requeue`,
    {},
  );
}
