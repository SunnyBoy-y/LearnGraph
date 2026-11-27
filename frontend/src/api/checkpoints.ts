import { apiClient } from "./client";

export interface CheckpointTurnSummary {
  turn_id: string;
  file_count: number;
  paths: string[];
  created_at: string;
}

export interface CheckpointRewindItem {
  path: string;
  action: "restore" | "delete" | "noop";
  existed_before: boolean;
  pre_sha256: string | null;
  expected_sha256: string | null;
  current_sha256: string | null;
  conflict: boolean;
  conflict_reason: string | null;
}

export interface CheckpointRewindPreview {
  chat_session_id: string;
  turn_id: string;
  restore_count: number;
  delete_count: number;
  conflict_count: number;
  noop_count: number;
  expected_current_hashes: Record<string, string | null>;
  items: CheckpointRewindItem[];
}

export interface CheckpointRewindResult {
  chat_session_id: string;
  turn_id: string;
  restored_paths: string[];
  deleted_paths: string[];
  skipped_paths: string[];
  conflicts: CheckpointRewindItem[];
}

export function listSessionCheckpoints(
  sessionId: string,
): Promise<{ items: CheckpointTurnSummary[] }> {
  return apiClient.get<{ items: CheckpointTurnSummary[] }>(
    `/sessions/${encodeURIComponent(sessionId)}/checkpoints`,
  );
}

export function previewTurnCheckpointRewind(
  sessionId: string,
  turnId: string,
  includeSubsequentTurns = true,
): Promise<CheckpointRewindPreview> {
  return apiClient.get<CheckpointRewindPreview>(
    `/sessions/${encodeURIComponent(sessionId)}/checkpoints/${encodeURIComponent(turnId)}/preview`,
    {
      query: {
        include_subsequent_turns: includeSubsequentTurns ? "true" : "false",
      },
    },
  );
}

export function executeTurnCheckpointRewind(
  sessionId: string,
  turnId: string,
  payload: {
    expected_current_hashes?: Record<string, string | null>;
    include_subsequent_turns?: boolean;
  } = {},
): Promise<CheckpointRewindResult> {
  return apiClient.post<
    CheckpointRewindResult,
    {
      expected_current_hashes?: Record<string, string | null>;
      include_subsequent_turns?: boolean;
    }
  >(
    `/sessions/${encodeURIComponent(sessionId)}/checkpoints/${encodeURIComponent(turnId)}/rewind`,
    payload,
  );
}
