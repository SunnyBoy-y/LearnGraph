import { apiClient } from "./client";

export interface VoiceTaskWire extends Record<string, unknown> {
  voice_session_id?: string;
  subagent_id?: string;
  task_id?: string;
}

export interface VoiceSessionWire extends Record<string, unknown> {
  id?: string;
  session_id?: string;
  sessionId?: string;
  tasks?: VoiceTaskWire[];
  results?: VoiceResultWire[];
}

export interface VoiceResultWire extends Record<string, unknown> {
  id?: string;
  subagent_id?: string;
  requirement_version?: number;
  result_version?: number;
  status?: string;
  summary?: string;
  safe_error?: string;
  payload?: Record<string, unknown>;
  source_count?: number;
  captured_at?: string;
  available_at?: string;
}

export interface VoiceTaskStartWire {
  prompt: string;
  title?: string;
  role_key?: string;
  thinking_mode?: string;
  tools?: string[];
  skills?: string[];
  write_set?: string[];
  output_contract?: Record<string, unknown>;
  sandbox_session_id?: string;
}

export function startVoiceTask(
  voiceSessionId: string,
  payload: VoiceTaskStartWire,
): Promise<VoiceTaskWire> {
  return apiClient.post<VoiceTaskWire, VoiceTaskStartWire>(
    `/voice/sessions/${encodeURIComponent(voiceSessionId)}/tasks`,
    payload,
  );
}

export function getVoiceTask(
  voiceSessionId: string,
  taskId: string,
  afterEventSeq?: number,
): Promise<VoiceTaskWire> {
  return apiClient.get<VoiceTaskWire>(
    `/voice/sessions/${encodeURIComponent(voiceSessionId)}/tasks/${encodeURIComponent(taskId)}`,
    afterEventSeq === undefined
      ? undefined
      : { query: { after_event_seq: afterEventSeq } },
  );
}

export function cancelVoiceTask(
  voiceSessionId: string,
  taskId: string,
): Promise<VoiceTaskWire> {
  return apiClient.post<VoiceTaskWire, Record<string, never>>(
    `/voice/sessions/${encodeURIComponent(voiceSessionId)}/tasks/${encodeURIComponent(taskId)}/cancel`,
    {},
  );
}

export function getVoiceSession(
  voiceSessionId: string,
  signal?: AbortSignal,
): Promise<VoiceSessionWire> {
  return apiClient.get<VoiceSessionWire>(
    `/voice/sessions/${encodeURIComponent(voiceSessionId)}`,
    signal ? { signal } : undefined,
  );
}
